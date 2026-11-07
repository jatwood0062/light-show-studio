"""Tests for light schedules (lights and areas on at a time, off later), with small fakes.

Plain pytest, no Home Assistant: the few Home Assistant modules the code imports are stubbed below, and time is a
variable the tests move. Needs voluptuous (the validation) which Home Assistant ships.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import sys
import time
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("voluptuous")
import voluptuous as vol  # noqa: E402

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"
TZ = ZoneInfo("America/Chicago")
UTC = timezone.utc
CLOCK = {"now": datetime(2026, 12, 7, 12, 0, tzinfo=UTC)}
STORAGE: dict[str, dict] = {}
TIMERS: list = []


def _stub(name: str, **attrs):
    mod = sys.modules.setdefault(name, types.ModuleType(name))
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class FakeStore:
    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        return copy.deepcopy(STORAGE.get(self.key))

    def async_delay_save(self, fn, delay=0):
        STORAGE[self.key] = copy.deepcopy(fn())

    async def async_save(self, data):
        STORAGE[self.key] = copy.deepcopy(data)


def _track(hass, cb, when):
    TIMERS.append(when)
    return lambda: None


def _sun(hass, event, day):
    return datetime(day.year, day.month, day.day, 7 if event == "sunrise" else 17, tzinfo=TZ).astimezone(UTC)


def _load():
    core = _stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f, CALLBACK_TYPE=object)
    helpers = _stub("homeassistant.helpers")
    util = _stub("homeassistant.util")
    dt = _stub("homeassistant.util.dt", utcnow=lambda: CLOCK["now"], get_default_time_zone=lambda: TZ,
               now=lambda: CLOCK["now"].astimezone(TZ))
    util.dt = dt
    _stub("homeassistant.exceptions", HomeAssistantError=Exception)
    cv = _stub("homeassistant.helpers.config_validation", string=str, boolean=bool, entity_id=str, ensure_list=list)
    er = _stub("homeassistant.helpers.entity_registry")
    ev = _stub("homeassistant.helpers.event")
    ev.async_track_point_in_time = _track
    st = _stub("homeassistant.helpers.storage")
    st.Store = FakeStore
    su = _stub("homeassistant.helpers.sun")
    su.get_astral_event_date = _sun
    helpers.config_validation, helpers.entity_registry = cv, er
    del core
    pkg = sys.modules.setdefault("light_show_studio_ls", types.ModuleType("light_show_studio_ls"))
    pkg.__path__ = [str(PKG)]
    names = ("const", "effects", "protocol", "hosts", "user_patterns", "patterns", "fade", "controller", "mapping", "schedule_math", "fseq",
             "library", "scheduler", "light_schedules")
    for name in names:
        full = f"light_show_studio_ls.{name}"
        if full not in sys.modules:
            spec = importlib.util.spec_from_file_location(full, PKG / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
    return {n: sys.modules[f"light_show_studio_ls.{n}"] for n in names}


M = _load()
controller, mapping, patterns, ls, const = M["controller"], M["mapping"], M["patterns"], M["light_schedules"], M["const"]
ALL = [0, 1, 2, 3, 4, 5, 6]


def at(h, m=0, day=7, month=12, year=2026):
    """A local wall-clock time as UTC."""
    return datetime(year, month, day, h, m, tzinfo=TZ).astimezone(UTC)


class FakeBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def async_fire(self, name, data):
        self.events.append((name, data))


class FakeHass:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.tasks = []
        self.data = {}
        self.bus = FakeBus()

    def async_create_background_task(self, coro, name=None):
        t = asyncio.ensure_future(coro)
        self.tasks.append(t)
        return t


class FakeLight(controller.C9Controller):
    def __init__(self, hass, bulbs=10, on=False):
        super().__init__(hass, "192.168.1.10", bulbs, True)
        self.is_on = on
        self.sent = []

    def _make_link(self, host):
        return None

    def _hw_power(self, on):
        pass

    def _hw_frame(self, frame):
        self.sent.append(frame)

    def _hw_close(self):
        pass


class Runner:
    running = True


class FakeMap:
    """Just what the light scheduler uses of the MapManager."""

    def __init__(self, hass, lights, areas, runs):
        self.hass = hass
        self.ctls = lights  # entry id -> FakeLight
        self.model = mapping.MapModel(tuple(runs), (), {"kind": "none", "w": 1, "h": 1}, tuple(areas))
        self.runners: dict[str, Runner] = {}
        self.started: list[tuple] = []
        self.stopped: list[str] = []
        self.fades: list[tuple] = []
        self.stop_fades: list[tuple] = []
        self._rows = [{"entry_id": e, "title": e, "area": {"id": next((r.area for r in runs if r.entry_id == e), "x")}}
                      for e in lights]

    def lights(self):
        return self._rows

    def controller(self, entry_id):
        return self.ctls.get(entry_id)

    async def async_start_area(self, ref, pattern=None, effect=None, speed=None, fade_in=None, fade_out=None):
        self.started.append((ref, pattern, effect, speed))
        self.fades.append((ref, fade_in, fade_out))
        self.runners["area:" + ref] = Runner()

    async def async_stop_area(self, ref, fade_out=None):
        self.stopped.append(ref)
        self.stop_fades.append((ref, fade_out))
        self.runners.pop("area:" + ref, None)


def aio(fn):
    def wrapper():
        async def go():
            STORAGE.clear()
            TIMERS.clear()
            CLOCK["now"] = at(12)
            hass = FakeHass()
            try:
                await fn(hass)
            finally:
                for t in hass.tasks:
                    t.cancel()
        asyncio.run(go())
    wrapper.__name__ = fn.__name__
    return wrapper


def world(hass):
    """e1 alone on the porch (outdoor), e2 in the garage (outdoor) and half in the living room, e3 in the living
    room, e4 (no runs) in the Kitchen."""
    areas = [mapping.Area("porch", "Porch", "porch_ha", True), mapping.Area("garage", "Garage", None, True),
             mapping.Area("living", "Living Room", "living_ha"), mapping.Area("kitchen", "Kitchen", "kitchen_ha")]
    runs = [mapping.Run("r1", "e1", "Porch eave", 0, 10, area="porch"),
            mapping.Run("r2", "e2", "Garage roof", 0, 5, area="garage"),
            mapping.Run("r3", "e2", "Garage inside", 5, 5, area="living"),
            mapping.Run("r4", "e3", "Tree", 0, 10, area="living")]
    lights = {e: FakeLight(hass) for e in ("e1", "e2", "e3", "e4")}
    lights["e4"].is_on = False
    mgr = FakeMap(hass, lights, areas, runs)
    mgr._rows[3]["area"] = {"id": "ha_kitchen_ha", "ha_area_id": "kitchen_ha"}  # no runs: placed by its HA area
    hass.data[const.MAP_KEY] = mgr
    return mgr


def new_scheduler(hass, started=True):
    s = ls.LightScheduler(hass)
    s._started = started
    return s


def data(**kw):
    base = {"name": "Porch lights", "days": ALL, "start": {"type": "time", "time": "18:00"},
            "end": {"type": "time", "time": "22:00"}, "targets": {"areas": ["Porch"]},
            "look": {"pattern": "Warm White", "effect": "Steady", "speed": 30, "brightness": 50},
            "fade_in_s": 0, "fade_out_s": 0}  # instant, so these tests see the end state at once; fades have their own tests
    base.update(kw)
    return base


async def save(s, **kw):
    return await s.async_save_schedule(kw.pop("schedule_id", None), data(**kw))


async def advance(s, when):
    CLOCK["now"] = when
    await s._process()


def events(hass, name):
    return [d for n, d in hass.bus.events if n == name]


# --- validation -----------------------------------------------------------------------------
def test_valid_schedule_gets_defaults():
    out = ls.validate_light_schedule(data(look={"pattern": "Rainbow", "effect": "Chase"}))
    assert out["enabled"] is True and out["end"]["type"] == "time"
    assert out["look"] == {"pattern": "Rainbow", "effect": "Chase", "speed": 50, "brightness": 100}
    assert out["targets"] == {"areas": ["Porch"], "lights": []}
    assert out["date_from"] is None and out["days"] == ALL


def test_end_may_be_missing_edge_or_duration():
    d = data()
    del d["end"]
    assert ls.validate_light_schedule(d)["end"] is None
    assert ls.validate_light_schedule(data(end={"type": "duration", "minutes": 90}))["end"] == {"type": "duration", "minutes": 90}
    assert ls.validate_light_schedule(data(end={"type": "sunrise", "offset_min": -30}))["end"] == {"type": "sunrise", "offset_min": -30}


@pytest.mark.parametrize("bad", [
    {"end": {"type": "duration", "minutes": 0}},
    {"end": {"type": "duration", "minutes": 1441}},
    {"end": {"type": "duration"}},
    {"end": {"type": "moon"}},
    {"start": {"type": "time"}},
    {"days": []},
    {"days": [7]},
    {"name": ""},
    {"name": "x" * 61},
    {"enabled": "yes"},
    {"targets": {"areas": [], "lights": []}},
    {"targets": {}},
    {"targets": {"areas": ["a"], "stuff": 1}},
    {"look": {"pattern": "Nope", "effect": "Steady"}},
    {"look": {"pattern": "Rainbow", "effect": "Nope"}},
    {"look": {"pattern": "Rainbow", "effect": "Steady", "speed": 0}},
    {"look": {"pattern": "Rainbow", "effect": "Steady", "speed": 101}},
    {"look": {"pattern": "Rainbow", "effect": "Steady", "brightness": 0}},
    {"look": {"pattern": "Rainbow", "effect": "Steady", "brightness": 101}},
    {"look": {"pattern": "Rainbow"}},
    {"date_from": "11-25"},
    {"date_to": "01-06"},
    {"date_from": "13-01", "date_to": "01-06"},
    {"surprise": True},
])
def test_invalid_schedules_are_refused(bad):
    with pytest.raises(vol.Invalid):
        ls.validate_light_schedule(data(**bad))


def test_special_areas_are_normalised_and_deduplicated():
    out = ls.validate_light_schedule(data(targets={"areas": ["Outdoor", "outdoor", "Porch"], "lights": ["e1", "e1"]}))
    assert out["targets"] == {"areas": ["outdoor", "Porch"], "lights": ["e1"]}
    only_lights = ls.validate_light_schedule(data(targets={"lights": ["e1"]}))
    assert only_lights["targets"]["areas"] == []


@aio
async def test_at_most_100_schedules(hass):
    world(hass)
    s = new_scheduler(hass, started=False)
    for i in range(100):
        await save(s, name=f"s{i}")
    with pytest.raises(ValueError):
        await save(s, name="one too many")


@aio
async def test_unknown_light_or_area_is_refused_on_save(hass):
    world(hass)
    s = new_scheduler(hass)
    with pytest.raises(ValueError):
        await save(s, targets={"lights": ["nope"]})
    with pytest.raises(ValueError):
        await save(s, targets={"areas": ["Moon base"]})
    await save(s, targets={"areas": ["porch_ha"]})  # a Home Assistant area id works, so do specials
    await save(s, targets={"areas": ["all"]})


@aio
async def test_load_drops_invalid_saved_schedules(hass):
    world(hass)
    s = new_scheduler(hass)
    await save(s)
    STORAGE[const.LIGHT_SCHEDULE_STORE_KEY]["schedules"].append({"id": "bad", "name": "x"})
    s2 = new_scheduler(hass)
    await s2.async_load()
    assert [x["name"] for x in s2.schedules] == ["Porch lights"]


def test_area_outdoor_flag_round_trips_and_defaults_false():
    area = mapping.parse_area({"id": "p", "name": "Porch", "outdoor": True})
    assert area.outdoor is True
    assert mapping.parse_area({"id": "p", "name": "Porch"}).outdoor is False  # old maps
    for bad in ("yes", 1, None):
        with pytest.raises(mapping.MapError):
            mapping.parse_area({"id": "p", "name": "Porch", "outdoor": bad})
    run = {"id": "r1", "entry_id": "e1", "name": "r", "first": 0, "count": 3, "area": "p"}
    model = mapping.from_dict({"version": 2, "areas": [{"id": "p", "name": "Porch", "outdoor": True},
                                                       {"id": "k", "name": "Kitchen"}], "runs": [run]}, {"e1": 10})
    out = mapping.to_dict(model)
    assert out["areas"] == [{"id": "p", "name": "Porch", "outdoor": True}, {"id": "k", "name": "Kitchen"}]
    assert mapping.from_dict(out, {"e1": 10}).areas[0].outdoor is True


# --- windows and targets ----------------------------------------------------------------------
@aio
async def test_window_opens_and_applies_the_look(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s)
    assert not mgr.ctls["e1"].is_on
    await advance(s, at(18, 1))
    e1 = mgr.ctls["e1"]
    assert e1.is_on and e1.effect == patterns.PATTERN
    assert (e1.pattern, e1.pattern_effect, e1.speed, e1.brightness) == ("Warm White", "Steady", 30, 128)
    assert not mgr.ctls["e3"].is_on  # not a target
    started = events(hass, const.EVENT_LIGHT_SCHEDULE_STARTED)
    assert len(started) == 1 and started[0]["name"] == "Porch lights"
    await advance(s, at(18, 30))  # nothing more happens, nothing is applied twice
    assert len(events(hass, const.EVENT_LIGHT_SCHEDULE_STARTED)) == 1


@aio
async def test_nothing_before_the_window_or_on_other_weekdays(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, days=[0, 1, 2, 3, 4, 5, 6])
    await advance(s, at(17, 59))
    assert not mgr.ctls["e1"].is_on
    # 2026-12-07 is a Monday: a Tuesday-only schedule does not open
    await save(s, name="tue", days=[1])
    await advance(s, at(19))
    assert not mgr.ctls["e3"].is_on


@aio
async def test_special_targets_resolve_through_the_map(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    plan = s._plan({"targets": {"areas": ["outdoor"], "lights": []}}, mgr.model, {r["entry_id"]: r for r in mgr.lights()})
    assert plan.lights == ["e1"] and plan.zones == ["garage"]  # e2 only has some of its runs outdoors
    plan = s._plan({"targets": {"areas": ["indoor"], "lights": []}}, mgr.model, {r["entry_id"]: r for r in mgr.lights()})
    assert plan.lights == ["e3", "e4"] and plan.zones == ["living"]  # e4 has no runs: placed by its HA area
    plan = s._plan({"targets": {"areas": ["all"], "lights": []}}, mgr.model, {r["entry_id"]: r for r in mgr.lights()})
    assert sorted(plan.lights) == ["e1", "e2", "e3", "e4"] and plan.zones == []  # e2's runs are all inside "all"
    plan = s._plan({"targets": {"areas": ["Living Room", "kitchen_ha"], "lights": ["e1"]}}, mgr.model,
                   {r["entry_id"]: r for r in mgr.lights()})  # by name and by Home Assistant area id
    assert plan.lights == ["e1", "e3", "e4"] and plan.zones == ["living"]


@aio
async def test_light_only_partly_in_the_area_is_driven_as_an_area_zone(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, targets={"areas": ["Garage"]})
    await advance(s, at(19))
    assert mgr.started == [("garage", "Warm White", "Steady", 30)]
    assert not mgr.ctls["e2"].pattern == "Warm White" or not mgr.ctls["e2"].is_on  # not set directly
    await advance(s, at(22, 1))
    assert mgr.stopped == ["garage"]


@aio
async def test_explicit_lights_are_driven_directly(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, targets={"lights": ["e3"]})
    await advance(s, at(19))
    assert mgr.ctls["e3"].is_on and mgr.ctls["e3"].pattern == "Warm White"
    assert not mgr.ctls["e1"].is_on


# --- the end of the window --------------------------------------------------------------------
@aio
async def test_window_close_turns_off_what_the_schedule_turned_on(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s)
    await advance(s, at(19))
    assert mgr.ctls["e1"].is_on
    await advance(s, at(22, 0))
    assert not mgr.ctls["e1"].is_on
    assert len(events(hass, const.EVENT_LIGHT_SCHEDULE_ENDED)) == 1
    assert events(hass, const.EVENT_LIGHT_SCHEDULE_ENDED)[0]["reason"] == "ended"


@aio
async def test_a_light_a_person_changed_is_left_alone_at_the_end(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, targets={"lights": ["e1", "e3"]})
    await advance(s, at(19))
    await mgr.ctls["e3"].async_turn_on(brightness=255)  # a person turns e3 up
    await advance(s, at(22, 5))
    assert not mgr.ctls["e1"].is_on  # untouched: off
    assert mgr.ctls["e3"].is_on  # touched: stays


@aio
async def test_a_person_picking_a_colour_or_a_show_keeps_the_light(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, targets={"lights": ["e1", "e3"]})
    await advance(s, at(19))
    await mgr.ctls["e1"].async_turn_on(colour=(255, 0, 0))
    mgr.ctls["e3"].enable_show()
    await advance(s, at(22, 5))
    assert mgr.ctls["e1"].is_on and mgr.ctls["e3"].is_on


@aio
async def test_area_zone_changed_by_a_person_is_not_stopped(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, targets={"areas": ["Garage"]})
    await advance(s, at(19))
    mgr.runners["area:garage"] = Runner()  # a person started their own zone on the area
    await advance(s, at(22, 5))
    assert mgr.stopped == []


@aio
async def test_duration_end(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, end={"type": "duration", "minutes": 90})
    await advance(s, at(18, 0))
    assert mgr.ctls["e1"].is_on
    await advance(s, at(19, 29))
    assert mgr.ctls["e1"].is_on
    await advance(s, at(19, 30))
    assert not mgr.ctls["e1"].is_on


@aio
async def test_end_after_midnight_and_sunrise_end(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, start={"type": "sunset", "offset_min": -30}, end={"type": "sunrise", "offset_min": 15})
    await advance(s, at(16, 29))
    assert not mgr.ctls["e1"].is_on
    await advance(s, at(16, 30))
    assert mgr.ctls["e1"].is_on
    await advance(s, at(7, 14, day=8))
    assert mgr.ctls["e1"].is_on
    await advance(s, at(7, 15, day=8))
    assert not mgr.ctls["e1"].is_on


@aio
async def test_no_end_turns_on_and_leaves_it_on(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, end=None)
    await advance(s, at(17, 59))
    s._last = at(17, 59)
    await advance(s, at(18, 1))
    assert mgr.ctls["e1"].is_on
    assert len(events(hass, const.EVENT_LIGHT_SCHEDULE_STARTED)) == 1
    mgr.ctls["e1"].is_on = False  # a person turns it off: it is not switched on again
    await advance(s, at(18, 30))
    assert not mgr.ctls["e1"].is_on
    await advance(s, at(23))
    assert len(events(hass, const.EVENT_LIGHT_SCHEDULE_STARTED)) == 1


@aio
async def test_season_wraps_the_new_year(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, date_from="11-25", date_to="01-06")
    for day, month, year, expect in ((24, 11, 2026, False), (25, 11, 2026, True), (31, 12, 2026, True), (6, 1, 2027, True),
                                     (7, 1, 2027, False), (4, 7, 2026, False)):
        CLOCK["now"] = at(12, day=day, month=month, year=year)
        await s._process()
        await advance(s, at(19, day=day, month=month, year=year))
        assert mgr.ctls["e1"].is_on is expect, (month, day)
        await advance(s, at(23, day=day, month=month, year=year))
        assert not mgr.ctls["e1"].is_on


# --- shows and overlaps ----------------------------------------------------------------------
@aio
async def test_a_light_with_a_show_playing_is_skipped_then_picked_up_when_it_ends(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    e1 = mgr.ctls["e1"]
    e1.enable_show()
    e1._ext_seen = time.monotonic()  # frames are arriving
    await save(s, targets={"lights": ["e1", "e3"]})
    await advance(s, at(19))
    assert e1.show_mode and e1.effect is None  # not touched
    assert mgr.ctls["e3"].pattern == "Warm White"
    assert s._retry  # look again soon
    assert TIMERS[-1] <= at(19) + ls.RETRY + timedelta(seconds=1)
    e1._ext_seen = time.monotonic() - 60  # the show is over
    await advance(s, at(19, 5))
    assert e1.pattern == "Warm White" and not e1.show_mode


@aio
async def test_a_show_light_inside_an_area_zone_skips_the_zone(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    mgr.ctls["e2"].enable_show()
    mgr.ctls["e2"]._ext_seen = time.monotonic()
    await save(s, targets={"areas": ["Garage"]})
    await advance(s, at(19))
    assert mgr.started == []


@aio
async def test_overlap_latest_start_wins_and_the_earlier_one_comes_back(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, name="early", start={"type": "time", "time": "18:00"}, end={"type": "time", "time": "23:00"})
    await save(s, name="late", start={"type": "time", "time": "20:00"}, end={"type": "time", "time": "21:00"},
               look={"pattern": "Rainbow", "effect": "Chase", "speed": 80, "brightness": 100})
    e1 = mgr.ctls["e1"]
    await advance(s, at(19))
    assert e1.pattern == "Warm White"
    await advance(s, at(20, 10))
    assert (e1.pattern, e1.pattern_effect, e1.brightness) == ("Rainbow", "Chase", 255)
    await advance(s, at(21, 0))  # the later one is over: the earlier one's look is back, the light stays on
    assert e1.is_on and e1.pattern == "Warm White" and e1.brightness == 128
    await advance(s, at(23, 0))
    assert not e1.is_on


@aio
async def test_overlap_without_a_person_keeps_one_owner_and_no_flicker(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, name="a")
    await save(s, name="b", start={"type": "time", "time": "19:00"}, look={"pattern": "Rainbow", "effect": "Steady"})
    await advance(s, at(20))
    offs = []
    orig = mgr.ctls["e1"].async_turn_off

    async def spy(*args, **kw):
        offs.append(1)
        await orig(*args, **kw)
    mgr.ctls["e1"].async_turn_off = spy
    await advance(s, at(21))
    await advance(s, at(22))  # both end together: one turn off
    assert len(offs) == 1


# --- control: disable, delete, stop, run now, edit -----------------------------------------------
@aio
async def test_disable_and_delete_end_an_active_schedule(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    sched = await save(s)
    await advance(s, at(19))
    assert mgr.ctls["e1"].is_on
    await s.async_set_enabled(sched["id"], False)
    assert not mgr.ctls["e1"].is_on
    assert events(hass, const.EVENT_LIGHT_SCHEDULE_ENDED)[-1]["reason"] == "disabled"
    await s.async_set_enabled(sched["id"], True)  # the window is still open: it starts again
    assert mgr.ctls["e1"].is_on
    await s.async_delete_schedule(sched["id"])
    assert not mgr.ctls["e1"].is_on and s.schedules == []
    assert events(hass, const.EVENT_LIGHT_SCHEDULE_ENDED)[-1]["reason"] == "deleted"


@aio
async def test_editing_the_look_of_an_active_schedule_applies_it_and_a_rename_does_not_restart(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    sched = await save(s)
    await advance(s, at(19))
    starts = len(events(hass, const.EVENT_LIGHT_SCHEDULE_STARTED))
    await save(s, schedule_id=sched["id"], name="Renamed")
    assert mgr.ctls["e1"].pattern == "Warm White" and len(events(hass, const.EVENT_LIGHT_SCHEDULE_ENDED)) == 0
    await save(s, schedule_id=sched["id"], look={"pattern": "Rainbow", "effect": "Chase", "speed": 50, "brightness": 100})
    assert mgr.ctls["e1"].pattern == "Rainbow" and mgr.ctls["e1"].is_on
    assert len(events(hass, const.EVENT_LIGHT_SCHEDULE_ENDED)) == 1
    del starts


@aio
async def test_stop_ends_the_window_early_and_it_stays_ended(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    sched = await save(s)
    await advance(s, at(19))
    assert await s.async_stop(sched["id"]) is True
    assert not mgr.ctls["e1"].is_on
    assert events(hass, const.EVENT_LIGHT_SCHEDULE_ENDED)[-1]["reason"] == "stopped"
    await advance(s, at(19, 30))  # not started again by the next check
    assert not mgr.ctls["e1"].is_on
    assert await s.async_stop(sched["id"]) is False
    await advance(s, at(18, 0, day=8))  # tomorrow's window is a new one
    await advance(s, at(19, 0, day=8))
    assert mgr.ctls["e1"].is_on


@aio
async def test_run_now_until_the_normal_end_and_for_a_duration(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    sched = await save(s)
    await s.async_run_now(sched["id"])  # 12:00: the window is 18:00-22:00; run now lasts until the next 22:00
    assert mgr.ctls["e1"].is_on
    view = s.views()[0]
    assert view["active"] and view["next_off"] == at(12).__class__(2026, 12, 7, 22, 0, tzinfo=TZ).astimezone(UTC).isoformat()
    await advance(s, at(22, 0))
    assert not mgr.ctls["e1"].is_on
    sched2 = await save(s, name="dur", targets={"lights": ["e3"]}, end={"type": "duration", "minutes": 45})
    await advance(s, at(13))
    await s.async_run_now(sched2["id"])
    assert mgr.ctls["e3"].is_on
    await advance(s, at(13, 44))
    assert mgr.ctls["e3"].is_on
    await advance(s, at(13, 45))
    assert not mgr.ctls["e3"].is_on


@aio
async def test_run_now_without_an_end_leaves_the_lights_on(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    sched = await save(s, end=None)
    await s.async_run_now(sched["id"])
    assert mgr.ctls["e1"].is_on
    await advance(s, at(15))
    assert mgr.ctls["e1"].is_on


@aio
async def test_unknown_schedule_ids_raise_keyerror(hass):
    world(hass)
    s = new_scheduler(hass)
    for call in (s.async_stop, s.async_run_now, s.async_delete_schedule):
        with pytest.raises(KeyError):
            await call("nope")
    with pytest.raises(KeyError):
        await s.async_set_enabled("nope", True)


# --- restart -------------------------------------------------------------------------------------
@aio
async def test_restart_inside_an_open_window_applies_it_once(hass):
    mgr = world(hass)
    s = new_scheduler(hass, started=False)
    await save(s)  # saved while not started: nothing applied
    CLOCK["now"] = at(19)
    s2 = new_scheduler(hass)  # a fresh process
    await s2.async_load()
    await s2._process()
    assert mgr.ctls["e1"].is_on and mgr.ctls["e1"].pattern == "Warm White"
    assert len(events(hass, const.EVENT_LIGHT_SCHEDULE_STARTED)) == 1
    # another restart (or a reload of the integration) later in the window: the same window is not applied again
    mgr.ctls["e1"].pattern = "Rainbow"  # but it can tell the light is no longer showing the look
    s3 = new_scheduler(hass)
    await s3.async_load()
    CLOCK["now"] = at(20)
    await s3._process()
    assert mgr.ctls["e1"].pattern == "Rainbow"
    assert len(events(hass, const.EVENT_LIGHT_SCHEDULE_STARTED)) == 1


@aio
async def test_restart_keeps_ownership_of_a_light_still_showing_the_look(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s)
    await advance(s, at(19))
    s2 = new_scheduler(hass)  # restart: the light still shows the look
    await s2.async_load()
    CLOCK["now"] = at(20)
    await s2._process()
    CLOCK["now"] = at(22, 1)
    await s2._process()
    assert not mgr.ctls["e1"].is_on  # the new process still knew to turn it off


@aio
async def test_restart_does_not_restart_a_stopped_window(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    sched = await save(s)
    await advance(s, at(19))
    await s.async_stop(sched["id"])
    s2 = new_scheduler(hass)
    await s2.async_load()
    CLOCK["now"] = at(20)
    await s2._process()
    assert not mgr.ctls["e1"].is_on


@aio
async def test_restart_catches_up_a_recent_leave_on_start_but_not_an_old_one(hass):
    mgr = world(hass)
    s = new_scheduler(hass, started=False)
    await save(s, end=None)
    CLOCK["now"] = at(18, 4)
    s2 = new_scheduler(hass)
    await s2.async_load()
    s2._last = CLOCK["now"] - ls.CATCHUP  # what async_start sets
    await s2._process()
    assert mgr.ctls["e1"].is_on
    mgr.ctls["e1"].is_on = False
    s3 = new_scheduler(hass)
    await s3.async_load()
    CLOCK["now"] = at(18, 6)
    s3._last = CLOCK["now"] - ls.CATCHUP
    await s3._process()
    assert not mgr.ctls["e1"].is_on  # already applied: not again
    s4 = new_scheduler(hass, started=False)
    await s4.async_load()
    CLOCK["now"] = at(21)
    s4._started = True
    s4._last = CLOCK["now"] - ls.CATCHUP
    mgr.ctls["e1"].is_on = False
    await s4._process()
    assert not mgr.ctls["e1"].is_on  # applied long ago


@aio
async def test_async_start_waits_before_looking(hass):
    mgr = world(hass)
    s = new_scheduler(hass, started=False)
    await save(s)
    CLOCK["now"] = at(19)
    old = ls.STARTUP_DELAY
    ls.STARTUP_DELAY = 0.01
    hass.async_create_task = lambda coro, name=None: hass.async_create_background_task(coro, name)
    try:
        await s.async_start()
        assert not mgr.ctls["e1"].is_on
        await asyncio.sleep(0.5)
    finally:
        ls.STARTUP_DELAY = old
    assert mgr.ctls["e1"].is_on


# --- the list ----------------------------------------------------------------------------------------
@aio
async def test_views_have_next_on_next_off_and_active(hass):
    world(hass)
    s = new_scheduler(hass)
    sched = await save(s)
    CLOCK["now"] = at(12)
    v = s.views()[0]
    assert v["id"] == sched["id"] and v["active"] is False
    assert v["next_on"] == at(18).isoformat() and v["next_off"] == at(22).isoformat()
    await advance(s, at(19))
    v = s.views()[0]
    assert v["active"] is True and v["next_off"] == at(22).isoformat() and v["next_on"] == at(18, day=8).isoformat()
    await s.async_set_enabled(sched["id"], False)
    v = s.views()[0]
    assert v["active"] is False and v["next_on"] is None and v["next_off"] is None


@aio
async def test_views_for_leave_on_and_duration_schedules(hass):
    world(hass)
    s = new_scheduler(hass)
    await save(s, name="forever", end=None)
    await save(s, name="dur", end={"type": "duration", "minutes": 30})
    a, b = s.views()
    assert a["next_on"] == at(18).isoformat() and a["next_off"] is None
    assert b["next_on"] == at(18).isoformat() and b["next_off"] == at(18, 30).isoformat()


@aio
async def test_listeners_hear_about_changes(hass):
    world(hass)
    s = new_scheduler(hass)
    hits = []
    s.add_listener(lambda: hits.append(1))
    await save(s)
    assert hits


# --- fades, map effects and your own patterns ------------------------------------------------------------------------
def test_fade_fields_default_to_two_seconds_and_are_limited():
    base = data()
    del base["fade_in_s"], base["fade_out_s"]
    out = ls.validate_light_schedule(base)
    assert out["fade_in_s"] == 2 and out["fade_out_s"] == 2
    assert ls.validate_light_schedule(data(fade_in_s=0, fade_out_s=1800))["fade_out_s"] == 1800
    assert ls.validate_light_schedule(data(fade_in_s=0.5))["fade_in_s"] == 0.5
    for bad in (-1, 1801, True, "2", None, float("nan"), float("inf")):
        with pytest.raises(vol.Invalid):
            ls.validate_light_schedule(data(fade_in_s=bad))
        with pytest.raises(vol.Invalid):
            ls.validate_light_schedule(data(fade_out_s=bad))


@aio
async def test_old_saved_schedules_without_fades_load_with_the_default(hass):
    STORAGE[const.LIGHT_SCHEDULE_STORE_KEY] = {"schedules": [{**data(), "id": "abc"}]}
    STORAGE[const.LIGHT_SCHEDULE_STORE_KEY]["schedules"][0].pop("fade_in_s")
    STORAGE[const.LIGHT_SCHEDULE_STORE_KEY]["schedules"][0].pop("fade_out_s")
    s = new_scheduler(hass)
    await s.async_load()
    assert s.schedules[0]["fade_in_s"] == 2 and s.schedules[0]["fade_out_s"] == 2


@aio
async def test_long_fade_in_is_a_brightness_ramp_from_black(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, fade_in_s=120, fade_out_s=120)
    light = mgr.ctls["e1"]
    clock = {"t": 1000.0}
    light._now = lambda: clock["t"]
    await advance(s, at(18, 0))
    assert light.is_on and light.effect == "Pattern" and light.pattern == "Warm White" and light.brightness == 128
    assert light._ramp is not None and light._ramp.dur == 120 and light._ramp.start == 0 and light._ramp.end == 1
    assert light._ramp.level(light._ramp.t0 + 60) == pytest.approx(0.5)
    assert light._xf is None  # a brightness ramp, not a frame blend


@aio
async def test_long_fade_out_ramps_down_then_the_light_goes_off(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, fade_in_s=0, fade_out_s=120)
    light = mgr.ctls["e1"]
    await advance(s, at(19))
    assert light.is_on and light._ramp is None or light._ramp.end == 1
    await advance(s, at(22, 0))
    assert light.is_on and light._off_handle is not None  # fading, not yet off
    assert light._ramp.end == 0 and light._ramp.dur == 120
    light._finish_off()
    assert not light.is_on


@aio
async def test_a_new_window_during_a_fade_out_brings_the_light_back(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, fade_in_s=0, fade_out_s=300)
    light = mgr.ctls["e1"]
    await advance(s, at(19))
    await advance(s, at(22, 0))
    assert light._off_handle is not None
    await light.async_turn_on(transition=0)
    assert light._off_handle is None and light.is_on


@aio
async def test_area_zone_gets_the_fades(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, targets={"areas": ["Garage"]}, fade_in_s=90, fade_out_s=45)
    await advance(s, at(19))
    assert mgr.fades and mgr.fades[-1] == ("garage", 90, 45)
    await advance(s, at(22, 0))
    assert mgr.stop_fades[-1] == ("garage", 45)


@aio
async def test_stop_early_and_disable_use_the_fade_too(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    sched = await save(s, fade_in_s=0, fade_out_s=30)
    await advance(s, at(19))
    await s.async_stop(sched["id"])
    assert mgr.ctls["e1"]._off_handle is not None and mgr.ctls["e1"]._ramp.dur == 30


def test_look_accepts_map_effects_and_rejects_unknown_ones():
    for eff in ("Sweep", "Ripple", "Snowfall", "Rise", "Rainbow Wash", "Fireworks", "Breathe Outward", "Chase"):
        assert ls.validate_light_schedule(data(look={"pattern": "Rainbow", "effect": eff}))["look"]["effect"] == eff
    with pytest.raises(vol.Invalid):
        ls.validate_light_schedule(data(look={"pattern": "Rainbow", "effect": "Nope"}))
    with pytest.raises(vol.Invalid):
        ls.validate_light_schedule(data(look={"pattern": "Nope", "effect": "Chase"}))


@aio
async def test_map_effect_on_a_single_light_becomes_its_string_equivalent(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, targets={"lights": ["e3"]}, look={"pattern": "Rainbow", "effect": "Breathe Outward"})
    await advance(s, at(19))
    assert mgr.ctls["e3"].pattern_effect == "Fade"


@aio
async def test_map_effect_on_an_area_is_passed_to_the_zone(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, targets={"areas": ["Garage"]}, look={"pattern": "Rainbow", "effect": "Ripple"})
    await advance(s, at(19))
    assert mgr.started[-1][:3] == ("garage", "Rainbow", "Ripple")


@aio
async def test_look_can_use_one_of_your_own_patterns_by_name_or_id_and_follows_a_rename(hass):
    up = M["user_patterns"].UserPattern({"name": "Mine", "kind": "palette", "colours": [[1, 2, 3]], "bulbs_per_colour": 1,
                                        "default_effect": "Twinkle", "default_speed": 50}, "ab12cd34")
    patterns.set_user_patterns([up])
    try:
        mgr = world(hass)
        s = new_scheduler(hass)
        sched = await save(s, look={"pattern": "ab12cd34", "effect": "Steady"})
        assert sched["look"]["pattern"] == "Mine"  # stored by name
        await advance(s, at(19))
        assert mgr.ctls["e1"].pattern == "Mine"
        await s.async_pattern_changed("Mine", "Renamed")
        assert s.get_schedule(sched["id"])["look"]["pattern"] == "Renamed"
        await s.async_pattern_changed("Renamed", None)  # deleted: the default stands in
        assert s.get_schedule(sched["id"])["look"]["pattern"] == patterns.DEFAULT_PATTERN
    finally:
        patterns.set_user_patterns([])
