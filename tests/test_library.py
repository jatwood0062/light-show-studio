"""Tests for the show library; plain pytest, no Home Assistant needed."""
from __future__ import annotations

import importlib.util
import io
import os
import struct
import sys
import types
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"


def _load(name: str):
    """Import a module of the integration without running its __init__ (which needs Home Assistant)."""
    pkg = sys.modules.setdefault("light_show_studio_under_test", types.ModuleType("light_show_studio_under_test"))
    pkg.__path__ = [str(PKG)]
    full = f"light_show_studio_under_test.{name}"
    if full not in sys.modules:
        spec = importlib.util.spec_from_file_location(full, PKG / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
    return sys.modules[full]


library = _load("library")
ShowLibrary, sanitize_name = library.ShowLibrary, library.sanitize_name


def fseq_bytes(frames: int = 100, step_ms: int = 50, channels: int = 6) -> bytes:
    """A minimal uncompressed FSEQ v2 file."""
    data_off = 32
    head = bytearray(data_off)
    head[0:4] = b"PSEQ"
    struct.pack_into("<HBBHIIB", head, 4, data_off, 0, 2, data_off, channels, frames, step_ms)
    return bytes(head) + bytes(frames * channels)


@pytest.fixture
def lib(tmp_path):
    return ShowLibrary(str(tmp_path / "xlights"), str(tmp_path), "local")


def put(lib, rel: str, data: bytes = b"x") -> str:
    path = os.path.join(lib.root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def test_scan_empty_creates_root(lib):
    assert lib.scan() == []
    assert os.path.isdir(lib.root)


def test_scan_folders_and_loose_files(lib):
    put(lib, "Carol/Carol.fseq", fseq_bytes(100, 50))
    put(lib, "Carol/song.mp3", b"abc")
    put(lib, "My Show.fseq", fseq_bytes(200, 25))
    put(lib, "My Show.m4a", b"12345")
    put(lib, "NoAudio/a.fseq", fseq_bytes())
    put(lib, "Empty/readme.txt")
    put(lib, "Lonely.mp3")  # a song with no sequence is not a show
    shows = {s.id: s for s in lib.scan()}
    assert set(shows) == {"Carol", "My Show", "NoAudio"}
    carol = shows["Carol"]
    assert carol.name == "Carol" and carol.audio.endswith("song.mp3")
    assert carol.frames == 100 and carol.duration == pytest.approx(5.0)
    assert carol.size == os.path.getsize(carol.fseq) + 3
    assert shows["My Show"].audio.endswith("My Show.m4a")
    assert shows["My Show"].duration == pytest.approx(5.0)
    assert shows["NoAudio"].audio is None


def test_default_order_is_alphabetical_and_playlist_wins(lib):
    for n in ("b", "A", "c"):
        put(lib, f"{n}/{n}.fseq", fseq_bytes())
    assert [s.id for s in lib.scan()] == ["A", "b", "c"]
    lib.reorder(["c", "ghost", "c"])
    assert [s.id for s in lib.scan()] == ["c", "A", "b"]  # unlisted go after, A to Z
    lib.reorder(["b", "c", "A"])
    assert [s.id for s in lib.scan()] == ["b", "c", "A"]
    assert not [f for f in os.listdir(lib.root) if f.endswith(".tmp")]


def test_broken_fseq_still_listed(lib):
    put(lib, "Bad/bad.fseq", b"not a sequence")
    (show,) = lib.scan()
    assert show.duration == 0 and show.frames == 0


def test_header_cache_follows_mtime(lib):
    path = put(lib, "S/s.fseq", fseq_bytes(100))
    assert lib.scan()[0].frames == 100
    with open(path, "wb") as f:
        f.write(fseq_bytes(40))
    os.utime(path, (1, 1))
    assert lib.scan()[0].frames == 40


def test_delete_folder_and_loose(lib):
    put(lib, "Folder/a.fseq", fseq_bytes())
    put(lib, "Folder/a.mp3")
    put(lib, "Loose.fseq", fseq_bytes())
    put(lib, "Loose.mp3")
    put(lib, "Keep.fseq", fseq_bytes())
    lib.reorder(["Loose", "Folder", "Keep"])
    assert lib.delete("Folder") is True
    assert not os.path.exists(os.path.join(lib.root, "Folder"))
    assert lib.delete("Loose") is True
    assert sorted(os.listdir(lib.root)) == ["Keep.fseq", "playlist.json"]
    assert lib.delete("Loose") is False
    assert [s.id for s in lib.scan()] == ["Keep"]


def test_delete_never_leaves_root(lib, tmp_path):
    victim = tmp_path / "outside"
    victim.mkdir()
    (victim / "a.fseq").write_bytes(fseq_bytes())
    assert lib.delete("../outside") is False
    assert lib.delete("..") is False
    assert (victim / "a.fseq").exists()


def test_symlink_out_of_root_is_refused(lib, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "a.fseq").write_bytes(fseq_bytes())
    os.makedirs(lib.root, exist_ok=True)
    os.symlink(outside, os.path.join(lib.root, "Linked"))
    assert lib.scan() == []  # links are never followed, so the show isn't even listed
    assert lib.delete("Linked") is False
    assert (outside / "a.fseq").exists()


@pytest.mark.parametrize("raw, clean", [
    ("../../etc/passwd", "passwd"),
    ("C:\\evil\\song.mp3", "song.mp3"),
    ("My Show (v2) & more!.fseq", "My Show (v2) & more!.fseq"),
    ("a<b>c|d*e?.mp3", "abcde.mp3"),
    ("  spaced  ", "spaced"),
    ("Caf\u00e9, Jack's-Mix_1.wav", "Caf\u00e9, Jack's-Mix_1.wav"),
])
def test_sanitize_name(raw, clean):
    assert sanitize_name(raw) == clean


@pytest.mark.parametrize("raw", ["", "   ", ".hidden", "..", "a/..", "/", "***", ". x"])
def test_sanitize_rejects(raw):
    with pytest.raises(ValueError):
        sanitize_name(raw)


def test_save_upload_groups_into_show_folder(lib):
    assert lib.save_upload("My Show.fseq", io.BytesIO(fseq_bytes(80))) == "My Show"
    assert lib.save_upload("My Show.MP3", io.BytesIO(b"song")) == "My Show"
    (show,) = lib.scan()
    assert show.id == "My Show" and show.frames == 80 and show.audio.endswith("My Show.mp3")
    # a different song replaces the old one instead of piling up
    lib.save_upload("other.m4a", io.BytesIO(b"new"), show="My Show")
    assert sorted(os.listdir(os.path.join(lib.root, "My Show"))) == ["My Show.fseq", "other.m4a"]
    assert not os.listdir(os.path.join(lib.root, ".uploading"))


def test_save_upload_rejects_bad_input(lib):
    with pytest.raises(ValueError):
        lib.save_upload("virus.exe", io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        lib.save_upload("a.fseq", io.BytesIO(b"x"), show="..")
    assert lib.save_upload("../../x.fseq", io.BytesIO(fseq_bytes())) == "x"
    assert os.listdir(lib.root).count("x") == 1


def test_media_source_id(lib):
    path = put(lib, "Show One/a.mp3")
    assert lib.media_source_id(path) == "media-source://media_source/local/xlights/Show One/a.mp3"


def test_commit_upload_replaces_old_file_after_new_is_in_place(lib):
    show = lib.root + "/Song"
    os.makedirs(show)
    Path(show, "old.fseq").write_bytes(fseq_bytes())
    Path(show, "old.mp3").write_bytes(b"audio")
    tmp = lib.new_temp()
    Path(tmp).write_bytes(fseq_bytes(frames=10))
    assert lib.commit_upload(tmp, "new.fseq", "Song") == "Song"
    assert sorted(os.listdir(show)) == ["new.fseq", "old.mp3"]  # audio kept, old fseq replaced
    tmp = lib.new_temp()
    Path(tmp).write_bytes(fseq_bytes(frames=5))
    lib.commit_upload(tmp, "new.fseq", "Song")  # same name: replaced in place, not deleted
    assert Path(show, "new.fseq").stat().st_size == len(fseq_bytes(frames=5))
