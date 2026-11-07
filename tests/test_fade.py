"""Transitions: the fade maths, and a light fading in, fading out and cross-fading between looks.

Plain pytest, no Home Assistant. The controller tests drive the fade clock by hand where they check exact values and
use real (short) fades where they check that frames really move over time."""
from __future__ import annotations

import asyncio
import importlib

import pytest

from test_zones import FakeHass, FakeLight, aio, controller, mapping, mapped_patterns, patterns, zoneplayer

fade = importlib.import_module("light_show_studio_zn.fade")

BLACK, RED, BLUE = (0, 0, 0), (255, 0, 0), (0, 0, 255)


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def lit(frame):
    return max(max(c) for c in frame)


# --- the maths ---------------------------------------------------------------------------------
def test_clean_seconds():
    assert fade.clean_seconds(2) == 2.0
    assert fade.clean_seconds("1.5") == 1.5
    assert fade.clean_seconds(-3) == 0.0
    assert fade.clean_seconds(99999) == fade.MAX_TRANSITION == 1800.0
    assert fade.clean_seconds(None, 0.7) == 0.7
    assert fade.clean_seconds("x", 0.7) == 0.7
    assert fade.clean_seconds(float("nan"), 0.7) == 0.7
    assert fade.clean_seconds(float("inf"), 0.7) == 0.7


def test_smoothstep_is_s_shaped_and_clamped():
    assert fade.smoothstep(-1) == 0 and fade.smoothstep(2) == 1 and fade.smoothstep(float("nan")) == 0
    assert fade.smoothstep(0.5) == pytest.approx(0.5)
    assert fade.smoothstep(0.25) < 0.25 < 0.5 < 0.75 < fade.smoothstep(0.75)


def test_lerp_frame_endpoints_midpoint_and_padding():
    a, b = [RED, RED], [BLUE, BLUE]
    assert fade.lerp_frame(a, b, 0) == a and fade.lerp_frame(a, b, 1) == b
    assert fade.lerp_frame(a, b, 0.5) == [(127, 0, 127)] * 2
    assert fade.lerp_frame([RED], [BLUE, BLUE], 0) == [RED, BLACK]  # a shorter old frame is padded with black
    assert fade.lerp_frame(a, b, 0.5) is not a


def test_scale_frame():
    f = [(200, 100, 50)]
    assert fade.scale_frame(f, 1) is f
    assert fade.scale_frame(f, 0) == [BLACK]
    assert fade.scale_frame(f, 0.5) == [(100, 50, 25)]


def test_ramp_levels_over_time():
    r = fade.Ramp(0.0, 1.0, 10.0, 50.0)
    assert r.level(50) == 0 and r.level(55) == pytest.approx(0.5) and r.level(60) == 1 and r.level(1000) == 1
    assert not r.done(59.9) and r.done(60)
    down = fade.Ramp(1.0, 0.0, 4.0, 0.0)
    assert down.level(2) == pytest.approx(0.5) and down.level(4) == 0
    assert fade.Ramp(0, 1, 0, 0).level(0) == 1  # no length: already there


def test_xfade_blends_old_into_new():
    x = fade.Xfade([RED], 2.0, 10.0)
    assert x.apply([BLUE], 10.0) == [RED] and x.apply([BLUE], 12.0) == [BLUE]
    mid = x.apply([BLUE], 11.0)[0]
    assert 120 <= mid[0] <= 135 and 120 <= mid[2] <= 135


def test_tick_interval_is_coarser_for_long_ramps_but_at_least_twice_a_second():
    assert fade.tick_interval(0.7, 0.04) == 0.04
    assert fade.tick_interval(120, 0.04) == pytest.approx(120 / 255)
    assert fade.tick_interval(1800, 0.04) == 0.5


# --- a light ----------------------------------------------------------------------------------------
@aio
async def test_default_is_instant_so_nothing_changes_unless_asked(hass):
    ctl = FakeLight(hass, 4, on=False)
    await ctl.async_turn_on(colour=RED)
    assert ctl.sent[-1] == [RED] * 4 and ctl._ramp is None and ctl._xf is None


@aio
async def test_turn_on_fades_in_from_black_to_full(hass):
    ctl = FakeLight(hass, 4, on=False)
    await ctl.async_turn_on(colour=(200, 100, 50), transition=0.5)
    await asyncio.sleep(0.01)
    assert ctl.is_on
    assert lit(ctl.sent[0]) < 60  # starts near black
    await asyncio.sleep(0.25)
    mid = lit(ctl.sent[-1])
    assert 40 < mid < 200  # part way
    await asyncio.sleep(0.5)
    assert ctl.sent[-1] == [(200, 100, 50)] * 4  # full
    levels = [lit(f) for f in ctl.sent]
    assert levels == sorted(levels)  # never dips on the way up
    await asyncio.sleep(0.2)
    n = len(ctl.sent)
    await asyncio.sleep(0.2)
    assert len(ctl.sent) == n  # a still colour stops sending once faded in


@aio
async def test_default_transition_is_used_when_none_is_given_and_zero_is_instant(hass):
    ctl = FakeLight(hass, 4, on=False)
    ctl.default_transition = 0.4
    await ctl.async_turn_on(colour=RED)
    await asyncio.sleep(0.01)
    assert lit(ctl.sent[0]) < 80
    ctl2 = FakeLight(hass, 4, on=False)
    ctl2.default_transition = 0.4
    await ctl2.async_turn_on(colour=RED, transition=0)
    await asyncio.sleep(0.01)
    assert ctl2.sent[0] == [RED] * 4


@aio
async def test_turn_off_fades_out_then_switches_off(hass):
    ctl = FakeLight(hass, 4)
    await ctl.async_turn_on(colour=RED, transition=0)
    ctl.sent.clear()
    await ctl.async_turn_off(transition=0.4)
    assert ctl.is_on and ctl.powered == []  # still on while it fades
    await asyncio.sleep(0.2)
    assert 0 < lit(ctl.sent[-1]) < 255
    await asyncio.sleep(0.5)
    assert not ctl.is_on and ctl.powered[-1] is False
    assert ctl.frame == [BLACK] * 4
    levels = [lit(f) for f in ctl.sent]
    assert levels == sorted(levels, reverse=True)  # only ever darker


@aio
async def test_turn_on_during_a_fade_out_brings_the_light_back_up_from_where_it_got_to(hass):
    ctl = FakeLight(hass, 4)
    await ctl.async_turn_on(colour=RED, transition=0)
    await ctl.async_turn_off(transition=0.5)
    await asyncio.sleep(0.25)
    dim = lit(ctl.sent[-1])
    await ctl.async_turn_on(transition=0.3)
    assert ctl._off_handle is None
    await asyncio.sleep(0.7)
    assert ctl.is_on and ctl.powered == [] and ctl.sent[-1] == [RED] * 4
    assert dim < 255


@aio
async def test_turn_off_with_no_transition_is_immediate(hass):
    ctl = FakeLight(hass, 4)
    await ctl.async_turn_off(transition=0)
    assert not ctl.is_on and ctl.powered == [False]


@aio
async def test_look_change_cross_fades_old_frame_into_new(hass):
    clock = Clock()
    ctl = FakeLight(hass, 2)
    ctl._now = clock
    await ctl.async_turn_on(colour=RED, transition=0)
    assert ctl.sent[-1] == [RED] * 2
    await ctl.async_turn_on(colour=BLUE, transition=2.0)
    assert ctl._xf is not None
    ctl._stop_animation()
    ctl._send_frame([BLUE] * 2, fx=True)
    assert ctl.sent[-1] == [RED] * 2  # t = 0: still the old picture
    clock.t += 1.0
    ctl._send_frame([BLUE] * 2, fx=True)
    r, g, b = ctl.sent[-1][0]
    assert 120 <= r <= 135 and g == 0 and 120 <= b <= 135  # halfway
    clock.t += 1.5
    ctl._send_frame([BLUE] * 2, fx=True)
    assert ctl.sent[-1] == [BLUE] * 2 and ctl._xf is None  # done


@aio
async def test_cross_fade_runs_over_real_time_for_a_still_look(hass):
    ctl = FakeLight(hass, 2)
    await ctl.async_turn_on(colour=RED, transition=0)
    ctl.sent.clear()
    await ctl.async_turn_on(colour=BLUE, transition=0.4)
    await asyncio.sleep(0.2)
    mid = ctl.sent[-1][0]
    assert 0 < mid[0] < 255 and 0 < mid[2] < 255
    await asyncio.sleep(0.4)
    assert ctl.sent[-1] == [BLUE] * 2
    reds = [f[0][0] for f in ctl.sent]
    assert reds == sorted(reds, reverse=True)


@aio
async def test_cross_fade_keeps_the_effect_clock_running(hass):
    """Changing the pattern of a moving effect blends, but the animation clock carries on (no restart, no jump)."""
    ctl = FakeLight(hass, 20)
    await ctl.async_set_pattern("Classic Multicolor", "Chase", 50, transition=0)
    await asyncio.sleep(0.3)
    before = ctl._fx_t
    assert before > 0.2
    await ctl.async_set_pattern("Candy Cane", None, None, transition=0.3)
    await asyncio.sleep(0.2)
    assert ctl._fx_t > before  # went on from where it was
    assert ctl._task is not None and not ctl._task.done()
    await asyncio.sleep(0.4)
    assert ctl._xf is None


@aio
async def test_show_frames_are_never_faded(hass):
    ctl = FakeLight(hass, 3)
    ctl.default_transition = 5.0
    ctl.enable_show()
    ctl._ramp = fade.Ramp(0.0, 1.0, 5.0, ctl._now())  # even with a ramp lying around
    ctl.show_external(bytes([200, 100, 50] * 3))
    assert ctl.sent[-1] == [(200, 100, 50)] * 3
    assert ctl._ramp is None or ctl.show_mode  # entering the show cleared fades


@aio
async def test_entering_show_mode_drops_a_fade_in_progress(hass):
    ctl = FakeLight(hass, 3, on=False)
    await ctl.async_turn_on(colour=RED, transition=5.0)
    assert ctl._ramp is not None
    ctl.enable_show()
    assert ctl._ramp is None and ctl._xf is None and ctl._off_handle is None


@aio
async def test_zone_frames_are_not_dimmed_by_the_lights_own_fade(hass):
    ctl = FakeLight(hass, 4, on=False)
    ctl.default_transition = 3.0
    await ctl.async_claim_zone("z", "Z")  # takes the light with no fade of its own
    assert ctl.push_zone_frame("z", [(0, [RED] * 4)])
    assert ctl.sent[-1] == [RED] * 4


@aio
async def test_a_plain_turn_on_while_a_zone_holds_the_light_does_not_blend(hass):
    ctl = FakeLight(hass, 4)
    await ctl.async_claim_zone("z", "Z")
    await ctl.async_turn_on(brightness=100, transition=1.0)
    assert ctl.zone == "Z" and ctl._xf is None


@aio
async def test_brightness_scales_on_top_of_the_fade(hass):
    clock = Clock()
    ctl = FakeLight(hass, 1)
    ctl._now = clock
    ctl.brightness = 128
    ctl._ramp = fade.Ramp(0.0, 1.0, 10.0, clock.t)
    clock.t += 5
    ctl._send_frame([(200, 0, 0)], fx=True)
    assert ctl.sent[-1][0][0] == int(int(200 * 0.5) * 128 / 255)


@aio
async def test_long_fade_is_a_slow_brightness_ramp_not_a_busy_loop(hass):
    """A 120 s sunset fade sends a couple of frames a second, not 25."""
    ctl = FakeLight(hass, 4, on=False)
    await ctl.async_turn_on(colour=RED, transition=120)
    await asyncio.sleep(0.9)
    assert len(ctl.sent) <= 4
    assert ctl._ramp.dur == 120
    clock = Clock()
    ctl._now = clock
    ctl._ramp = fade.Ramp(0.0, 1.0, 120.0, clock.t)
    levels = []
    for sec in (0, 30, 60, 90, 119, 121):
        clock.t = 100.0 + sec
        ctl._send_frame([RED] * 4, fx=True)
        levels.append(ctl.sent[-1][0][0])
    assert levels == sorted(levels) and levels[0] == 0 and levels[-1] == 255
    assert 100 < levels[2] < 155  # halfway at 60 s


@aio
async def test_long_fade_out_ends_with_the_light_off(hass):
    ctl = FakeLight(hass, 4)
    await ctl.async_turn_on(colour=RED, transition=0)
    await ctl.async_turn_off(transition=120)
    assert ctl.is_on and ctl._off_handle is not None
    ctl._finish_off()  # what the timer does at the end
    assert not ctl.is_on and ctl.powered == [False]


@aio
async def test_speed_change_alone_does_not_cross_fade(hass):
    ctl = FakeLight(hass, 10)
    await ctl.async_set_pattern("Classic Multicolor", "Chase", 50, transition=0)
    await ctl.async_set_pattern(None, None, 80, transition=1.0)
    assert ctl._xf is None and ctl.speed == 80


@aio
async def test_set_pattern_in_one_call_turns_on_with_brightness_and_fade(hass):
    ctl = FakeLight(hass, 6, on=False)
    await ctl.async_set_pattern("Warm White", "Steady", 30, brightness=128, transition=0.4)
    assert ctl.is_on and ctl.brightness == 128 and ctl.pattern == "Warm White"
    await asyncio.sleep(0.7)
    assert ctl.sent[-1][0] == (128, 75, 20)


# --- zones fade ------------------------------------------------------------------------------------------
def _plan(n=4, pattern="Warm White", effect="Steady"):
    run = mapping.Run("r1", "e1", "Run", 0, n, area="a")
    zone = mapping.Zone("z", "Z", ("r1",), pattern=pattern, effect=effect)
    return mapped_patterns.ZonePlan(zone, {"r1": run})


@aio
async def test_zone_fades_in_and_gives_the_light_back_after_fading_out(hass):
    ctl = FakeLight(hass, 4)
    runner = zoneplayer.ZoneRunner(_plan(), 50, lambda e: ctl, hass.async_create_background_task, None, 0.4, 0.4)
    await runner.async_start()
    await asyncio.sleep(0.1)
    first = lit(ctl.sent[0])
    assert first < 150
    await asyncio.sleep(0.5)
    full = lit(ctl.sent[-1])
    assert full == 255 and lit(ctl.sent[0]) < full
    n = len(ctl.sent)
    runner.stop()  # fades out
    assert runner.leaving and ctl.zone == "Z"
    await asyncio.sleep(0.2)
    assert len(ctl.sent) > n and 0 < lit(ctl.sent[-1]) < full
    await asyncio.sleep(0.5)
    assert ctl.zone is None and not runner.running


@aio
async def test_zone_stop_without_fade_is_immediate_and_restart_keeps_the_lights(hass):
    ctl = FakeLight(hass, 4)
    runner = zoneplayer.ZoneRunner(_plan(), 50, lambda e: ctl, hass.async_create_background_task)
    await runner.async_start()
    runner.stop()
    assert ctl.zone is None
    ctl2 = FakeLight(hass, 4)
    r2 = zoneplayer.ZoneRunner(_plan(), 50, lambda e: ctl2, hass.async_create_background_task, None, 0.0, 1.0)
    await r2.async_start()
    r2.stop(keep={"e1"})  # restart: no fade, lights stay claimed for the new run
    assert ctl2.zone == "Z" and not r2.running
    r2.stop()


@aio
async def test_stop_with_fade_zero_overrides_the_zones_own_fade(hass):
    ctl = FakeLight(hass, 4)
    runner = zoneplayer.ZoneRunner(_plan(), 50, lambda e: ctl, hass.async_create_background_task, None, 0.0, 2.0)
    await runner.async_start()
    runner.stop(fade_out=0)
    assert ctl.zone is None
