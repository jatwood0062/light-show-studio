"""Saved looks (controller snapshot / restore, the store, the websocket commands) and the settings backup."""
from __future__ import annotations

import copy

import pytest

from wsharness import (STORAGE, FakeConn, FakeLight, FakeMgr, FakeStore, FakeUser, aio, call, controller, mod, palette,
                       patterns)

looks_mod, ws_looks, backup, ws_backup = mod("looks"), mod("ws_looks"), mod("backup"), mod("ws_backup")
pattern_store = mod("pattern_store")
RED, BLUE = (255, 0, 0), (0, 0, 255)


@pytest.fixture(autouse=True)
def _fake_stores():
    """Another test module may have imported these modules under its own Store stub: use ours."""
    for m in (looks_mod, pattern_store):
        m.Store = FakeStore
    yield


def setup(hass, n=2, bulbs=6):
    lights = {f"e{i}": FakeLight(hass, bulbs, on=True, entry_id=f"e{i}") for i in range(n)}
    for ctl in lights.values():
        ctl.show_mode = False
    mgr = FakeMgr(hass, lights)
    looks = hass.data[looks_mod.LOOKS_KEY] = looks_mod.Looks(hass, mgr)
    return lights, looks


# --- the controller ---------------------------------------------------------------------------------
@aio
async def test_snapshot_restore_colour_and_brightness(hass):
    ctl = FakeLight(hass, 5)
    await ctl.async_turn_on(brightness=120, colour=RED, transition=0)
    snap = copy.deepcopy(ctl.snapshot())
    assert snap["on"] and snap["colour"] == [255, 0, 0] and "bulbs" not in snap
    await ctl.async_turn_on(brightness=255, colour=BLUE, transition=0)
    assert await ctl.async_restore(snap, 0) is True
    assert (ctl.colour, ctl.brightness, ctl.effect, ctl.custom) == (RED, 120, None, False)
    assert ctl.sent[-1] == [(120, 0, 0)] * 5


@aio
async def test_snapshot_restore_pattern(hass):
    ctl = FakeLight(hass, 5)
    await ctl.async_set_pattern("Candy Cane", patterns.STEADY, speed=70, transition=0)
    snap = ctl.snapshot()
    await ctl.async_turn_on(colour=BLUE, transition=0)
    ctl.speed = 10
    await ctl.async_restore(snap, 0)
    assert (ctl.effect, ctl.pattern, ctl.pattern_effect, ctl.speed) == (patterns.PATTERN, "Candy Cane", patterns.STEADY, 70)
    assert ctl.snapshot() == snap


@aio
async def test_snapshot_restore_per_bulb(hass):
    ctl = FakeLight(hass, 4)
    await ctl.async_turn_on(colour=RED, transition=0)
    await ctl.async_set_bulb(1, True, BLUE, 128)
    await ctl.async_set_bulb(2, False)
    snap = copy.deepcopy(ctl.snapshot())
    assert snap["custom"] and snap["bulbs"]["levels"] == [255, 128, 0, 255]
    await ctl.async_turn_on(effect=controller.EFFECTS and next(iter(controller.EFFECTS)), transition=0)
    assert not ctl.custom
    await ctl.async_restore(snap, 0)
    assert ctl.custom and ctl.effect is None
    assert ctl.bulb_colour[1] == BLUE and ctl.bulb_level == [255, 128, 0, 255]
    assert ctl.snapshot() == snap
    assert ctl.sent[-1][2] == (0, 0, 0) and ctl.sent[-1][0] == (255, 0, 0)


@aio
async def test_restore_off_on_and_resized(hass):
    ctl = FakeLight(hass, 4)
    await ctl.async_turn_on(colour=RED, transition=0)
    on = ctl.snapshot()
    await ctl.async_turn_off(0)
    off = ctl.snapshot()
    assert off["on"] is False
    await ctl.async_restore(on, 0)
    assert ctl.is_on and ctl.powered[-1] is True
    await ctl.async_restore(off, 0)
    assert not ctl.is_on
    # a per-bulb look saved when the light had more bulbs
    snap = {**on, "custom": True, "bulbs": {"colours": [[0, 0, 255]] * 9, "levels": [200] * 9}}
    await ctl.async_restore(snap, 0)
    assert ctl.is_on and ctl.bulb_level == [200] * 4


@aio
async def test_restore_leaves_a_playing_show_alone(hass):
    ctl = FakeLight(hass, 4)
    await ctl.async_turn_on(colour=RED, transition=0)
    snap = ctl.snapshot()
    ctl.enable_show()
    ctl.show_external(bytes([9, 9, 9] * 4))  # a show is feeding it right now
    assert await ctl.async_restore(snap, 0) is False
    assert ctl.show_mode and ctl.colour == RED
    ctl._ext_seen -= 10  # the show stopped a while ago
    assert await ctl.async_restore(snap, 0) is True
    assert not ctl.show_mode


# --- the looks ----------------------------------------------------------------------------------------
@aio
async def test_save_apply_round_trip_and_store(hass):
    lights, looks = setup(hass)
    await lights["e0"].async_turn_on(brightness=90, colour=RED, transition=0)
    await lights["e1"].async_set_pattern("Candy Cane", patterns.STEADY, transition=0)
    await looks.async_save_current("  Evening ")
    assert [v["name"] for v in looks.view()] == ["Evening"] and looks.view()[0]["lights"] == ["e0", "e1"]
    assert STORAGE[looks_mod.LOOKS_STORE_KEY]["version"] == 1
    await lights["e0"].async_turn_on(brightness=255, colour=BLUE, transition=0)
    await lights["e1"].async_turn_off(0)
    res = await looks.async_apply("evening", 0)  # names match without case
    assert res == {"applied": ["e0", "e1"], "skipped": []}
    assert (lights["e0"].colour, lights["e0"].brightness) == (RED, 90)
    assert lights["e1"].is_on and lights["e1"].pattern == "Candy Cane"
    # a fresh instance reads the same looks back
    again = looks_mod.Looks(hass, looks.mgr)
    await again.async_load()
    assert again.items == looks.items
    await looks.async_delete("Evening")
    assert looks.view() == [] and STORAGE[looks_mod.LOOKS_STORE_KEY]["looks"] == []
    with pytest.raises(KeyError):
        await looks.async_apply("Evening")


@aio
async def test_targets_missing_lights_and_limits(hass):
    lights, looks = setup(hass, 3)
    await looks.async_save_current("One", ["e1"])
    assert list(looks.get("One")["lights"]) == ["e1"] and "zones" not in looks.get("One")
    await looks.async_save_current("All")
    del looks.mgr.ctls["e2"]  # the light was removed
    lights["e0"].enable_show()
    lights["e0"].show_external(bytes(18))
    res = await looks.async_apply("All", 0)
    assert res == {"applied": ["e1"], "skipped": ["e0", "e2"]}
    with pytest.raises(ValueError):
        await looks.async_save_current("Gone", ["nope"])
    with pytest.raises(ValueError):
        await looks.async_save_current("   ")
    for i in range(looks_mod.MAX_LOOKS - 2):
        await looks.async_save_current(f"L{i}")
    with pytest.raises(ValueError):
        await looks.async_save_current("One too many")
    await looks.async_save_current("one")  # replacing is still fine at the limit
    assert len(looks.items) == looks_mod.MAX_LOOKS


@aio
async def test_load_drops_damaged_looks(hass):
    lights, looks = setup(hass)
    await looks.async_save_current("Good")
    good = copy.deepcopy(looks.get("Good"))
    bad = copy.deepcopy(good)
    bad["name"], bad["lights"]["e0"]["brightness"] = "Bad", 999
    STORAGE[looks_mod.LOOKS_STORE_KEY] = {"version": 1, "looks": [good, bad, "junk", {**good, "name": "GOOD"}]}
    await looks.async_load()
    assert list(looks.items) == ["good"]
    STORAGE[looks_mod.LOOKS_STORE_KEY] = "junk"
    await looks.async_load()
    assert looks.items == {}


@aio
async def test_ws_looks(hass):
    lights, looks = setup(hass)
    ws_looks.async_register(hass)
    assert {c.__name__ for c in hass.commands if getattr(c, "_admin", False)} == {"ws_save", "ws_delete"}
    conn = FakeConn()
    await call(ws_looks.ws_save, hass, conn, {"type": "light_show_studio/looks/save", "name": "Night"})
    await call(ws_looks.ws_list, hass, conn, {"type": "light_show_studio/looks/list"})
    assert conn.results[-1][1]["looks"][0]["name"] == "Night"
    await call(ws_looks.ws_apply, hass, conn, {"type": "light_show_studio/looks/apply", "name": "Night", "transition": 0})
    assert conn.results[-1][1]["applied"] == ["e0", "e1"]
    await call(ws_looks.ws_apply, hass, conn, {"type": "light_show_studio/looks/apply", "name": "Nope"})
    assert conn.errors[-1][1] == "not_found"
    await call(ws_looks.ws_save, hass, conn, {"type": "light_show_studio/looks/save", "name": "X", "entry_ids": ["zz"]})
    assert conn.errors[-1][1] == "invalid_format"
    await call(ws_looks.ws_delete, hass, conn, {"type": "light_show_studio/looks/delete", "name": "Night"})
    assert conn.results[-1][1] == {"looks": []}
    await call(ws_looks.ws_delete, hass, conn, {"type": "light_show_studio/looks/delete", "name": "Night"})
    assert conn.errors[-1][1] == "not_found"


@aio
async def test_ws_apply_needs_control(hass, monkeypatch=None):
    lights, looks = setup(hass)
    await looks.async_save_current("Night")
    ws_music = mod("ws_music")
    old = ws_music.light_entities
    ws_music.light_entities = lambda hass, entry_id: [f"light.{entry_id}"]
    try:
        some = FakeConn(FakeUser(admin=False, allowed=["light.e0"]))
        await call(ws_looks.ws_apply, hass, some, {"type": "light_show_studio/looks/apply", "name": "Night"})
        assert some.errors[-1][1] == "unauthorized" and not some.results
        both = FakeConn(FakeUser(admin=False, allowed=["light.e0", "light.e1"]))
        await call(ws_looks.ws_apply, hass, both, {"type": "light_show_studio/looks/apply", "name": "Night"})
        assert both.results and not both.errors
    finally:
        ws_music.light_entities = old


# --- zones in a look (a small stand-in for the MapManager's zones) --------------------------------------------
class ZoneMgr(FakeMgr):
    def __init__(self, hass, lights):
        super().__init__(hass, lights)
        self.runners, self.calls = {}, []

    @property
    def running(self):
        return list(self.runners)

    def run(self, zone_id, pattern="Candy Cane", effect="Chase", speed=40):
        zone = type("Z", (), {"pattern": pattern, "effect": effect})
        self.runners[zone_id] = type("R", (), {"running": True, "speed": speed, "plan": type("P", (), {"zone": zone})})

    async def async_stop_zone(self, ref, fade_out=None):
        self.calls.append(("stop", ref))
        self.runners.pop(ref, None)

    async def async_start_zone(self, ref, transition=None):
        if ref == "gone":
            raise RuntimeError("No zone called gone")
        self.calls.append(("zone", ref))
        self.run(ref)

    async def async_start_area(self, ref, pattern, effect, speed, fade_in=None, fade_out=None):
        self.calls.append(("area", ref, pattern, effect, speed))
        self.run("area:" + ref)


@aio
async def test_zones_are_saved_and_brought_back(hass):
    lights, _ = setup(hass)
    mgr = ZoneMgr(hass, lights)
    looks = looks_mod.Looks(hass, mgr)
    mgr.run("z1")
    mgr.run("gone")
    mgr.run("area:porch", "Warm White", "Twinkle", 70)
    lights["e1"]._zones["z1"] = ("Zone", [])  # e1 is drawn by the zone
    await looks.async_save_current("Party")
    assert [z["id"] for z in looks.get("Party")["zones"]] == ["z1", "gone", "area:porch"]
    lights["e1"]._zones.clear()
    mgr.runners.clear()
    mgr.run("other")
    res = await looks.async_apply("Party", 0)
    assert res == {"applied": ["e0"], "skipped": ["e1"]}
    assert mgr.calls == [("stop", "other"), ("zone", "z1"), ("area", "porch", "Warm White", "Twinkle", 70)]


# --- the settings backup ------------------------------------------------------------------------------------
class Holder:
    """A manager with a store that reads itself back, like the schedulers."""

    def __init__(self, hass, key, **state):
        self._store = FakeStore(hass, 1, key)
        self.loaded = None
        self.notified = 0
        self.__dict__.update(state)

    async def async_load(self):
        self.loaded = await self._store.async_load()
        for k in ("schedules", "playlists", "active_playlist"):
            if k in (self.loaded or {}) and hasattr(self, k):
                setattr(self, k, self.loaded[k])
        if hasattr(self, "holiday_overrides"):
            self.holiday_overrides = self.loaded.get("holidays", {})

    def _data(self):
        return {"schedules": self.schedules, "windows": {"w": {"end": "x"}}, "manual": {}, "holidays": self.holiday_overrides}

    def _notify(self):
        self.notified += 1


async def world(hass):
    lights, looks = setup(hass)
    store = hass.data[backup.USER_PATTERNS_KEY] = pattern_store.PatternStore(hass)
    await store.async_load()
    sched = hass.data[backup.SCHEDULER_KEY] = Holder(hass, "sched", playlists=[{"id": "p", "name": "P", "show_ids": []}],
                                                     schedules=[{"id": "s"}], active_playlist="p")
    ls = hass.data[backup.LIGHT_SCHEDULER_KEY] = Holder(hass, "ls", schedules=[{"id": "a"}], holiday_overrides={"h": {}})
    return looks, store, sched, ls


@aio
async def test_export_import_round_trip(hass):
    looks, store, sched, ls = await world(hass)
    await store.async_save_pattern(palette("Mine"))
    await looks.async_save_current("Night")
    doc = backup.export(hass)
    assert doc["format"] == backup.FORMAT and doc["version"] == backup.VERSION
    assert set(doc["data"]) == {"user_patterns", "looks", "show_schedules", "light_schedules"}
    assert doc["data"]["user_patterns"]["patterns"][0]["name"] == "Mine"
    pid = next(iter(store.items))
    await store.async_delete_pattern(pid)
    await looks.async_delete("Night")
    sched.playlists, sched.schedules, ls.schedules = [], [], []
    res = await backup.async_import(hass, doc)
    assert res == {"imported": ["user_patterns", "looks", "show_schedules", "light_schedules"], "restart_required": True}
    assert [p.name for p in store.items.values()] == ["Mine"] and patterns.canonical("Mine") == "Mine"
    assert [v["name"] for v in looks.view()] == ["Night"]
    assert sched.playlists == doc["data"]["show_schedules"]["playlists"] and sched.active_playlist == "p"
    assert ls.schedules == [{"id": "a"}] and ls.loaded["windows"] == {"w": {"end": "x"}}  # open windows are kept
    assert sched.notified == ls.notified == 1
    assert backup.export(hass)["data"] == doc["data"]
    # only the parts in the file are replaced, and looks alone need no restart
    res = await backup.async_import(hass, {**doc, "data": {"looks": {"version": 1, "looks": []}}})
    assert res == {"imported": ["looks"], "restart_required": False}
    assert looks.view() == [] and [p.name for p in store.items.values()] == ["Mine"]


@aio
async def test_import_validation_changes_nothing(hass):
    looks, store, sched, ls = await world(hass)
    await looks.async_save_current("Night")
    good = backup.export(hass)
    look = good["data"]["looks"]["looks"][0]
    bad_docs = [
        None, [], {}, {**good, "format": "other"}, {**good, "version": backup.VERSION + 1}, {**good, "version": "1"},
        {**good, "version": True}, {**good, "data": {}}, {**good, "data": []},
        {**good, "data": {"surprise": {}}}, {**good, "data": {"looks": None}},
        {**good, "data": {"user_patterns": {"patterns": {}}}}, {**good, "data": {"show_schedules": {"playlists": []}}},
        {**good, "data": {"light_schedules": {"schedules": [], "holidays": []}}}, {**good, "data": {"map": []}},
        {**good, "data": {"looks": {"version": 2, "looks": []}}},
        {**good, "data": {"looks": {"looks": [{**look, "lights": {"e0": {"brightness": "high"}}}]}}},
        {**good, "data": {"looks": {"looks": [{**look, "name": ""}]}}},
    ]
    before = copy.deepcopy(STORAGE)
    for doc in bad_docs:
        with pytest.raises(ValueError):
            await backup.async_import(hass, doc)
    assert STORAGE == before and [v["name"] for v in looks.view()] == ["Night"] and sched.loaded is None
    assert backup.validate({**good, "data": {"map": None}}) == {"map": None}


@aio
async def test_ws_backup(hass):
    looks, store, sched, ls = await world(hass)
    ws_backup.async_register(hass)
    assert all(getattr(c, "_admin", False) for c in hass.commands)
    conn = FakeConn()
    await call(ws_backup.ws_export, hass, conn, {"type": "light_show_studio/backup/export"})
    doc = conn.results[-1][1]
    await call(ws_backup.ws_import, hass, conn, {"type": "light_show_studio/backup/import", "backup": doc})
    assert conn.results[-1][1]["imported"] == ["user_patterns", "looks", "show_schedules", "light_schedules"]
    await call(ws_backup.ws_import, hass, conn, {"type": "light_show_studio/backup/import", "backup": {"format": "x"}})
    assert conn.errors[-1][1] == "invalid_format"
