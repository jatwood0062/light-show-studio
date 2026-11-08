"""Tests for zones on the controller and the zone runner, with small fakes; plain pytest, no Home Assistant."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"


def _stub(name: str, **attrs) -> None:
    mod = sys.modules.setdefault(name, types.ModuleType(name))
    for k, v in attrs.items():
        setattr(mod, k, v)


def _load_all():
    _stub("homeassistant")
    _stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    pkg = sys.modules.setdefault("light_show_studio_zn", types.ModuleType("light_show_studio_zn"))
    pkg.__path__ = [str(PKG)]
    for name in ("const", "effects", "protocol", "hosts", "patterns", "controller", "mapping", "mapped_patterns",
                 "zoneplayer"):
        full = f"light_show_studio_zn.{name}"
        if full not in sys.modules:
            spec = importlib.util.spec_from_file_location(full, PKG / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
    return {n: sys.modules[f"light_show_studio_zn.{n}"] for n in ("controller", "mapping", "mapped_patterns", "zoneplayer", "patterns")}


M = _load_all()
controller, mapping, mapped_patterns, zoneplayer, patterns = (M[n] for n in
                                                              ("controller", "mapping", "mapped_patterns", "zoneplayer", "patterns"))
BLACK = (0, 0, 0)


class FakeHass:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.tasks = []

    def async_create_background_task(self, coro, name=None):
        t = asyncio.ensure_future(coro)
        self.tasks.append(t)
        return t


class FakeLight(controller.C9Controller):
    """A controller that records what it would send instead of using the network."""

    def __init__(self, hass, bulbs=10, on=True):
        super().__init__(hass, "192.168.1.10", bulbs, True)
        self.is_on = on
        self.sent: list[list] = []
        self.powered: list[bool] = []

    def _make_link(self, host):
        return None

    def _hw_power(self, on):
        self.powered.append(on)

    def _hw_frame(self, frame):
        self.sent.append(frame)

    def _hw_close(self):
        pass


def aio(fn):
    def wrapper():
        async def go():
            hass = FakeHass()
            try:
                await fn(hass)
            finally:
                for t in hass.tasks:
                    t.cancel()
        asyncio.run(go())
    wrapper.__name__ = fn.__name__
    return wrapper


R, G, B = (255, 0, 0), (0, 255, 0), (0, 0, 255)


# --- controller ------------------------------------------------------------------------------
@aio
async def test_push_builds_a_full_frame_with_black_elsewhere(hass):
    ctl = FakeLight(hass, 10)
    await ctl.async_claim_zone("z", "Kitchen")
    assert ctl.zone == "Kitchen"
    assert ctl.push_zone_frame("z", [(2, [R, R]), (7, [G])])
    await asyncio.sleep(0)  # the send is coalesced into the next loop turn
    assert ctl.sent[-1] == [BLACK, BLACK, R, R, BLACK, BLACK, BLACK, G, BLACK, BLACK]
    assert ctl.frame == ctl.sent[-1]  # the card sees it


@aio
async def test_push_keeps_brightness_and_clips_to_the_light(hass):
    ctl = FakeLight(hass, 4)
    ctl.brightness = 128
    await ctl.async_claim_zone("z", "Z")
    ctl.push_zone_frame("z", [(2, [(200, 100, 50)] * 5)])  # runs past the end: clipped
    await asyncio.sleep(0)  # the send is coalesced into the next loop turn
    assert ctl.sent[-1] == [BLACK, BLACK, (100, 50, 25), (100, 50, 25)]


@aio
async def test_unclaimed_zone_is_refused(hass):
    ctl = FakeLight(hass)
    assert ctl.push_zone_frame("nope", [(0, [R])]) is False and ctl.sent == []


@aio
async def test_colour_or_effect_takes_the_light_back(hass):
    for kw in ({"colour": R}, {"effect": "Rainbow"}, {"effect": patterns.PATTERN}):
        ctl = FakeLight(hass)
        await ctl.async_claim_zone("z", "Z")
        ctl.push_zone_frame("z", [(0, [G])])
        await ctl.async_turn_on(**kw)
        assert ctl.zone is None
        assert ctl.push_zone_frame("z", [(0, [G])]) is False


@aio
async def test_brightness_and_plain_on_keep_the_zone(hass):
    ctl = FakeLight(hass, 3)
    await ctl.async_claim_zone("z", "Z")
    ctl.push_zone_frame("z", [(0, [(200, 200, 200)] * 3)])
    await ctl.async_turn_on(brightness=100)
    assert ctl.zone == "Z" and ctl.brightness == 100
    assert ctl.sent[-1][0] == (200 * 100 // 255,) * 3  # resent at the new level
    await ctl.async_turn_on()
    assert ctl.zone == "Z"
    assert ctl.push_zone_frame("z", [(0, [R])]) is True


@aio
async def test_off_pixels_pattern_bulb_and_show_release(hass):
    async def claimed():
        ctl = FakeLight(hass)
        await ctl.async_claim_zone("z", "Z")
        return ctl
    ctl = await claimed()
    await ctl.async_turn_off()
    assert ctl.zone is None and ctl.push_zone_frame("z", [(0, [R])]) is False
    ctl = await claimed()
    await ctl.async_set_pixels([R])
    assert ctl.zone is None
    ctl = await claimed()
    await ctl.async_set_bulb(1, True, G)
    assert ctl.zone is None
    ctl = await claimed()
    ctl.enable_show()
    assert ctl.zone is None and ctl.show_mode is True
    ctl = await claimed()
    await ctl.async_set_pattern(pattern="Rainbow")
    assert ctl.zone is None


@aio
async def test_speed_only_keeps_the_zone(hass):
    ctl = FakeLight(hass)
    await ctl.async_claim_zone("z", "Z")
    await ctl.async_set_pattern(speed=80)
    assert ctl.zone == "Z" and ctl.speed == 80


@aio
async def test_zone_ignores_sequencer_frames(hass):
    ctl = FakeLight(hass, 3)
    await ctl.async_claim_zone("z", "Z")
    ctl.push_zone_frame("z", [(0, [R, R, R])])
    n = len(ctl.sent)
    ctl.show_external(bytes([0, 255, 0] * 3))
    assert len(ctl.sent) == n


@aio
async def test_two_zones_share_a_light_and_release_separately(hass):
    ctl = FakeLight(hass, 6)
    await ctl.async_claim_zone("a", "A")
    await ctl.async_claim_zone("b", "B")
    ctl.push_zone_frame("a", [(0, [R, R])])
    ctl.push_zone_frame("b", [(4, [B, B])])
    await asyncio.sleep(0)  # the send is coalesced into the next loop turn
    assert ctl.sent[-1] == [R, R, BLACK, BLACK, B, B]
    assert ctl.zone == "A, B"
    ctl.release_zone("a")
    assert ctl.zone == "B" and ctl.sent[-1] == [BLACK] * 4 + [B, B]
    assert ctl.push_zone_frame("a", [(0, [R])]) is False


@aio
async def test_stop_restores_and_turns_off_what_the_zone_turned_on(hass):
    ctl = FakeLight(hass, 3, on=False)
    await ctl.async_claim_zone("z", "Z")  # powers the light on
    assert ctl.is_on and ctl.powered == [True]
    ctl.release_zone("z", restore=True)
    await asyncio.sleep(0.05)
    assert ctl.is_on is False and ctl.powered == [True, False] and ctl.zone is None
    ctl = FakeLight(hass, 3, on=True)
    ctl.colour, ctl.show_mode = G, False
    await ctl.async_claim_zone("z", "Z")
    ctl.push_zone_frame("z", [(0, [R, R, R])])
    ctl.release_zone("z", restore=True)
    assert ctl.is_on and ctl.sent[-1] == [G, G, G]  # back to its own colour


@aio
async def test_stop_returns_to_show_mode_if_it_was_in_it(hass):
    ctl = FakeLight(hass, 3)
    ctl.show_mode = True
    await ctl.async_claim_zone("z", "Z")
    assert ctl.show_mode is False
    ctl.release_zone("z", restore=True)
    assert ctl.show_mode is True


# --- runner --------------------------------------------------------------------------------------
def make_plan(mode="string", effect="Chase", runs=None, runs_in_zone=None, pattern="Rainbow"):
    runs = runs or [{"id": "a", "entry_id": "e1", "name": "A", "first": 0, "count": 10},
                    {"id": "b", "entry_id": "e2", "name": "B", "first": 2, "count": 8, "reverse": True}]
    model = mapping.from_dict({"runs": runs, "zones": [{"id": "z", "name": "Z", "runs": runs_in_zone or [r["id"] for r in runs],
                                                         "mode": mode, "effect": effect, "pattern": pattern}]})
    return mapped_patterns.ZonePlan(model.zones[0], model.run_map())


def make_runner(hass, lights, plan, speed=50, ended=None):
    runner = zoneplayer.ZoneRunner(plan, speed, lights.get, hass.async_create_background_task, ended)
    runner.interval = 0.005
    return runner


@aio
async def test_runner_slices_one_render_across_lights(hass):
    lights = {"e1": FakeLight(hass, 10), "e2": FakeLight(hass, 12)}
    plan = make_plan(effect="Steady")
    runner = make_runner(hass, lights, plan)
    await runner.async_start()
    await asyncio.sleep(0.05)
    virtual = plan.render(0)
    assert lights["e1"].sent[-1] == virtual[:10]
    e2 = lights["e2"].sent[-1]
    assert e2[:2] == [BLACK, BLACK] and e2[2:10] == list(reversed(virtual[10:])) and e2[10:] == [BLACK, BLACK]
    runner.stop()


@aio
async def test_chase_is_continuous_across_two_lights(hass):
    lights = {"e1": FakeLight(hass, 10), "e2": FakeLight(hass, 8)}
    plan = make_plan(runs=[{"id": "a", "entry_id": "e1", "name": "A", "first": 0, "count": 10},
                           {"id": "b", "entry_id": "e2", "name": "B", "first": 0, "count": 8}])
    rendered, render = [], plan.render
    plan.render = lambda t: rendered.append(render(t)) or rendered[-1]
    runner = make_runner(hass, lights, plan)
    await runner.async_start()
    await asyncio.sleep(0.1)
    runner.stop()
    assert len(lights["e1"].sent) > 3 and len({tuple(f) for f in rendered}) > 3
    # the frames of the two lights always come from the same render of the 18-pixel string
    assert len(lights["e1"].sent) == len(lights["e2"].sent)
    for a, b in zip(lights["e1"].sent, lights["e2"].sent):
        assert a + b in rendered  # one render of the 18-pixel string, cut at the join


@aio
async def test_light_turned_off_is_skipped_and_the_rest_carry_on(hass):
    lights = {"e1": FakeLight(hass, 10), "e2": FakeLight(hass, 12)}
    ended = []
    runner = make_runner(hass, lights, make_plan(), ended=ended.append)
    await runner.async_start()
    await asyncio.sleep(0.03)
    await lights["e2"].async_turn_off()
    n1, n2 = len(lights["e1"].sent), len(lights["e2"].sent)
    await asyncio.sleep(0.05)
    assert len(lights["e1"].sent) > n1 and len(lights["e2"].sent) == n2
    assert runner.running and not ended
    runner.stop()


@aio
async def test_runner_ends_when_every_light_is_taken(hass):
    lights = {"e1": FakeLight(hass, 10), "e2": FakeLight(hass, 12)}
    ended = []
    runner = make_runner(hass, lights, make_plan(), ended=ended.append)
    await runner.async_start()
    await asyncio.sleep(0.02)
    await lights["e1"].async_turn_on(colour=R)
    await lights["e2"].async_turn_off()
    await asyncio.sleep(0.05)
    assert ended == [runner] and not runner.running
    assert lights["e1"].zone is None  # and nothing was forced back on the lights


@aio
async def test_missing_light_is_skipped_then_rejoins(hass):
    first = FakeLight(hass, 10)
    lights = {"e1": first}  # e2 is not loaded
    runner = make_runner(hass, lights, make_plan(effect="Steady"))
    await runner.async_start()
    await asyncio.sleep(0.03)
    assert runner.running and first.sent
    lights["e2"] = e2 = FakeLight(hass, 12)  # it loads later
    await asyncio.sleep(1.1)  # a still zone ticks once a second
    assert e2.zone == "Z" and e2.sent
    runner.stop()


@aio
async def test_light_replaced_by_a_reload_is_claimed_again(hass):
    lights = {"e1": FakeLight(hass, 10)}
    plan = make_plan(runs=[{"id": "a", "entry_id": "e1", "name": "A", "first": 0, "count": 10}])
    runner = make_runner(hass, lights, plan)
    await runner.async_start()
    await asyncio.sleep(0.02)
    lights["e1"] = new = FakeLight(hass, 10)
    await asyncio.sleep(0.05)
    assert new.zone == "Z" and new.sent
    runner.stop()


@aio
async def test_stop_releases_lights_but_keep_leaves_them_claimed(hass):
    lights = {"e1": FakeLight(hass, 10), "e2": FakeLight(hass, 12)}
    runner = make_runner(hass, lights, make_plan(effect="Steady"))
    await runner.async_start()
    runner.stop(keep={"e1"})
    assert lights["e1"].zone == "Z" and lights["e2"].zone is None
    assert not runner.running


@aio
async def test_set_speed_changes_the_clock_rate_live(hass):
    def last_frame_time(speed):
        return speed
    light = {"e1": FakeLight(hass, 10)}
    plan = make_plan(runs=[{"id": "a", "entry_id": "e1", "name": "A", "first": 0, "count": 10}], effect="Chase")
    runner = make_runner(hass, light, plan, speed=100)
    await runner.async_start()
    await asyncio.sleep(0.1)
    runner.set_speed(1)
    assert runner.speed == 1
    runner.set_speed(500)
    assert runner.speed == 100
    runner.stop()


@aio
async def test_steady_zone_is_not_rerendered_every_frame(hass):
    light = {"e1": FakeLight(hass, 10)}
    plan = make_plan(runs=[{"id": "a", "entry_id": "e1", "name": "A", "first": 0, "count": 10}], effect="Steady")
    runner = make_runner(hass, light, plan)
    await runner.async_start()
    await asyncio.sleep(0.2)
    assert 1 <= len(light["e1"].sent) <= 2
    runner.stop()


@aio
async def test_large_zone_runs_slower(hass):
    runs = [{"id": "a", "entry_id": "e1", "name": "A", "first": 0, "count": 1100}]
    runner = zoneplayer.ZoneRunner(make_plan(runs=runs), 50, {}.get, hass.async_create_background_task)
    assert runner.interval == pytest.approx(1 / 20)
    small = zoneplayer.ZoneRunner(make_plan(), 50, {}.get, hass.async_create_background_task)
    assert small.interval == pytest.approx(1 / 25)


# --- resuming after a restart ------------------------------------------------------------------
@aio
async def test_moving_effect_resumes_after_restart(hass):
    ctl = FakeLight(hass, 10)
    ctl.effect, ctl.show_mode = "Pattern", False  # as restored from the last state
    ctl.pattern_effect = "Twinkle"
    ctl.resume_after_restart(delay=0.01)
    await asyncio.sleep(0.15)
    assert ctl._task is not None and ctl.sent  # animating again
    ctl._stop_animation()


@aio
async def test_still_colour_show_and_off_are_not_touched_after_restart(hass):
    still = FakeLight(hass, 10)
    still.effect, still.show_mode = None, False
    steady = FakeLight(hass, 10)
    steady.effect, steady.pattern_effect, steady.show_mode = "Pattern", "Steady", False
    show = FakeLight(hass, 10)
    show.show_mode = True
    off = FakeLight(hass, 10, on=False)
    off.effect, off.show_mode = "Twinkle", False
    for c in (still, steady, show, off):
        c.resume_after_restart(delay=0.01)
    await asyncio.sleep(0.1)
    for c in (still, steady, show, off):
        assert c._task is None and not c.sent and not c.powered


@aio
async def test_restarted_zone_carries_on_its_clock(hass):
    """A map edit / pattern edit / new look restarts the runner with the same zone: a chase must not start over."""
    lights = {"e1": FakeLight(hass, 10), "e2": FakeLight(hass, 12)}
    plan = make_plan(effect="Chase")
    old = make_runner(hass, lights, plan)
    await old.async_start()
    await asyncio.sleep(0.15)
    t_old = old._t
    assert t_old > 0.05
    old.stop(keep={"e1", "e2"})
    new = make_runner(hass, lights, plan)
    assert new._t >= t_old
    # a plain stop (nothing taking over) leaves nothing behind: a later start begins at 0
    new.stop(restore=False)
    fresh = make_runner(hass, lights, plan)
    assert fresh._t == 0.0


@aio
async def test_speed_change_keeps_the_clock_continuous(hass):
    lights = {"e1": FakeLight(hass, 10), "e2": FakeLight(hass, 12)}
    runner = make_runner(hass, lights, make_plan(effect="Chase"))
    await runner.async_start()
    await asyncio.sleep(0.1)
    before = runner._t
    runner.set_speed(100)
    await asyncio.sleep(0.02)
    assert runner._t >= before  # the clock never jumps back or restarts when the speed changes
    assert runner._t - before < 0.5
    runner.stop()


# --- smoother timing: send divisor, unchanged frames, coalescing ------------------------------------------
def test_send_divisor_follows_the_lights_own_rate():
    class Slow:
        max_fps = 12.0

    class Whole:
        segmented = False

    assert zoneplayer.send_divisor(Slow(), 25) == 2  # 25 fps to a 12 fps strip: every second frame
    assert zoneplayer.send_divisor(Slow(), 20) == 2
    assert zoneplayer.send_divisor(Whole(), 25) == 4
    assert zoneplayer.send_divisor(object(), 25) == 1


class CountingLight:
    def __init__(self, max_fps=None):
        if max_fps:
            self.max_fps = max_fps
        self.pushed = 0

    async def async_claim_zone(self, zone_id, name):
        pass

    def push_zone_frame(self, zone_id, slices):
        self.pushed += 1
        return True

    def release_zone(self, zone_id=None, restore=False):
        pass


@aio
async def test_runner_sends_a_slow_light_every_kth_frame_evenly(hass):
    fast, slow = CountingLight(), CountingLight(12.5)
    runner = make_runner(hass, {"e1": fast, "e2": slow}, make_plan(effect="Chase"))
    runner.interval = 0.04  # 25 fps
    await runner.async_start()
    runner._task.cancel()
    for k in range(10):
        runner._tick(k * 0.04)
    assert fast.pushed == 10 and slow.pushed == 5
    runner._tick(0.0, force=True)  # a light joining late shows the current frame at once
    assert fast.pushed == 11 and slow.pushed == 6


@aio
async def test_unchanged_frames_are_not_sent_again_within_a_second(hass):
    ctl = FakeLight(hass, 3)
    ctl._send_frame([R, G, B])
    ctl._send_frame([R, G, B])
    assert len(ctl.sent) == 1
    ctl._send_frame([R, G, R])
    assert len(ctl.sent) == 2
    ctl._wire = (ctl._wire[0], ctl._wire[1] - 2.0)  # that send was long ago: sent again
    ctl._send_frame([R, G, R])
    assert len(ctl.sent) == 3
    ctl._set_frame([R, G, R])  # something else drew: the next frame always goes out
    ctl._send_frame([R, G, R])
    assert len(ctl.sent) == 4


@aio
async def test_full_brightness_frames_are_sent_as_they_are(hass):
    ctl = FakeLight(hass, 2)
    frame = [(10, 20, 30), (40, 50, 60)]
    ctl._send_frame(frame)
    assert ctl.sent[-1] is frame
    ctl.brightness = 128
    ctl._send_frame(frame)
    assert ctl.sent[-1] == [(5, 10, 15), (20, 25, 30)]


@aio
async def test_show_external_skips_identical_bytes(hass):
    ctl = FakeLight(hass, 2)
    ctl.enable_show()
    ctl.show_external(bytes([1, 2, 3, 4, 5, 6]))
    ctl._ext_sent -= 0.1
    ctl.show_external(bytes([1, 2, 3, 4, 5, 6]))
    assert ctl.sent == [[(1, 2, 3), (4, 5, 6)]]
    ctl._ext_sent -= 0.1
    ctl.show_external(bytes([1, 2, 3, 4, 5, 7]))
    assert ctl.sent[-1] == [(1, 2, 3), (4, 5, 7)]


@aio
async def test_zones_on_one_light_make_one_send_per_loop_turn(hass):
    ctl = FakeLight(hass, 6)
    await ctl.async_claim_zone("a", "A")
    await ctl.async_claim_zone("b", "B")
    before = len(ctl.sent)
    ctl.push_zone_frame("a", [(0, [R, R])])
    ctl.push_zone_frame("b", [(4, [B, B])])
    assert len(ctl.sent) == before
    await asyncio.sleep(0)
    assert len(ctl.sent) == before + 1 and ctl.sent[-1] == [R, R, BLACK, BLACK, B, B]


def test_plan_base_is_computed_once_and_refreshes_with_user_patterns():
    plan = make_plan(mode="string", effect="Twinkle", pattern="Candy Cane")
    plan.render(1.0)
    first = plan._flat
    plan.render(2.0)
    assert plan._flat is first
    patterns.touch_user_patterns()
    plan.render(3.0)
    assert plan._flat is not first and plan._flat == first
    assert plan.render(1.5) == patterns.render("Candy Cane", "Twinkle", 1.5, plan.n)
    chase = make_plan(mode="string", effect="Chase", pattern="Candy Cane")
    assert chase.render(1.5) == patterns.render("Candy Cane", "Chase", 1.5, chase.n) and chase._flat is None


@aio
async def test_a_lone_zone_is_sent_at_once(hass):
    ctl = FakeLight(hass, 3)
    await ctl.async_claim_zone("a", "A")
    before = len(ctl.sent)
    ctl.push_zone_frame("a", [(0, [R])])
    assert len(ctl.sent) == before + 1
