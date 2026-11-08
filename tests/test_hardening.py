"""Security hardening: per-light permissions (M1), pattern limits and rate limits (M5, L8), identity checks on
session / preview ends (L1), the library lock (L5) and reserved show ids (L6). Plain pytest (see wsharness)."""
from __future__ import annotations

import asyncio
import threading

import pytest

import wsharness as H
from wsharness import FakeConn, FakeLight, FakeMgr, FakeUser, aio, call, palette, pixels

const = H.mod("const")
ws_music, ws_patterns = H.mod("ws_music"), H.mod("ws_patterns")
mm, patternpreview, pattern_store = H.mod("musicmanager"), H.mod("patternpreview"), H.mod("pattern_store")
library, up_mod, patterns = H.mod("library"), H.user_patterns, H.patterns

ENTITIES = {"e1": ["light.e1", "light.e1_bulb_1"], "e2": ["light.e2"]}


@pytest.fixture(autouse=True)
def entities(monkeypatch):
    monkeypatch.setattr(ws_music, "light_entities", lambda hass, entry_id: ENTITIES.get(entry_id, []))


def music_world(hass):
    mgr = FakeMgr(hass, {"e1": FakeLight(hass, 10, entry_id="e1"), "e2": FakeLight(hass, 6, entry_id="e2")})
    hass.data[const.MUSIC_KEY] = mm.MusicManager(hass, mgr)
    return mgr


def preview_world(hass):
    mgr = FakeMgr(hass, {"e1": FakeLight(hass, 10, entry_id="e1"), "e2": FakeLight(hass, 6, entry_id="e2")})
    hass.data[const.PREVIEW_KEY] = patternpreview.PreviewManager(hass, mgr, idle=3.0)
    return mgr


# --- M1: per-light permissions ------------------------------------------------------------------------------
@aio
async def test_music_start_denied_without_permission_and_allowed_with_it(hass):
    music_world(hass)
    msg = {"type": "light_show_studio/music/start", "targets": {"lights": ["e1"]}}
    nobody = FakeConn(FakeUser(admin=False, allowed={"light.e2"}))
    await call(ws_music.ws_start, hass, nobody, msg)
    assert nobody.errors[-1][1] == "unauthorized" and not hass.data[const.MUSIC_KEY].sessions
    partial = FakeConn(FakeUser(admin=False, allowed={"light.e1"}))  # one of the light's two entities is not enough
    await call(ws_music.ws_start, hass, partial, msg)
    assert partial.errors[-1][1] == "unauthorized"
    allowed = FakeConn(FakeUser(admin=False, allowed={"light.e1", "light.e1_bulb_1"}))
    await call(ws_music.ws_start, hass, allowed, msg)
    assert not allowed.errors and allowed.results[-1][1]["lights"] == ["e1"]
    admin = FakeConn()
    await call(ws_music.ws_start, hass, admin, {**msg, "targets": {"lights": ["e2"]}})
    assert not admin.errors
    for c in (allowed, admin):
        c.close()


@aio
async def test_music_start_resolves_areas_to_their_lights(hass):
    mgr = music_world(hass)
    mgr.model = H.mapping.MapModel(areas=(H.mapping.Area("room", "Room"),),
                                   runs=(H.mapping.Run("r1", "e2", "r1", 0, 6, area="room"),))
    msg = {"type": "light_show_studio/music/start", "targets": {"areas": ["Room"]}}
    no = FakeConn(FakeUser(admin=False, allowed={"light.e1", "light.e1_bulb_1"}))
    await call(ws_music.ws_start, hass, no, msg)
    assert no.errors[-1][1] == "unauthorized"
    yes = FakeConn(FakeUser(admin=False, allowed={"light.e2"}))
    await call(ws_music.ws_start, hass, yes, msg)
    assert not yes.errors
    yes.close()


@aio
async def test_preview_and_paint_check_each_light(hass):
    preview_world(hass)
    no = FakeConn(FakeUser(admin=False, allowed={"light.e2"}))
    await call(ws_patterns.ws_preview, hass, no, {"type": "light_show_studio/patterns/preview", "entry_id": "e1",
                                                  "pattern": palette()})
    assert no.errors[-1][1] == "unauthorized" and not hass.data[const.PREVIEW_KEY].sessions
    await call(ws_patterns.ws_paint, hass, no, {"type": "light_show_studio/patterns/paint", "entry_id": "e1",
                                                "set": [[0, 1, 2, 3]]})
    assert no.errors[-1][1] == "unauthorized"
    yes = FakeConn(FakeUser(admin=False, allowed={"light.e1", "light.e1_bulb_1"}))
    await call(ws_patterns.ws_preview, hass, yes, {"type": "light_show_studio/patterns/preview", "entry_id": "e1",
                                                   "pattern": pixels(lights={"e1": {"colours": [[0, 0, 0]] * 10}})})
    assert not yes.errors
    await call(ws_patterns.ws_paint, hass, yes, {"type": "light_show_studio/patterns/paint", "entry_id": "e1",
                                                 "set": [[0, 255, 0, 0]]})
    assert yes.results[-1][1] == {"painted": 1}
    yes.close()


# --- M2: area lookups are cached, rate limit per user ------------------------------------------------------
@aio
async def test_frames_without_areas_never_build_the_model_and_area_lookups_are_cached(hass):
    mgr = music_world(hass)
    built = []

    class Counting:
        @property
        def model(self_inner):
            built.append(1)
            return H.mapping.MapModel(areas=(H.mapping.Area("room", "Room"),))
    mgr.__class__ = type("M", (FakeMgr,), {"model": Counting.model})
    music = hass.data[const.MUSIC_KEY]
    assert music._unit_set(["e1"], []) == {("light", "e1")} and not built
    assert music._unit_set([], ["Room"]) == {("area", "room")} and len(built) == 1
    music._unit_set([], ["Room"])
    assert len(built) == 1  # cached
    music._area_ids.clear()  # what the map listener does
    music._unit_set([], ["Room"])
    assert len(built) == 2


# --- M5 / L8 -------------------------------------------------------------------------------------------------
def test_validate_fails_early_on_too_many_painted():
    lights = {f"e{i}": {"colours": [[1, 2, 3]] * 2000} for i in range(10)}
    with pytest.raises(ValueError, match="At most"):
        up_mod.validate(pixels(lights=lights), builtin_names=(), effects=patterns.PATTERN_EFFECTS)
    pts = [{"pos": [1, 2, 3], "colour": [1, 2, 3]}] * 2000
    areas = {f"a{i}": pts for i in range(7)}
    with pytest.raises(ValueError, match="At most"):
        up_mod.validate(pixels(areas=areas), builtin_names=(), effects=patterns.PATTERN_EFFECTS)


@aio
async def test_global_painted_budget(hass):
    store = pattern_store.PatternStore(hass)
    store._store = H.FakeStore(hass, 1, const.USER_PATTERNS_STORE_KEY)
    up_mod.MAX_TOTAL_PAINTED, was = 25, up_mod.MAX_TOTAL_PAINTED
    one = lambda name: pixels(name, lights={"e1": {"colours": [[1, 2, 3]] * 10}})  # noqa: E731
    saved = await store.async_save_pattern(one("A"))
    await store.async_save_pattern(one("B"))
    with pytest.raises(ValueError, match="across all"):
        await store.async_save_pattern(one("C"))
    await store.async_save_pattern({**one("A"), "id": saved["id"]})  # replacing one doesn't count it twice
    await store.async_delete_pattern(saved["id"])
    await store.async_save_pattern(one("C"))
    up_mod.MAX_TOTAL_PAINTED = was


@aio
async def test_preview_calls_are_rate_limited_per_connection(hass):
    preview_world(hass)
    conn = FakeConn()
    msg = {"type": "light_show_studio/patterns/preview", "entry_id": "e1", "pattern": palette()}
    for _ in range(30):
        await call(ws_patterns.ws_preview, hass, conn, msg)
    assert conn.errors and all(e[1] == "rate_limited" for e in conn.errors)
    assert 8 <= len(conn.results) <= 12
    other = FakeConn()
    await call(ws_patterns.ws_preview, hass, other, msg)
    assert other.results and not other.errors
    conn.close()
    other.close()
    assert not [k for k in ws_patterns._limits if k[1] in (id(conn), id(other))]


# --- L1: identity checks --------------------------------------------------------------------------------------
@aio
async def test_stopping_an_old_music_session_leaves_a_newer_one_with_the_same_id(hass):
    music_world(hass)
    music = hass.data[const.MUSIC_KEY]
    a = FakeConn()
    await call(ws_music.ws_start, hass, a, {"type": "light_show_studio/music/start", "targets": {"lights": ["e1"]}})
    old = next(iter(music.sessions.values()))
    await call(ws_music.ws_start, hass, a, {"type": "light_show_studio/music/start", "targets": {"lights": ["e1"]}})
    new = next(iter(music.sessions.values()))
    assert new is not old and new.id == old.id
    music.stop_session(old)
    assert music.sessions.get(new.id) is new
    music._ended(old, old.runner)
    assert music.sessions.get(new.id) is new
    a.close()


@aio
async def test_an_old_preview_cannot_stop_or_idle_out_a_newer_one(hass):
    preview_world(hass)
    pm = hass.data[const.PREVIEW_KEY]
    conn = FakeConn()
    await pm.async_start(conn, "e1", None, palette(), "Steady", 50)
    old = pm.sessions[id(conn)]
    await pm.async_start(conn, "e1", None, palette(), "Steady", 50)
    new = pm.sessions[id(conn)]
    assert new is not old
    assert pm.stop(conn, only=old) is False and pm.sessions[id(conn)] is new
    pm._idle(old, conn)
    assert pm.sessions[id(conn)] is new
    assert pm.stop(conn) is True and not pm.sessions
    for t in list(hass.tasks):
        t.cancel()


# --- L5 / L6: the library -----------------------------------------------------------------------------------
def test_library_changes_wait_for_the_lock(tmp_path):
    lib = library.ShowLibrary(str(tmp_path / "xlights"))
    done = threading.Event()
    lib._lock.acquire()
    try:
        t = threading.Thread(target=lambda: (lib._write_order(["a"]), done.set()))
        t.start()
        assert not done.wait(0.3)  # blocked while someone else changes the library
    finally:
        lib._lock.release()
    t.join(2)
    assert done.is_set()
    for fn in (lambda: lib.reorder([]), lambda: lib.delete("nope")):
        t2 = threading.Thread(target=fn)
        with lib._lock:
            t2.start()
            t2.join(0.2)
            assert t2.is_alive()
        t2.join(2)
        assert not t2.is_alive()


def test_concurrent_uploads_and_reorders_leave_a_consistent_library(tmp_path):
    lib = library.ShowLibrary(str(tmp_path / "xlights"))
    errors = []

    def work(i):
        try:
            for j in range(5):
                for name in (f"song{i}.mp3", f"seq{i}.fseq"):
                    tmp = lib.new_temp()
                    with open(tmp, "wb") as f:
                        f.write(b"x" * 10)
                    lib.commit_upload(tmp, name, "Same")
                lib.reorder(["Same"])
        except Exception as err:  # noqa: BLE001
            errors.append(err)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert not errors
    assert len([f for f in (tmp_path / "xlights" / "Same").iterdir()]) == 2  # one of each kind survives
    assert lib._read_order() == ["Same"]


@pytest.mark.parametrize("bad", ["playlist.json", "Playlist.JSON", "PLAYLIST.json", ".uploading", ".Uploading"])
def test_reserved_names_are_not_show_ids(bad, tmp_path):
    with pytest.raises(ValueError):
        library.sanitize_name(bad)
    lib = library.ShowLibrary(str(tmp_path / "xlights"))
    tmp = lib.new_temp()
    with pytest.raises(ValueError):
        lib.commit_upload(tmp, "song.mp3", bad)
    with pytest.raises(ValueError):
        lib.commit_upload(tmp, bad + ".fseq")  # the id would come from the file name
    assert library.sanitize_name("My playlist") == "My playlist"


def test_real_root_is_only_created_when_missing(tmp_path, monkeypatch):
    lib = library.ShowLibrary(str(tmp_path / "xlights"))
    calls = []
    real = library.os.makedirs
    monkeypatch.setattr(library.os, "makedirs", lambda *a, **k: (calls.append(a), real(*a, **k)))
    lib._real_root()
    lib._real_root()
    lib._real_root()
    assert len(calls) == 1
