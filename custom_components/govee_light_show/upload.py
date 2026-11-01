"""POST /api/govee_light_show/shows/upload: add .fseq and song files to the show library."""
from __future__ import annotations

import os
from http import HTTPStatus

from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant

from .library import AUDIO_EXTS, FSEQ_EXT, SHOW_PLAYER_KEY, library_for

MAX_BYTES = 300 * 1024 * 1024
CHUNK = 1 << 20
ALLOWED = (FSEQ_EXT, *AUDIO_EXTS)


def async_register(hass: HomeAssistant) -> None:
    hass.http.register_view(ShowUploadView())


class ShowUploadView(HomeAssistantView):
    url = "/api/govee_light_show/shows/upload"
    name = "api:govee_light_show:shows:upload"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        if not request["hass_user"].is_admin:
            return self.json_message("Admin access required", HTTPStatus.UNAUTHORIZED)
        hass: HomeAssistant = request.app[KEY_HASS]
        library = library_for(hass)
        files: list[tuple[str, str]] = []  # (original name, temp path)
        touched: list[str] = []
        show: str | None = None
        try:
            reader = await request.multipart()
            while (part := await reader.next()) is not None:
                if part.name == "show":
                    show = (await part.text()).strip() or None
                    continue
                if part.name != "file" or not part.filename:
                    continue
                if not part.filename.lower().endswith(ALLOWED):
                    return self.json_message(f"{part.filename}: only .fseq and audio files", HTTPStatus.BAD_REQUEST)
                tmp = await hass.async_add_executor_job(library.new_temp)
                files.append((part.filename, tmp))
                size = 0
                f = await hass.async_add_executor_job(open, tmp, "wb")
                try:
                    while chunk := await part.read_chunk(CHUNK):
                        size += len(chunk)
                        if size > MAX_BYTES:
                            return self.json_message(f"{part.filename} is over 300 MB", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                        await hass.async_add_executor_job(f.write, chunk)
                finally:
                    await hass.async_add_executor_job(f.close)
            if not files:
                return self.json_message("No files in the upload", HTTPStatus.BAD_REQUEST)
            for name, tmp in files:
                try:
                    show_id = await hass.async_add_executor_job(library.commit_upload, tmp, name, show)
                except ValueError as err:
                    return self.json_message(f"{name}: {err}", HTTPStatus.BAD_REQUEST)
                if show_id not in touched:
                    touched.append(show_id)
        finally:
            await hass.async_add_executor_job(_cleanup, [tmp for _, tmp in files])
        if player := hass.data.get(SHOW_PLAYER_KEY):
            await player.async_refresh()
        return self.json({"shows": touched})


def _cleanup(paths: list[str]) -> None:
    for p in paths:
        if os.path.exists(p):
            os.unlink(p)
