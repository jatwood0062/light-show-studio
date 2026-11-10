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
             "library", "scheduler", "holiday_calendar", "light_schedules")
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
sm, hc = M["schedule_math"], M["holiday_calendar"]
ALL = [0, 1, 2, 3, 4, 5, 6]


def at(h, m=0, day=7, month=12, year=2026):
    """A local wall-clock time as UTC."""
    return datetime(year, month, day, h, m, tzinfo=TZ).astimezone(UTC)


class FakeBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def async_fire(self, name, data):
        self.events.append((name, data))


class FakeStates:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, entity_id):
        return types.SimpleNamespace(state=self.values[entity_id]) if entity_id in self.values else None


class FakeHass:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.tasks = []
        self.data = {}
        self.bus = FakeBus()
        self.states = FakeStates()

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
    # a light removed since the schedule was made is dropped, not an error, while something is left to drive
    out = await save(s, targets={"lights": ["e1", "gone"]})
    assert out["targets"]["lights"] == ["e1"]
    out = await save(s, targets={"areas": ["porch"], "lights": ["gone"]})
    assert out["targets"] == {"areas": ["porch"], "lights": []}


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


# === 2.6.1 scheduling features: look rotation, holidays, random offset, presence ===========================
def look(pattern, effect="Steady"):
    return {"pattern": pattern, "effect": effect, "speed": 50, "brightness": 100}


THREE = [look("Warm White"), look("Cool White"), look("Candy Cane")]


# --- back-compat ----------------------------------------------------------------------------------
@aio
async def test_old_saved_schedules_load_with_the_new_options_off(hass):
    old = {"id": "a1b2c3d4", "name": "Porch", "enabled": True, "days": ALL, "start": {"type": "time", "time": "18:00",
           "offset_min": 0}, "end": {"type": "time", "time": "22:00", "offset_min": 0}, "date_from": None,
           "date_to": None, "targets": {"areas": ["Porch"], "lights": []},
           "look": {"pattern": "Warm White", "effect": "Steady", "speed": 30, "brightness": 50},
           "fade_in_s": 2, "fade_out_s": 2}
    STORAGE[const.LIGHT_SCHEDULE_STORE_KEY] = {"schedules": [old], "windows": {}, "manual": {}}  # no "holidays" key
    mgr = world(hass)
    s = new_scheduler(hass)
    await s.async_load()
    [got] = s.schedules
    assert got["look"] == old["look"] and got["looks"] == [] and got["rotate"] is None
    assert got["holiday_looks"] is False and got["random_offset_min"] == 0 and got["only_when"] == "any"
    assert s.holiday_overrides == {}
    await advance(s, at(19))
    assert mgr.ctls["e1"].pattern == "Warm White"  # behaves exactly as before
    view = s.views()[0]
    assert view["look_now"]["pattern"] == "Warm White" and view["holiday_now"] is None and view["skipped_now"] is False
    await s.async_flush()
    assert STORAGE[const.LIGHT_SCHEDULE_STORE_KEY]["holidays"] == {}


@aio
async def test_a_single_look_stays_valid_and_looks_is_optional_extra(hass):
    out = ls.validate_light_schedule(data())
    assert out["look"]["pattern"] == "Warm White" and out["looks"] == [] and out["rotate"] is None
    d = data()
    del d["look"]
    d["looks"] = THREE
    out = ls.validate_light_schedule(d)
    assert out["look"] == out["looks"][0] and out["rotate"] == {"type": "night"}  # look = first; default rotation


def test_saved_view_round_trips_through_save():
    d = data(looks=THREE, rotate={"type": "interval", "minutes": 30}, holiday_looks=True, random_offset_min=15,
             only_when="away")
    del d["look"]
    first = ls.validate_light_schedule(d)
    again = ls.validate_light_schedule({k: v for k, v in first.items()})  # look AND looks together (equal) is fine
    assert again == first


# --- validation of the new fields ---------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    {"looks": THREE[:1], "rotate": {"type": "night"}},  # one look cannot rotate
    {"rotate": {"type": "night"}},  # no looks
    {"looks": THREE, "look": look("Rainbow")},  # look must equal looks[0]
    {"looks": THREE * 5},  # more than 12
    {"looks": [look("Nope")]},
    {"looks": THREE, "rotate": {"type": "interval", "minutes": 0}},
    {"looks": THREE, "rotate": {"type": "interval", "minutes": 1441}},
    {"looks": THREE, "rotate": {"type": "interval", "minutes": True}},
    {"looks": THREE, "rotate": {"type": "interval", "minutes": 5.5}},
    {"looks": THREE, "rotate": {"type": "night", "minutes": 5}},
    {"looks": THREE, "rotate": {"type": "random"}},
    {"looks": THREE, "rotate": "night"},
    {"holiday_looks": "yes"},
    {"holiday_looks": 1},
    {"random_offset_min": 61},
    {"random_offset_min": -1},
    {"random_offset_min": True},
    {"random_offset_min": 5.0},
    {"random_offset_min": "5"},
    {"only_when": "sometimes"},
    {"only_when": None},
    {"bogus": 1},
])
def test_new_fields_are_strict(bad):
    d = data(**bad)
    if bad.get("looks") and "look" not in bad:
        del d["look"]
    with pytest.raises(vol.Invalid):
        ls.validate_light_schedule(d)


def test_a_schedule_needs_a_look_or_looks():
    d = data()
    del d["look"]
    with pytest.raises(vol.Invalid):
        ls.validate_light_schedule(d)


def test_valid_new_fields():
    d = data(random_offset_min=60, only_when="home", holiday_looks=True)
    out = ls.validate_light_schedule(d)
    assert out["random_offset_min"] == 60 and out["only_when"] == "home" and out["holiday_looks"] is True
    d = data(looks=THREE[:2], rotate={"type": "interval", "minutes": 1440})
    del d["look"]
    assert ls.validate_light_schedule(d)["rotate"] == {"type": "interval", "minutes": 1440}
    d = data(looks=[look("Rainbow")] * 12)
    del d["look"]
    assert len(ls.validate_light_schedule(d)["looks"]) == 12


# --- look rotation -----------------------------------------------------------------------------------
def test_rotation_index_is_deterministic_and_steps_each_night():
    start = at(18)
    seen = []
    for i in range(12):
        day = date(2026, 12, 1) + timedelta(days=i)
        a = sm.rotation_index(3, {"type": "night"}, "sched1", day, start, start)
        assert a == sm.rotation_index(3, {"type": "night"}, "sched1", day, start, start + timedelta(hours=3))  # same night
        seen.append(a)
    assert all((b - a) % 3 == 1 for a, b in zip(seen, seen[1:]))  # a different look every night, cycling in order
    assert sorted(set(seen)) == [0, 1, 2]
    # other schedules start at other places in the cycle, but the same schedule always agrees with itself
    firsts = {sm.rotation_index(3, {"type": "night"}, f"s{i}", date(2026, 12, 1), start, start) for i in range(20)}
    assert len(firsts) > 1
    assert sm.rotation_index(1, {"type": "night"}, "x", date(2026, 12, 1), start, start) == 0
    assert sm.rotation_index(3, None, "x", date(2026, 12, 1), start, start) == 0


def test_interval_rotation_steps_and_wraps():
    start = at(18)
    rot = {"type": "interval", "minutes": 30}
    base = sm.rotation_index(3, rot, "s", date(2026, 12, 7), start, start)
    got = [sm.rotation_index(3, rot, "s", date(2026, 12, 7), start, start + timedelta(minutes=m))
           for m in (0, 29, 30, 59, 60, 90, 120)]
    assert got == [(base + k) % 3 for k in (0, 0, 1, 1, 2, 3, 4)]
    assert sm.next_rotation(rot, start, start + timedelta(minutes=29)) == start + timedelta(minutes=30)
    assert sm.next_rotation(rot, start, start + timedelta(minutes=30)) == start + timedelta(minutes=60)
    assert sm.next_rotation({"type": "night"}, start, start) is None and sm.next_rotation(None, start, start) is None


@aio
async def test_each_night_shows_a_different_look(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    d = data(looks=THREE, rotate={"type": "night"})
    del d["look"]
    sched = await s.async_save_schedule(None, d)
    shown = []
    for n in range(4):
        day = 7 + n
        await advance(s, at(19, day=day))
        shown.append(mgr.ctls["e1"].pattern)
        want = THREE[sm.rotation_index(3, {"type": "night"}, sched["id"], date(2026, 12, day), at(18, day=day), at(19, day=day))]
        assert shown[-1] == want["pattern"]
        await advance(s, at(22, 1, day=day))
    assert all(a != b for a, b in zip(shown, shown[1:]))  # neighbouring nights differ
    assert len(set(shown[:3])) == 3


@aio
async def test_interval_rotation_swaps_the_look_inside_one_window(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    d = data(looks=THREE, rotate={"type": "interval", "minutes": 60})
    del d["look"]
    sched = await s.async_save_schedule(None, d)
    order = [THREE[(sm.rotation_index(3, sched["rotate"], sched["id"], date(2026, 12, 7), at(18), at(18)) + k) % 3]["pattern"]
             for k in range(4)]
    await advance(s, at(18, 1))
    assert mgr.ctls["e1"].pattern == order[0]
    assert len(events(hass, const.EVENT_LIGHT_SCHEDULE_STARTED)) == 1
    TIMERS.clear()
    await advance(s, at(18, 30))
    assert mgr.ctls["e1"].pattern == order[0]
    assert at(19) in TIMERS or min(TIMERS) <= at(19)  # a timer wakes the scheduler for the next step
    await advance(s, at(19, 0))
    assert mgr.ctls["e1"].pattern == order[1]
    await advance(s, at(20, 1))
    assert mgr.ctls["e1"].pattern == order[2]
    assert len(events(hass, const.EVENT_LIGHT_SCHEDULE_STARTED)) == 1  # still the same window
    await advance(s, at(22, 1))
    assert not mgr.ctls["e1"].is_on  # and it still turns off at the end


@aio
async def test_a_rotation_step_leaves_a_light_a_person_changed(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    d = data(looks=THREE, rotate={"type": "interval", "minutes": 60})
    del d["look"]
    await s.async_save_schedule(None, d)
    await advance(s, at(18, 1))
    mgr.ctls["e1"].speed = 99  # a person tweaked it
    mine = mgr.ctls["e1"].pattern
    await advance(s, at(19, 1))
    assert mgr.ctls["e1"].pattern == mine and mgr.ctls["e1"].speed == 99


@aio
async def test_pattern_rename_follows_into_looks_and_holiday_overrides(hass):
    world(hass)
    s = new_scheduler(hass)
    up = M["user_patterns"].UserPattern({"name": "Mine", "kind": "palette", "colours": [[1, 2, 3]], "bulbs_per_colour": 1,
                                        "default_effect": "Twinkle", "default_speed": 50}, "ab12cd34")
    patterns.set_user_patterns([up])
    try:
        d = data(looks=[look("Mine"), look("Cool White")], rotate={"type": "night"})
        del d["look"]
        sched = await s.async_save_schedule(None, d)
        await s.async_set_holiday("halloween", {"look": look("Mine")})
        await s.async_pattern_changed("Mine", "Renamed")
        got = s.get_schedule(sched["id"])
        assert got["looks"][0]["pattern"] == "Renamed" and got["look"]["pattern"] == "Renamed"
        assert s.holiday_overrides["halloween"]["look"]["pattern"] == "Renamed"
    finally:
        patterns.set_user_patterns([])


# --- holidays ------------------------------------------------------------------------------------------
@aio
async def test_holiday_look_replaces_the_normal_look_inside_a_holiday_window(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, holiday_looks=True)
    await advance(s, at(19, day=20))  # Dec 20 2026: Christmas window (Dec 1-26); Hanukkah ended on the 11th
    assert mgr.ctls["e1"].pattern == "Red & Green" and mgr.ctls["e1"].pattern_effect == "Twinkle"
    assert s.views()[0]["holiday_now"] == "christmas"
    await advance(s, at(22, 1, day=20))
    await advance(s, at(19, day=7))  # Dec 7: Hanukkah (Dec 4-11) opened after Christmas, so it wins
    assert mgr.ctls["e1"].pattern == "Blue & White"
    await advance(s, at(22, 1, day=7))
    await advance(s, at(19, day=29))  # Dec 29: no holiday
    assert mgr.ctls["e1"].pattern == "Warm White" and s.views()[0]["holiday_now"] is None


@aio
async def test_holiday_looks_off_ignores_the_calendar(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s)
    await advance(s, at(19, day=20))
    assert mgr.ctls["e1"].pattern == "Warm White"


@aio
async def test_holiday_override_look_window_and_enabled(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, holiday_looks=True)
    await s.async_set_holiday("christmas", {"look": look("Candy Cane", "Chase")})
    await advance(s, at(19, day=20))
    assert (mgr.ctls["e1"].pattern, mgr.ctls["e1"].pattern_effect) == ("Candy Cane", "Chase")
    # changing the holiday while the window is open swaps the look in place
    await s.async_set_holiday("christmas", {"enabled": False})
    assert mgr.ctls["e1"].pattern == "Warm White"
    await s.async_set_holiday("christmas", {"enabled": None, "look": None, "before": 2, "after": 0})  # clears two, sets window
    assert s.holiday_overrides["christmas"] == {"before": 2, "after": 0}
    assert mgr.ctls["e1"].pattern == "Warm White"  # Dec 20 is outside Dec 23-25 now
    await s.async_set_holiday("christmas", {"before": None, "after": None})
    assert "christmas" not in s.holiday_overrides  # an empty override is not kept
    assert mgr.ctls["e1"].pattern == "Red & Green"
    await s.async_set_holiday("christmas", {"enabled": False})
    await s.async_reset_holiday("christmas")
    assert s.holiday_overrides == {} and mgr.ctls["e1"].pattern == "Red & Green"


@aio
async def test_holiday_overrides_are_stored_and_reloaded_strictly(hass):
    world(hass)
    s = new_scheduler(hass)
    await s.async_set_holiday("july_4", {"look": look("Rainbow", "Wave"), "after": 5})
    await s.async_flush()
    saved = STORAGE[const.LIGHT_SCHEDULE_STORE_KEY]["holidays"]
    assert saved["july_4"]["after"] == 5 and saved["july_4"]["look"]["pattern"] == "Rainbow"
    saved["nonsense"] = {"enabled": False}  # unknown holiday
    saved["easter"] = {"before": 61}  # out of range
    saved["labor_day"] = {"enabled": "yes"}
    saved["christmas"] = "x"
    saved["halloween"] = {"look": {"pattern": "Halloween", "effect": "Nope"}}
    saved["diwali"] = {"surprise": True}
    s2 = new_scheduler(hass)
    await s2.async_load()
    assert list(s2.holiday_overrides) == ["july_4"]
    for hid, bad in (("nonsense", {"enabled": True}), ):
        with pytest.raises(KeyError):
            await s2.async_set_holiday(hid, bad)
    for bad in ({"before": 61}, {"before": -1}, {"before": True}, {"enabled": "no"}, {"look": {"pattern": "Nope", "effect": "Steady"}},
                {"look": {"pattern": "Halloween"}}, {"surprise": 1}):
        with pytest.raises(vol.Invalid):
            await s2.async_set_holiday("easter", bad)
    with pytest.raises(KeyError):
        await s2.async_reset_holiday("nonsense")


@aio
async def test_holidays_view_lists_the_calendar_and_upcoming(hass):
    world(hass)
    s = new_scheduler(hass)
    v = s.holidays_view(30, today=date(2026, 12, 7))
    by = {h["id"]: h for h in v["holidays"]}
    assert len(by) == len(hc.HOLIDAYS) and v["active"] == "hanukkah" and v["today"] == "2026-12-07"
    assert by["christmas"]["date"] == "2026-12-25" and by["christmas"]["window_start"] == "2026-12-01"
    assert by["christmas"]["active"] is True and by["easter"]["date"] == "2027-03-28"
    assert by["thanksgiving"]["date"] == "2027-11-25"  # this year's has gone by
    assert by["christmas"]["default_look"] == by["christmas"]["look"] and by["christmas"]["overridden"] is False
    up = [u["id"] for u in v["upcoming"]]
    assert up[:3] == ["christmas", "hanukkah", "new_year"] or "hanukkah" in up
    assert "easter" not in up and "new_year" in up
    await s.async_set_holiday("christmas", {"enabled": False})
    v = s.holidays_view(30, today=date(2026, 12, 7))
    assert {h["id"]: h for h in v["holidays"]}["christmas"]["overridden"] is True
    assert {u["id"]: u for u in v["upcoming"]}["christmas"]["enabled"] is False


def test_every_default_look_uses_an_existing_pattern_and_valid_effect():
    for h in hc.HOLIDAYS:
        assert h.look["pattern"] in patterns.PATTERNS, h.id
        assert h.look["effect"] in patterns.ALL_EFFECTS, h.id
        ls._LOOK(h.look)  # passes the same schema a person's override would
        assert 0 <= h.before <= hc.MAX_WINDOW_DAYS and 0 <= h.after <= hc.MAX_WINDOW_DAYS


# --- random offset -----------------------------------------------------------------------------------
def _sun_fn(day, event):
    return datetime(day.year, day.month, day.day, 7 if event == "sunrise" else 17, tzinfo=TZ).astimezone(UTC)


def test_random_offset_is_deterministic_and_bounded():
    seen = set()
    for i in range(400):
        day = date(2026, 1, 1) + timedelta(days=i)
        for kind in ("start", "end"):
            a = sm.random_offset("abc", day, kind, 30)
            assert a == sm.random_offset("abc", day, kind, 30) and -30 <= a <= 30
            seen.add(a)
    assert len(seen) > 40 and min(seen) < -20 and max(seen) > 20  # it does spread across the range
    assert all(sm.random_offset("abc", date(2026, 1, 1), "start", 0) == 0 for _ in range(3))
    assert {sm.random_offset("abc", date(2026, 3, d), "start", 1) for d in range(1, 29)} <= {-1, 0, 1}
    # start and end and different schedules are shaken independently
    assert [sm.random_offset("abc", date(2026, 1, d), "start", 60) for d in range(1, 15)] != \
           [sm.random_offset("abc", date(2026, 1, d), "end", 60) for d in range(1, 15)]
    assert [sm.random_offset("abc", date(2026, 1, d), "start", 60) for d in range(1, 15)] != \
           [sm.random_offset("xyz", date(2026, 1, d), "start", 60) for d in range(1, 15)]


def test_random_offset_shifts_window_edges_within_bounds_and_is_stable():
    plain = {"id": "s1", "days": ALL, "start": {"type": "time", "time": "18:00"}, "end": {"type": "time", "time": "23:00"}}
    shaken = {**plain, "random_offset_min": 45}
    moved = 0
    for i in range(120):
        day = date(2026, 9, 1) + timedelta(days=i)
        base = sm.window_for_day(plain, day, TZ, _sun_fn)
        w = sm.window_for_day(shaken, day, TZ, _sun_fn)
        assert w == sm.window_for_day(shaken, day, TZ, _sun_fn)  # same answer every time
        assert abs((w.start - base.start).total_seconds()) <= 45 * 60 and abs((w.end - base.end).total_seconds()) <= 45 * 60
        assert w.end > w.start and w.day == day
        moved += w.start != base.start
    assert moved > 80  # most days really moved
    # offset 0 / missing is exactly the old behaviour
    assert sm.window_for_day({**plain, "random_offset_min": 0}, date(2026, 9, 1), TZ, _sun_fn) == \
           sm.window_for_day(plain, date(2026, 9, 1), TZ, _sun_fn)
    # a "leave on" start moves too, and sunset edges are shaken after the offset
    one = {"id": "s1", "days": ALL, "start": {"type": "sunset", "offset_min": 0}, "end": None, "random_offset_min": 20}
    w = sm.window_for_day(one, date(2026, 9, 1), TZ, _sun_fn)
    assert abs((w.start - _sun_fn(date(2026, 9, 1), "sunset")).total_seconds()) <= 20 * 60 and w.end is None


def test_a_short_window_never_turns_inside_out():
    s = {"id": "s", "days": ALL, "start": {"type": "time", "time": "18:00"}, "end": {"type": "time", "time": "18:05"},
         "random_offset_min": 60}
    for i in range(200):
        w = sm.window_for_day(s, date(2026, 1, 1) + timedelta(days=i), TZ, _sun_fn)
        assert w.end > w.start


@aio
async def test_scheduler_opens_the_window_at_the_shifted_time(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    sched = await save(s, random_offset_min=40)
    w = sm.window_for_day(sched, date(2026, 12, 7), TZ, s._sun)
    assert w.start != at(18) or w.end != at(22)  # (this day's draw moves at least one edge for this id)
    await advance(s, w.start - timedelta(minutes=1))
    assert not mgr.ctls["e1"].is_on
    await advance(s, w.start + timedelta(seconds=30))
    assert mgr.ctls["e1"].is_on
    await advance(s, w.end + timedelta(seconds=30))
    assert not mgr.ctls["e1"].is_on
    assert s.views()[0]["next_on"] == sm.window_for_day(sched, date(2026, 12, 8), TZ, s._sun).start.isoformat()


# --- presence ----------------------------------------------------------------------------------------
@aio
async def test_only_when_home_skips_the_start_when_nobody_is_home(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    hass.states.values["zone.home"] = "0"
    await save(s, only_when="home")
    await advance(s, at(18, 1))
    assert not mgr.ctls["e1"].is_on
    assert events(hass, const.EVENT_LIGHT_SCHEDULE_STARTED) == []
    assert s.views()[0]["skipped_now"] is True and s.views()[0]["active"] is False
    hass.states.values["zone.home"] = "2"  # someone comes home mid-window: checked at window start only
    await advance(s, at(19))
    assert not mgr.ctls["e1"].is_on
    await advance(s, at(18, 1, day=8))  # the next day's window is checked afresh
    assert mgr.ctls["e1"].is_on


@aio
async def test_only_when_home_runs_when_someone_is_home_and_is_not_rechecked(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    hass.states.values["zone.home"] = "1"
    await save(s, only_when="home")
    await advance(s, at(18, 1))
    assert mgr.ctls["e1"].is_on
    hass.states.values["zone.home"] = "0"  # everyone leaves: the open window carries on
    await advance(s, at(20))
    assert mgr.ctls["e1"].is_on
    await advance(s, at(22, 1))
    assert not mgr.ctls["e1"].is_on


@aio
async def test_only_when_away(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    hass.states.values["zone.home"] = "3"
    await save(s, only_when="away")
    await advance(s, at(18, 1))
    assert not mgr.ctls["e1"].is_on
    hass.states.values["zone.home"] = "0"
    await advance(s, at(18, 1, day=8))
    assert mgr.ctls["e1"].is_on


@aio
async def test_only_when_with_unknown_state_runs_and_any_never_looks(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    await save(s, only_when="home")  # no zone.home state at all
    await advance(s, at(18, 1))
    assert mgr.ctls["e1"].is_on
    await advance(s, at(22, 1))
    hass.states.values["zone.home"] = "unavailable"
    await advance(s, at(18, 1, day=8))
    assert mgr.ctls["e1"].is_on
    # "any" never reads the state
    s2 = new_scheduler(hass)
    hass.states = None
    assert s2._presence_ok({"only_when": "any"}) is True


@aio
async def test_only_when_applies_to_leave_on_starts_and_run_now_ignores_it(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    s._last = at(12)
    hass.states.values["zone.home"] = "0"
    sched = await save(s, only_when="home", end=None, start={"type": "time", "time": "18:00"})
    await advance(s, at(18, 1))
    assert not mgr.ctls["e1"].is_on
    await advance(s, at(18, 2))
    assert not mgr.ctls["e1"].is_on  # not retried
    await s.async_run_now(sched["id"])  # a person asked: it runs
    assert mgr.ctls["e1"].is_on


@aio
async def test_skipped_window_survives_a_restart(hass):
    mgr = world(hass)
    s = new_scheduler(hass)
    hass.states.values["zone.home"] = "0"
    await save(s, only_when="home")
    await advance(s, at(18, 1))
    await s.async_flush()
    hass.states.values["zone.home"] = "2"
    s2 = new_scheduler(hass)
    await s2.async_load()
    await advance(s2, at(19))
    assert not mgr.ctls["e1"].is_on  # the window was already judged


# --- websocket ---------------------------------------------------------------------------------------
def _ws():
    wsmod = _stub("homeassistant.components.websocket_api")
    comp = _stub("homeassistant.components")
    comp.websocket_api = wsmod

    def websocket_command(schema):
        def deco(f):
            f._ws_schema = schema
            return f
        return deco

    def require_admin(f):
        f._admin = True
        return f

    wsmod.websocket_command, wsmod.require_admin = websocket_command, require_admin
    wsmod.async_response = lambda f: f
    wsmod.ActiveConnection = object
    wsmod.event_message = lambda i, d: {"id": i, "event": d}
    full = "light_show_studio_ls.ws_light_schedules"
    if full not in sys.modules:
        spec = importlib.util.spec_from_file_location(full, PKG / "ws_light_schedules.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[full] = m
        spec.loader.exec_module(m)
    return sys.modules[full]


class Conn:
    def __init__(self):
        self.results, self.errors = [], []

    def send_result(self, i, result=None):
        self.results.append(result)

    def send_error(self, i, code, message):
        self.errors.append((code, message))


async def _call(handler, hass, msg):
    conn = Conn()
    data = vol.Schema({**handler._ws_schema, vol.Required("id"): int})({"id": 1, **msg})
    res = handler(hass, conn, data)
    if asyncio.iscoroutine(res):
        await res
    return conn


@aio
async def test_ws_holiday_commands_and_admin_flags(hass):
    ws = _ws()
    world(hass)
    s = new_scheduler(hass)
    hass.data[const.LIGHT_SCHEDULER_KEY] = s
    P = "light_show_studio/light_schedules"
    for h in (ws.ws_holiday_set, ws.ws_holiday_reset):
        assert getattr(h, "_admin", False) is True  # writes are admin only
    assert not getattr(ws.ws_holidays, "_admin", False)  # reading is not
    c = await _call(ws.ws_holidays, hass, {"type": f"{P}/holidays"})
    res = c.results[0]
    assert len(res["holidays"]) == len(hc.HOLIDAYS) and "upcoming" in res and res["table_years"] == [2026, 2030]
    c = await _call(ws.ws_holiday_set, hass, {"type": f"{P}/holiday_set", "holiday_id": "christmas", "enabled": False,
                                               "before": 10, "look": look("Candy Cane", "Chase")})
    assert c.errors == [] and c.results[0]["holiday"]["enabled"] is False and c.results[0]["holiday"]["before"] == 10
    assert c.results[0]["holiday"]["look"]["pattern"] == "Candy Cane"
    c = await _call(ws.ws_holiday_set, hass, {"type": f"{P}/holiday_set", "holiday_id": "nope", "enabled": True})
    assert c.errors[0][0] == "not_found"
    c = await _call(ws.ws_holiday_set, hass, {"type": f"{P}/holiday_set", "holiday_id": "easter",
                                               "look": {"pattern": "Nope", "effect": "Steady"}})
    assert c.errors[0][0] == "invalid_format"
    c = await _call(ws.ws_holiday_reset, hass, {"type": f"{P}/holiday_reset", "holiday_id": "christmas"})
    assert c.results[0]["holiday"]["overridden"] is False
    # strict schemas: unknown keys, wrong types and sizes are refused by the command schema itself
    for bad in ({"holiday_id": "easter", "color": 1}, {"holiday_id": "x" * 65}, {"holiday_id": "easter", "before": 99},
                {"holiday_id": "easter", "before": "3"}, {"holiday_id": "easter", "enabled": "yes"}, {"holiday_id": "easter", "look": "red"}):
        with pytest.raises(vol.Invalid):
            await _call(ws.ws_holiday_set, hass, {"type": f"{P}/holiday_set", **bad})
    for bad in ({"days": 0}, {"days": 367}, {"days": "5"}, {"extra": 1}):
        with pytest.raises(vol.Invalid):
            await _call(ws.ws_holidays, hass, {"type": f"{P}/holidays", **bad})


@aio
async def test_ws_save_accepts_the_new_fields_and_view_reports_them(hass):
    ws = _ws()
    world(hass)
    s = new_scheduler(hass)
    hass.data[const.LIGHT_SCHEDULER_KEY] = s
    msg = {"type": "light_show_studio/light_schedules/save", **data(looks=THREE, rotate={"type": "night"},
           holiday_looks=True, random_offset_min=20, only_when="away")}
    del msg["look"]
    c = await _call(ws.ws_save, hass, msg)
    assert c.errors == []
    v = c.results[0]["schedule"]
    assert v["looks"] == THREE and v["rotate"] == {"type": "night"} and v["holiday_looks"] is True
    assert v["random_offset_min"] == 20 and v["only_when"] == "away" and v["look"] == THREE[0]
    c = await _call(ws.ws_save, hass, {"type": "light_show_studio/light_schedules/save", **data(random_offset_min=99)})
    assert c.errors[0][0] == "invalid_format"
