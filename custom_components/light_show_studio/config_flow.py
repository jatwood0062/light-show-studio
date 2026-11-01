from __future__ import annotations

import ipaddress
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_AUDIO_OFFSET,
    CONF_BULB_ENTITIES,
    CONF_BULBS,
    CONF_DEVICE_ID,
    CONF_GAP,
    CONF_LED_PROFILE,
    CONF_SEGMENTED,
    CONF_SKU,
    CONF_SPEAKER,
    CONF_START_PIXEL,
    CONF_TRANSITION,
    CONF_TRANSITION_EFFECT,
    CONF_TYPE,
    DEFAULT_BULBS,
    DOMAIN,
    TYPE_PLAYER,
    TYPE_TWINKLY,
    TYPE_WLED,
)
from .effects import EFFECT_LIST
from .protocol import async_scan
from .transitions import DEFAULT_TRANSITION, MODES, default_effect
from .twinkly import async_discover, async_gestalt
from .wled import async_info, format_mac

PIXEL = vol.All(int, vol.Range(min=1, max=2040))
MANUAL = "manual"


def _model(sku: str):
    try:
        from .models import lookup
    except ImportError:
        return None
    return lookup(sku) if sku else None


def _light_schema(cur: dict[str, Any], with_name: bool, twinkly: bool = False, name: str = "Twinkly") -> vol.Schema:
    fields: dict = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=cur.get(CONF_NAME, name if twinkly else "Govee lights"))] = str
    fields[vol.Required(CONF_BULBS, default=cur.get(CONF_BULBS, DEFAULT_BULBS))] = vol.All(
        int, vol.Range(min=1, max=1020 if twinkly else 255))
    if not twinkly:
        fields[vol.Required(CONF_SEGMENTED, default=cur.get(CONF_SEGMENTED, True))] = bool
    fields[vol.Required(CONF_BULB_ENTITIES, default=cur.get(CONF_BULB_ENTITIES, not twinkly))] = bool
    fields[vol.Required(CONF_START_PIXEL, default=cur.get(CONF_START_PIXEL, 1))] = PIXEL
    return vol.Schema(fields)


def _player_schema(cur: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_SPEAKER, description={"suggested_value": cur.get(CONF_SPEAKER)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="media_player")),
        vol.Required(CONF_AUDIO_OFFSET, default=cur.get(CONF_AUDIO_OFFSET, 0.0)): vol.Coerce(float),
        vol.Required(CONF_GAP, default=cur.get(CONF_GAP, 3.0)): vol.All(vol.Coerce(float), vol.Range(min=0, max=120)),
        vol.Required(CONF_TRANSITION, default=cur.get(CONF_TRANSITION, DEFAULT_TRANSITION)):
            selector.SelectSelector(selector.SelectSelectorConfig(
                options=list(MODES), mode=selector.SelectSelectorMode.DROPDOWN, translation_key="transition")),
        vol.Required(CONF_TRANSITION_EFFECT, default=cur.get(CONF_TRANSITION_EFFECT, default_effect(EFFECT_LIST))):
            selector.SelectSelector(selector.SelectSelectorConfig(
                options=EFFECT_LIST, mode=selector.SelectSelectorMode.DROPDOWN)),
    })


class C9ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, dict] = {}
        self._tw_found: dict[str, str] = {}
        self._wled_found: dict[str, str] = {}
        self._light: dict[str, Any] = {}
        self._defaults: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["light", "twinkly", "wled"]
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
            found = self._found.get(host, {})
            return await self._start_light(host, found.get("sku", ""), found.get("device", ""))
        known = {e.data.get(CONF_HOST) for e in self._async_current_entries()}
        self._found = {ip: d for ip, d in (await async_scan() or {}).items() if ip not in known}
        options = [selector.SelectOptionDict(value=ip, label=f"{d['sku'] or 'Govee light'} at {ip}")
                   for ip, d in sorted(self._found.items())]
        options.append(selector.SelectOptionDict(value=MANUAL, label="Enter an IP address"))
        if len(options) == 1:
            return await self.async_step_manual()
        return self.async_show_form(step_id="light", data_schema=vol.Schema({
            vol.Required(CONF_HOST): selector.SelectSelector(selector.SelectSelectorConfig(options=options)),
        }))

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:  # a name would be resolved inside the event loop when sending
                ipaddress.IPv4Address(host)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                return await self._start_light(host, "", "")
        return self.async_show_form(step_id="manual", data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
                                    errors=errors)

    async def _start_light(self, host: str, sku: str, device: str) -> ConfigFlowResult:
        await self.async_set_unique_id(host)
        self._abort_if_unique_id_configured()
        model = _model(sku)
        self._light = {CONF_HOST: host, CONF_SKU: sku}
        if device:
            self._light[CONF_DEVICE_ID] = device
        if model:  # only models known to take per-segment frames start segmented
            segmented = model.razer is True
            bulbs = model.segments or (DEFAULT_BULBS if segmented else 1)
        elif sku:  # reported but not in the table: play safe
            segmented, bulbs = False, 1
        else:  # typed IP: assume a string
            segmented, bulbs = True, DEFAULT_BULBS
        self._defaults = {
            CONF_NAME: model.name if model else (f"Govee {sku}" if sku else "Govee lights"),
            CONF_BULBS: bulbs,
            CONF_SEGMENTED: segmented,
            CONF_BULB_ENTITIES: segmented,
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

    # --- a Twinkly light ---------------------------------------------------
    async def async_step_twinkly(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a Twinkly found on the network, or type its IP."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            if host == MANUAL:
                return await self.async_step_twinkly_manual()
            if (result := await self._start_twinkly(host)) is not None:
                return result
            errors["base"] = "cannot_connect"
        else:
            known = {e.data.get(CONF_HOST) for e in self._async_current_entries()}
            self._tw_found = {ip: n for ip, n in (await async_discover()).items() if ip not in known}
            if not self._tw_found:
                return await self.async_step_twinkly_manual()
        options = [selector.SelectOptionDict(value=ip, label=f"{name} at {ip}")
                   for ip, name in sorted(self._tw_found.items())]
        options.append(selector.SelectOptionDict(value=MANUAL, label="Enter an IP address"))
        return self.async_show_form(step_id="twinkly", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_HOST): selector.SelectSelector(selector.SelectSelectorConfig(options=options)),
        }))

    async def async_step_twinkly_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                ipaddress.IPv4Address(host)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                if (result := await self._start_twinkly(host)) is not None:
                    return result
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="twinkly_manual", data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
                                    errors=errors)

    async def _start_twinkly(self, host: str) -> ConfigFlowResult | None:
        """Ask the light about itself; None if it doesn't answer."""
        try:
            info = await async_gestalt(async_get_clientsession(self.hass), host)
            mac = str(info["mac"]).lower()
        except Exception:  # unreachable, or not a Twinkly
            return None
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        profile = info.get("led_profile", "RGB")
        self._light = {CONF_HOST: host, CONF_TYPE: TYPE_TWINKLY, CONF_SKU: info.get("product_code", ""),
                       CONF_DEVICE_ID: mac, CONF_LED_PROFILE: profile}
        self._defaults = {
            CONF_NAME: info.get("device_name") or "Twinkly",
            CONF_BULBS: int(info.get("number_of_led") or 1),
            CONF_BULB_ENTITIES: False,
            CONF_START_PIXEL: self._next_pixel(),
        }
        return await self.async_step_twinkly_details()

    async def async_step_twinkly_details(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            name = user_input.pop(CONF_NAME)
            return self.async_create_entry(title=name, data={**self._light, **user_input})
        return self.async_show_form(step_id="twinkly_details", data_schema=_light_schema(self._defaults, True, True),
                                    description_placeholders={"host": self._light[CONF_HOST],
                                                              "sku": self._light[CONF_SKU] or "Twinkly"})

    # --- a WLED light ------------------------------------------------------
    async def async_step_wled(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a WLED that Home Assistant's own WLED integration knows, or type its IP."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            if host == MANUAL:
                return await self.async_step_wled_manual()
            if (result := await self._start_wled(host)) is not None:
                return result
            errors["base"] = "cannot_connect"
        else:
            known = {e.data.get(CONF_HOST) for e in self._async_current_entries()}
            self._wled_found = {e.data[CONF_HOST]: e.title for e in self.hass.config_entries.async_entries("wled")
                                if e.data.get(CONF_HOST) and e.data[CONF_HOST] not in known}
            if not self._wled_found:
                return await self.async_step_wled_manual()
        options = [selector.SelectOptionDict(value=ip, label=f"{name} at {ip}")
                   for ip, name in sorted(self._wled_found.items())]
        options.append(selector.SelectOptionDict(value=MANUAL, label="Enter an IP address"))
        return self.async_show_form(step_id="wled", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_HOST): selector.SelectSelector(selector.SelectSelectorConfig(options=options)),
        }))

    async def async_step_wled_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                ipaddress.IPv4Address(host)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                if (result := await self._start_wled(host)) is not None:
                    return result
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="wled_manual", data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
                                    errors=errors)

    async def _start_wled(self, host: str) -> ConfigFlowResult | None:
        """Ask the light about itself; None if it doesn't answer."""
        try:
            info = await async_info(async_get_clientsession(self.hass), host)
            mac = format_mac(info["mac"])
            count = int(info["leds"]["count"])
        except Exception:  # unreachable, or not a WLED
            return None
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._light = {CONF_HOST: host, CONF_TYPE: TYPE_WLED, CONF_SKU: info.get("arch") or "WLED",
                       CONF_DEVICE_ID: mac}
        self._defaults = {
            CONF_NAME: info.get("name") or "WLED",
            CONF_BULBS: max(1, min(count, 1020)),
            CONF_BULB_ENTITIES: False,
            CONF_START_PIXEL: self._next_pixel(),
        }
        return await self.async_step_wled_details()

    async def async_step_wled_details(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            name = user_input.pop(CONF_NAME)
            return self.async_create_entry(title=name, data={**self._light, **user_input})
        return self.async_show_form(step_id="wled_details",
                                    data_schema=_light_schema(self._defaults, True, True, "WLED"),
                                    description_placeholders={"host": self._light[CONF_HOST],
                                                              "sku": self._light[CONF_SKU]})

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
        twinkly = self.config_entry.data.get(CONF_TYPE) in (TYPE_TWINKLY, TYPE_WLED)
        schema = _player_schema(cur) if player else _light_schema(cur, False, twinkly)
        return self.async_show_form(step_id="init", data_schema=schema)
