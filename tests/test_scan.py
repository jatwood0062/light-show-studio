"""Tests for camera scanning: the code sequence, the controller owning a light for a scan, and the manager's
sessions (idle timeout, release on close). Small fakes, plain pytest, no Home Assistant."""
from __future__ import annotations

import asyncio
import importlib.util
import math
import sys
import types
from types import SimpleNamespace

import pytest

import test_zones as tz
from test_library import _load

scan_codes = _load("scan_codes")
BLACK, R = (0, 0, 0), (255, 0, 0)
LEVEL = (scan_codes.SCAN_LEVEL,) * 3


# --- the code sequence ---------------------------------------------------------------------------
@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8, 9, 50, 279, 904, 1404, 2040])
def test_frame_count_and_round_trip(n):
    frames = scan_codes.frame_count(n)
    assert frames == 2 + 2 * math.ceil(math.log2(n)) if n > 1 else frames == 2
    masks = [scan_codes.frame_mask(n, f) for f in range(frames)]
    assert masks[0] == [False] * n and masks[1] == [True] * n
    for i in range(n):
        assert scan_codes.decode_pixel([1.0 if m[i] else 0.0 for m in masks], n) == i
        assert scan_codes.decode_pixel(scan_codes.simulate(n, i, dark=0.1, lit=0.9), n) == i


def test_904_leds_need_22_frames():
    assert scan_codes.frame_count(904) == 22 and scan_codes.bit_count(1024) == 10 and scan_codes.bit_count(1025) == 11


def test_each_bit_frame_has_its_inverse_and_every_pixel_unique():
    n = 37
    for bit in range(scan_codes.bit_count(n)):
        a, b = scan_codes.frame_mask(n, 2 + 2 * bit), scan_codes.frame_mask(n, 3 + 2 * bit)
        assert all(x != y for x, y in zip(a, b))
    codes = {tuple(scan_codes.frame_mask(n, f)[i] for f in range(scan_codes.frame_count(n))) for i in range(n)}
    assert len(codes) == n


def test_frame_colours_are_a_fixed_white():
    cols = scan_codes.frame_colours(5, 2)
    assert cols == [BLACK, LEVEL, BLACK, LEVEL, BLACK] and scan_codes.SCAN_LEVEL < 255
    assert scan_codes.frame_colours(3, 1) == [LEVEL] * 3 and scan_codes.frame_colours(3, 0) == [BLACK] * 3


def test_decode_rejects_unclear_readings():
    n = 10
    good = scan_codes.simulate(n, 6)
    assert scan_codes.decode_pixel(good, n) == 6
    assert scan_codes.decode_pixel([0.0] * len(good), n) is None  # never lit
    assert scan_codes.decode_pixel([0.5, 0.5] + [0.5] * (len(good) - 2), n) is None  # no contrast
    muddy = list(good)
    muddy[2], muddy[3] = 0.5, 0.52  # bit 0 can't be told
    assert scan_codes.decode_pixel(muddy, n) is None
    # an index past the end (all bits on for n = 10 is 15) is refused
    assert scan_codes.decode_pixel(scan_codes.simulate(16, 15), 10) is None
    # a noisy camera still reads right
    noisy = [v + (0.04 if k % 2 else -0.04) for k, v in enumerate(scan_codes.simulate(n, 7, dark=0.1, lit=0.9))]
    assert scan_codes.decode_pixel(noisy, n) == 7
    with pytest.raises(ValueError):
        scan_codes.decode_pixel([1, 0], n)
    with pytest.raises(ValueError):
        scan_codes.frame_mask(n, 99)
    with pytest.raises(ValueError):
        scan_codes.frame_count(0)


# --- the controller ----------------------------------------------------------------------------------
FakeLight, aio, controller = tz.FakeLight, tz.aio, tz.controller


@aio
async def test_scan_takes_the_light_and_shows_frames_at_a_fixed_level(hass):
    ctl = FakeLight(hass, 4)
    ctl.brightness = 30  # must not dim the scan
    ctl.colour, ctl.show_mode = tz.G, False
    ctl.effect = "Rainbow"
    await ctl.async_claim_scan()
    assert ctl.scanning
    assert ctl.push_scan_frame([LEVEL, BLACK, LEVEL, BLACK])
    assert ctl.sent[-1] == [LEVEL, BLACK, LEVEL, BLACK]
    await asyncio.sleep(0.1)
    n = len(ctl.sent)
    await asyncio.sleep(0.2)
    assert len(ctl.sent) == n  # the effect's animation task is gone


@aio
async def test_scan_stops_a_running_zone_and_ignores_the_sequencer(hass):
    ctl = FakeLight(hass, 3)
    await ctl.async_claim_zone("z", "Zone")
    ctl.push_zone_frame("z", [(0, [R, R, R])])
    await ctl.async_claim_scan()
    assert ctl.zone is None and ctl.push_zone_frame("z", [(0, [R])]) is False  # the zone is dropped
    n = len(ctl.sent)
    ctl.show_mode = True
    ctl.show_external(bytes([9, 9, 9] * 3))
    assert len(ctl.sent) == n
    ctl.show_mode = False
    with pytest.raises(RuntimeError):  # and a zone can't take it back while the scan has it
        await ctl.async_claim_zone("z2", "Zone 2")
    await ctl.async_turn_on(brightness=100)  # a brightness change doesn't end the scan
    assert ctl.scanning


@aio
async def test_release_restores_the_colour_effect_or_off(hass):
    ctl = FakeLight(hass, 3)
    ctl.colour, ctl.show_mode = tz.G, False
    await ctl.async_claim_scan()
    ctl.push_scan_frame([LEVEL] * 3)
    ctl.release_scan(restore=True)
    assert not ctl.scanning and ctl.sent[-1] == [tz.G] * 3
    off = FakeLight(hass, 3, on=False)
    await off.async_claim_scan()
    assert off.is_on
    off.release_scan(restore=True)
    await asyncio.sleep(0.05)
    assert off.is_on is False and off.powered == [True, False]


@aio
async def test_release_returns_to_show_mode_with_the_last_picture(hass):
    ctl = FakeLight(hass, 3)
    ctl.enable_show()
    ctl.show_external(bytes([0, 255, 0] * 3))
    shown = list(ctl.sent[-1])
    await ctl.async_claim_scan()
    ctl.push_scan_frame([LEVEL] * 3)
    ctl.release_scan(restore=True)
    assert ctl.show_mode is True and ctl.sent[-1] == shown
    ctl._ext_sent = 0
    ctl.show_external(bytes([255, 0, 0] * 3))  # the sequencer drives it again
    assert ctl.sent[-1][0] == R


@aio
async def test_release_after_a_zone_goes_back_to_before_the_zone(hass):
    ctl = FakeLight(hass, 3, on=False)
    await ctl.async_claim_zone("z", "Zone")  # the zone turned it on
    await ctl.async_claim_scan()
    ctl.release_scan(restore=True)
    await asyncio.sleep(0.05)
    assert ctl.is_on is False  # off, as it was before the zone


@aio
async def test_a_person_taking_the_light_ends_the_scan(hass):
    for act in (lambda c: c.async_turn_on(colour=R), lambda c: c.async_turn_on(effect="Rainbow"),
                lambda c: c.async_turn_off(), lambda c: c.async_set_pixels([R]), lambda c: c.async_set_bulb(0, True, R),
                lambda c: _show(c)):
        ctl = FakeLight(hass, 3)
        await ctl.async_claim_scan()
        await act(ctl)
        assert not ctl.scanning and ctl.push_scan_frame([LEVEL] * 3) is False


async def _show(c):
    c.enable_show()


@aio
async def test_release_is_idempotent_and_shutdown_clears(hass):
    ctl = FakeLight(hass, 3)
    ctl.release_scan(restore=True)  # nothing to release
    await ctl.async_claim_scan()
    await ctl.async_claim_scan()  # twice: still one scan
    ctl.release_scan(restore=True)
    ctl.release_scan(restore=True)
    await ctl.async_claim_scan()
    ctl.shutdown()
    assert not ctl.scanning


@aio
async def test_identify_waits_for_the_scan(hass):
    ctl = FakeLight(hass, 3)
    await ctl.async_claim_scan()
    n = len(ctl.sent)
    await ctl.async_identify()
    assert len(ctl.sent) == n and ctl.scanning


# --- the manager -------------------------------------------------------------------------------------
def _stub(name, **attrs):
    mod = sys.modules.setdefault(name, types.ModuleType(name))
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class _Store:
    def __init__(self, hass, version, key):
        self.key, self.saved = key, None

    async def async_load(self):
        return None

    async def async_save(self, data):
        self.saved = data

    def async_delay_save(self, fn, delay):
        self.saved = fn()


class _HAError(Exception):
    pass


DEVICES: dict[str, list] = {}
AREA_NAMES = {"porch": SimpleNamespace(name="Front Porch")}


def _load_manager():
    cfg = _stub("homeassistant.config_entries", ConfigEntry=object, ConfigEntryState=SimpleNamespace(LOADED="loaded"))
    core = _stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    _stub("homeassistant.exceptions", HomeAssistantError=_HAError)
    ar = _stub("homeassistant.helpers.area_registry", async_get=lambda hass: SimpleNamespace(async_get_area=AREA_NAMES.get))
    dr = _stub("homeassistant.helpers.device_registry", async_get=lambda hass: None,
               async_entries_for_config_entry=lambda reg, eid: DEVICES.get(eid, []))
    er = _stub("homeassistant.helpers.entity_registry", async_get=lambda hass: None,
               async_entries_for_config_entry=lambda reg, eid: [])
    store = _stub("homeassistant.helpers.storage", Store=_Store)
    helpers = _stub("homeassistant.helpers", area_registry=ar, device_registry=dr, entity_registry=er, storage=store)
    sys.modules["homeassistant"].helpers = helpers
    for name in ("scan_codes", "mapmanager"):
        full = f"light_show_studio_zn.{name}"
        if full not in sys.modules:
            spec = importlib.util.spec_from_file_location(full, tz.PKG / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
    return sys.modules["light_show_studio_zn.mapmanager"]


mapmanager = _load_manager()
mapping = tz.mapping


class FakeEntry:
    def __init__(self, entry_id, ctl, title="Light", data=None):
        self.entry_id, self.runtime_data, self.title = entry_id, ctl, title
        self.domain, self.state = "light_show_studio", "loaded"
        self.data, self.options = data or {}, {}


class FakeConfigEntries:
    def __init__(self, entries):
        self.entries = {e.entry_id: e for e in entries}

    def async_entries(self, domain):
        return list(self.entries.values())

    def async_get_entry(self, entry_id):
        return self.entries.get(entry_id)


def make_manager(hass, lights):
    hass.config_entries = FakeConfigEntries([FakeEntry(eid, ctl, title) for eid, ctl, title in lights])
    return mapmanager.MapManager(hass)


@aio
async def test_manager_scan_start_frame_stop(hass):
    ctl = FakeLight(hass, 20)
    ctl.colour, ctl.show_mode = tz.G, False
    m = make_manager(hass, [("e1", ctl, "Roof")])
    tok = object()
    info = await m.async_scan_start("e1", tok)
    assert info == {"bulbs": 20, "frames": 2 + 2 * 5, "first": 0} and m.scanning == ["e1"]
    assert ctl.sent[-1] == [BLACK] * 20  # starts dark
    await m.async_scan_frame("e1", 1)
    assert ctl.sent[-1] == [LEVEL] * 20
    await m.async_scan_frame("e1", 2)  # bit 0 set: the odd pixels
    assert ctl.sent[-1] == [LEVEL if i & 1 else BLACK for i in range(20)]
    with pytest.raises(mapmanager.HomeAssistantError, match="Frame must be"):
        await m.async_scan_frame("e1", 12)
    m.scan_stop("e1")
    assert m.scanning == [] and not ctl.scanning and ctl.sent[-1] == [tz.G] * 20
    with pytest.raises(mapmanager.HomeAssistantError, match="isn't being scanned"):
        await m.async_scan_frame("e1", 1)
    m.scan_stop("e1")  # stopping twice is fine


@aio
async def test_manager_scan_a_stretch_of_the_light(hass):
    ctl = FakeLight(hass, 20)
    m = make_manager(hass, [("e1", ctl, "Roof")])
    info = await m.async_scan_start("e1", object(), first=5, count=8)
    assert info == {"bulbs": 8, "frames": 2 + 2 * 3, "first": 5}
    await m.async_scan_frame("e1", 1)
    assert ctl.sent[-1] == [BLACK] * 5 + [LEVEL] * 8 + [BLACK] * 7
    for bad in ({"first": 20}, {"first": 15, "count": 6}, {"count": 0}):
        with pytest.raises(mapmanager.HomeAssistantError):
            await m.async_scan_start("e1", object(), **bad)
    m.scan_stop("e1", restore=False)


@aio
async def test_manager_refuses_unknown_and_unsegmented_lights(hass):
    whole = FakeLight(hass, 5)
    whole.segmented = False
    m = make_manager(hass, [("w", whole, "Plain")])
    with pytest.raises(mapmanager.HomeAssistantError, match="separate bulbs"):
        await m.async_scan_start("w", object())
    with pytest.raises(mapmanager.HomeAssistantError, match="isn't set up"):
        await m.async_scan_start("nope", object())
    assert m.scanning == [] and not whole.scanning


@aio
async def test_scan_ends_when_a_person_takes_the_light(hass):
    ctl = FakeLight(hass, 8)
    m = make_manager(hass, [("e1", ctl, "Roof")])
    await m.async_scan_start("e1", object())
    await ctl.async_turn_on(colour=R)
    with pytest.raises(mapmanager.HomeAssistantError, match="taken"):
        await m.async_scan_frame("e1", 1)
    assert m.scanning == []


@aio
async def test_scan_idle_timeout_releases_the_light(hass, monkeypatch=None):
    ctl = FakeLight(hass, 8)
    ctl.colour, ctl.show_mode = tz.G, False
    m = make_manager(hass, [("e1", ctl, "Roof")])
    mapmanager.SCAN_IDLE = 0.15
    try:
        await m.async_scan_start("e1", object())
        await asyncio.sleep(0.1)
        await m.async_scan_frame("e1", 1)  # a frame restarts the timer
        await asyncio.sleep(0.1)
        assert ctl.scanning
        await asyncio.sleep(0.2)
        assert not ctl.scanning and m.scanning == [] and ctl.sent[-1] == [tz.G] * 8
    finally:
        mapmanager.SCAN_IDLE = 120.0
    assert mapmanager.SCAN_IDLE == 120.0


@aio
async def test_closed_connection_releases_but_a_newer_scan_survives(hass):
    ctl = FakeLight(hass, 8)
    m = make_manager(hass, [("e1", ctl, "Roof")])
    t1, t2 = object(), object()
    await m.async_scan_start("e1", t1)
    await m.async_scan_start("e1", t2)  # another connection takes over
    m.scan_stop("e1", t1)  # the first socket closes late
    assert ctl.scanning and m.scanning == ["e1"]
    m.scan_stop("e1", t2)  # the second closes
    assert not ctl.scanning and m.scanning == []


@aio
async def test_stop_all_lets_every_light_go(hass):
    a, b = FakeLight(hass, 4), FakeLight(hass, 4)
    m = make_manager(hass, [("a", a, "A"), ("b", b, "B")])
    await m.async_scan_start("a", object())
    await m.async_scan_start("b", object())
    m.async_stop_all()
    assert not a.scanning and not b.scanning and m.scanning == []


@aio
async def test_scan_of_a_light_in_a_running_zone_drops_it_from_the_zone(hass):
    a, b = FakeLight(hass, 10), FakeLight(hass, 10)
    m = make_manager(hass, [("a", a, "A"), ("b", b, "B")])
    await m.async_set_map({"version": 2, "areas": [{"id": "x", "name": "X"}], "zones": [{"id": "z", "name": "Z", "areas": ["x"]}],
                           "runs": [{"id": "r1", "entry_id": "a", "name": "A", "first": 0, "count": 10, "area": "x"},
                                    {"id": "r2", "entry_id": "b", "name": "B", "first": 0, "count": 10, "area": "x"}]})
    await m.async_start_zone("Z")
    await asyncio.sleep(0.1)
    await m.async_scan_start("a", object())
    n_b = len(b.sent)
    await asyncio.sleep(0.2)
    assert len(b.sent) > n_b and a.scanning and a.zone is None  # b carries on, a is the scan's
    m.async_stop_all()


# --- ad-hoc area zones and default areas ------------------------------------------------------------------
@aio
async def test_start_an_area_without_a_saved_zone(hass):
    a, b, c = FakeLight(hass, 10), FakeLight(hass, 10), FakeLight(hass, 10)
    DEVICES["a"] = [SimpleNamespace(area_id="porch")]
    DEVICES["b"] = [SimpleNamespace(area_id="porch")]
    try:
        m = make_manager(hass, [("a", a, "Roof"), ("b", b, "Rail"), ("c", c, "Tree")])
        model = m.model
        assert [(x.id, x.name) for x in model.areas] == [("ha_porch", "Front Porch"), ("light_c", "Tree")]
        assert m.lights()[0]["area"] == {"id": "ha_porch", "name": "Front Porch", "ha_area_id": "porch"}
        await m.async_start_area("front porch", "Rainbow", "Twinkle", 60)  # by name, any case
        assert m.running == ["area:ha_porch"]
        await asyncio.sleep(0.1)
        assert a.sent and b.sent and not c.sent and a.zone == "Front Porch"
        await m.async_set_speed("area:ha_porch", 90)
        assert m.runners["area:ha_porch"].speed == 90
        await m.async_stop_area("ha_porch")
        assert m.running == [] and a.zone is None
        await m.async_start_area("Tree")
        await m.async_stop_zone("area:light_c")
        assert m.running == []
        with pytest.raises(mapmanager.HomeAssistantError, match="No area"):
            await m.async_start_area("Garage")
        with pytest.raises(mapmanager.HomeAssistantError, match="Unknown effect"):
            await m.async_start_area("Tree", effect="Nope")
    finally:
        DEVICES.clear()
        m.async_stop_all()


@aio
async def test_area_zone_follows_a_map_change_and_ends_with_its_area(hass):
    a = FakeLight(hass, 10)
    m = make_manager(hass, [("a", a, "Roof")])
    base = {"version": 2, "areas": [{"id": "x", "name": "X"}, {"id": "y", "name": "Y"}], "zones": [],
            "runs": [{"id": "r1", "entry_id": "a", "name": "A", "first": 0, "count": 5, "area": "x"},
                     {"id": "r2", "entry_id": "a", "name": "B", "first": 5, "count": 5, "area": "y"}]}
    await m.async_set_map(base)
    await m.async_start_area("x", "Rainbow", "Steady")
    await asyncio.sleep(0.05)
    assert a.sent[-1][5:] == [BLACK] * 5 and a.sent[-1][0] != BLACK
    await m.async_set_map({**base, "areas": [{"id": "x", "name": "X"}]  , "runs": base["runs"][:1]})  # y goes away, x stays
    assert m.running == ["area:x"]
    await m.async_set_map({**base, "areas": [{"id": "y", "name": "Y"}], "runs": base["runs"][1:]})
    assert m.running == []
    m.async_stop_all()


@aio
async def test_v1_store_is_migrated_and_backed_up_on_load(hass):
    a = FakeLight(hass, 50)
    m = make_manager(hass, [("a", a, "Roof")])
    v1 = {"version": 1, "runs": [{"id": "r1", "entry_id": "a", "name": "A", "first": 0, "count": 10, "reverse": False,
                                  "pts": [[0.1, 0.9], [0.6, 0.9]]}],
          "zones": [{"id": "z", "name": "Z", "runs": ["r1"], "mode": "spatial", "axis": {"angle": 90}}]}

    async def load():
        return v1
    m._store.async_load = load
    await m.async_load()
    assert m._backup.saved == v1  # the old map is kept exactly as it was
    assert m.model.runs[0].area == "main" and m.model.zones[0].axis["dir"] == [0.0, 1.0, 0.0]
    assert m._store.saved["version"] == 2  # and the migrated one is written
    assert not m.is_default
