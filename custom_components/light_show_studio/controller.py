"""Holds the string's state and turns it into frames on the wire."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, EXTERNAL_MAX_FPS, FLUSH_DELAY, FPS, WHOLE_LIGHT_FPS
from .effects import ANIMATED, EFFECTS
from . import patterns
from .patterns import PATTERN
from .hosts import valid_ipv4
from .protocol import Colour, GoveeLan, brightness_msg, colour_msg, colours_msg, realtime_msg, turn_msg

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
        # zones (see mapmanager): zone id -> (name, [(first pixel, colours)]) for the runs of it on this light
        self._zones: dict[str, tuple[str, list[tuple[int, list[Colour]]]]] = {}
        self._zone_prev_show = False  # show mode before the first zone took over
        self._zone_powered = False  # the first zone turned the light on

    # --- hardware hooks (a subclass for another brand overrides these) ----
    def _make_link(self, host: str) -> GoveeLan | None:
        return GoveeLan(host)

    def _hw_power(self, on: bool) -> None:
        self.lan.send(turn_msg(on))

    def _hw_frame(self, frame: list[Colour]) -> None:
        """Show a per-bulb frame that is already scaled by the brightness."""
        if not self._realtime:
            self.lan.send(brightness_msg(100))  # the string's own dimmer would dim every frame
            self.lan.send(realtime_msg(True))
            self._realtime = True
        self.lan.send(colours_msg(frame))

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
        for cb in list(self._frame_listeners):
            cb()

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # --- rendering -------------------------------------------------------
    def _base_frame(self, t: float) -> list[Colour]:
        if self.effect == PATTERN:
            return patterns.render(self.pattern, self.pattern_effect, t, self.bulbs)
        if self.effect in EFFECTS:
            return EFFECTS[self.effect](t, self.bulbs, self.colour)
        if self.custom:
            return [tuple(v * lvl // 255 for v in c) for c, lvl in zip(self.bulb_colour, self.bulb_level)]
        return [self.colour] * self.bulbs

    def _send_frame(self, frame: list[Colour]) -> None:
        if not self.segmented:
            self._send_whole(frame)
            return
        f = self.brightness / 255
        frame = [(int(r * f), int(g * f), int(b * f)) for r, g, b in frame]
        self._hw_frame(frame)
        self._set_frame(frame)

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
        if not self.is_on or self.show_mode or self._zones:
            return  # in show mode the sequencer's frames are the picture; with a zone, the zone's
        if self.effect in ANIMATED or (self.effect == PATTERN and patterns.is_animated(self.pattern_effect)):
            self._task = self.hass.async_create_background_task(self._animate(), "light_show_studio effect")
        else:
            self._send_frame(self._base_frame(0))

    async def _animate(self) -> None:
        # effect time runs at the speed setting; accumulating it lets the speed change smoothly mid-effect
        # staying on Pattern (new colours or motion) keeps the clock running, so nothing jumps
        t = self._fx_t if self.effect == PATTERN and self._fx_effect == PATTERN else 0.0
        self._fx_effect, last = self.effect, time.monotonic()
        while True:
            now = time.monotonic()
            t += (now - last) * patterns.speed_factor(self.speed)
            last = now
            self._fx_t = t
            self._send_frame(self._base_frame(t))
            await asyncio.sleep(1 / FPS)

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
                            effect: str | None = None) -> None:
        if not self.is_on:
            self._hw_power(True)
            self._realtime = False  # the light may leave realtime mode while off
            self._whole = None
            self.is_on = True
            await asyncio.sleep(0.3)
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
        self._render()
        self._notify()

    async def async_set_pattern(self, pattern: str | None = None, effect: str | None = None,
                                speed: int | None = None) -> None:
        """Pick a pattern and/or how it moves (turning the light on to show it), and/or the speed.

        Speed alone just changes the speed of whatever effect is running."""
        if pattern is not None:
            self.pattern = pattern if pattern in patterns.PATTERNS else patterns.DEFAULT_PATTERN
        if effect is not None:
            self.pattern_effect = effect if effect in patterns.PATTERN_EFFECTS else patterns.DEFAULT_EFFECT
        if speed is not None:
            self.speed = max(1, min(100, int(speed)))
        if pattern is None and effect is None:
            self._notify()  # the running animation picks the new speed up on its next frame
            return
        await self.async_turn_on(effect=PATTERN)

    async def async_turn_off(self) -> None:
        self.release_zone()
        self._stop_animation()
        if self._whole_handle:
            self._whole_handle.cancel()
            self._whole_handle = None
        self._hw_power(False)
        self.is_on = False
        self._realtime = False
        self._set_frame([(0, 0, 0)] * self.bulbs)
        self._notify()

    async def async_set_pixels(self, colours: list[Colour], repeat: bool = True) -> None:
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

    # --- zones: a pattern spread over runs of this light and other lights --------------
    @property
    def zone(self) -> str | None:
        """Name of the zone(s) showing on this light, or None."""
        return ", ".join(name for name, _ in self._zones.values()) or None

    async def async_claim_zone(self, zone_id: str, name: str) -> None:
        """A zone takes the light: it is turned on if needed, and the zone's frames become the picture."""
        was_on = self.is_on
        if not was_on:
            await self.async_turn_on()
        if not self._zones:
            self._zone_prev_show, self._zone_powered = self.show_mode, not was_on
            self._stop_animation()
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
        if not self._identifying:
            self._send_frame(self._zone_frame())
        return True

    def _zone_frame(self) -> list[Colour]:
        frame: list[Colour] = [(0, 0, 0)] * self.bulbs
        for _, slices in self._zones.values():
            for first, colours in slices:
                end = min(first + len(colours), self.bulbs)
                if 0 <= first < end:
                    frame[first:end] = colours[: end - first]
        return frame

    def _resend_zone(self) -> None:
        if self._zones and self.is_on and not self._identifying:
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

    # --- frames from a sequencer (xLights / Light-O-Rama) ----------------
    @callback
    def enable_show(self) -> None:
        self.release_zone()
        self._stop_animation()
        if not self.is_on:
            self._hw_power(True)
            self.is_on = True
            self._realtime = False
            self._whole = None
        self.show_mode, self.effect, self.custom = True, None, False
        self._ext_sent = 0.0
        self._notify()

    @callback
    def show_external(self, rgb: bytes) -> None:
        now = time.monotonic()
        self._ext_seen = now
        if self._identifying:
            return
        if not self.show_mode or not self.is_on:
            return  # an effect or colour was picked, or the light was turned off; ignore the sequencer
        if now - self._ext_sent < 0.8 / EXTERNAL_MAX_FPS:  # slack for jitter, or 25 ms shows lose every other frame
            return
        if now - self._ext_sent > 2:  # a new show (or the first frame after a pause)
            self._stop_animation()
            self._realtime = False  # re-arm realtime mode in case the string dropped it
            self._whole = None
            self.effect, self.custom = None, False
            self._notify()
        self._ext_sent = now
        self._send_frame([tuple(rgb[i : i + 3]) for i in range(0, self.bulbs * 3, 3)])

    def shutdown(self) -> None:
        self._zones.clear()
        self._stop_animation()
        if self._identify_task:  # don't let a flash wake a light that's being removed
            self._identify_task.cancel()
        for handle in (self._flush_handle, self._whole_handle):
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
        if self._identifying:
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
                await asyncio.sleep(0.3)
            await self._hw_wait()
            for _ in range(3):
                self._flash((255, 255, 255))
                await asyncio.sleep(0.4)
                self._flash((0, 0, 0))
                await asyncio.sleep(0.3)
        finally:
            self._whole = None
            if not was_on:
                self._hw_power(False)
                self._realtime = False
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
