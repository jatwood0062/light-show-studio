"""Saved looks: what every light shows now, kept under a name and brought back later (one Store,
"light_show_studio.looks"). A look holds each light's own setting (see C9Controller.snapshot) by config entry id
and, when it was saved for the whole house, the zones that were running."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
LOOKS_KEY = f"{DOMAIN}_looks"  # hass.data key of the Looks, made in async_setup
LOOKS_STORE_KEY = f"{DOMAIN}.looks"
VERSION = 1  # of the stored document
SAVE_DELAY = 1.0
MAX_LOOKS, MAX_NAME, MAX_LIGHTS, MAX_ZONES, MAX_BULBS, MAX_ID = 50, 64, 200, 50, 5000, 64
AD_HOC = "area:"  # the id prefix of the zone made for "this area" (mapmanager.AD_HOC)


def _int(value: Any, lo: int, hi: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{what} must be a whole number from {lo} to {hi}")
    return value


def _colour(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("A colour must be [red, green, blue]")
    return [_int(v, 0, 255, "A colour value") for v in value]


def _text(value: Any, what: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not 0 < len(value) <= MAX_NAME or "\0" in value:
        raise ValueError(f"{what} must be 1 to {MAX_NAME} characters")
    return value


def clean_name(value: Any) -> str:
    name = value.strip() if isinstance(value, str) else ""
    return _text(name, "A look's name")  # type: ignore[return-value]


def clean_light(raw: Any) -> dict[str, Any]:
    """A checked copy of one light's part of a look. ValueError says what is wrong."""
    if not isinstance(raw, dict):
        raise ValueError("A light in a look must be an object")
    out: dict[str, Any] = {
        "on": raw.get("on") is True, "brightness": _int(raw.get("brightness", 255), 0, 255, "Brightness"),
        "colour": _colour(raw.get("colour", [255, 150, 40])), "effect": _text(raw.get("effect"), "An effect", True),
        "pattern": _text(raw.get("pattern"), "A pattern", True),
        "pattern_effect": _text(raw.get("pattern_effect"), "A pattern effect", True),
        "speed": _int(raw.get("speed", 50), 1, 100, "Speed"), "show_mode": raw.get("show_mode") is True,
        "custom": raw.get("custom") is True, "zone": raw.get("zone") is True}
    if (bulbs := raw.get("bulbs")) is not None:
        colours, levels = (bulbs.get("colours"), bulbs.get("levels")) if isinstance(bulbs, dict) else (None, None)
        if not isinstance(colours, list) or not isinstance(levels, list) or len(colours) != len(levels) \
                or len(colours) > MAX_BULBS:
            raise ValueError("Per-bulb colours and levels must be lists of the same length")
        out["bulbs"] = {"colours": [_colour(c) for c in colours], "levels": [_int(v, 0, 255, "A bulb level") for v in levels]}
    return out


def clean_look(raw: Any) -> dict[str, Any]:
    """A checked copy of a stored look. ValueError says what is wrong."""
    if not isinstance(raw, dict):
        raise ValueError("A look must be an object")
    lights = raw.get("lights")
    if not isinstance(lights, dict) or len(lights) > MAX_LIGHTS:
        raise ValueError("A look needs its lights")
    out: dict[str, Any] = {"name": clean_name(raw.get("name")),
                           "saved": raw["saved"] if isinstance(raw.get("saved"), str) else None,
                           "lights": {_text(k, "A light id"): clean_light(v) for k, v in lights.items()}}
    if (zones := raw.get("zones")) is not None:
        if not isinstance(zones, list) or len(zones) > MAX_ZONES or not all(isinstance(z, dict) for z in zones):
            raise ValueError("A look's zones must be a list")
        out["zones"] = [{"id": _text(z.get("id"), "A zone id"), "pattern": _text(z.get("pattern"), "A pattern", True),
                         "effect": _text(z.get("effect"), "An effect", True),
                         "speed": _int(z.get("speed", 50), 1, 100, "Speed")} for z in zones]
    return out


def clean_document(data: Any) -> list[dict[str, Any]]:
    """The looks in a stored (or imported) document, checked. ValueError says what is wrong."""
    if not isinstance(data, dict) or not isinstance(data.get("looks"), list):
        raise ValueError("The looks must be an object with a list of looks")
    if data.get("version", VERSION) != VERSION:
        raise ValueError("The looks were saved by a newer version")
    if len(data["looks"]) > MAX_LOOKS:
        raise ValueError(f"At most {MAX_LOOKS} looks")
    return [clean_look(look) for look in data["looks"]]


class Looks:
    """The saved looks, and saving / applying them on the lights the map manager knows."""

    def __init__(self, hass: HomeAssistant, mgr: Any) -> None:
        self.hass = hass
        self.mgr = mgr  # the MapManager: finds the lights and runs the zones
        self._store: Store[dict[str, Any]] = Store(hass, VERSION, LOOKS_STORE_KEY)
        self.items: dict[str, dict[str, Any]] = {}  # name (casefolded) -> look, in the order they were made

    # --- storage -------------------------------------------------------------------------
    async def async_load(self) -> None:
        try:
            data = await self._store.async_load()
        except Exception:  # noqa: BLE001  an unreadable store must not stop the integration
            _LOGGER.exception("Could not read your saved looks; starting with none")
            data = None
        self.items = {}
        raw = data.get("looks") if isinstance(data, dict) else None
        for row in raw[:MAX_LOOKS] if isinstance(raw, list) else []:
            try:
                look = clean_look(row)
                self.items.setdefault(look["name"].casefold(), look)
            except (ValueError, TypeError, AttributeError) as err:
                _LOGGER.warning("Dropping a saved look that is no longer valid: %s", err)

    def data(self) -> dict[str, Any]:
        return {"version": VERSION, "looks": list(self.items.values())}

    def _save(self) -> None:
        self._store.async_delay_save(self.data, SAVE_DELAY)

    async def async_flush(self) -> None:
        await self._store.async_save(self.data())

    async def async_replace(self, data: Any) -> None:
        """Replace every look with a checked document (an imported backup). ValueError says what is wrong."""
        looks = clean_document(data)
        self.items = {}
        for look in looks:
            self.items.setdefault(look["name"].casefold(), look)
        await self.async_flush()

    # --- the looks -------------------------------------------------------------------------
    def get(self, name: str) -> dict[str, Any]:
        look = self.items.get(name.strip().casefold()) if isinstance(name, str) else None
        if look is None:
            raise KeyError(f"No look called {name}")
        return look

    def view(self) -> list[dict[str, Any]]:
        """The looks without their per-bulb detail, for the list in the app."""
        return [{"name": k["name"], "saved": k["saved"], "lights": list(k["lights"]),
                 "on": sum(1 for v in k["lights"].values() if v["on"]), "zones": len(k.get("zones") or ())}
                for k in self.items.values()]

    def capture(self, entries: Collection[str] | None = None) -> dict[str, Any]:
        """What the lights show now. `entries` = only those lights (config entry ids); the zones that are running
        are kept only for a look of every light."""
        lights = {}
        for row in self.mgr.lights():
            entry_id = row["entry_id"]
            ctl = self.mgr.controller(entry_id)
            if ctl is not None and (entries is None or entry_id in entries):
                lights[entry_id] = ctl.snapshot()
        look: dict[str, Any] = {"saved": datetime.now(timezone.utc).isoformat(timespec="seconds"), "lights": lights}
        if entries is None:
            look["zones"] = [{"id": zone_id, "pattern": r.plan.zone.pattern, "effect": r.plan.zone.effect, "speed": r.speed}
                             for zone_id, r in getattr(self.mgr, "runners", {}).items() if r.running]
        return look

    async def async_save_current(self, name: str, entries: Collection[str] | None = None) -> dict[str, Any]:
        """Save what the lights show now under `name` (a look with that name is replaced)."""
        name = clean_name(name)
        if name.casefold() not in self.items and len(self.items) >= MAX_LOOKS:
            raise ValueError(f"At most {MAX_LOOKS} looks")
        look = clean_look({**self.capture(entries), "name": name})
        if not look["lights"]:
            raise ValueError("None of those lights are set up")
        self.items[name.casefold()] = look
        self._save()
        return look

    async def async_delete(self, name: str) -> None:
        del self.items[self.get(name)["name"].casefold()]
        self._save()

    async def async_apply(self, name: str, transition: float | None = None) -> dict[str, list[str]]:
        """Bring a look back. Lights that are gone are skipped, a light a show is playing on is left alone, and
        a light a zone of the look draws is left to that zone. Returns what was applied and what was skipped."""
        look = self.get(name)
        zones = look.get("zones")
        running = getattr(self.mgr, "running", None)
        skipped: list[str] = []
        if zones is not None and running is not None:  # a look of every light: zones that are not part of it stop
            wanted = {z["id"] for z in zones}
            for zone_id in [z for z in running if z not in wanted]:
                await self.mgr.async_stop_zone(zone_id, transition)
        todo = []
        for entry_id, snap in look["lights"].items():
            ctl = self.mgr.controller(entry_id)
            if ctl is None or (snap["zone"] and zones and running is not None):
                skipped.append(entry_id)
            else:
                todo.append((entry_id, ctl.async_restore(snap, transition)))
        applied = []
        for (entry_id, _), res in zip(todo, await asyncio.gather(*(c for _, c in todo), return_exceptions=True)):
            if isinstance(res, BaseException):
                _LOGGER.warning("Could not apply the look %s to %s: %s", look["name"], entry_id, res)
            (applied if res is True else skipped).append(entry_id)
        for z in zones or () if running is not None else ():
            try:
                if z["id"].startswith(AD_HOC):  # a pattern on an area: started the same way again
                    await self.mgr.async_start_area(z["id"][len(AD_HOC):], z["pattern"], z["effect"], z["speed"],
                                                    fade_in=transition, fade_out=transition)
                elif z["id"] not in running:  # a saved zone plays as it is defined now
                    await self.mgr.async_start_zone(z["id"], transition=transition)
            except Exception as err:  # noqa: BLE001  a zone or area that is gone must not stop the rest
                _LOGGER.warning("Could not start %s for the look %s: %s", z["id"], look["name"], err)
        return {"applied": applied, "skipped": sorted(skipped)}


async def async_setup_looks(hass: HomeAssistant, mgr: Any) -> Looks:
    looks = hass.data[LOOKS_KEY] = Looks(hass, mgr)
    await looks.async_load()
    return looks
