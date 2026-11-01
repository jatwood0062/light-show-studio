from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AUDIO_OFFSET,
    CONF_BULB_ENTITIES,
    CONF_BULBS,
    CONF_GAP,
    CONF_SEGMENTED,
    CONF_SKU,
    CONF_SPEAKER,
    CONF_START_PIXEL,
    CONF_TYPE,
    DEFAULT_BULBS,
    DOMAIN,
    TYPE_PLAYER,
)
from .protocol import async_scan

PIXEL = vol.All(int, vol.Range(min=1, max=1020))
MANUAL = "manual"


def _model(sku: str):
    try:
        from .models import lookup
    except ImportError:
        return None
    return lookup(sku) if sku else None


def _light_schema(cur: dict[str, Any], with_name: bool) -> vol.Schema:
    fields: dict = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=cur.get(CONF_NAME, "Govee lights"))] = str
    fields.update({
        vol.Required(CONF_BULBS, default=cur.get(CONF_BULBS, DEFAULT_BULBS)): vol.All(int, vol.Range(min=1, max=255)),
        vol.Required(CONF_SEGMENTED, default=cur.get(CONF_SEGMENTED, True)): bool,
        vol.Required(CONF_BULB_ENTITIES, default=cur.get(CONF_BULB_ENTITIES, True)): bool,
        vol.Required(CONF_START_PIXEL, default=cur.get(CONF_START_PIXEL, 1)): PIXEL,
    })
    return vol.Schema(fields)


def _player_schema(cur: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_SPEAKER, description={"suggested_value": cur.get(CONF_SPEAKER)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="media_player")),
        vol.Required(CONF_AUDIO_OFFSET, default=cur.get(CONF_AUDIO_OFFSET, 0.0)): vol.Coerce(float),
        vol.Required(CONF_GAP, default=cur.get(CONF_GAP, 3.0)): vol.All(vol.Coerce(float), vol.Range(min=0, max=120)),
    })


class C9ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, dict] = {}
        self._light: dict[str, Any] = {}
        self._defaults: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["light"]
        if not any(e.data.get(CONF_TYPE) == TYPE_PLAYER for e in self._async_current_entries()):
            menu.append("player")
        return self.async_show_menu(step_id="user", menu_options=menu)

    # --- a light -----------------------------------------------------------
    async def async_step_light(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a light found on the network, or type its IP."""
        if user_input is not None:
            host = user_input[CONF_HOST]
            if host == MANUAL:
                return await self.async_step_manual()
            return await self._start_light(host, self._found.get(host, {}).get("sku", ""))
        known = {e.data.get(CONF_HOST) for e in self._async_current_entries()}
        self._found = {ip: d for ip, d in (await async_scan()).items() if ip not in known}
        options = [selector.SelectOptionDict(value=ip, label=f"{d['sku'] or 'Govee light'} at {ip}")
                   for ip, d in sorted(self._found.items())]
        options.append(selector.SelectOptionDict(value=MANUAL, label="Enter an IP address"))
        if len(options) == 1:
            return await self.async_step_manual()
        return self.async_show_form(step_id="light", data_schema=vol.Schema({
            vol.Required(CONF_HOST): selector.SelectSelector(selector.SelectSelectorConfig(options=options)),
        }))

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._start_light(user_input[CONF_HOST].strip(), "")
        return self.async_show_form(step_id="manual", data_schema=vol.Schema({vol.Required(CONF_HOST): str}))

    async def _start_light(self, host: str, sku: str) -> ConfigFlowResult:
        await self.async_set_unique_id(host)
        self._abort_if_unique_id_configured()
        model = _model(sku)
        self._light = {CONF_HOST: host, CONF_SKU: sku}
        self._defaults = {
            CONF_NAME: model.name if model else (f"Govee {sku}" if sku else "Govee lights"),
            CONF_BULBS: (model.segments if model and model.segments else DEFAULT_BULBS),
            CONF_SEGMENTED: model.razer is not False if model else True,
            CONF_START_PIXEL: self._next_pixel(),
        }
        return await self.async_step_details()

    async def async_step_details(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            name = user_input.pop(CONF_NAME)
            return self.async_create_entry(title=name, data={**self._light, **user_input})
        return self.async_show_form(step_id="details", data_schema=_light_schema(self._defaults, True),
                                    description_placeholders={"host": self._light[CONF_HOST],
                                                              "sku": self._light[CONF_SKU] or "unknown"})

    def _next_pixel(self) -> int:
        """First pixel after the lights already set up, so each gets its own slice."""
        end = 0
        for e in self._async_current_entries():
            if e.data.get(CONF_TYPE) == TYPE_PLAYER:
                continue
            o = {**e.data, **e.options}
            end = max(end, o.get(CONF_START_PIXEL, 1) - 1 + o.get(CONF_BULBS, DEFAULT_BULBS))
        return end + 1

    # --- the show player ---------------------------------------------------
    async def async_step_player(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(TYPE_PLAYER)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Light Show Player", data={CONF_TYPE: TYPE_PLAYER},
                                           options=user_input)
        return self.async_show_form(step_id="player", data_schema=_player_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return C9OptionsFlow()


class C9OptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        player = self.config_entry.data.get(CONF_TYPE) == TYPE_PLAYER
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        cur = {**self.config_entry.data, **self.config_entry.options}
        schema = _player_schema(cur) if player else _light_schema(cur, False)
        return self.async_show_form(step_id="player" if player else "init", data_schema=schema)
