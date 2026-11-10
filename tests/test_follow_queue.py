"""After a show, its song is taken off the speaker's queue (its signed link expires and Sonos keeps asking for it)."""
from __future__ import annotations

import asyncio
import enum
import importlib.util
import sys
import types
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "light_show_studio"


def _stub(name: str, **attrs) -> None:
    mod = sys.modules.setdefault(name, types.ModuleType(name))
    for k, v in attrs.items():
        if not hasattr(mod, k):
            setattr(mod, k, v)


class _Feature(enum.IntFlag):
    CLEAR_PLAYLIST = 8192


def _load():
    _stub("homeassistant")
    _stub("homeassistant.components")
    _stub("homeassistant.components.media_player", MediaPlayerEntityFeature=_Feature)
    _stub("homeassistant.const", ATTR_ENTITY_ID="entity_id")
    _stub("homeassistant.core", Event=object, EventStateChangedData=dict, HomeAssistant=object, State=object,
          callback=lambda f: f)
    _stub("homeassistant.exceptions", ServiceValidationError=Exception, HomeAssistantError=Exception)
    _stub("homeassistant.helpers")
    _stub("homeassistant.helpers.event", async_track_state_change_event=lambda *a: (lambda: None))
    _stub("homeassistant.util")
    _stub("homeassistant.util.dt", utcnow=None, parse_datetime=None)
    sys.modules["homeassistant.util"].dt = sys.modules["homeassistant.util.dt"]
    pkg = sys.modules.setdefault("light_show_studio_fq", types.ModuleType("light_show_studio_fq"))
    pkg.__path__ = [str(PKG)]
    for name in ("fade", "fseq", "playback", "follow"):
        full = f"light_show_studio_fq.{name}"
        if full not in sys.modules:
            spec = importlib.util.spec_from_file_location(full, PKG / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[full] = mod
            spec.loader.exec_module(mod)
    return sys.modules["light_show_studio_fq.follow"]


follow = _load()
follow.CLEAR_DELAY = 0


class St:
    def __init__(self, state, content="", features=_Feature.CLEAR_PLAYLIST):
        self.state = state
        self.attributes = {"media_content_id": content, "supported_features": int(features)}


class FakeHass:
    def __init__(self, state):
        self.states = types.SimpleNamespace(get=lambda e: state)
        self.calls = []
        self.services = types.SimpleNamespace(async_call=self._call)

    async def _call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data["entity_id"]))


SONG = "http://ha:8123/media/local/xlights/Sarajevo/Sarajevo.m4a?authSig=abc"


def run(state):
    hass = FakeHass(state)
    asyncio.run(follow.clear_stale_queue(hass, "media_player.living_room_sonos"))
    return hass.calls


def test_clears_a_stopped_speaker_left_on_a_show_song():
    assert run(St("idle", SONG)) == [("media_player", "clear_playlist", "media_player.living_room_sonos")]
    assert run(St("paused", SONG))  # Sonos stops "paused" at the end of its queue


def test_leaves_the_speaker_alone_otherwise():
    assert run(St("playing", SONG)) == []  # the next show (or anything) is playing
    assert run(St("idle", "x-sonos-spotify:track")) == []  # someone else's music
    assert run(St("idle", SONG, features=0)) == []  # can't clear a queue
    assert run(None) == []
