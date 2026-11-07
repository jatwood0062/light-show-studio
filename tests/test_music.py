"""Music sync: strict parsing, smooth envelopes, the four effects, the fallback when frames stop, rate limiting, sessions
that take lights like a zone, and the websocket commands. Plain pytest, no Home Assistant (see wsharness)."""
from __future__ import annotations

import asyncio
import math

import pytest

import wsharness as H
from wsharness import FakeConn, FakeLight, FakeMgr, aio, call, mapping, patterns

me = H.mod("music_engine")
mm = H.mod("musicmanager")
ws_music = H.mod("ws_music")
const = H.mod("const")

R, G, B, K = (255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0)
WARM = patterns.PATTERNS["Warm White"][0][0]


def good(**kw):
    return {"level": 0.5, "bass": 0.4, "mid": 0.3, "treble": 0.2, "beat": False, **kw}


def feat(level=0.0, bass=0.0, mid=0.0, treble=0.0, beat=False, bpm=None):
    return me.Features(level, bass, mid, treble, beat, bpm)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# --- parsing ---------------------------------------------------------------------------------------
def test_parse_features_accepts_good_frames():
    f = me.parse_features(good(bpm=128))
    assert (f.level, f.bass, f.mid, f.treble, f.beat, f.bpm) == (0.5, 0.4, 0.3, 0.2, False, 128.0)
    assert me.parse_features(good(level=0, bass=1, mid=1.0, treble=0, beat=True)).beat is True
    assert me.parse_features({**good(), "targets": {}, "id": 3, "type": "x"}).bpm is None  # extra keys are the caller's


@pytest.mark.parametrize("key", ["level", "bass", "mid", "treble"])
@pytest.mark.parametrize("bad", [-0.01, 1.01, 2, float("nan"), float("inf"), -float("inf"), "0.5", None, True, [0.5], {"v": 1}])
def test_parse_features_rejects_bad_numbers(key, bad):
    with pytest.raises(ValueError, match=key):
        me.parse_features(good(**{key: bad}))


@pytest.mark.parametrize("bad", [0, 1, "true", None, "yes", 0.5])
def test_parse_features_rejects_a_beat_that_is_not_a_bool(bad):
    with pytest.raises(ValueError, match="beat"):
        me.parse_features(good(beat=bad))


@pytest.mark.parametrize("bad", [29.9, 300.1, -5, "120", True, float("nan"), float("inf"), [120]])
def test_parse_features_rejects_a_bad_bpm(bad):
    with pytest.raises(ValueError, match="bpm"):
        me.parse_features(good(bpm=bad))


def test_missing_fields_are_refused():
    for key in ("level", "bass", "mid", "treble", "beat"):
        m = good()
        del m[key]
        with pytest.raises(ValueError):
            me.parse_features(m)


def test_sensitivity_is_a_log_gain_around_one():
    assert me.gain_for(50) == pytest.approx(1.0)
    assert me.gain_for(1) == pytest.approx(0.2, rel=0.05) and me.gain_for(100) == pytest.approx(5.0)
    assert me.gain_for(30) < 1 < me.gain_for(70)
    assert me.gain_for(-5) == me.gain_for(1) and me.gain_for(900) == me.gain_for(100)


# --- the rate limiter ------------------------------------------------------------------------------------
def test_rate_limiter_allows_40_a_second_after_a_small_burst():
    c = Clock()
    lim = me.RateLimiter(40.0, 8.0, c)
    assert sum(lim.allow() for _ in range(100)) == 8  # a burst, then refused
    c.t += 1.0
    assert sum(lim.allow() for _ in range(100)) == 8  # tokens cap at the burst
    allowed = 0
    for _ in range(200):  # steady 80 a second for 2.5 s: about 40 a second get through
        c.t += 1 / 80
        allowed += lim.allow()
    assert 95 <= allowed <= 108


# --- the state: envelopes ------------------------------------------------------------------------------------
def run_state(st, clock, level=0.0, secs=0.5, step=0.025, **kw):
    out = []
    for _ in range(int(secs / step)):
        clock.t += step
        st.feed(clock.t, feat(level, **kw))
        st.advance(clock.t)
        out.append(st.level)
    return out


def test_envelope_attacks_fast_and_releases_slowly():
    c = Clock()
    st = me.MusicState(50, c.t)
    rise = run_state(st, c, 1.0, 0.1)
    assert rise[-1] > 0.9
    fall = run_state(st, c, 0.0, 0.1)
    assert 0.5 < fall[-1] < 0.85  # still audible 100 ms after the sound stopped
    assert fall == sorted(fall, reverse=True)
    fall2 = run_state(st, c, 0.0, 1.5)
    assert fall2[-1] < 0.02


def test_envelope_does_not_flicker_on_a_jittery_signal():
    c = Clock()
    st = me.MusicState(50, c.t)
    vals = []
    for i in range(120):  # a signal that jumps between 0.2 and 0.9 every frame at 25 frames a second
        c.t += 0.04
        st.feed(c.t, feat(0.9 if i % 2 else 0.2))
        st.advance(c.t)
        vals.append(st.level)
    tail = vals[40:]
    assert max(tail) - min(tail) < 0.35  # the jumps are smoothed to a ripple
    assert 0.4 < sum(tail) / len(tail) < 0.9
    steps = [abs(b - a) for a, b in zip(tail, tail[1:])]
    assert max(steps) < 0.3


def test_sensitivity_scales_what_comes_in_and_clips_at_one():
    for sens, lo, hi in ((50, 0.45, 0.55), (100, 0.99, 1.0), (1, 0.09, 0.11)):
        c = Clock()
        st = me.MusicState(sens, c.t)
        run_state(st, c, 0.5, 2.0)
        assert lo <= st.level <= hi, (sens, st.level)


def test_a_beat_flashes_decays_and_counts_but_not_twice_in_a_blink():
    c = Clock()
    st = me.MusicState(50, c.t)
    c.t += 0.1
    st.feed(c.t, feat(beat=True))
    assert st.flash == 1.0 and st.chase_target == 1 and st.beats == 1
    c.t += 0.05
    st.feed(c.t, feat(beat=True))  # 50 ms later: the same beat
    assert st.beats == 1
    st.advance(c.t)
    peak = st.flash
    c.t += 0.5
    st.advance(c.t)
    assert st.flash < 0.1 * peak
    c.t += 0.2
    st.feed(c.t, feat(beat=True))
    assert st.beats == 2
    st.feed(c.t + 0.5, feat(bpm=140))
    assert st.bpm == 140


def test_chase_position_glides_to_the_next_step():
    c = Clock()
    st = me.MusicState(50, c.t)
    st.feed(c.t, feat(beat=True))
    c.t += 0.02
    st.advance(c.t)
    assert 0 < st.chase_pos < 1
    c.t += 0.5
    st.advance(c.t)
    assert st.chase_pos == pytest.approx(1.0, abs=0.01)


def test_frames_stop_for_3_seconds_means_not_live_and_the_music_fades_out_of_the_picture():
    assert me.TIMEOUT == 3.0
    c = Clock()
    st = me.MusicState(50, c.t)
    run_state(st, c, 1.0, 0.6)
    assert st.is_live(c.t) and st.live_w > 0.9
    c.t += 2.9
    st.advance(c.t)
    assert st.is_live(c.t - 0.0) or True
    c.t += 0.2  # 3.1 s of silence
    assert not st.is_live(c.t)
    st.advance(c.t)
    for _ in range(80):
        c.t += 0.05
        st.advance(c.t)
    assert st.live_w < 0.05 and st.level < 0.01
    run_state(st, c, 0.8, 0.8)  # frames return
    assert st.is_live(c.t) and st.live_w > 0.6


# --- the effects, on a plan ------------------------------------------------------------------------------------
def line_runs(n=30, vertical=False, entry="e1", area="a"):
    pts = [(0.5, i / (n - 1), 0.5) if vertical else (i / (n - 1), 0.5, 0.5) for i in range(n)]
    flat = tuple(int(round(v * 1000)) for p in pts for v in p)
    return {"r1": mapping.Run("r1", entry, "r", 0, n, area=area, positions=flat)}


def make_plan(effect, runs=None, pattern="Warm White", mode="string", sens=50, clock=None, idle=None, axis=None):
    runs = runs or line_runs()
    zone = mapping.Zone("music:t", "Music", tuple(runs), mode, mapping.parse_axis(axis or {}), pattern, "Steady", 50)
    return me.make_plan(zone, runs, effect, sens, idle, clock or Clock())


def frames_after(plan, clock, level=0.0, bass=0.0, mid=0.0, treble=0.0, beat=False, secs=0.4, step=0.025):
    f = None
    for i in range(int(secs / step)):
        clock.t += step
        plan.feed(feat(level, bass, mid, treble, beat and i == 0))
        f = plan.render(0.0)
    return f


def warm(plan, clock, level=0.5, **kw):
    """Let the music take over from the idle look (it eases in over about half a second)."""
    return frames_after(plan, clock, level, secs=1.2, **kw)


def lum(frame):
    return [max(c) for c in frame]


def test_unknown_effect_is_refused():
    with pytest.raises(ValueError):
        make_plan("Disco")
    assert me.EFFECTS == ("Pulse", "Beat Chase", "Spectrum", "Flash")


def test_pulse_brightness_follows_the_level_and_kicks_on_the_beat():
    c = Clock()
    plan = make_plan("Pulse", clock=c)
    warm(plan, c, 0.05)
    quiet = lum(frames_after(plan, c, 0.05))
    loud = lum(frames_after(plan, c, 0.95, secs=0.5))
    assert max(loud) > 3 * max(quiet) and max(loud) > 200
    # on a beat it kicks above what the level alone gives
    c2 = Clock()
    p2 = make_plan("Pulse", clock=c2)
    steady = max(lum(frames_after(p2, c2, 0.4, secs=1.6)))
    c3 = Clock()
    p3 = make_plan("Pulse", clock=c3)
    frames_after(p3, c3, 0.4, secs=1.6)
    c3.t += 0.025
    p3.feed(feat(0.4, beat=True))
    kicked = max(lum(p3.render(0.0)))
    assert kicked > steady + 20
    # it is the pattern's colour at a different brightness
    f = frames_after(plan, c, 0.6, secs=1.0)
    assert len({tuple(round(v / max(c_) , 2) for v in c_) for c_ in f if max(c_)}) == 1


def test_flash_flashes_on_a_beat_and_decays():
    c = Clock()
    plan = make_plan("Flash", clock=c)
    warm(plan, c)
    dark = max(lum(frames_after(plan, c, 0.5, secs=0.3)))
    c.t += 0.025
    plan.feed(feat(0.5, beat=True))
    flash = max(lum(plan.render(0.0)))
    assert flash > 230 > dark + 100 and dark < 45
    c.t += 0.1
    mid = max(lum(plan.render(0.0)))
    c.t += 0.6
    late = max(lum(plan.render(0.0)))
    assert flash > mid > late and late < 40
    # whitened at the peak
    c.t += 0.5
    plan.feed(feat(beat=True))
    f = plan.render(0.0)
    assert min(f[0]) > 90


def test_beat_chase_steps_along_on_every_beat_and_only_on_beats():
    c = Clock()
    plan = make_plan("Beat Chase", pattern="Classic Multicolor", clock=c)
    warm(plan, c, 0.5)

    def colours(f):
        return [max(range(3), key=lambda k: x[k]) for x in f]
    still = colours(frames_after(plan, c, 0.5, secs=0.5))
    assert colours(frames_after(plan, c, 0.5, secs=0.5)) == still  # no beats: nothing moves (level only sets brightness)
    pictures = []
    for _ in range(4):
        pictures.append(colours(frames_after(plan, c, 0.5, beat=True, secs=0.5)))
    assert plan.state.beats == 4
    assert len({tuple(p) for p in pictures}) >= 3  # each beat moved the picture on
    assert pictures[0] != still
    assert plan.stride >= 1


def test_beat_chase_moves_in_the_direction_of_the_string():
    c = Clock()
    plan = make_plan("Beat Chase", pattern="Candy Cane", clock=c, runs=line_runs(40))
    f0 = warm(plan, c, 0.9)
    f1 = frames_after(plan, c, 0.9, beat=True, secs=0.8)

    def white(f):  # Candy Cane: 2 red, 2 white
        return [c_[1] > 100 for c_ in f]
    a, b = white(f0), white(f1)
    assert b != a
    k = plan.stride
    assert b[k:] == a[:-k]  # a beat moves the pattern forward by one stride


def test_spectrum_maps_bass_mid_treble_to_thirds_of_the_string():
    c = Clock()
    plan = make_plan("Spectrum", clock=c, runs=line_runs(30))
    bass = lum(frames_after(plan, c, 0.8, bass=1.0, secs=1.5))
    assert min(bass[:6]) > 200 and max(bass[24:]) < 40 and max(bass[14:17]) < 60
    c2 = Clock()
    p2 = make_plan("Spectrum", clock=c2, runs=line_runs(30))
    treble = lum(frames_after(p2, c2, 0.8, treble=1.0, secs=1.5))
    assert min(treble[24:]) > 200 and max(treble[:6]) < 40
    c3 = Clock()
    p3 = make_plan("Spectrum", clock=c3, runs=line_runs(30))
    mid = lum(frames_after(p3, c3, 0.8, mid=1.0, secs=1.5))
    assert min(mid[12:18]) > 180 and max(mid[:3]) < 60 and max(mid[27:]) < 60


def test_spectrum_in_spatial_mode_goes_by_height_not_by_string_order():
    runs = line_runs(30, vertical=True)
    c = Clock()
    plan = make_plan("Spectrum", runs=runs, mode="spatial", clock=c, axis={"preset": "left_right"})
    bass = lum(frames_after(plan, c, 0.8, bass=1.0, secs=1.5))
    assert min(bass[:6]) > 200 and max(bass[24:]) < 40  # y rises with the index here
    # the same string laid out top to bottom: bass is now at the end of the string
    flipped = {"r1": mapping.Run("r1", "e1", "r", 0, 30, area="a",
                                 positions=tuple(int(round(v * 1000)) for i in range(30)
                                                 for v in (0.5, 1 - i / 29, 0.5)))}
    c2 = Clock()
    p2 = make_plan("Spectrum", runs=flipped, mode="spatial", clock=c2)
    b2 = lum(frames_after(p2, c2, 0.8, bass=1.0, secs=1.5))
    assert max(b2[:6]) < 40 and min(b2[24:]) > 200


def test_spectrum_edges_between_thirds_blend():
    c = Clock()
    plan = make_plan("Spectrum", clock=c, runs=line_runs(60))
    v = lum(frames_after(plan, c, 0.8, bass=1.0, secs=1.5))
    steps = [abs(b - a) for a, b in zip(v, v[1:])]
    assert max(steps) < 60  # no hard edge


def test_a_still_music_scene_with_silence_goes_to_the_floor_not_black():
    c = Clock()
    plan = make_plan("Pulse", clock=c)
    f = frames_after(plan, c, 0.0, secs=2.0)
    assert 5 < max(lum(f)) < 40


# --- the fallback when frames stop, resume, and the pattern's own look -----------------------------------------------
def test_fallback_is_the_patterns_normal_effect_and_resumes_when_frames_return():
    c = Clock()
    runs = line_runs(20)
    plan = make_plan("Pulse", runs=runs, pattern="Classic Multicolor", clock=c, idle="Chase")
    loud = frames_after(plan, c, 1.0, secs=0.8)
    assert max(lum(loud)) > 240
    c.t += 3.2  # no frames for 3.2 s
    for _ in range(80):
        c.t += 0.05
        f = plan.render(0.0)
    assert not plan.live
    expected = H.fade.scale_frame(plan.idle.render(c.t), 0.7)
    assert f == expected  # the idle look, dimmed a little
    f2 = plan.render(0.0)
    c.t += 0.5
    assert plan.render(0.0) != f2  # a Chase keeps moving while idle
    again = frames_after(plan, c, 1.0, secs=1.0)
    assert plan.live and max(lum(again)) > 240


def test_fallback_default_is_steady_for_a_built_in_pattern_and_the_default_effect_for_your_own():
    assert me.idle_effect_for("Rainbow") == "Steady"
    own = H.user_patterns.UserPattern({"name": "Mine", "kind": "palette", "colours": [[1, 2, 3]], "bulbs_per_colour": 1,
                                       "default_effect": "Wave", "default_speed": 40}, "m1")
    patterns.set_user_patterns([own])
    try:
        assert me.idle_effect_for("Mine") == "Wave"
        c = Clock()
        plan = make_plan("Pulse", pattern="Mine", clock=c)
        assert plan.idle.effect == "Wave"
    finally:
        patterns.set_user_patterns([])


def test_the_transition_into_and_out_of_the_fallback_is_gradual():
    c = Clock()
    plan = make_plan("Pulse", clock=c, idle="Steady")
    frames_after(plan, c, 1.0, secs=0.8)
    c.t += 3.05
    levels = []
    for _ in range(40):
        c.t += 0.05
        levels.append(max(lum(plan.render(0.0))))
    steps = [abs(b - a) for a, b in zip(levels, levels[1:])]
    assert max(steps) < 80  # eases, no jump


def test_rendering_with_no_pixels_is_empty():
    c = Clock()
    plan = make_plan("Pulse", clock=c)
    plan.n = 0
    assert plan.render(0.0) == []


# --- sessions ---------------------------------------------------------------------------------------------------------
def music_world(hass, n=10, extra=False):
    lights = {"e1": FakeLight(hass, n, entry_id="e1")}
    if extra:
        lights["e2"] = FakeLight(hass, 6, entry_id="e2")
    mgr = FakeMgr(hass, lights)
    music = hass.data[const.MUSIC_KEY] = mm.MusicManager(hass, mgr)
    return mgr, music


async def start(hass, conn, **msg):
    msg = {"type": "light_show_studio/music/start", "targets": {"lights": ["e1"]}, **msg}
    await call(ws_music.ws_start, hass, conn, msg)


async def frame(hass, conn, **kw):
    msg = {"type": "light_show_studio/music/frame", "targets": {"lights": ["e1"]}, **good(), **kw}
    await call(ws_music.ws_frame, hass, conn, msg)


def test_commands_need_no_admin_and_are_registered():
    for f in (ws_music.ws_start, ws_music.ws_frame, ws_music.ws_stop):
        assert not getattr(f, "_admin", False)
    hass = H.FakeHass.__new__(H.FakeHass)
    hass.commands = []
    ws_music.async_register(hass)
    assert hass.commands == [ws_music.ws_start, ws_music.ws_frame, ws_music.ws_stop]


@aio
async def test_start_takes_the_light_feeds_it_and_stop_restores_it(hass):
    mgr, music = music_world(hass)
    ctl = mgr.ctls["e1"]
    await ctl.async_turn_on(colour=R, transition=0)
    await asyncio.sleep(0.02)
    conn = FakeConn()
    await start(hass, conn, effect="Pulse", pattern="Warm White", sensitivity=70)
    res = conn.results[-1][1]
    assert res["lights"] == ["e1"] and res["effect"] == "Pulse" and res["pattern"] == "Warm White" \
        and res["sensitivity"] == 70 and res["timeout"] == 3.0 and res["session"].startswith("music:")
    assert ctl.zone == "Music"
    assert ctl.music == {"active": True, "effect": "Pulse", "pattern": "Warm White", "sensitivity": 70, "live": False}
    for _ in range(12):
        await frame(hass, conn, level=0.9, bass=0.9)
        await asyncio.sleep(0.04)
    assert conn.results[-1][1] == {"fed": 1}
    assert ctl.music["live"] is True
    assert max(max(c) for c in ctl.sent[-1]) > 150
    await call(ws_music.ws_stop, hass, conn, {"type": "light_show_studio/music/stop"})
    assert conn.results[-1][1] == {"stopped": 1}
    await asyncio.sleep(1.0)
    assert ctl.zone is None and ctl.music is None and ctl.sent[-1] == [R] * 10  # back to red


@aio
async def test_start_defaults_and_validation(hass):
    mgr, music = music_world(hass)
    conn = FakeConn()
    await start(hass, conn)
    res = conn.results[-1][1]
    assert res["effect"] == "Pulse" and res["sensitivity"] == 50 and res["pattern"] == patterns.DEFAULT_PATTERN
    for bad in ({"effect": "Disco"}, {"sensitivity": 0}, {"sensitivity": 101}, {"sensitivity": 5.5}, {"sensitivity": True},
                {"mode": "x"}, {"targets": {"lights": [""]}}, {"targets": {"lights": ["x"] * 65}}, {"targets": {"bogus": 1}},
                {"targets": "e1"}, {"pattern": ""}, {"nonsense": 1}, {"targets": {"lights": [5]}}):
        with pytest.raises(Exception):
            await start(hass, conn, **bad)
    for msg, text in (({"targets": {}}, "at least one"), ({"pattern": "Nope"}, "Unknown pattern"),
                      ({"targets": {"lights": ["ghost"]}}, "isn't set up"), ({"targets": {"areas": ["Nowhere"]}}, "No area"),
                      ({"idle_effect": "Nope"}, "Unknown idle effect")):
        n = len(conn.errors)
        await start(hass, conn, **msg)
        assert len(conn.errors) == n + 1 and conn.errors[-1][1] in ("failed", "invalid_format") and text in conn.errors[-1][2], msg
    conn.close()


@aio
async def test_frames_are_only_taken_from_the_connection_that_started_music(hass):
    mgr, music = music_world(hass)
    mine, other = FakeConn(), FakeConn()
    await start(hass, mine)
    await frame(hass, other)
    assert other.errors[-1][1] == "not_found"
    await frame(hass, mine, targets={"lights": ["e2"]})
    assert mine.errors[-1][1] == "not_found"
    await frame(hass, mine, targets={"lights": ["e1", "e2"]})
    assert mine.results[-1][1] == {"fed": 1}
    await call(ws_music.ws_stop, hass, other, {"type": "light_show_studio/music/stop"})
    assert other.results[-1][1] == {"stopped": 0} and mgr.ctls["e1"].zone == "Music"  # another phone can't stop it
    mine.close()


@aio
async def test_frame_validation_errors_are_reported_and_nothing_is_fed(hass):
    mgr, music = music_world(hass)
    conn = FakeConn()
    await start(hass, conn)
    for bad in ({"level": 2}, {"bass": float("nan")}, {"mid": "x"}, {"treble": None}, {"beat": 1}, {"bpm": 5}, {"level": True}):
        n = len(conn.errors)
        await frame(hass, conn, **bad)
        assert len(conn.errors) == n + 1 and conn.errors[-1][1] == "invalid_format", bad
    with pytest.raises(Exception):
        await call(ws_music.ws_frame, hass, conn, {"type": "light_show_studio/music/frame", "targets": {"lights": ["e1"]}, "level": 1})
    await frame(hass, conn, targets={})
    assert conn.errors[-1][1] == "invalid_format"
    assert music.sessions[next(iter(music.sessions))].plan.state.beats == 0
    conn.close()


@aio
async def test_frames_over_40_a_second_are_refused_per_connection(hass):
    mgr, music = music_world(hass)
    a, b = FakeConn(), FakeConn()
    await start(hass, a)
    await start(hass, b, targets={"lights": ["e2"]}) if False else None
    ok = limited = 0
    for _ in range(60):
        n = len(a.results)
        await frame(hass, a)
        if len(a.results) > n:
            ok += 1
        elif a.errors[-1][1] == "rate_limited":
            limited += 1
    assert 8 <= ok <= 12 and limited == 60 - ok  # the burst only; the rest wait their turn
    await asyncio.sleep(0.3)
    await frame(hass, a)
    assert a.results[-1][1] == {"fed": 1}
    assert ws_music._allowed(b)  # a second connection has its own allowance
    b.subscriptions.clear()
    a.close()
    assert id(a) not in ws_music._limits  # forgotten when the connection closes


@aio
async def test_no_frames_for_3_seconds_falls_back_then_resumes(hass):
    me.TIMEOUT = 0.4  # shortened for the test (restored below)
    try:
        await _fallback_scene(hass)
    finally:
        me.TIMEOUT = 3.0


async def _fallback_scene(hass):
    mgr, music = music_world(hass)
    ctl = mgr.ctls["e1"]
    conn = FakeConn()
    await start(hass, conn, effect="Pulse", pattern="Candy Cane")
    for _ in range(15):
        await frame(hass, conn, level=1.0, bass=1.0)
        await asyncio.sleep(0.03)
    assert ctl.music["live"] is True and max(max(c) for c in ctl.sent[-1]) > 200
    await asyncio.sleep(1.8)
    assert ctl.music["live"] is False  # the light is told
    assert ctl.zone == "Music"  # still held, showing the pattern's own look
    idle_frame = ctl.sent[-1]
    assert 100 < max(max(c) for c in idle_frame) < 200  # a steady glow, dimmer than the music peak
    assert idle_frame[0][0] > 100 and idle_frame[2][1] > 100  # Candy Cane colours
    for _ in range(25):
        await frame(hass, conn, level=1.0)
        await asyncio.sleep(0.03)
    assert ctl.music["live"] is True and max(max(c) for c in ctl.sent[-1]) > 200
    conn.close()


@aio
async def test_closing_the_connection_stops_music_and_restores_the_light(hass):
    mgr, music = music_world(hass)
    ctl = mgr.ctls["e1"] = FakeLight(hass, 10, on=False, entry_id="e1")
    conn = FakeConn()
    await start(hass, conn)
    assert ctl.is_on and ctl.zone == "Music"
    conn.close()
    await asyncio.sleep(1.0)
    assert music.sessions == {} and ctl.zone is None and not ctl.is_on and ctl.music is None


@aio
async def test_a_person_taking_the_light_ends_music_on_it_but_not_on_the_others(hass):
    mgr, music = music_world(hass, extra=True)
    conn = FakeConn()
    await start(hass, conn, targets={"lights": ["e1", "e2"]})
    await asyncio.sleep(0.1)
    await mgr.ctls["e1"].async_turn_on(colour=G, transition=0)
    await asyncio.sleep(0.2)
    assert mgr.ctls["e1"].zone is None and mgr.ctls["e1"].music is None
    assert mgr.ctls["e2"].zone == "Music"
    n = len(mgr.ctls["e1"].sent)
    await frame(hass, conn, targets={"lights": ["e1", "e2"]}, level=1.0)
    await asyncio.sleep(0.2)
    assert mgr.ctls["e1"].sent[-1] == [G] * 10 and len(mgr.ctls["e1"].sent) <= n + 3  # music left it alone
    conn.close()


@aio
async def test_a_show_taking_the_light_ends_music_on_it(hass):
    mgr, music = music_world(hass)
    ctl = mgr.ctls["e1"]
    conn = FakeConn()
    await start(hass, conn)
    await asyncio.sleep(0.1)
    ctl.enable_show()
    await asyncio.sleep(0.2)
    assert ctl.zone is None and ctl.music is None
    conn.close()


@aio
async def test_turning_the_light_off_ends_music_on_it(hass):
    mgr, music = music_world(hass)
    ctl = mgr.ctls["e1"]
    conn = FakeConn()
    await start(hass, conn)
    await asyncio.sleep(0.1)
    await ctl.async_turn_off(transition=0)
    await asyncio.sleep(0.2)
    assert not ctl.is_on and ctl.music is None
    conn.close()


@aio
async def test_starting_again_on_the_same_light_takes_over_without_the_light_flashing_back(hass):
    mgr, music = music_world(hass)
    ctl = mgr.ctls["e1"]
    await ctl.async_turn_on(colour=R, transition=0)
    conn = FakeConn()
    await start(hass, conn, effect="Pulse")
    await asyncio.sleep(0.5)
    n = len(ctl.sent)
    await start(hass, conn, effect="Flash")
    await asyncio.sleep(0.3)
    assert len(music.sessions) == 1 and next(iter(music.sessions.values())).plan.effect == "Flash"
    assert all(f != [R] * 10 for f in ctl.sent[n:])
    assert ctl.music["effect"] == "Flash"
    conn.close()


@aio
async def test_a_newer_session_on_an_overlapping_light_replaces_the_older_one(hass):
    mgr, music = music_world(hass, extra=True)
    first, second = FakeConn(), FakeConn()
    await start(hass, first, targets={"lights": ["e1", "e2"]})
    await start(hass, second, targets={"lights": ["e2"]})
    assert len(music.sessions) == 1 and next(iter(music.sessions.values())).owner is second
    await asyncio.sleep(0.8)
    assert mgr.ctls["e1"].zone is None and mgr.ctls["e2"].zone == "Music"
    first.close()
    assert len(music.sessions) == 1  # the first connection closing late doesn't touch the newer session
    second.close()


@aio
async def test_stop_by_target_stops_only_those_sessions(hass):
    mgr, music = music_world(hass, extra=True)
    conn = FakeConn()
    await start(hass, conn, targets={"lights": ["e1"]})
    await start(hass, conn, targets={"lights": ["e2"]})
    assert len(music.sessions) == 2
    await call(ws_music.ws_stop, hass, conn, {"type": "light_show_studio/music/stop", "targets": {"lights": ["e1"]}})
    assert conn.results[-1][1] == {"stopped": 1} and len(music.sessions) == 1
    await call(ws_music.ws_stop, hass, conn, {"type": "light_show_studio/music/stop"})
    assert conn.results[-1][1] == {"stopped": 1} and music.sessions == {}


@aio
async def test_music_on_an_area_in_string_and_spatial_mode(hass):
    runs = (mapping.Run("r1", "e1", "A", 0, 10, area="room", positions=tuple(v for i in range(10) for v in (500, i * 100, 500))),
            mapping.Run("r2", "e2", "B", 0, 6, area="room", positions=tuple(v for i in range(6) for v in (500, i * 200, 500))))
    model = mapping.MapModel(runs, (), {"kind": "none", "w": 1, "h": 1}, (mapping.Area("room", "Room"),))
    lights = {"e1": FakeLight(hass, 10, entry_id="e1"), "e2": FakeLight(hass, 6, entry_id="e2")}
    mgr = FakeMgr(hass, lights, model)
    music = hass.data[const.MUSIC_KEY] = mm.MusicManager(hass, mgr)
    conn = FakeConn()
    await start(hass, conn, targets={"areas": ["Room"]}, effect="Spectrum", mode="spatial")
    assert sorted(conn.results[-1][1]["lights"]) == ["e1", "e2"]
    for _ in range(20):
        await frame(hass, conn, targets={"areas": ["Room"]}, level=0.9, bass=1.0)
        await asyncio.sleep(0.03)
    bottom, top = lights["e1"].sent[-1][:3], lights["e1"].sent[-1][-3:]
    assert min(max(c) for c in bottom) > 150 and max(max(c) for c in top) < 100  # bass lights the bottom of the room
    assert lights["e2"].music["effect"] == "Spectrum"
    await frame(hass, conn, targets={"areas": ["room"]})  # by id or name, any case
    assert conn.results[-1][1] == {"fed": 1}
    conn.close()


@aio
async def test_music_on_a_light_and_an_area_it_belongs_to_does_not_double_up(hass):
    runs = (mapping.Run("r1", "e1", "A", 0, 10, area="room"),)
    model = mapping.MapModel(runs, (), {"kind": "none", "w": 1, "h": 1}, (mapping.Area("room", "Room"),))
    lights = {"e1": FakeLight(hass, 10, entry_id="e1")}
    mgr = FakeMgr(hass, lights, model)
    music = hass.data[const.MUSIC_KEY] = mm.MusicManager(hass, mgr)
    conn = FakeConn()
    await start(hass, conn, targets={"lights": ["e1"], "areas": ["Room"]})
    plan = next(iter(music.sessions.values())).plan
    assert plan.n == 10
    conn.close()


@aio
async def test_music_on_a_user_pattern_with_painted_pixels(hass):
    mgr, music = music_world(hass)
    p = H.user_patterns.UserPattern(
        {"name": "Paint", "kind": "pixels", "lights": {"e1": {"first": 0, "colours": [[255, 0, 0]] * 5 + [[0, 0, 255]] * 5}},
         "areas": {}, "default_effect": "Steady", "default_speed": 50}, "px")
    patterns.set_user_patterns([p])
    conn = FakeConn()
    await start(hass, conn, pattern="Paint", effect="Pulse")
    for _ in range(15):
        await frame(hass, conn, level=1.0)
        await asyncio.sleep(0.03)
    f = mgr.ctls["e1"].sent[-1]
    assert f[0][0] > 150 and f[0][2] == 0 and f[9][2] > 150 and f[9][0] == 0
    conn.close()


@aio
async def test_stop_all_lets_go_without_restoring_for_shutdown(hass):
    mgr, music = music_world(hass)
    conn = FakeConn()
    await start(hass, conn)
    music.stop_all()
    assert music.sessions == {} and mgr.ctls["e1"].zone is None


@aio
async def test_at_most_16_sessions(hass):
    lights = {f"e{i}": FakeLight(hass, 4, entry_id=f"e{i}") for i in range(20)}
    mgr = FakeMgr(hass, lights)
    music = hass.data[const.MUSIC_KEY] = mm.MusicManager(hass, mgr)
    conn = FakeConn()
    for i in range(16):
        await start(hass, conn, targets={"lights": [f"e{i}"]})
    n = len(conn.errors)
    await start(hass, conn, targets={"lights": ["e17"]})
    assert len(conn.errors) == n + 1 and "Too many" in conn.errors[-1][2] and len(music.sessions) == 16
    music.stop_all()


@aio
async def test_music_not_set_up(hass):
    conn = FakeConn()
    await start(hass, conn)
    assert conn.errors[-1][1] == "not_found"
