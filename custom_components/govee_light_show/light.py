"""The whole string as one light (with effects), plus one light per bulb."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import CONF_HOST, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import C9ConfigEntry
from .const import CONF_BULB_ENTITIES, CONF_SKU, DOMAIN
from .controller import SHOW, C9Controller
from .effects import EFFECT_LIST, EFFECTS
from .protocol import Colour


def _colour(value: Any) -> Colour:
    if isinstance(value, str):
        v = value.lstrip("#")
        if len(v) != 6:
            raise vol.Invalid(f"expected #rrggbb, got {value}")
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return tuple(max(0, min(255, int(x))) for x in value)
    raise vol.Invalid(f"expected #rrggbb or [r, g, b], got {value}")


SET_PIXELS_SCHEMA = {
    vol.Required("colors"): vol.All(cv.ensure_list, [_colour]),
    vol.Optional("repeat", default=True): cv.boolean,
}


async def async_setup_entry(hass: HomeAssistant, entry: C9ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    ctl = entry.runtime_data
    device = DeviceInfo(identifiers={(DOMAIN, entry.data[CONF_HOST])}, name=entry.title,
                        manufacturer="Govee", model=entry.data.get(CONF_SKU) or "LAN light")
    entities: list[LightEntity] = [C9StringLight(ctl, entry, device)]
    if entry.options.get(CONF_BULB_ENTITIES, entry.data.get(CONF_BULB_ENTITIES, True)):
        entities += [C9BulbLight(ctl, entry, device, i) for i in range(ctl.bulbs)]
    async_add_entities(entities)

    entity_platform.async_get_current_platform().async_register_entity_service(
        "set_pixels", SET_PIXELS_SCHEMA, "async_set_pixels"
    )


class _Base(LightEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_assumed_state = True
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, ctl: C9Controller, device: DeviceInfo) -> None:
        self.ctl = ctl
        self._attr_device_info = device

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.ctl.add_listener(self.async_write_ha_state))


class C9StringLight(_Base, RestoreEntity):
    _attr_name = None
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = [SHOW, *EFFECT_LIST]

    def __init__(self, ctl, entry, device) -> None:
        super().__init__(ctl, device)
        self._attr_unique_id = f"{entry.data[CONF_HOST]}_string"

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
        if (effect := attrs.get(ATTR_EFFECT)) == SHOW:
            ctl.show_mode = True
        elif effect in EFFECTS:
            ctl.effect, ctl.show_mode = effect, False
        elif ctl.is_on:  # on with a plain colour
            ctl.show_mode = False

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
        return SHOW if self.ctl.show_mode else self.ctl.effect

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"per_bulb": self.ctl.custom, "bulbs": self.ctl.bulbs}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.ctl.async_turn_on(kwargs.get(ATTR_BRIGHTNESS), kwargs.get(ATTR_RGB_COLOR),
                                     kwargs.get(ATTR_EFFECT))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.ctl.async_turn_off()

    async def async_set_pixels(self, colors: list[Colour], repeat: bool = True) -> None:
        await self.ctl.async_set_pixels(colors, repeat)


class C9BulbLight(_Base):
    def __init__(self, ctl, entry, device, index: int) -> None:
        super().__init__(ctl, device)
        self.index = index
        self._attr_unique_id = f"{entry.data[CONF_HOST]}_bulb_{index + 1}"
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
