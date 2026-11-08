"""POST /api/light_show_studio/shows/upload: add .fseq and song files to the show library."""
from __future__ import annotations

import logging
import os
from http import HTTPStatus

import aiohttp
from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from .library import AUDIO_EXTS, FSEQ_EXT, SHOW_PLAYER_KEY, invalidate_scan, library_for, sanitize_name, show_id_for

_LOGGER = logging.getLogger(__name__)
MAX_BYTES = 300 * 1024 * 1024  # per file
MAX_TOTAL = 1024 * 1024 * 1024  # per request
MAX_FILES = 20
MAX_PARTS = 60  # form fields of any kind per request
MAX_FIELD = 256  # bytes of the "show" field
CHUNK = 1 << 20
ALLOWED = (FSEQ_EXT, *AUDIO_EXTS)


def async_register(hass: HomeAssistant) -> None:
    hass.http.register_view(ShowUploadView())


class ShowUploadView(HomeAssistantView):
    url = "/api/light_show_studio/shows/upload"
    name = "api:light_show_studio:shows:upload"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            return self.json_message("Admin access required", HTTPStatus.FORBIDDEN)
        # not trusted (the real size is counted while streaming below), but refuses an honest oversize early
        if request.content_length is not None and request.content_length > MAX_TOTAL + (1 << 20):
            return self.json_message("Upload is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        hass: HomeAssistant = request.app[KEY_HASS]
        library = library_for(hass)
        files: list[tuple[str, str]] = []  # (original name, temp path)
        touched: list[str] = []
        show: str | None = None
        total = parts = 0
        try:
            reader = await request.multipart()
            while (part := await reader.next()) is not None:
                parts += 1
                if parts > MAX_PARTS:
                    return self.json_message("Too many form fields", HTTPStatus.BAD_REQUEST)
                if not isinstance(part, aiohttp.BodyPartReader):  # a nested multipart is not accepted
                    return self.json_message("Invalid upload", HTTPStatus.BAD_REQUEST)
                if part.name == "show":
                    raw = b""
                    while (piece := await part.read_chunk(MAX_FIELD + 1)) and len(raw) <= MAX_FIELD:
                        raw += piece  # never read an unbounded field
                    if len(raw) > MAX_FIELD:
                        return self.json_message("Show name is too long", HTTPStatus.BAD_REQUEST)
                    try:
                        show = raw.decode("utf-8").strip() or None
                        if show:
                            sanitize_name(show)
                    except (UnicodeDecodeError, ValueError):
                        return self.json_message("Invalid show name", HTTPStatus.BAD_REQUEST)
                    continue
                if part.name != "file" or not part.filename:
                    continue
                if len(files) >= MAX_FILES:
                    return self.json_message(f"At most {MAX_FILES} files per upload", HTTPStatus.BAD_REQUEST)
                if not part.filename.lower().endswith(ALLOWED):
                    return self.json_message("Only .fseq and audio files", HTTPStatus.BAD_REQUEST)
                try:
                    sanitize_name(part.filename)
                except ValueError:
                    return self.json_message("Invalid file name", HTTPStatus.BAD_REQUEST)
                tmp = await hass.async_add_executor_job(library.new_temp)
                files.append((part.filename, tmp))
                size = 0
                f = await hass.async_add_executor_job(open, tmp, "wb")
                try:
                    while chunk := await part.read_chunk(CHUNK):
                        size += len(chunk)
                        total += len(chunk)
                        if size > MAX_BYTES:
                            return self.json_message("A file is over 300 MB", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                        if total > MAX_TOTAL:
                            return self.json_message("Upload is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                        await hass.async_add_executor_job(f.write, chunk)
                finally:
                    await hass.async_add_executor_job(f.close)
            if not files:
                return self.json_message("No files in the upload", HTTPStatus.BAD_REQUEST)
            player = hass.data.get(SHOW_PLAYER_KEY)
            for name, tmp in files:
                try:
                    show_id = show_id_for(name, show)
                    if player and player.show and player.show.id == show_id and player.state != "idle":
                        await player.async_stop(reason="show_replaced")  # its files are about to change under it
                    show_id = await hass.async_add_executor_job(library.commit_upload, tmp, name, show)
                except ValueError as err:
                    _LOGGER.warning("Upload of %r refused: %s", name, err)
                    return self.json_message("File not accepted", HTTPStatus.BAD_REQUEST)
                except OSError:
                    _LOGGER.exception("Could not store the upload %r", name)
                    return self.json_message("Couldn't store the upload", HTTPStatus.INTERNAL_SERVER_ERROR)
                if show_id not in touched:
                    touched.append(show_id)
        except (aiohttp.ClientError, ValueError):  # a malformed multipart body
            _LOGGER.warning("Malformed upload request", exc_info=True)
            return self.json_message("Invalid upload", HTTPStatus.BAD_REQUEST)
        except OSError:
            _LOGGER.exception("Could not store an upload")
            return self.json_message("Couldn't store the upload", HTTPStatus.INTERNAL_SERVER_ERROR)
        finally:
            await hass.async_add_executor_job(_cleanup, [tmp for _, tmp in files])
            invalidate_scan(hass)
        if player := hass.data.get(SHOW_PLAYER_KEY):
            await player.async_refresh()
        return self.json({"shows": touched})


def _cleanup(paths: list[str]) -> None:
    for p in paths:
        try:
            if os.path.lexists(p):
                os.unlink(p)
        except OSError:
            pass
