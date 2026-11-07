"""The whole string as one light (with effects), plus one light per bulb."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import CONF_HOST, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import C9ConfigEntry
from .const import CONF_BULB_ENTITIES, CONF_DEVICE_ID, CONF_SKU, CONF_START_PIXEL, CONF_TYPE, DOMAIN, TYPE_TWINKLY, TYPE_WLED
from .controller import SHOW, C9Controller
from .effects import EFFECT_LIST, EFFECTS
from . import fade, patterns
from .patterns import PATTERN
from .protocol import Colour


MAX_COLOURS = 1020  # a string never has more bulbs


def _colour(value: Any) -> Colour:
    try:
        if isinstance(value, str):
            v = value.lstrip("#")
            if len(v) != 6:
                raise vol.Invalid("expected #rrggbb")
            return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return tuple(max(0, min(255, int(x))) for x in value)
    except (ValueError, TypeError, OverflowError) as err:
        raise vol.Invalid("expected #rrggbb or [r, g, b]") from err
    raise vol.Invalid("expected #rrggbb or [r, g, b]")


SET_PIXELS_SCHEMA = {
    vol.Required("colors"): vol.All(cv.ensure_list, vol.Length(min=1, max=MAX_COLOURS), [_colour]),
    vol.Optional("repeat", default=True): cv.boolean,
}


SET_PATTERN_SCHEMA = {
    vol.Optional("pattern"): vol.All(cv.string, patterns.valid_pattern),  # a built-in pattern or one of your own
    vol.Optional("effect"): vol.All(cv.string, patterns.valid_effect),  # on one light a map effect shows as its nearest string effect
    vol.Optional("speed"): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    vol.Optional("transition"): vol.All(vol.Coerce(float), vol.Range(min=0, max=fade.MAX_TRANSITION)),
}


async def async_setup_entry(hass: HomeAssistant, entry: C9ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    ctl = entry.runtime_data
    twinkly = entry.data.get(CONF_TYPE) == TYPE_TWINKLY
    wled = entry.data.get(CONF_TYPE) == TYPE_WLED
    uid = entry.unique_id or entry.data[CONF_HOST]  # stays put when the IP changes
    device = DeviceInfo(identifiers={(DOMAIN, uid)}, name=entry.title,
                        manufacturer="Twinkly" if twinkly else "WLED" if wled else "Govee",
                        model=entry.data.get(CONF_SKU) or ("Twinkly light" if twinkly else "WLED" if wled else "LAN light"))
    if (twinkly or wled) and entry.data.get(CONF_DEVICE_ID):  # a MAC: lets Home Assistant match the device
        device["connections"] = {(CONNECTION_NETWORK_MAC, format_mac(entry.data[CONF_DEVICE_ID]))}
    entities: list[LightEntity] = [C9StringLight(ctl, entry, device, uid)]
    if entry.options.get(CONF_BULB_ENTITIES, entry.data.get(CONF_BULB_ENTITIES, not (twinkly or wled))):
        entities += [C9BulbLight(ctl, device, uid, i) for i in range(ctl.bulbs)]
    async_add_entities(entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("set_pixels", SET_PIXELS_SCHEMA, "async_set_pixels")
    platform.async_register_entity_service("set_pattern", SET_PATTERN_SCHEMA, "async_set_pattern")


class _Base(LightEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = True
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, ctl: C9Controller, device: DeviceInfo) -> None:
        self.ctl = ctl
        self._attr_device_info = device

    @property
    def available(self) -> bool:
        return self.ctl.online is not False

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.ctl.add_listener(self.async_write_ha_state))


class C9StringLight(_Base, RestoreEntity):
    _attr_name = None
    _attr_supported_features = LightEntityFeature.EFFECT | LightEntityFeature.TRANSITION
    _attr_effect_list = [SHOW, PATTERN, *EFFECT_LIST]

    def __init__(self, ctl, entry, device, uid: str) -> None:
        super().__init__(ctl, device)
        self._attr_unique_id = f"{uid}_string"
        self._start = entry.options.get(CONF_START_PIXEL, entry.data.get(CONF_START_PIXEL, 1))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is None or last.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        ctl, attrs = self.ctl, last.attributes  # restore the controller's idea of the light; send nothing
        ctl.is_on = last.state == STATE_ON
        if (level := attrs.get(ATTR_BRIGHTNESS)) is not None:
            ctl.brightness = int(level)
        if (rgb := attrs.get(ATTR_RGB_COLOR)) is not None:
            ctl.colour = tuple(rgb)
        if (name := patterns.canonical(attrs.get("pattern"))) is not None:  # a built-in or one of your own
            ctl.pattern = name
        if attrs.get("pattern_effect") in patterns.PATTERN_EFFECTS:
            ctl.pattern_effect = attrs["pattern_effect"]
        try:
            ctl.speed = max(1, min(100, int(attrs["speed"])))
        except (KeyError, TypeError, ValueError):
            pass
        if (effect := attrs.get(ATTR_EFFECT)) == SHOW:
            ctl.show_mode = True
        elif effect in EFFECTS or effect == PATTERN:
            ctl.effect, ctl.show_mode = effect, False
        elif ctl.is_on:  # on with a plain colour
            ctl.show_mode = False
        ctl.resume_after_restart()  # a moving effect froze when Home Assistant stopped: start it again

    @property
    def is_on(self) -> bool:
        return self.ctl.is_on

    @property
    def brightness(self) -> int:
        return self.ctl.brightness

    @property
    def rgb_color(self) -> Colour:
        return self.ctl.colour

    @property
    def effect(self) -> str | None:
        return None if self.ctl.zone or self.ctl.scanning else SHOW if self.ctl.show_mode else self.ctl.effect

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        first, n = self._start, self.ctl.bulbs
        return {"per_bulb": self.ctl.custom, "bulbs": n, "ip_address": self.ctl.host, "start_pixel": first,
                "pixels": f"{first}-{first + n - 1}", "channels": f"{(first - 1) * 3 + 1}-{(first + n - 1) * 3}",
                "pattern": self.ctl.pattern, "pattern_effect": self.ctl.pattern_effect, "speed": self.ctl.speed,
                "zone": self.ctl.zone, "scanning": self.ctl.scanning,
                "pattern_list": patterns.pattern_names(), "pattern_effect_list": patterns.PATTERN_EFFECTS,
                "music": self.ctl.music, "default_transition": self.ctl.default_transition}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ctl.async_turn_on(kwargs.get(ATTR_BRIGHTNESS), kwargs.get(ATTR_RGB_COLOR),
                                     kwargs.get(ATTR_EFFECT), kwargs.get(ATTR_TRANSITION))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ctl.async_turn_off(kwargs.get(ATTR_TRANSITION))

    async def async_set_pixels(self, colors: list[Colour], repeat: bool = True) -> None:
        await self.ctl.async_set_pixels(colors, repeat)

    async def async_set_pattern(self, pattern: str | None = None, effect: str | None = None,
                                speed: int | None = None, transition: float | None = None) -> None:
        await self.ctl.async_set_pattern(pattern, effect, speed, transition=transition)


class C9BulbLight(_Base):
    def __init__(self, ctl, device, uid: str, index: int) -> None:
        super().__init__(ctl, device)
        self.index = index
        self._attr_unique_id = f"{uid}_bulb_{index + 1}"
        self._attr_name = f"Bulb {index + 1}"

    @property
    def is_on(self) -> bool:
        return self.ctl.is_on and self.ctl.custom and self.ctl.bulb_level[self.index] > 0

    @property
    def brightness(self) -> int:
        return self.ctl.bulb_level[self.index] or 255

    @property
    def rgb_color(self) -> Colour:
        return self.ctl.bulb_colour[self.index]

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ctl.async_set_bulb(self.index, True, kwargs.get(ATTR_RGB_COLOR),
                                      kwargs.get(ATTR_BRIGHTNESS))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ctl.async_set_bulb(self.index, False)
