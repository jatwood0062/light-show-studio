from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ALLOWED_SOURCES,
    CONF_AUDIO_OFFSET,
    CONF_BULB_ENTITIES,
    CONF_BULBS,
    CONF_DEFAULT_TRANSITION,
    CONF_DEVICE_ID,
    CONF_DEVICE_OFFSET,
    CONF_GAP,
    CONF_LED_PROFILE,
    CONF_SEGMENTED,
    CONF_SHOW_LEAD_MS,
    CONF_SKU,
    CONF_START_PIXEL,
    CONF_TYPE,
    DEFAULT_BULBS,
    DOMAIN,
    TYPE_PLAYER,
    TYPE_TWINKLY,
    TYPE_WLED,
)
from .hosts import is_lan_ipv4, parse_sources, valid_ipv4
from .protocol import async_scan
from .twinkly import async_discover, async_gestalt
from .wled import async_info, fits, format_mac

PIXEL = vol.All(int, vol.Range(min=1, max=2040))
LEAD = vol.All(int, vol.Range(min=-500, max=500))
OFFSET = vol.All(int, vol.Range(min=0, max=2039))
MANUAL = "manual"


def _model(sku: str):
    try:
        from .models import lookup
    except ImportError:
        return None
    return lookup(sku) if sku else None


def _light_schema(cur: dict[str, Any], with_name: bool, twinkly: bool = False, name: str = "Light",
                  wled: bool = False) -> vol.Schema:
    fields: dict = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=cur.get(CONF_NAME, name))] = vol.All(str, vol.Length(min=1, max=100))
    fields[vol.Required(CONF_BULBS, default=cur.get(CONF_BULBS, DEFAULT_BULBS))] = vol.All(
        int, vol.Range(min=1, max=1020 if twinkly else 255))
    if not twinkly:
        fields[vol.Required(CONF_SEGMENTED, default=cur.get(CONF_SEGMENTED, True))] = bool
    fields[vol.Required(CONF_BULB_ENTITIES, default=cur.get(CONF_BULB_ENTITIES, not twinkly))] = bool
    fields[vol.Required(CONF_START_PIXEL, default=cur.get(CONF_START_PIXEL, 1))] = PIXEL
    if wled:  # first LED on the controller this light writes (a dummy output or another strip before it)
        fields[vol.Required(CONF_DEVICE_OFFSET, default=cur.get(CONF_DEVICE_OFFSET, 0))] = OFFSET
    fields[vol.Required(CONF_SHOW_LEAD_MS, default=cur.get(CONF_SHOW_LEAD_MS, 0))] = LEAD
    return vol.Schema(fields)


def _too_long(user_input: dict[str, Any], leds: int | None) -> dict[str, str]:
    """Error for a WLED whose bulbs + offset run past the LEDs the device reports (nothing when unknown)."""
    if fits(user_input.get(CONF_BULBS, 0), user_input.get(CONF_DEVICE_OFFSET, 0), leds):
        return {}
    return {CONF_DEVICE_OFFSET: "exceeds_leds"}


def _sources(value: Any) -> str:
    """Validate the allowed-senders text and tidy it into 'a, b, c'."""
    text = str(value or "").strip()
    try:
        nets = parse_sources(text)
    except ValueError as err:
        raise vol.Invalid(str(err)) from err
    return ", ".join(str(n.network_address) if n.prefixlen == 32 else str(n) for n in nets)


def _player_schema(cur: dict[str, Any]) -> vol.Schema:
    """Speakers and the between-shows look are chosen in the app; the timing and who may send live here."""
    return vol.Schema({
        vol.Optional(CONF_ALLOWED_SOURCES, default=cur.get(CONF_ALLOWED_SOURCES, "")): _sources,
        vol.Required(CONF_AUDIO_OFFSET, default=cur.get(CONF_AUDIO_OFFSET, 0.0)): vol.Coerce(float),
        vol.Required(CONF_GAP, default=cur.get(CONF_GAP, 3.0)): vol.All(vol.Coerce(float), vol.Range(min=0, max=120)),
    })


class C9ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 2  # 1.2: the player keeps a `speakers` list instead of one `speaker`

    def __init__(self) -> None:
        self._found: dict[str, dict] = {}
        self._tw_found: dict[str, str] = {}
        self._wled_found: dict[str, str] = {}
        self._light: dict[str, Any] = {}
        self._wled_leds: int | None = None
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
        self._found = {ip: d for ip, d in (await async_scan() or {}).items() if ip not in known and is_lan_ipv4(ip)}
        options = [selector.SelectOptionDict(value=ip, label=f"{d['sku'] or 'Light'} at {ip}")
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
            try:  # a name would be resolved inside the event loop when sending; only LAN addresses
                host = valid_ipv4(host)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                return await self._start_light(host, "", "")
        return self.async_show_form(step_id="manual", data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
                                    errors=errors)

    async def _start_light(self, host: str, sku: str, device: str) -> ConfigFlowResult:
        try:
            host = valid_ipv4(host)
        except ValueError:
            return self.async_abort(reason="invalid_host")
        await self.async_set_unique_id(host)
        self._abort_if_unique_id_configured()
        model = _model(sku)
        sku, device = str(sku)[:64], str(device)[:64]
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
            CONF_NAME: model.name if model else (sku or "Light"),
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
            self._tw_found = {ip: n for ip, n in (await async_discover()).items() if ip not in known and is_lan_ipv4(ip)}
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
                host = valid_ipv4(host)
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
            host = valid_ipv4(host)
            info = await async_gestalt(async_get_clientsession(self.hass), host)
            mac = str(info["mac"]).lower()[:64]
            leds = int(info.get("number_of_led") or 1)
        except Exception:  # unreachable, or not a Twinkly
            return None
        await self.async_set_unique_id(mac)
        await self._abort_or_follow(host, lambda old: async_gestalt(async_get_clientsession(self.hass), old))
        profile = info.get("led_profile", "RGB")
        if profile not in ("RGB", "RGBW"):
            profile = "RGB"
        self._light = {CONF_HOST: host, CONF_TYPE: TYPE_TWINKLY, CONF_SKU: str(info.get("product_code", ""))[:64],
                       CONF_DEVICE_ID: mac, CONF_LED_PROFILE: profile}
        self._defaults = {
            CONF_NAME: str(info.get("device_name") or "Twinkly")[:100],
            CONF_BULBS: max(1, min(leds, 1020)),
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
                                if is_lan_ipv4(e.data.get(CONF_HOST)) and e.data[CONF_HOST] not in known}
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
                host = valid_ipv4(host)
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
            host = valid_ipv4(host)
            info = await async_info(async_get_clientsession(self.hass), host)
            mac = format_mac(info["mac"])[:64]
            count = int(info["leds"]["count"])
        except Exception:  # unreachable, or not a WLED
            return None
        self._wled_leds = count
        await self.async_set_unique_id(mac)
        await self._abort_or_follow(host, lambda old: async_info(async_get_clientsession(self.hass), old))
        self._light = {CONF_HOST: host, CONF_TYPE: TYPE_WLED, CONF_SKU: str(info.get("arch") or "WLED")[:64],
                       CONF_DEVICE_ID: mac}
        self._defaults = {
            CONF_NAME: str(info.get("name") or "WLED")[:100],
            CONF_BULBS: max(1, min(count, 1020)),
            CONF_BULB_ENTITIES: False,
            CONF_START_PIXEL: self._next_pixel(),
        }
        return await self.async_step_wled_details()

    async def async_step_wled_details(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not (errors := _too_long(user_input, self._wled_leds)):
                name = user_input.pop(CONF_NAME)
                return self.async_create_entry(title=name, data={**self._light, **user_input})
            self._defaults = {**self._defaults, **user_input}
        return self.async_show_form(step_id="wled_details", errors=errors,
                                    data_schema=_light_schema(self._defaults, True, True, "WLED", True),
                                    description_placeholders={"host": self._light[CONF_HOST],
                                                              "sku": self._light[CONF_SKU],
                                                              "leds": str(self._wled_leds or "?")})

    async def _abort_or_follow(self, host: str, probe) -> None:
        """Abort when this device (unique id) is already set up. Its stored host is only replaced when the
        new one is free and the old one no longer answers, so a rogue device claiming a known id (or mac)
        can't take over an entry whose light is still there."""
        entry = self.hass.config_entries.async_entry_for_domain_unique_id(self.handler, self.unique_id)
        if entry is None:
            return
        old = entry.data.get(CONF_HOST)
        if old == host:
            self._abort_if_unique_id_configured()
        taken = any(e.entry_id != entry.entry_id and e.data.get(CONF_HOST) == host
                    for e in self._async_current_entries())
        if taken:
            self._abort_if_unique_id_configured()
        try:
            await probe(old)
        except Exception:  # noqa: BLE001  the old address is gone: follow the device
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._abort_if_unique_id_configured()

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
        wled = self.config_entry.data.get(CONF_TYPE) == TYPE_WLED
        errors: dict[str, str] = {}
        if user_input is not None and wled:
            leds = None
            try:  # the device's own LED count, when it answers
                leds = int((await async_info(async_get_clientsession(self.hass), self.config_entry.data[CONF_HOST]))
                           ["leds"]["count"])
            except Exception:  # noqa: BLE001  unreachable now: can't check
                pass
            errors = _too_long(user_input, leds)
        if user_input is not None and not errors:
            # the app manages the speakers and transition: keep them
            return self.async_create_entry(data={**self.config_entry.options, **user_input} if player else user_input)
        cur = {**self.config_entry.data, **self.config_entry.options, **(user_input or {})}
        twinkly = self.config_entry.data.get(CONF_TYPE) in (TYPE_TWINKLY, TYPE_WLED)
        schema = _player_schema(cur) if player else _light_schema(cur, False, twinkly, wled=wled).extend({
            vol.Optional(CONF_DEFAULT_TRANSITION, default=cur.get(CONF_DEFAULT_TRANSITION, 0.7)):
                vol.All(vol.Coerce(float), vol.Range(min=0, max=30))})
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
