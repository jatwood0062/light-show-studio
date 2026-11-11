"""Holds the string's state and turns it into frames on the wire."""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable

from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, EXTERNAL_MAX_FPS, FLUSH_DELAY, FPS, WHOLE_LIGHT_FPS
from .effects import ANIMATED, EFFECTS
from . import fade, patterns
from .patterns import PATTERN
from .hosts import valid_ipv4
from .protocol import Colour, GoveeLan, brightness_msg, colour_msg, colours_msg, realtime_msg, turn_msg

SCAN_RESEND = (0.05, 0.15)  # seconds after a scan frame at which it is sent once more (UDP may lose it)
RESEND_AFTER = 1.0  # an unchanged frame is sent again after this long (a light may have dropped it)
REARM_EVERY = 2.0  # realtime mode is switched on again this often while frames flow (a light can drop out of it)
KEEPALIVE = 1.0  # a still picture is sent again this often...
KEEP_FOR = 10.0  # ...for this long after it last changed (a lost UDP packet can't leave the wrong picture up)
SHOW = "Light show"  # effect name: let xLights / EZPlayer / play_sequence drive the string


def _split(c: Colour) -> tuple[Colour, int]:
    """Colour -> (full-brightness colour, level 0-255)."""
    m = max(c)
    if m == 0:
        return (255, 255, 255), 0
    return (c[0] * 255 // m, c[1] * 255 // m, c[2] * 255 // m), m


def enable_shows(hass: HomeAssistant) -> None:
    """Let every loaded light (Govee, Twinkly, WLED) show frames from a sequence."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if isinstance(ctl := getattr(entry, "runtime_data", None), C9Controller):
            ctl.enable_show()


class C9Controller:
    def __init__(self, hass: HomeAssistant, host: str, bulbs: int, segmented: bool = True) -> None:
        self.hass = hass
        self.host = valid_ipv4(host)  # only LAN addresses; ValueError otherwise
        self.lan = self._make_link(host)
        self.bulbs = bulbs
        self.show_lead_ms = 0  # -500..500: this light gets .fseq frames for t + lead (set by the integration from the option)
        self.segmented = segmented  # False: the light takes one colour, the frame's average
        self._whole: tuple[Colour, int] | None = None
        self._whole_sent = 0.0
        self._whole_latest: list[Colour] = []
        self._whole_handle: asyncio.TimerHandle | None = None
        self.is_on = False
        self.brightness = 255
        self.colour: Colour = (255, 150, 40)
        self.effect: str | None = None
        self.pattern = patterns.DEFAULT_PATTERN
        self.pattern_effect = patterns.DEFAULT_EFFECT
        self._fx_t, self._fx_effect = 0.0, None  # animation clock, kept across pattern changes
        self.speed = patterns.DEFAULT_SPEED  # 1-100, scales every animated effect
        self.show_mode = True  # accept sequencer frames; picking a colour or effect turns it off
        self.custom = False  # True once single bulbs have been set
        self.bulb_colour: list[Colour] = [self.colour] * bulbs
        self.bulb_level: list[int] = [255] * bulbs
        self._realtime = False
        self._armed_at = 0.0  # when realtime mode was last switched on
        self._hw_last: list[Colour] | None = None  # the last per-bulb frame put on the wire, for the keep-alive
        self._hw_sent = 0.0
        self._hw_changed = 0.0  # when the frame on the wire last changed
        self._keep_handle: asyncio.TimerHandle | None = None
        self._ext_latest: bytes | None = None  # a sequencer frame that came too soon after the last, sent when due
        self._ext_handle: asyncio.TimerHandle | None = None
        self._task: asyncio.Task | None = None
        self._flush_handle: asyncio.TimerHandle | None = None
        self._listeners: list[Callable[[], None]] = []
        self.frame: list[Colour] = [(0, 0, 0)] * bulbs  # what the bulbs show now, for the card
        self._frame_listeners: list[Callable[[], None]] = []
        self._ext_sent = 0.0
        self._ext_seen = 0.0  # last sequencer frame received, used or not
        self.online: bool | None = None  # None = not checked yet
        self.last_seen: float | None = None  # wall clock
        self._misses = 0
        self._identifying = False
        self._identify_task: asyncio.Task | None = None
        self._resume_handle: asyncio.TimerHandle | None = None
        # zones (see mapmanager): zone id -> (name, [(first pixel, colours)]) for the runs of it on this light
        self._zones: dict[str, tuple[str, list[tuple[int, list[Colour]]]]] = {}
        self._zone_prev_show = False  # show mode before the first zone took over
        self._zone_powered = False  # the first zone turned the light on
        # camera scan (see mapmanager): the scan owns the light like a zone does, and puts it back when it ends
        self._scanning = False
        self._scan_prev_show = False
        self._scan_powered = False
        self._scan_before: list[Colour] = []
        self._scan_resend: list[asyncio.TimerHandle] = []  # re-sends of the last scan frame (UDP can lose one)
        # transitions: a brightness ramp (turn on / off, long fades) and a cross-fade from the previous look
        self.default_transition = 0.0  # seconds; the integration sets it from the `default_transition` option
        self.entry_id: str | None = None  # the config entry this light belongs to (set by the integration)
        self._music: dict | None = None  # what the music session driving this light says about itself
        self._now: Callable[[], float] = time.monotonic  # the clock fades use (tests replace it)
        self._ramp: fade.Ramp | None = None
        self._xf: fade.Xfade | None = None
        self._wire: tuple[list[Colour], float] | None = None  # the last frame sent to the light, and when
        self._wire_raw: tuple[bytes, int] | None = None  # ...and the sequencer bytes + brightness it was made from
        self._zone_dirty = False  # a zone frame is waiting to be sent (several zones on one light make one send)
        self._base_key: tuple | None = None
        self._base: list[Colour] = []
        self._last_raw: list[Colour] = []  # the last effect frame before the brightness setting and any fade-in/out
        self._off_handle: asyncio.TimerHandle | None = None

    # --- hardware hooks (a subclass for another brand overrides these) ----
    def _make_link(self, host: str) -> GoveeLan | None:
        return GoveeLan(host)

    def _hw_power(self, on: bool) -> None:
        self.lan.send(turn_msg(on))

    def _hw_frame(self, frame: list[Colour]) -> None:
        """Show a per-bulb frame that is already scaled by the brightness."""
        now = time.monotonic()
        if not self._realtime:
            self.lan.send(brightness_msg(100))  # the string's own dimmer would dim every frame
        if not self._realtime or now - self._armed_at >= REARM_EVERY:
            # switched on again every few seconds, not just once: a light that dropped out of realtime mode (a lost
            # packet, a command from another app) ignores every frame until it is, which looks like a freeze
            self._realtime = self.lan.send(realtime_msg(True)) is not False
            self._armed_at = now
        self.lan.send(colours_msg(frame))
        if frame != self._hw_last:
            self._hw_changed = now
        self._hw_last, self._hw_sent = frame, now
        if self._keep_handle is None:
            self._keep_handle = self.hass.loop.call_later(KEEPALIVE, self._keep_alive)

    def _keep_alive(self) -> None:
        """Send a still picture again for a while: UDP can lose the one frame a still look or a held show sends."""
        self._keep_handle = None
        if not self.is_on or self._hw_last is None or self._identifying or self._scanning:
            return
        now = time.monotonic()
        if now - self._hw_changed > KEEP_FOR:
            return  # long enough: the light has it (and another app may take the light over)
        if now - self._hw_sent >= KEEPALIVE - 0.05:
            self._hw_frame(self._hw_last)  # schedules the next check
        else:
            self._keep_handle = self.hass.loop.call_later(KEEPALIVE, self._keep_alive)

    def _hw_close(self) -> None:
        self.lan.close()

    async def _hw_wait(self) -> None:
        """Wait until frames are accepted (a subclass that needs time to get ready)."""

    # --- health ----------------------------------------------------------
    def mark_seen(self) -> None:
        changed = self.online is not True
        self.online, self._misses, self.last_seen = True, 0, time.time()
        if changed:
            self._notify()

    def mark_missed(self, threshold: int) -> None:
        self._misses += 1
        if self._misses >= threshold and self.online is not False:
            self.online = False
            self._notify()

    # --- listeners -------------------------------------------------------
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def add_frame_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._frame_listeners.append(cb)
        return lambda: self._frame_listeners.remove(cb)

    def _set_frame(self, frame: list[Colour]) -> None:
        self.frame = frame
        self._wire = None  # something other than _send_frame is showing this: the next frame must go out
        for cb in list(self._frame_listeners):
            cb()

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # --- rendering -------------------------------------------------------
    def _base_frame(self, t: float) -> list[Colour]:
        if self.effect == PATTERN:
            base = None
            if patterns.light_effect(self.pattern_effect) != patterns.CHASE:  # (Chase must see no base: it looks the same)
                base = self._pattern_base()
            return patterns.render(self.pattern, self.pattern_effect, t, self.bulbs, self.entry_id, base=base)
        if self.effect in EFFECTS:
            return EFFECTS[self.effect](t, self.bulbs, self.colour)
        if self.custom:
            return [tuple(v * lvl // 255 for v in c) for c, lvl in zip(self.bulb_colour, self.bulb_level)]
        return [self.colour] * self.bulbs

    def _pattern_base(self) -> list[Colour]:
        """The pattern's colours along the string, worked out once (again when a user pattern changes)."""
        name = patterns.canonical(self.pattern) or patterns.DEFAULT_PATTERN
        key = (name, self.bulbs, self.entry_id, patterns.user_epoch())
        if self._base_key != key:
            self._base_key, self._base = key, patterns.base_colours(name, self.bulbs, self.entry_id)
        return self._base

    def _send_frame(self, frame: list[Colour], scaled: bool = True, fx: bool = False,
                    raw: tuple[bytes, int] | None = None) -> None:
        """Show a frame; `scaled` applies the brightness setting (a scan frame must not depend on it); `fx` is a
        frame of this light's own colour / effect / pattern, which turn on / off and look changes fade (sequencer,
        scan and zone frames are never faded here)."""
        if fx:
            frame = self._fade_frame(frame)
        if not self.segmented:
            self._send_whole(frame)
            return
        if scaled and self.brightness != 255:
            f = self.brightness / 255
            frame = [(int(r * f), int(g * f), int(b * f)) for r, g, b in frame]
        now = time.monotonic()
        wire = self._wire
        if wire is not None and now - wire[1] < RESEND_AFTER and wire[0] == frame:
            self._wire_raw = raw  # nothing changed: don't send it again (a periodic resend covers a dropped one)
            return
        self._hw_frame(frame)
        self._set_frame(frame)
        self._wire, self._wire_raw = (frame, now), raw

    def _fade_frame(self, frame: list[Colour]) -> list[Colour]:
        now = self._now()
        if self._xf is not None:
            if self._xf.done(now):
                self._xf = None
            else:
                frame = self._xf.apply(frame, now)
        self._last_raw = frame
        if self._ramp is not None:
            gain = self._ramp.level(now)
            if self._ramp.end >= 1 and self._ramp.done(now):
                self._ramp = None
            if gain < 1:
                frame = fade.scale_frame(frame, gain)
        return frame

    def _transitioning(self) -> bool:
        """A fade is still moving, so frames must keep coming even for a still look."""
        now = self._now()
        return (self._xf is not None and not self._xf.done(now)) or (self._ramp is not None and not self._ramp.done(now))

    def _fade_length(self) -> float:
        lengths = [x.dur for x in (self._xf, self._ramp) if x is not None]
        return max(lengths, default=0.0)

    def _clear_fx(self) -> None:
        """Something else took the picture (a show, a zone, a scan): drop fades and a pending fade-out."""
        self._ramp = self._xf = None
        self._last_raw = []
        self._cancel_off()

    def _cancel_off(self) -> float | None:
        """Cancel a fade-out in progress; the brightness gain it had reached, or None when none was running."""
        if self._off_handle is None:
            return None
        self._off_handle.cancel()
        self._off_handle = None
        return self._ramp.level(self._now()) if self._ramp is not None else 1.0

    def _send_whole(self, frame: list[Colour]) -> None:
        """One colour for the whole light: the average of the frame, at most WHOLE_LIGHT_FPS."""
        now = time.monotonic()
        wait = 1 / WHOLE_LIGHT_FPS - (now - self._whole_sent)
        if wait > 0:  # too soon: send the latest frame when allowed, so a still colour isn't lost
            self._whole_latest = frame
            if self._whole_handle is None:
                self._whole_handle = self.hass.loop.call_later(wait, self._send_whole_latest)
            return
        f = self.brightness / 255
        avg = tuple(int(sum(c[k] for c in frame) / max(1, len(frame)) * f) for k in range(3))
        colour, level = _split(avg)
        if (colour, level) != self._whole:
            if level == 0:
                self.lan.send(brightness_msg(1))
                self.lan.send(colour_msg((0, 0, 0)))
            else:
                if self._whole is None or self._whole[1] != level:
                    self.lan.send(brightness_msg(round(level * 100 / 255)))
                if self._whole is None or self._whole[0] != colour:
                    self.lan.send(colour_msg(colour))
            self._whole, self._whole_sent = (colour, level), now
        self._set_frame([avg] * self.bulbs)

    def _send_whole_latest(self) -> None:
        self._whole_handle = None
        self._send_whole(self._whole_latest)

    def _render(self) -> None:
        self._stop_animation()
        if not self.is_on or self.show_mode or self._zones or self._scanning:
            return  # in show mode the sequencer's frames are the picture; with a zone, the zone's; a scan, its own
        if self._is_animated() or self._transitioning():
            self._task = self.hass.async_create_background_task(self._animate(), "light_show_studio effect")
        else:
            self._send_frame(self._base_frame(0), fx=True)

    @callback
    def refresh(self) -> None:
        """Draw the look again (a pattern it uses was edited)."""
        self._render()

    def _is_animated(self) -> bool:
        return self.effect in ANIMATED or (self.effect == PATTERN and patterns.is_animated(self.pattern_effect))

    @callback
    def resume_after_restart(self, delay: float = 3.0) -> None:
        """After Home Assistant restarts, start a moving effect again (it froze when the old process stopped).

        Only moving effects: a still colour or a show is left alone, so nothing else (an xLights show, another
        integration driving the same light) is overridden at startup."""
        def _go() -> None:
            self._resume_handle = None
            if self.is_on and not self.show_mode and not self._zones and not self._identifying and not self._scanning \
                    and self._task is None and self._is_animated():
                self._render()
        if self._resume_handle is None:
            self._resume_handle = self.hass.loop.call_later(delay, _go)

    async def _animate(self) -> None:
        # Effect time runs at the speed setting; accumulating it lets the speed change smoothly mid-effect.
        # Re-rendering the same effect (or changing a Pattern's colours or motion) keeps the clock running, so
        # nothing jumps back to the start.
        # a light already running this look sets the time (strings started together move as one); otherwise this
        # light's own clock carries on, so re-rendering the same effect doesn't jump back to the start
        peer = self._peer_clock()
        t = peer if peer is not None else (self._fx_t if self.effect == self._fx_effect else 0.0)
        self._fx_effect = self.effect
        step = 1 / FPS
        last = time.monotonic()
        due = math.floor(last / step) * step  # every light ticks on the same beat, so their frames go out together
        while True:
            now = time.monotonic()
            t += min(now - last, 0.25) * patterns.speed_factor(self.speed)  # a long stall doesn't leap ahead
            last = now
            self._fx_t = t
            self._send_frame(self._base_frame(t), fx=True)
            if not self._is_animated():
                if not self._transitioning():  # a still look has finished fading in / blending: nothing more to send
                    if self._task is asyncio.current_task():
                        self._task = None
                    return
                step = fade.tick_interval(self._fade_length(), 1 / FPS)  # a long ramp doesn't need 25 frames a second
            else:
                step = 1 / FPS
            # frames on a fixed beat: sleep to the next due time, not a fixed gap after the work (which drifts
            # and makes the motion uneven); if we fell behind, start the beat again from now
            due += step
            wait = due - time.monotonic()
            if wait < -step:
                due = math.floor(time.monotonic() / step) * step
                wait = 0.0
            await asyncio.sleep(max(0.0, wait))

    def _peer_clock(self) -> float | None:
        """Where another light running the same look has got to, so strings started together stay in step
        (a chase across several strings moves as one); None when none is."""
        try:
            entries = self.hass.config_entries.async_loaded_entries(DOMAIN)
        except AttributeError:
            return None
        look = (self.effect, self.pattern, self.pattern_effect, self.speed)
        for entry in entries:
            peer = getattr(entry, "runtime_data", None)
            if (peer is not self and isinstance(peer, C9Controller) and peer._task is not None
                    and (peer.effect, peer.pattern, peer.pattern_effect, peer.speed) == look
                    and peer._fx_effect == peer.effect):
                return peer._fx_t
        return None

    def _stop_animation(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    @callback
    def _schedule_flush(self) -> None:
        if self._flush_handle is None:
            self._flush_handle = self.hass.loop.call_later(FLUSH_DELAY, self._flush)

    @callback
    def _flush(self) -> None:
        self._flush_handle = None
        self._render()
        self._notify()

    def _seed_bulbs(self) -> None:
        """Entering per-bulb mode: start from what the string shows now."""
        if not self.custom:
            for i, c in enumerate(self._base_frame(0)):
                self.bulb_colour[i], self.bulb_level[i] = _split(c)
            self.custom = True
            self.effect = None
            self.show_mode = False

    # --- whole-string commands ------------------------------------------
    async def async_turn_on(self, brightness: int | None = None, colour: Colour | None = None,
                            effect: str | None = None, transition: float | None = None) -> None:
        """Turn on / change the look. `transition` (seconds, None = the default_transition option, 0 = instant):
        turning on fades in, and a new colour or effect cross-fades from the old picture."""
        dur = self.default_transition if transition is None else fade.clean_seconds(transition)
        resumed = self._cancel_off()  # turning on while a fade-out runs brings the light back up from where it got to
        # the picture to blend from, when this is a change of look on a light showing its own look
        blend_from = list(self._last_raw) if (self.is_on and dur > 0 and self._last_raw and not self.show_mode
                                              and not self._zones and not self._scanning) else None
        powered = False
        if not self.is_on:
            self._hw_power(True)
            self._realtime = False  # the light may leave realtime mode while off
            self._hw_last = None  # nothing for the keep-alive to put back
            self._whole = None
            self._wire = None
            self.is_on = True
            powered = True
            await asyncio.sleep(0.3)
        if self._scanning:
            if colour is None and effect is None:  # brightness, or a plain "on": the scan carries on, at its own level
                if brightness is not None:
                    self.brightness = brightness
                self._notify()
                return
            self.release_scan()  # a colour or effect was picked: the light is the person's again
        if self._zones:
            if colour is None and effect is None:  # brightness, or a plain "on": the zone keeps the picture
                if brightness is not None:
                    self.brightness = brightness
                    self._resend_zone()
                self._notify()
                return
            self.release_zone()  # a colour or effect was picked: the light is the person's again
        # a plain turn_on during a show nobody is feeding would draw nothing: leave show mode
        stale = time.monotonic() - self._ext_seen > 2  # frames still arriving (even while off) = keep the show
        if self.show_mode and colour is None and effect is None and stale:
            self.show_mode = False
        if brightness is not None:
            self.brightness = brightness
        if colour is not None:
            self.colour, self.effect, self.custom, self.show_mode = colour, None, False, False
        if effect == SHOW:
            self.enable_show()
        elif effect is not None:
            valid = effect in EFFECTS or effect == PATTERN
            self.effect, self.custom, self.show_mode = (effect if valid else None), False, False
        now = self._now()
        if powered and dur > 0 and not self.show_mode:
            self._ramp = fade.Ramp(0.0, 1.0, dur, now)  # fade in from black
        elif resumed is not None:
            self._ramp = fade.Ramp(resumed, 1.0, dur, now) if dur > 0 and resumed < 1 else None
        if blend_from is not None and (colour is not None or effect is not None) and not self.show_mode:
            self._xf = fade.Xfade(blend_from, dur, now)  # the old picture dissolves into the new look
        if colour is None and effect is None and self._task is not None and not self._task.done() \
                and resumed is None:
            self._notify()  # brightness only, or "on" again: the running effect picks it up next frame; don't restart it
            return
        self._render()
        self._notify()

    async def async_set_pattern(self, pattern: str | None = None, effect: str | None = None,
                                speed: int | None = None, brightness: int | None = None,
                                transition: float | None = None) -> None:
        """Pick a pattern (a built-in or one of yours) and/or how it moves (turning the light on to show it),
        and/or the speed. Speed alone just changes the speed of whatever effect is running.

        A map effect (Sweep, Ripple ...) needs positions, so on one light it becomes its nearest string effect."""
        if pattern is not None:
            self.pattern = patterns.canonical(pattern) or patterns.DEFAULT_PATTERN
        if effect is not None:
            self.pattern_effect = patterns.light_effect(effect)
        if speed is not None:
            self.speed = max(1, min(100, int(speed)))
        if pattern is None and effect is None:
            self._notify()  # the running animation picks the new speed up on its next frame
            return
        await self.async_turn_on(brightness=brightness, effect=PATTERN, transition=transition)

    async def async_turn_off(self, transition: float | None = None) -> None:
        """Turn off. With a transition (None = the default_transition option) the light fades to black first and
        is switched off at the end; a person turning it on again meanwhile brings it back up."""
        dur = self.default_transition if transition is None else fade.clean_seconds(transition)
        self._cancel_off()
        if dur > 0 and self.is_on and not self.show_mode and not self._zones and not self._scanning \
                and not self._identifying:
            now = self._now()
            current = self._ramp.level(now) if self._ramp is not None else 1.0
            self._ramp, self._xf = fade.Ramp(current, 0.0, dur, now), None
            if self._task is None or self._task.done():
                self._render()
            self._off_handle = self.hass.loop.call_later(dur + 0.02, self._finish_off)
            self._notify()
            return
        self._hard_off()
        self._notify()

    @callback
    def _finish_off(self) -> None:
        self._off_handle = None
        self._hard_off()
        self._notify()

    def _hard_off(self) -> None:
        self.release_scan()
        self.release_zone()
        self._stop_animation()
        self._clear_fx()
        if self._whole_handle:
            self._whole_handle.cancel()
            self._whole_handle = None
        self._hw_power(False)
        self.is_on = False
        self._realtime = False
        self._hw_last = None  # nothing for the keep-alive to put back
        self._set_frame([(0, 0, 0)] * self.bulbs)

    async def async_set_pixels(self, colours: list[Colour], repeat: bool = True) -> None:
        self.release_scan()
        self.release_zone()
        self._seed_bulbs()
        for i in range(self.bulbs):
            if i < len(colours) or (repeat and colours):
                self.bulb_colour[i], self.bulb_level[i] = _split(colours[i % len(colours)])
        if not self.is_on:
            await self.async_turn_on()
        else:
            self._render()
            self._notify()

    # --- single bulbs ----------------------------------------------------
    async def async_set_bulb(self, index: int, on: bool, colour: Colour | None = None,
                             level: int | None = None) -> None:
        self.release_scan()
        self.release_zone()
        self._seed_bulbs()
        if colour is not None:
            self.bulb_colour[index] = colour
        if not on:
            self.bulb_level[index] = 0
        elif level is not None:
            self.bulb_level[index] = level
        elif self.bulb_level[index] == 0:
            self.bulb_level[index] = 255
        if on and not self.is_on:
            await self.async_turn_on()
            return
        self._schedule_flush()

    # --- music sync (see musicmanager): a music session holds the light like a zone ------------
    @property
    def music(self) -> dict | None:
        """{"active", "effect", "pattern", "sensitivity", "live"} while a music session has this light, else None."""
        if self._music and any(z.startswith("music:") for z in self._zones):
            return self._music
        return None

    @callback
    def set_music(self, info: dict | None) -> None:
        if info != self._music:
            self._music = info
            self._notify()

    # --- zones: a pattern spread over runs of this light and other lights --------------
    @property
    def zone(self) -> str | None:
        """Name of the zone(s) showing on this light, or None."""
        return ", ".join(name for name, _ in self._zones.values()) or None

    async def async_claim_zone(self, zone_id: str, name: str) -> None:
        """A zone takes the light: it is turned on if needed, and the zone's frames become the picture."""
        if self._scanning:
            raise RuntimeError("the light is being scanned")
        was_on = self.is_on
        if not was_on:
            await self.async_turn_on(transition=0)
        if not self._zones:
            self._zone_prev_show, self._zone_powered = self.show_mode, not was_on
            self._stop_animation()
            self._clear_fx()
            self.show_mode = False
        if zone_id in self._zones:
            self._zones[zone_id] = (name, self._zones[zone_id][1])
        else:
            self._zones[zone_id] = (name, [])
        self._notify()

    def push_zone_frame(self, zone_id: str, slices: list[tuple[int, list[Colour]]]) -> bool:
        """Show a zone's colours for the runs it has on this light: [(first pixel, colours)]. Pixels no zone
        covers are black. False when the zone no longer has the light (a person took it, or it was turned off)."""
        cur = self._zones.get(zone_id)
        if cur is None or not self.is_on:
            return False
        self._zones[zone_id] = (cur[0], slices)
        if self._identifying or self._scanning:
            return True
        if len(self._zones) == 1:  # nothing to merge with: show it now (a painted pixel appears at once)
            self._send_frame(self._zone_frame())
        elif not self._zone_dirty:
            self._zone_dirty = True  # several zones on this light push in the same loop turn: one send for all
            self.hass.loop.call_soon(self._send_zone)
        return True

    def _send_zone(self) -> None:
        if self._zone_dirty:
            self._zone_dirty = False
            if self._zones and self.is_on and not self._identifying and not self._scanning:
                self._send_frame(self._zone_frame())

    def _zone_frame(self) -> list[Colour]:
        frame: list[Colour] = [(0, 0, 0)] * self.bulbs
        for _, slices in self._zones.values():
            for first, colours in slices:
                end = min(first + len(colours), self.bulbs)
                if 0 <= first < end:
                    frame[first:end] = colours[: end - first]
        return frame

    def _resend_zone(self) -> None:
        if self._zones and self.is_on and not self._identifying and not self._scanning:
            self._send_frame(self._zone_frame())

    @callback
    def release_zone(self, zone_id: str | None = None, restore: bool = False) -> None:
        """Let go of one zone (or all). restore: the zone was stopped, so put back what the light showed before
        (or turn it off if the zone turned it on); otherwise a person or a show is taking over."""
        gone = [zone_id] if zone_id is not None else list(self._zones)
        if not [z for z in gone if self._zones.pop(z, None) is not None]:
            return
        if self._zones:
            self._resend_zone()  # the other zones carry on
        elif restore:
            self.show_mode = self._zone_prev_show
            if self._zone_powered:
                self.hass.async_create_background_task(self.async_turn_off(), "light_show_studio zone off")
            else:
                self._render()
        self._notify()

    # --- camera scan: the light shows code frames so a phone can find each pixel --------
    @property
    def scanning(self) -> bool:
        return self._scanning

    async def async_claim_scan(self) -> None:
        """The scan takes the light: it is turned on if needed, any effect or zone on it stops, sequencer frames
        are ignored. release_scan(restore=True) puts back what it showed before."""
        if self._scanning:
            return
        was_on = self.is_on
        if not was_on:
            await self.async_turn_on(transition=0)
        if self._scanning:  # another scan started while the light was turning on
            return
        if self._zones:  # a zone had it: what it showed before the zone is what to go back to
            prev_show, powered = self._zone_prev_show, self._zone_powered
        else:
            prev_show, powered = self.show_mode, not was_on
        self._scan_before = list(self.frame)
        self._stop_animation()
        self._clear_fx()
        self.release_zone()
        self._scan_prev_show, self._scan_powered, self._scanning = prev_show, powered, True
        self.show_mode = False
        self._notify()

    def push_scan_frame(self, frame: list[Colour]) -> bool:
        """Show one code frame, at exactly the colours given (not dimmed by the brightness setting). Lights take
        frames over UDP, so the same frame is sent again after 50 and 150 ms (the last one wins; a lost or late
        packet can't leave the previous code showing). False when the light isn't being scanned any more (a
        person took it, or it was turned off)."""
        if not self._scanning or not self.is_on:
            return False
        self._cancel_scan_resend()
        self._send_frame(frame, scaled=False)
        if self.segmented:
            self._scan_resend = [self.hass.loop.call_later(d, self._scan_resend_frame, frame) for d in SCAN_RESEND]
        return True

    def _scan_resend_frame(self, frame: list[Colour]) -> None:
        if self._scanning and self.is_on:
            self._hw_frame(frame)

    def _cancel_scan_resend(self) -> None:
        for handle in self._scan_resend:
            handle.cancel()
        self._scan_resend = []

    @callback
    def release_scan(self, restore: bool = False) -> None:
        """End the scan. restore: put back what the light showed before (or turn it off if the scan turned
        it on); otherwise a person or a show is taking over and brings its own picture."""
        if not self._scanning:
            return
        self._scanning = False
        self._cancel_scan_resend()
        if restore:
            self.show_mode = self._scan_prev_show
            before, self._scan_before = self._scan_before, []
            if self._scan_powered:
                self.hass.async_create_background_task(self.async_turn_off(), "light_show_studio scan off")
                return
            self._whole = None
            self._realtime = False
            self._hw_last = None  # nothing for the keep-alive to put back
            if self._zones:
                self._resend_zone()
            elif self.show_mode:  # the sequencer isn't sending: put the last picture back until it does
                if self.segmented:
                    self._hw_frame(before)
                    self._set_frame(before)
                self._ext_sent = 0.0
            else:
                self._render()
        self._notify()

    # --- frames from a sequencer (xLights / Light-O-Rama) ----------------
    @callback
    def enable_show(self) -> None:
        self.release_scan()
        self.release_zone()
        self._stop_animation()
        self._clear_fx()
        if not self.is_on:
            self._hw_power(True)
            self.is_on = True
            self._realtime = False
            self._hw_last = None  # nothing for the keep-alive to put back
            self._whole = None
            self._wire = None
        self.show_mode, self.effect, self.custom = True, None, False
        self._ext_sent = 0.0
        self._notify()

    @callback
    def show_external(self, rgb: bytes) -> None:
        now = time.monotonic()
        self._ext_seen = now
        if self._identifying or self._scanning:
            return
        if not self.show_mode or not self.is_on:
            return  # an effect or colour was picked, or the light was turned off; ignore the sequencer
        # the gap is half a 40 fps frame: 20 ms shows, and 25 ms shows running 25% fast to catch up with a speaker,
        # pass untouched even when a frame is a little late; only real bursts (live xLights over Wi-Fi) are merged
        wait = 0.5 / EXTERNAL_MAX_FPS - (now - self._ext_sent)
        if wait > 0:  # too soon after the last: keep the newest and send it when due, never drop it
            self._ext_latest = bytes(rgb)
            if self._ext_handle is None:
                self._ext_handle = self.hass.loop.call_later(wait, self._ext_flush)
            return
        if self._ext_handle is not None:
            self._ext_handle.cancel()
            self._ext_handle = None
        self._ext_latest = None
        if now - self._ext_sent > 2:  # a new show (or the first frame after a pause)
            self._stop_animation()
            self._realtime = False  # re-arm realtime mode in case the string dropped it
            self._hw_last = None  # nothing for the keep-alive to put back
            self._whole = None
            self._wire = None
            self.effect, self.custom = None, False
            self._notify()
        self._ext_sent = now
        n = self.bulbs * 3
        raw = (bytes(rgb[:n]), self.brightness)
        wire = self._wire
        if self.segmented and wire is not None and self._wire_raw == raw and now - wire[1] < RESEND_AFTER:
            return  # the same bytes as the frame on the wire: don't even build the frame
        data = raw[0].ljust(n, b"\0")
        self._send_frame(list(zip(data[0::3], data[1::3], data[2::3])), raw=raw)

    def _ext_flush(self) -> None:
        self._ext_handle = None
        rgb, self._ext_latest = self._ext_latest, None
        if rgb is not None:
            self.show_external(rgb)

    def shutdown(self) -> None:
        self._zones.clear()
        self._scanning = False
        self._cancel_scan_resend()
        self._stop_animation()
        if self._identify_task:  # don't let a flash wake a light that's being removed
            self._identify_task.cancel()
        for handle in (self._flush_handle, self._whole_handle, self._resume_handle, self._off_handle,
                       self._keep_handle, self._ext_handle):
            if handle:
                handle.cancel()
        self._hw_close()

    # --- identify ----------------------------------------------------------
    @callback
    def start_identify(self) -> None:
        if not self._identifying:
            self._identify_task = self.hass.async_create_background_task(
                self.async_identify(), "light_show_studio identify")

    async def async_identify(self) -> None:
        """Flash the whole light white three times, then put it back as it was."""
        if self._identifying or self._scanning:
            return
        self._identifying = True
        was_on, was_show, before = self.is_on, self.show_mode, list(self.frame)
        try:
            self._stop_animation()
            if self._whole_handle:
                self._whole_handle.cancel()
                self._whole_handle = None
            if not was_on:
                self._hw_power(True)
                self._realtime = False
                self._hw_last = None  # nothing for the keep-alive to put back
                await asyncio.sleep(0.3)
            await self._hw_wait()
            for _ in range(3):
                self._flash((255, 255, 255))
                await asyncio.sleep(0.4)
                self._flash((0, 0, 0))
                await asyncio.sleep(0.3)
        finally:
            self._whole = None
            self._wire = None
            if not was_on:
                self._hw_power(False)
                self._realtime = False
                self._hw_last = None  # nothing for the keep-alive to put back
                self._set_frame([(0, 0, 0)] * self.bulbs)
            elif self._zones:
                self._resend_zone()
            elif was_show:
                if self.segmented:
                    self._hw_frame(before)
                    self._set_frame(before)
                else:
                    self._send_whole(before)
                self._ext_sent = 0.0
            else:
                self._render()
            self._identifying = False

    def _flash(self, colour: Colour) -> None:
        if self.segmented:
            self._hw_frame([colour] * self.bulbs)
        else:
            self.lan.send(brightness_msg(100))
            self.lan.send(colour_msg(colour))
