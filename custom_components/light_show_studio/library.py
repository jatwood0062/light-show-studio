"""The show library: .fseq files (and their songs) in <media>/xlights. All methods block."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO

from .fseq import FseqFile

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

SHOW_PLAYER_KEY = "light_show_studio_show_player"  # hass.data key of the ShowPlayer
AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac")
FSEQ_EXT = ".fseq"
PLAYLIST = "playlist.json"
STAGING = ".uploading"  # temp files for uploads, hidden from scans
EXTRA_NAME_CHARS = " -_.,'()&!"
MAX_NAME_LEN = 120  # of a file or folder name
STALE_STAGING = 24 * 3600  # seconds before a leftover temp upload is swept away
RESERVED_NAMES = frozenset({PLAYLIST.casefold(), STAGING.casefold()})  # can't be show ids (any case)
SCAN_CACHE_KEY = "light_show_studio_scan_cache"
SCAN_TTL = 3.0  # seconds a library scan is reused by shows/list


@dataclass
class Show:
    id: str
    name: str
    fseq: str
    audio: str | None
    duration: float
    frames: int
    size: int


def sanitize_name(name: str) -> str:
    """A safe single file or folder name: path parts stripped, odd characters dropped."""
    if "\0" in name:
        raise ValueError("invalid name")
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if name.startswith("."):
        raise ValueError(f"invalid name {name!r}")
    name = "".join(c for c in name if c.isalnum() or c in EXTRA_NAME_CHARS).strip()
    if not name or name.startswith(".") or len(name) > MAX_NAME_LEN or name.casefold() in RESERVED_NAMES:
        raise ValueError("invalid name")
    return name


def show_id_for(filename: str, show: str | None = None) -> str:
    """The show id an upload of `filename` (into `show`, if given) lands in. ValueError for bad names."""
    return sanitize_name(show) if show else sanitize_name(os.path.splitext(sanitize_name(filename))[0])


async def async_scan(hass: HomeAssistant, ttl: float = SCAN_TTL) -> list[Show]:
    """The library scan, reused for `ttl` seconds (shows/list is called often); changes drop it at once."""
    state = hass.data.setdefault(SCAN_CACHE_KEY, {"at": 0.0, "shows": None, "gen": 0})
    if state["shows"] is not None and time.monotonic() - state["at"] < ttl:
        return state["shows"]
    gen = state["gen"]
    shows = await hass.async_add_executor_job(library_for(hass).scan)
    if state["gen"] == gen:
        state["shows"], state["at"] = shows, time.monotonic()
    return shows


def invalidate_scan(hass: HomeAssistant) -> None:
    if state := hass.data.get(SCAN_CACHE_KEY):
        state["shows"] = None
        state["gen"] += 1


def media_root(hass: HomeAssistant) -> tuple[str, str]:
    """(media source id, directory) of the first media dir, preferring `local`."""
    dirs = hass.config.media_dirs
    if "local" in dirs:
        return "local", dirs["local"]
    if dirs:
        key = next(iter(dirs))
        return key, dirs[key]
    return "local", hass.config.path("media")


def library_for(hass: HomeAssistant) -> ShowLibrary:
    """The running player's library, or a fresh one for the same folder."""
    if player := hass.data.get(SHOW_PLAYER_KEY):
        return player.library
    source, base = media_root(hass)
    return ShowLibrary(os.path.join(base, "xlights"), base, source)


class ShowLibrary:
    def __init__(self, root: str, media_dir: str | None = None, source_id: str = "local") -> None:
        self.root = root
        self.media_dir = media_dir or os.path.dirname(root)
        self.source_id = source_id
        self._meta: dict[str, tuple[float, int, float, int]] = {}  # fseq path -> mtime, size, secs, frames
        self._lock = threading.RLock()  # one change to the library (upload, delete, reorder, playlist write) at a time

    # --- paths ------------------------------------------------------------
    def _real_root(self) -> str:
        if not os.path.isdir(self.root):
            os.makedirs(self.root, exist_ok=True)
        return os.path.realpath(self.root)

    def _inside(self, path: str) -> str:
        """The real path, which must be below the root."""
        root, real = self._real_root(), os.path.realpath(path)
        if real != root and not real.startswith(root + os.sep):
            raise ValueError(f"{path} is outside the show library")
        return real

    def media_source_id(self, path: str) -> str:
        """media-source:// id for a file in the library, for resolving a URL a speaker can fetch."""
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(self.media_dir))
        if rel == os.pardir or rel.startswith(os.pardir + os.sep):
            raise ValueError(f"{path} is outside the media folder")
        return f"media-source://media_source/{self.source_id}/{rel.replace(os.sep, '/')}"

    # --- reading ----------------------------------------------------------
    def _header(self, path: str) -> tuple[float, int]:
        st = os.stat(path)
        cached = self._meta.get(path)
        if cached and cached[:2] == (st.st_mtime, st.st_size):
            return cached[2], cached[3]
        try:
            seq = FseqFile(path)
            secs, frames = seq.duration, seq.frames
        except Exception:  # noqa: BLE001  a broken file still shows up (duration 0), so it can be deleted
            secs, frames = 0.0, 0
        self._meta[path] = (st.st_mtime, st.st_size, secs, frames)
        return secs, frames

    def _show(self, id_: str, fseq: str, audio: str | None) -> Show:
        secs, frames = self._header(fseq)
        size = os.path.getsize(fseq) + (os.path.getsize(audio) if audio else 0)
        return Show(id_, id_, fseq, audio, secs, frames, size)

    def scan(self) -> list[Show]:
        """All shows in playlist order, then the rest A to Z."""
        root = self._real_root()
        found: dict[str, Show] = {}
        loose: dict[str, dict[str, str]] = {}
        for entry in sorted(os.listdir(root)):
            if entry.startswith(".") or entry == PLAYLIST:
                continue
            path = os.path.join(root, entry)
            if os.path.islink(path):  # a link could lead anywhere: never followed
                continue
            if os.path.isdir(path):
                files = sorted(f for f in os.listdir(path) if not f.startswith(".") and not os.path.islink(os.path.join(path, f))
                               and os.path.isfile(os.path.join(path, f)))
                fseq = next((f for f in files if f.lower().endswith(FSEQ_EXT)), None)
                audio = next((f for f in files if f.lower().endswith(AUDIO_EXTS)), None)
                if fseq:
                    found[entry] = self._show(entry, os.path.join(path, fseq),
                                              os.path.join(path, audio) if audio else None)
            elif os.path.isfile(path):
                stem, ext = os.path.splitext(entry)
                ext = ext.lower()
                if ext == FSEQ_EXT:
                    loose.setdefault(stem, {})["fseq"] = path
                elif ext in AUDIO_EXTS:
                    loose.setdefault(stem, {}).setdefault("audio", path)
        for stem, parts in loose.items():
            if "fseq" in parts and stem not in found:  # a folder of the same name wins
                found[stem] = self._show(stem, parts["fseq"], parts.get("audio"))
        order = [i for i in self._read_order() if i in found]
        rest = sorted((i for i in found if i not in order), key=str.casefold)
        return [found[i] for i in order + rest]

    def get(self, id_: str) -> Show | None:
        return next((s for s in self.scan() if s.id == id_), None)

    def _read_order(self) -> list[str]:
        try:
            with open(os.path.join(self.root, PLAYLIST), encoding="utf-8") as f:
                order = json.load(f)["order"]
            return [i for i in order if isinstance(i, str)]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    # --- changing ---------------------------------------------------------
    def _write_order(self, ids: list[str]) -> None:
        with self._lock:
            root = self._real_root()
            fd, tmp = tempfile.mkstemp(dir=root, prefix=".playlist-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"order": ids}, f)
                os.replace(tmp, os.path.join(root, PLAYLIST))
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    def reorder(self, ids: list[str]) -> None:
        """Save the order; unknown ids are dropped and shows not listed go after, A to Z."""
        with self._lock:
            known = {s.id for s in self.scan()}
            seen: list[str] = []
            for i in ids:
                if i in known and i not in seen:
                    seen.append(i)
            self._write_order(seen)

    def delete(self, id_: str) -> bool:
        """Remove a show's folder, or its loose files. False if there is no such show."""
        with self._lock:
            return self._delete(id_)

    def _delete(self, id_: str) -> bool:
        show = self.get(id_)
        if show is None:
            return False
        folder = os.path.dirname(os.path.realpath(show.fseq))
        if folder != self._real_root():
            shutil.rmtree(self._inside(folder))
        else:
            for p in (show.fseq, show.audio):
                if p:
                    os.unlink(self._inside(p))
        self._meta.pop(show.fseq, None)
        self._write_order([i for i in self._read_order() if i != id_])
        return True

    def new_temp(self) -> str:
        """Path of an empty temp file for an upload, on the same filesystem as the library."""
        staging = os.path.join(self._real_root(), STAGING)
        os.makedirs(staging, exist_ok=True)
        self._sweep(staging)
        fd, tmp = tempfile.mkstemp(dir=staging)
        os.close(fd)
        return tmp

    @staticmethod
    def _sweep(staging: str) -> None:
        """Remove temp uploads left behind by a crash or a dropped connection."""
        now = time.time()
        for name in os.listdir(staging):
            path = os.path.join(staging, name)
            try:
                if os.path.isfile(path) and not os.path.islink(path) and now - os.path.getmtime(path) > STALE_STAGING:
                    os.unlink(path)
            except OSError:
                pass

    def commit_upload(self, tmp: str, filename: str, show: str | None = None) -> str:
        """Move a finished temp file into its show folder (replacing that show's old file of the
        same kind) and return the show id. ValueError for names or types that aren't allowed."""
        name = sanitize_name(filename)
        stem, ext = os.path.splitext(name)
        ext = ext.lower()
        if ext != FSEQ_EXT and ext not in AUDIO_EXTS:
            raise ValueError(f"{ext or 'files without an extension'} is not allowed")
        with self._lock:
            return self._commit(tmp, stem, ext, sanitize_name(show) if show else sanitize_name(stem))

    def _commit(self, tmp: str, stem: str, ext: str, show_id: str) -> str:
        folder = self._inside(os.path.join(self._real_root(), show_id))
        if os.path.islink(os.path.join(self._real_root(), show_id)):
            raise ValueError("refusing to write through a symbolic link")
        if not os.path.isfile(tmp) or os.path.islink(tmp):
            raise ValueError("the upload is missing")
        os.makedirs(folder, exist_ok=True)
        kinds = (FSEQ_EXT,) if ext == FSEQ_EXT else AUDIO_EXTS
        dest_path = os.path.join(folder, stem + ext)
        if os.path.islink(dest_path) or os.path.islink(folder):
            raise ValueError("refusing to write through a symbolic link")
        dest = self._inside(dest_path)
        os.replace(tmp, dest)  # the new file is in place before the old one goes
        for old in os.listdir(folder):
            path = os.path.join(folder, old)
            if old.lower().endswith(kinds) and os.path.realpath(path) != dest:
                os.unlink(path)
        return show_id

    def save_upload(self, filename: str, stream: BinaryIO, show: str | None = None) -> str:
        """Store a file from a binary stream; returns the show id."""
        tmp = self.new_temp()
        try:
            with open(tmp, "wb") as f:
                shutil.copyfileobj(stream, f, 1 << 20)
            return self.commit_upload(tmp, filename, show)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
