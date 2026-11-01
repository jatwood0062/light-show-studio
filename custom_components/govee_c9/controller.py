"""Holds the string's state and turns it into frames on the wire."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from homeassistant.core import HomeAssistant, callback

from .const import EXTERNAL_MAX_FPS, FLUSH_DELAY, FPS
from .effects import ANIMATED, EFFECTS
from .protocol import Colour, GoveeLan, brightness_msg, colours_msg, realtime_msg, turn_msg

SHOW = "Light show"  # effect name: let xLights / EZPlayer / play_sequence drive the string


def _split(c: Colour) -> tuple[Colour, int]:
    """Colour -> (full-brightness colour, level 0-255)."""
    m = max(c)
    if m == 0:
        return (255, 255, 255), 0
    return (c[0] * 255 // m, c[1] * 255 // m, c[2] * 255 // m), m


class C9Controller:
    def __init__(self, hass: HomeAssistant, host: str, bulbs: int) -> None:
        self.hass = hass
        self.lan = GoveeLan(host)
        self.bulbs = bulbs
        self.is_on = False
        self.brightness = 255
        self.colour: Colour = (255, 150, 40)
        self.effect: str | None = None
        self.show_mode = True  # accept sequencer frames; picking a colour or effect turns it off
        self.custom = False  # True once single bulbs have been set
        self.bulb_colour: list[Colour] = [self.colour] * bulbs
        self.bulb_level: list[int] = [255] * bulbs
        self._realtime = False
        self._task: asyncio.Task | None = None
        self._flush_handle: asyncio.TimerHandle | None = None
        self._listeners: list[Callable[[], None]] = []
        self._ext_sent = 0.0

    # --- listeners -------------------------------------------------------
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # --- rendering -------------------------------------------------------
    def _base_frame(self, t: float) -> list[Colour]:
        if self.effect in EFFECTS:
            return EFFECTS[self.effect](t, self.bulbs, self.colour)
        if self.custom:
            return [tuple(v * lvl // 255 for v in c) for c, lvl in zip(self.bulb_colour, self.bulb_level)]
        return [self.colour] * self.bulbs

    def _send_frame(self, frame: list[Colour]) -> None:
        if not self._realtime:
            self.lan.send(brightness_msg(100))  # the string's own dimmer would dim every frame
            self.lan.send(realtime_msg(True))
            self._realtime = True
        f = self.brightness / 255
        self.lan.send(colours_msg([(int(r * f), int(g * f), int(b * f)) for r, g, b in frame]))

    def _render(self) -> None:
        self._stop_animation()
        if not self.is_on or self.show_mode:
            return  # in show mode the sequencer's frames are the picture
        if self.effect in ANIMATED:
            self._task = self.hass.async_create_background_task(self._animate(), "govee_c9 effect")
        else:
            self._send_frame(self._base_frame(0))

    async def _animate(self) -> None:
        start = time.monotonic()
        while True:
            self._send_frame(self._base_frame(time.monotonic() - start))
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
            self.lan.send(turn_msg(True))
            self._realtime = False  # the light may leave realtime mode while off
            self.is_on = True
            await asyncio.sleep(0.3)
        if brightness is not None:
            self.brightness = brightness
        if colour is not None:
            self.colour, self.effect, self.custom, self.show_mode = colour, None, False, False
        if effect == SHOW:
            self.enable_show()
        elif effect is not None:
            self.effect, self.custom, self.show_mode = (effect if effect in EFFECTS else None), False, False
        self._render()
        self._notify()

    async def async_turn_off(self) -> None:
        self._stop_animation()
        self.lan.send(turn_msg(False))
        self.is_on = False
        self._realtime = False
        self._notify()

    async def async_set_pixels(self, colours: list[Colour], repeat: bool = True) -> None:
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

    # --- frames from a sequencer (xLights / Light-O-Rama) ----------------
    @callback
    def enable_show(self) -> None:
        self._stop_animation()
        self.show_mode, self.effect, self.custom = True, None, False
        self._ext_sent = 0.0
        self._notify()

    @callback
    def show_external(self, rgb: bytes) -> None:
        if not self.show_mode:
            return  # an effect or colour was picked; ignore the sequencer
        now = time.monotonic()
        if now - self._ext_sent < 0.8 / EXTERNAL_MAX_FPS:  # slack for jitter, or 25 ms shows lose every other frame
            return
        if now - self._ext_sent > 2:  # a new show (or the first frame after a pause)
            self._stop_animation()
            if not self.is_on:
                self.lan.send(turn_msg(True))
                self.is_on = True
            self._realtime = False  # re-arm realtime mode in case the string dropped it
            self.effect, self.custom = None, False
            self._notify()
        self._ext_sent = now
        self._send_frame([tuple(rgb[i : i + 3]) for i in range(0, self.bulbs * 3, 3)])

    def shutdown(self) -> None:
        self._stop_animation()
        if self._flush_handle:
            self._flush_handle.cancel()
        self.lan.close()
