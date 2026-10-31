from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback

from .const import CONF_BULB_ENTITIES, CONF_BULBS, CONF_START_PIXEL, DEFAULT_BULBS, DOMAIN

PIXEL = vol.All(int, vol.Range(min=1, max=1020))


class C9ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()
            name = user_input.pop(CONF_NAME)
            return self.async_create_entry(title=name, data=user_input)
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_NAME, default="Govee C9"): str,
            vol.Required(CONF_BULBS, default=DEFAULT_BULBS): vol.All(int, vol.Range(min=1, max=255)),
            vol.Required(CONF_BULB_ENTITIES, default=True): bool,
            vol.Required(CONF_START_PIXEL, default=self._next_pixel()): PIXEL,
        }))

    def _next_pixel(self) -> int:
        """First pixel after the strings already set up, so each gets its own slice."""
        end = 0
        for e in self._async_current_entries():
            o = {**e.data, **e.options}
            end = max(end, o.get(CONF_START_PIXEL, 1) - 1 + o.get(CONF_BULBS, DEFAULT_BULBS))
        return end + 1

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return C9OptionsFlow()


class C9OptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        cur = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_BULBS, default=cur.get(CONF_BULBS, DEFAULT_BULBS)): vol.All(int, vol.Range(min=1, max=255)),
            vol.Required(CONF_BULB_ENTITIES, default=cur.get(CONF_BULB_ENTITIES, True)): bool,
            vol.Required(CONF_START_PIXEL, default=cur.get(CONF_START_PIXEL, 1)): PIXEL,
        }))
