"""Music sync that follows a speaker: the analysis of synthetic audio (beats, bands), the sidecar cache, the speaker's
position carried forward, finding the library song a speaker plays, and the session with a fake Home Assistant.
Plain pytest, no Home Assistant, numpy or ffmpeg (see wsharness)."""
from __future__ import annotations

import asyncio
import math
import os
import struct
from datetime import datetime, timedelta, timezone

import pytest

import wsharness as H
from wsharness import FakeConn, FakeLight, FakeMgr, FakeUser, aio, call

aa = H.mod("audio_analysis")
mf = H.mod("music_follow")
mm = H.mod("musicmanager")
library = H.mod("library")
ws_follow = H.mod("ws_music_follow")
const = H.mod("const")

RATE, HOP_S = aa.RATE, aa.HOP_S
R = (255, 0, 0)
T0 = datetime(2026, 11, 11, 12, 0, 0, tzinfo=timezone.utc)
SPEAKER = "media_player.living_room"


# --- synthetic audio -------------------------------------------------------------------------------------
def silence(seconds):
    return [0.0] * int(seconds * RATE)


def tone(freq, seconds, amp=0.8, fade=0.05):
    """A sine that fades in and out (a hard edge is a click: every band hears it)."""
    n = int(seconds * RATE)
    k = max(1, int(fade * RATE))
    return [amp * min(1.0, i / k, (n - 1 - i) / k) * math.sin(2 * math.pi * freq * i / RATE) for i in range(n)]


def clicks(times, seconds):
    """Silence with a short decaying burst (about 7 ms) starting at each time."""
    x = silence(seconds)
    for t in times:
        s = int(round(t * RATE))
        for j in range(80):
            x[s + j] = 0.9 * math.exp(-j / 20) * math.sin(2 * math.pi * 900 * j / RATE)
    return x


def pcm(samples):
    return struct.pack(f"<{len(samples)}h", *(int(max(-1.0, min(1.0, v)) * 32767) for v in samples))


# --- analysis ----------------------------------------------------------------------------------------------
def test_hops_are_about_23_ms_and_bands_split_at_150_and_2000_hz():
    assert 0.022 < HOP_S < 0.024
    assert aa.BASS[0] == 1 and aa.BASS[1] == aa.MID[0] and aa.MID[1] == aa.TREBLE[0] and aa.TREBLE[1] == aa.WIN // 2
    assert (aa.BASS[1] - 1) * aa.BIN_HZ >= 150 > (aa.BASS[1] - 2) * aa.BIN_HZ


def test_clicks_are_found_as_beats_within_a_hop():
    times = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    a = aa.analyse(clicks(times, 3.5), use_numpy=False)
    found = a.beat_times()
    assert len(found) == len(times)
    for want, got in zip(times, found):
        assert abs(want - got) <= HOP_S + 1e-9
    assert a.bpm == pytest.approx(120, abs=6)
    assert a.n == len(a.bass) == len(a.mid) == len(a.treble) == len(a.beats) and a.duration == pytest.approx(3.5, abs=0.05)
    i = a.index(1.0)
    assert a.beat_between(i - 3, i + 3) and not a.beat_between(i + 3, i + 12)
    assert a.level[a.index(1.01)] > 100 and a.level[a.index(1.3)] == 0  # loud at the click, silent between


def test_uneven_clicks_keep_their_times_and_close_ones_are_one_beat():
    times = [0.4, 0.47, 1.13, 2.02]  # the second is within the minimum gap of the first
    found = aa.analyse(clicks(times, 2.5), use_numpy=False).beat_times()
    assert len(found) == 3
    for want, got in zip([0.4, 1.13, 2.02], found):
        assert abs(want - got) <= HOP_S + 1e-9


def test_bands_respond_to_low_mid_and_high_tones():
    lo, mid, hi = (aa.analyse(tone(f, 0.6), use_numpy=False) for f in (60, 600, 3000))
    lo, mid, hi = (a.values(a.index(0.3)) for a in (lo, mid, hi))  # (level, bass, mid, treble)
    assert lo[1] > 0.8 and lo[2] < 0.2 and lo[3] < 0.05
    assert mid[2] > 0.8 and mid[1] < 0.3 and mid[3] < 0.1
    assert hi[3] > 0.8 and hi[1] < 0.05 and hi[2] < 0.3
    assert min(lo[0], mid[0], hi[0]) > 0.8  # the level is there for all three
    # in one song the raw bands follow the tone too
    _, bass, mids, treble, _ = aa.frames(tone(60, 0.5) + tone(3000, 0.5), use_numpy=False)
    early, late = int(0.25 / HOP_S), int(0.75 / HOP_S)
    assert bass[early] > 10 * bass[late] and treble[late] > 10 * treble[early]


def test_a_quiet_passage_stays_quieter_than_a_loud_one_and_silence_is_dark():
    a = aa.analyse(tone(440, 0.6, 0.8) + tone(440, 0.6, 0.08) + silence(0.5), use_numpy=False)
    loud, quiet, none = a.values(a.index(0.3))[0], a.values(a.index(0.9))[0], a.values(a.index(1.5))[0]
    assert loud > 0.9 and 0.05 < quiet < 0.5 and none == 0.0
    assert not any(aa.analyse(silence(0.5), use_numpy=False).beats)
    assert aa.analyse([], use_numpy=False).n == 1 and aa.find_beats([]) == [] and aa.tempo([1, 2]) is None


def test_numpy_frames_match_the_plain_ones():
    pytest.importorskip("numpy")
    x = clicks([0.3, 0.8], 1.2)
    x = [v + 0.2 * math.sin(2 * math.pi * 220 * i / RATE) for i, v in enumerate(x)]
    for plain, quick in zip(aa.frames(x, use_numpy=False), aa.frames(x, use_numpy=True)):
        assert quick == pytest.approx(plain, abs=1e-6)


def test_pcm_is_read_as_signed_16_bit():
    a = aa.analyse_pcm(pcm(clicks([0.5, 1.0], 1.4)) + b"\x01")  # an odd last byte is dropped
    assert [round(t, 1) for t in a.beat_times()] == [0.5, 1.0]
    with pytest.raises(aa.AnalysisError, match="longer"):
        aa.analyse_pcm(bytes(aa.MAX_SECONDS * RATE * 2 + 2))


# --- the sidecar -------------------------------------------------------------------------------------------
def small():
    return aa.analyse(clicks([0.2, 0.6], 0.9), use_numpy=False)


def test_sidecar_round_trip_and_staleness(tmp_path):
    song = str(tmp_path / "song.mp3")
    with open(song, "wb") as f:
        f.write(b"not really an mp3")
    a = small()
    assert aa.read_sidecar(song) is None
    assert aa.write_sidecar(song, a, aa.file_key(song))
    assert sorted(os.listdir(tmp_path)) == ["song.mp3", "song.mp3.lssa"]  # no temp file is left
    assert aa.read_sidecar(song) == a
    assert os.path.getsize(aa.sidecar_path(song)) < 5 * a.n  # compressed
    with open(song, "ab") as f:  # the song changed: the sidecar no longer counts
        f.write(b"more")
    assert aa.read_sidecar(song) is None
    assert aa.read_sidecar(str(tmp_path / "missing.mp3")) is None


def test_damaged_or_other_version_sidecars_are_ignored():
    a = small()
    data = aa.pack(a, (10, 20))
    assert aa.unpack(data) == a and aa.unpack(data, (10, 20)) == a
    assert aa.unpack(data, (10, 21)) is None
    assert aa.unpack(data[:4] + bytes([aa.VERSION + 1]) + data[5:]) is None
    assert aa.unpack(b"NOPE" + data[4:]) is None
    assert aa.unpack(data[:-7]) is None and aa.unpack(data[:5] + b"junk") is None and aa.unpack(b"") is None


def test_a_sidecar_that_cannot_be_written_is_not_an_error(tmp_path):
    assert aa.write_sidecar(str(tmp_path / "no such folder" / "song.mp3"), small(), (1, 2)) is False


# --- a fake Home Assistant ------------------------------------------------------------------------------------
class State:
    def __init__(self, state="playing", **attrs):
        self.state, self.attributes = state, attrs


class States(dict):
    pass  # .get(entity_id), as hass.states


def world(hass, tmp_path=None):
    hass.states = States()
    hass.async_add_executor_job = lambda fn, *args: hass.loop.run_in_executor(None, fn, *args)
    lights = {"e1": FakeLight(hass, 10, entry_id="e1"), "e2": FakeLight(hass, 6, entry_id="e2")}
    mgr = FakeMgr(hass, lights)
    music = hass.data[const.MUSIC_KEY] = mm.MusicManager(hass, mgr)
    lib = None
    if tmp_path is not None:
        lib = library.ShowLibrary(str(tmp_path / "xlights"), str(tmp_path), "local")
        for rel in ("Carol/Carol of the Bells.mp3", "Other/other.mp3", "Carol/notes.txt"):
            os.makedirs(os.path.dirname(os.path.join(lib.root, rel)), exist_ok=True)
            with open(os.path.join(lib.root, rel), "wb") as f:
                f.write(rel.encode())
    return mgr, music, lib


URL = "http://ha.local:8123/media/local/xlights/Carol/Carol%20of%20the%20Bells.mp3?authSig=abc.def"
OTHER = "http://ha.local:8123/media/local/xlights/Other/other.mp3?authSig=x"


def flat(n=2000, beats=(), level=200, bpm=None):
    marks = bytearray(n)
    for i in beats:
        marks[i] = 1
    return aa.Analysis(bytes([level]) * n, bytes([150]) * n, bytes([100]) * n, bytes([50]) * n, bytes(marks), bpm)


def loader(by_name, calls=None):
    async def load(hass, path):
        if calls is not None:
            calls.append(os.path.basename(path))
        if os.path.basename(path) not in by_name:
            raise aa.AnalysisError("The song could not be decoded")
        return by_name[os.path.basename(path)]
    return load


def spy(session):
    fed = []
    real = session.plan.feed
    session.plan.feed = lambda f: (fed.append(f), real(f))[1]
    return fed


# --- the analysis job ---------------------------------------------------------------------------------------
@aio
async def test_a_song_is_decoded_once_then_comes_from_memory_or_its_sidecar(hass):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        world(hass)
        song = os.path.join(d, "song.mp3")
        with open(song, "wb") as f:
            f.write(b"x" * 100)
        decoded = []

        async def decode(path):
            decoded.append(path)
            return pcm(clicks([0.3, 0.8], 1.2))

        a = await aa.async_analysis(hass, song, decode)
        assert [round(t, 1) for t in a.beat_times()] == [0.3, 0.8] and decoded == [song]
        assert await aa.async_analysis(hass, song, decode) is a and len(decoded) == 1  # memory
        hass.data.pop(aa.CACHE_KEY)
        assert await aa.async_analysis(hass, song, decode) == a and len(decoded) == 1  # the sidecar
        with open(song, "ab") as f:
            f.write(b"changed")
        await aa.async_analysis(hass, song, decode)
        assert len(decoded) == 2  # a changed file is analysed again

        async def long(path):
            return bytes(aa.MAX_SECONDS * RATE * 2 + 2)

        with open(song, "ab") as f:
            f.write(b"again")
        with pytest.raises(aa.AnalysisError, match="longer than 20 minutes"):
            await aa.async_analysis(hass, song, long)
        with pytest.raises(aa.AnalysisError, match="could not be read"):
            await aa.async_analysis(hass, os.path.join(d, "missing.mp3"), decode)


# --- the speaker's position -----------------------------------------------------------------------------------
def test_position_is_carried_forward_while_playing():
    at = T0 - timedelta(seconds=5)
    assert mf.speaker_position(State("playing", media_position=30, media_position_updated_at=at), T0) == pytest.approx(35.0)
    assert mf.speaker_position(State("playing", media_position=30.5, media_position_updated_at=at.isoformat()), T0) == pytest.approx(35.5)
    assert mf.speaker_position(State("paused", media_position=30, media_position_updated_at=at), T0) == 30.0
    assert mf.speaker_position(State("playing", media_position=30), T0) == 30.0  # no time of the report: as reported
    assert mf.speaker_position(State("playing", media_position=30, media_position_updated_at="soon"), T0) == 30.0
    assert mf.speaker_position(State("playing", media_position=30, media_position_updated_at=T0 + timedelta(seconds=2)), T0) == 30.0
    naive = (T0 - timedelta(seconds=2)).replace(tzinfo=None)
    assert mf.speaker_position(State("playing", media_position=1, media_position_updated_at=naive), T0) == pytest.approx(3.0)
    for bad in (None, "12", True):
        assert mf.speaker_position(State("playing", media_position=bad, media_position_updated_at=at), T0) is None
    assert mf.speaker_position(State("playing"), T0) is None


# --- which song is it ---------------------------------------------------------------------------------------------
@aio
async def test_a_speakers_song_is_matched_to_a_library_file(hass):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path
        _, _, lib = world(hass, Path(d))
        want = os.path.realpath(os.path.join(lib.root, "Carol", "Carol of the Bells.mp3"))
        assert mf.resolve_song(lib, URL) == want
        assert mf.resolve_song(lib, "media-source://media_source/local/xlights/Carol/Carol of the Bells.mp3") == want
        assert mf.resolve_song(lib, "Carol/Carol of the Bells.mp3") == want
        with open(os.path.join(d, "secret.mp3"), "wb") as f:
            f.write(b"x")
        for bad in (None, "", 5, "http://radio.example/stream.mp3", "x-sonos-spotify:track", "/etc/passwd.mp3",
                    "http://ha/media/local/xlights/../secret.mp3", "../secret.mp3", "http://ha/xlights/Carol/missing.mp3",
                    "http://ha/xlights/Carol/notes.txt", "http://ha/xlights/Carol/%00.mp3", "http://ha/xlights/"):
            assert mf.resolve_song(lib, bad) is None


# --- the session ----------------------------------------------------------------------------------------------------
def playing(pos=0.0, track=URL, at=T0, state="playing"):
    return State(state, media_content_id=track, media_position=pos, media_position_updated_at=at)


async def follow_world(hass, d, analysis=None, **kw):
    from pathlib import Path
    mgr, music, lib = world(hass, Path(d))
    hass.states[SPEAKER] = playing(10.0)
    now = {"t": T0}
    calls = []
    songs = {"Carol of the Bells.mp3": analysis or flat(), "other.mp3": flat(level=40)}
    res = await music.async_follow(SPEAKER, ["e1"], [], library=lib, load=loader(songs, calls),
                                   utcnow=lambda: now["t"], **kw)
    session = music.sessions[res["session"]]
    return mgr, music, session, session.owner, now, calls, res


def tmp(fn):
    @aio
    async def inner(hass):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            await fn(hass, d)
    def wrapper():
        inner()
    wrapper.__name__ = fn.__name__
    return wrapper


@tmp
async def test_follow_feeds_the_engine_from_the_speakers_position(hass, d):
    a = flat(beats=(440, 450))
    mgr, music, session, follow, now, calls, res = await follow_world(hass, d, a, effect="Pulse", sensitivity=60)
    ctl = mgr.ctls["e1"]
    assert res["media_player"] == SPEAKER and res["song"] == "Carol of the Bells.mp3" and res["effect"] == "Pulse"
    assert res["duration"] == pytest.approx(a.duration, abs=0.1) and calls == ["Carol of the Bells.mp3"]
    assert ctl.music["follow"] == SPEAKER and ctl.zone == "Music"
    fed = spy(session)
    hop = a.hop_s
    now["t"] = T0 + timedelta(seconds=436 * hop - 10.0)  # the speaker said 10 s at T0: now it is at hop 436
    assert follow.tick() and follow.position == pytest.approx(436 * hop)
    assert (fed[-1].level, fed[-1].bass, fed[-1].mid, fed[-1].treble, fed[-1].beat) == \
        (pytest.approx(200 / 255), pytest.approx(150 / 255), pytest.approx(100 / 255), pytest.approx(50 / 255), False)
    now["t"] += timedelta(seconds=5 * hop)  # hop 441: the beat at 440 fell between two ticks and is not lost
    assert follow.tick() and fed[-1].beat is True
    now["t"] += timedelta(seconds=hop)
    assert follow.tick() and fed[-1].beat is False
    await asyncio.sleep(0.7)  # the session's own loop feeds too
    assert session.plan.live and ctl.music["live"] is True and session.plan.state.beats >= 1
    assert max(max(c) for c in ctl.sent[-1]) > 100


@tmp
async def test_a_seek_resyncs_and_does_not_play_the_beats_it_skipped(hass, d):
    a = flat(n=4000, beats=(1000, 3000))
    mgr, music, session, follow, now, _, _ = await follow_world(hass, d, a)
    fed = spy(session)
    assert follow.tick() and fed[-1].beat is False
    hass.states[SPEAKER] = playing(3500 * a.hop_s, at=now["t"])  # a seek past both beats
    assert follow.tick() and follow.position == pytest.approx(3500 * a.hop_s) and fed[-1].beat is False
    hass.states[SPEAKER] = playing(999 * a.hop_s, at=now["t"])  # and back
    assert follow.tick() and fed[-1].beat is False
    now["t"] += timedelta(seconds=2 * a.hop_s)
    assert follow.tick() and fed[-1].beat is True
    n = len(fed)
    hass.states[SPEAKER] = playing(5000 * a.hop_s, at=now["t"])  # past the end of the analysis: nothing to feed
    assert follow.tick() and len(fed) == n and follow.active


@tmp
async def test_offset_moves_the_lights_against_the_speaker(hass, d):
    _, _, _, follow, _, _, _ = await follow_world(hass, d, offset=-0.25)
    assert follow.tick() and follow.position == pytest.approx(9.75)


@tmp
async def test_a_pause_stops_the_feed_and_playing_again_resumes_it(hass, d):
    mgr, music, session, follow, now, _, _ = await follow_world(hass, d)
    fed = spy(session)
    follow.tick()
    n = len(fed)
    hass.states[SPEAKER] = playing(12.0, state="paused")
    now["t"] += timedelta(seconds=60)
    await asyncio.sleep(0.1)
    assert follow.tick() and len(fed) == n and follow.active  # paused: the session waits, nothing is fed
    hass.states[SPEAKER] = playing(12.0, at=now["t"])
    assert follow.tick() and len(fed) == n + 1 and follow.position == pytest.approx(12.0)


@tmp
async def test_an_unknown_song_ends_the_session_and_restores_the_light(hass, d):
    from pathlib import Path
    mgr, music, lib = world(hass, Path(d))
    ctl = mgr.ctls["e1"]
    await ctl.async_turn_on(colour=R, transition=0)
    await asyncio.sleep(0.02)
    hass.states[SPEAKER] = playing(1.0)
    res = await music.async_follow(SPEAKER, ["e1"], [], library=lib, load=loader({"Carol of the Bells.mp3": flat()}))
    assert res["session"] in music.sessions and ctl.zone == "Music"
    hass.states[SPEAKER] = playing(0.0, track="x-sonos-spotify:track%3a123")
    await asyncio.sleep(0.3)
    assert not music.sessions
    await asyncio.sleep(1.0)
    assert ctl.zone is None and ctl.music is None and ctl.sent[-1] == [R] * 10


@tmp
async def test_the_next_library_song_is_followed(hass, d):
    mgr, music, session, follow, now, calls, _ = await follow_world(hass, d)
    fed = spy(session)
    hass.states[SPEAKER] = playing(2.0, track=OTHER, at=now["t"])
    assert follow.tick() and follow.analysis is None  # loading: nothing fed yet
    await asyncio.sleep(0.2)
    assert calls == ["Carol of the Bells.mp3", "other.mp3"] and follow.active
    assert follow.tick() and fed[-1].level == pytest.approx(40 / 255)


@tmp
async def test_a_library_song_that_cannot_be_analysed_ends_the_session(hass, d):
    from pathlib import Path
    mgr, music, lib = world(hass, Path(d))
    hass.states[SPEAKER] = playing(1.0)
    await music.async_follow(SPEAKER, ["e1"], [], library=lib, load=loader({"Carol of the Bells.mp3": flat()}))
    hass.states[SPEAKER] = playing(0.0, track=OTHER)
    await asyncio.sleep(0.3)
    assert not music.sessions


@tmp
async def test_a_stopped_speaker_ends_the_session_after_a_moment(hass, d):
    mgr, music, session, follow, now, _, _ = await follow_world(hass, d)
    t = {"v": 100.0}
    follow._clock = lambda: t["v"]
    hass.states[SPEAKER] = State("idle")
    assert follow.tick()  # between two songs a speaker is idle for a moment
    t["v"] += mf.GONE_GRACE - 1
    assert follow.tick() and follow.active
    hass.states[SPEAKER] = playing(0.0)
    assert follow.tick()
    del hass.states[SPEAKER]
    assert follow.tick()
    t["v"] += mf.GONE_GRACE + 1
    assert not follow.tick() and not follow.active and not music.sessions
    assert not follow.tick()


@tmp
async def test_a_named_song_is_followed_whatever_the_speaker_calls_it(hass, d):
    from pathlib import Path
    mgr, music, lib = world(hass, Path(d))
    hass.states[SPEAKER] = playing(3.0, track="library://track/42")
    load = loader({"Carol of the Bells.mp3": flat()})
    with pytest.raises(mm.MusicError, match="not playing a song from the show library"):
        await music.async_follow(SPEAKER, ["e1"], [], library=lib, load=load)
    with pytest.raises(mm.MusicError, match="not in the show library"):
        await music.async_follow(SPEAKER, ["e1"], [], library=lib, load=load, song="Carol/nope.mp3")
    res = await music.async_follow(SPEAKER, ["e1"], [], library=lib, load=load, song="Carol/Carol of the Bells.mp3",
                                   utcnow=lambda: T0)
    follow = music.sessions[res["session"]].owner
    assert follow.tick() and follow.position == pytest.approx(3.0, abs=0.5)
    hass.states[SPEAKER] = playing(0.0, track="library://track/43")  # the next track: not the named song any more
    assert not follow.tick() and not music.sessions


@tmp
async def test_follow_refuses_what_it_cannot_do(hass, d):
    from pathlib import Path
    mgr, music, lib = world(hass, Path(d))
    hass.states[SPEAKER] = playing(1.0)
    load = loader({})
    for entity in ("media_player.nope", "light.kitchen", None):
        with pytest.raises(mm.MusicError, match="not a media player"):
            await music.async_follow(entity, ["e1"], [], library=lib, load=load)
    with pytest.raises(mm.MusicError, match="could not be decoded"):
        await music.async_follow(SPEAKER, ["e1"], [], library=lib, load=load)
    ok = loader({"Carol of the Bells.mp3": flat()})
    with pytest.raises(mm.MusicError, match="Offset"):
        await music.async_follow(SPEAKER, ["e1"], [], library=lib, load=ok, offset=60)
    with pytest.raises(mm.MusicError, match="Effect"):
        await music.async_follow(SPEAKER, ["e1"], [], library=lib, load=ok, effect="Nope")
    with pytest.raises(mm.MusicError):
        await music.async_follow(SPEAKER, ["nope"], [], library=lib, load=ok)
    assert not music.sessions and not [t for t in hass.tasks if not t.done()]


@tmp
async def test_starting_again_swaps_the_session_and_stop_ends_it(hass, d):
    mgr, music, session, follow, now, _, _ = await follow_world(hass, d)
    lib = follow.library
    res = await music.async_follow(SPEAKER, ["e1"], [], library=lib, load=loader({"Carol of the Bells.mp3": flat()}),
                                   effect="Spectrum")
    assert list(music.sessions) == [res["session"]] and music.sessions[res["session"]] is not session
    assert not follow.active and not follow.tick()  # the old follower lets go
    assert music.view()[0]["follow"] == SPEAKER and music.view()[0]["effect"] == "Spectrum"
    assert music.stop(None, ["e2"]) == 0 and music.stop(None, ["e1"]) == 1 and not music.sessions


# --- the websocket commands -------------------------------------------------------------------------------------------
def test_follow_commands_are_registered_beside_the_microphone_ones():
    hass = H.FakeHass.__new__(H.FakeHass)
    hass.commands = []
    ws_follow.async_register(hass)
    assert hass.commands == [ws_follow.ws_follow, ws_follow.ws_follow_stop]
    assert not getattr(ws_follow.ws_follow, "_admin", False)


@tmp
async def test_websocket_follow_and_stop(hass, d):
    from pathlib import Path
    mgr, music, lib = world(hass, Path(d))
    hass.states[SPEAKER] = playing(1.0)
    real, entities = mf.async_follow, H.mod("ws_music").light_entities

    async def patched(manager, *args, **kw):
        return await real(manager, *args, library=lib, load=loader({"Carol of the Bells.mp3": flat(bpm=120.0)}), **kw)

    mf.async_follow = patched
    try:
        conn = FakeConn()
        msg = {"type": "light_show_studio/music/follow", "media_player": SPEAKER, "targets": {"lights": ["e1"]}}
        await call(ws_follow.ws_follow, hass, conn, {**msg, "effect": "Beat Chase", "sensitivity": 80, "offset": 0.1})
        res = conn.results[-1][1]
        assert res["lights"] == ["e1"] and res["effect"] == "Beat Chase" and res["bpm"] == 120.0 and res["song"].endswith(".mp3")
        conn.close()  # the session does not belong to the connection
        await asyncio.sleep(0.05)
        assert len(music.sessions) == 1

        await call(ws_follow.ws_follow, hass, conn, {**msg, "targets": {}})
        assert conn.errors[-1][1] == "invalid_format"
        hass.states[SPEAKER] = playing(1.0, track="http://radio.example/live")
        await call(ws_follow.ws_follow, hass, conn, {**msg, "targets": {"lights": ["e2"]}})
        assert conn.errors[-1][1] == "failed" and "show library" in conn.errors[-1][2]
        for bad in ({"media_player": "light.kitchen"}, {"sensitivity": 0}, {"effect": "Nope"}, {"offset": 99}, {"offset": "1"}):
            with pytest.raises(Exception):
                await call(ws_follow.ws_follow, hass, conn, {**msg, **bad})

        hass.states[SPEAKER] = playing(1.0)
        H.mod("ws_music").light_entities = lambda hass, entry_id: [f"light.{entry_id}"]
        guest = FakeConn(FakeUser(admin=False, allowed={"light.e2"}))
        await call(ws_follow.ws_follow, hass, guest, {**msg, "targets": {"lights": ["e2"]}})
        assert guest.errors[-1][1] == "unauthorized"  # may control the light, may not see the speaker
        stop = {"type": "light_show_studio/music/follow_stop"}
        await call(ws_follow.ws_follow_stop, hass, guest, stop)
        assert guest.results[-1][1] == {"stopped": 0} and len(music.sessions) == 1  # not their light
        await call(ws_follow.ws_follow_stop, hass, conn, {**stop, "targets": {"lights": ["e2"]}})
        assert conn.results[-1][1] == {"stopped": 0}
        await call(ws_follow.ws_follow_stop, hass, conn, stop)
        assert conn.results[-1][1] == {"stopped": 1} and not music.sessions
    finally:
        mf.async_follow, H.mod("ws_music").light_entities = real, entities
