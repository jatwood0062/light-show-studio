"""The map of runs and zones: stored, edited, and played (one ZoneRunner per running zone)."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from . import mapping, patterns
from .const import CONF_BULBS, CONF_SEGMENTED, CONF_TYPE, DEFAULT_BULBS, DOMAIN, MAP_KEY, MAP_STORE_KEY, TYPE_PLAYER, TYPE_TWINKLY, TYPE_WLED
from .controller import C9Controller
from .mapped_patterns import ZonePlan
from .mapping import LightSpec, MapError, MapModel, Zone
from .zoneplayer import ZoneRunner

_LOGGER = logging.getLogger(__name__)
SAVE_DELAY = 1.0


class ZoneError(HomeAssistantError):
    """A zone can't be started or found; the message is shown to the person."""


def _kind(entry: ConfigEntry) -> str:
    return {TYPE_TWINKLY: "twinkly", TYPE_WLED: "wled"}.get(entry.data.get(CONF_TYPE), "govee")


class MapManager:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, 1, MAP_STORE_KEY)
        self._model: MapModel | None = None  # None until a map is saved: the defaults follow the lights
        self.runners: dict[str, ZoneRunner] = {}
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()

    # --- lights ------------------------------------------------------------------------
    def _entries(self) -> list[ConfigEntry]:
        return [e for e in self.hass.config_entries.async_entries(DOMAIN) if e.data.get(CONF_TYPE) != TYPE_PLAYER]

    def controller(self, entry_id: str) -> C9Controller | None:
        """The loaded light for a config entry, or None (not set up, unloaded, a player)."""
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            return None
        ctl = getattr(entry, "runtime_data", None)
        return ctl if isinstance(ctl, C9Controller) else None

    def light_specs(self) -> list[LightSpec]:
        out = []
        for e in self._entries():
            o = {**e.data, **e.options}
            ctl = self.controller(e.entry_id)
            layout = getattr(ctl, "layout", None) if ctl else None
            out.append(LightSpec(e.entry_id, e.title, ctl.bulbs if ctl else int(o.get(CONF_BULBS, DEFAULT_BULBS)),
                                 _kind(e), layout))
        return out

    def lights(self) -> list[dict[str, Any]]:
        """The lights the map can use, for the panel."""
        out = []
        for spec in self.light_specs():
            ctl = self.controller(spec.entry_id)
            entry = self.hass.config_entries.async_get_entry(spec.entry_id)
            o = {**entry.data, **entry.options} if entry else {}
            row: dict[str, Any] = {
                "entry_id": spec.entry_id, "title": spec.title, "bulbs": spec.bulbs, "kind": spec.kind,
                "segmented": ctl.segmented if ctl else bool(o.get(CONF_SEGMENTED, True)), "loaded": ctl is not None,
            }
            if spec.layout is not None:
                row["layout"] = spec.layout
            out.append(row)
        return out

    # --- the model ---------------------------------------------------------------------------
    async def async_load(self) -> None:
        try:
            data = await self._store.async_load()
        except Exception:  # noqa: BLE001  an unreadable store must not stop the integration
            _LOGGER.exception("Could not read the saved light map; using the defaults")
            data = None
        if not isinstance(data, dict):
            return
        try:
            # lights may not be set up yet, so only config entries that are gone count as missing
            self._model = mapping.from_dict(data, {s.entry_id: s.bulbs for s in self.light_specs()}, strict=False)
        except MapError:
            _LOGGER.exception("The saved light map is damaged; using the defaults")

    @property
    def is_default(self) -> bool:
        return self._model is None

    @property
    def model(self) -> MapModel:
        specs = self.light_specs()
        if self._model is None:
            return mapping.default_map(specs)
        return mapping.prune(self._model, (s.entry_id for s in specs))

    def zone(self, ref: str) -> Zone:
        z = self.model.zone(ref)
        if z is None:
            raise ZoneError(f"No zone called {ref}")
        return z

    @callback
    def _save(self) -> None:
        if self._model is not None:
            self._store.async_delay_save(lambda: mapping.to_dict(self._model), SAVE_DELAY)

    async def async_flush(self) -> None:
        if self._model is not None:
            await self._store.async_save(mapping.to_dict(self._model))

    async def async_set_map(self, data: Any) -> MapModel:
        """Replace the map with a validated copy (MapError says what is wrong). Running zones that are
        still in the map restart with their new definition; the others stop."""
        model = mapping.from_dict(data, {s.entry_id: s.bulbs for s in self.light_specs()})
        async with self._lock:
            self._model = model
            self._save()
            for zone_id in list(self.runners):
                old = self.runners.pop(zone_id)
                zone = next((z for z in model.zones if z.id == zone_id), None)
                plan = ZonePlan(zone, model.run_map()) if zone else None
                if plan is None or plan.n == 0:
                    old.stop()
                    continue
                old.stop(keep={s.run.entry_id for s in plan.slices})
                await self._launch(plan, old.speed)
        self._notify()
        return model

    # --- listeners -------------------------------------------------------------------------
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb) if cb in self._listeners else None

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001  one bad listener must not stop the rest
                _LOGGER.exception("Map listener failed")

    @property
    def running(self) -> list[str]:
        return [z for z, r in self.runners.items() if r.running]

    # --- zones ---------------------------------------------------------------------------------
    async def _launch(self, plan: ZonePlan, speed: int) -> ZoneRunner:
        runner = ZoneRunner(plan, speed, self.controller, self.hass.async_create_background_task, self._ended)
        self.runners[plan.zone.id] = runner
        await runner.async_start()
        return runner

    @callback
    def _ended(self, runner: ZoneRunner) -> None:
        if self.runners.get(runner.zone_id) is runner:
            del self.runners[runner.zone_id]
            self._notify()

    async def async_start_zone(self, ref: str, pattern: str | None = None, effect: str | None = None,
                               speed: int | None = None) -> None:
        """Start (or restart) a zone. A pattern, effect or speed given here is kept as the zone's own."""
        async with self._lock:
            zone = self.zone(ref)
            if pattern is not None and pattern not in patterns.PATTERNS:
                raise ZoneError(f"Unknown pattern {pattern}")
            if effect is not None and effect not in patterns.PATTERN_EFFECTS:
                raise ZoneError(f"Unknown effect {effect}")
            changes = {k: v for k, v in (("pattern", pattern), ("effect", effect),
                                         ("speed", None if speed is None else max(1, min(100, int(speed))))) if v is not None}
            if changes:
                zone = replace(zone, **changes)
                base = self.model
                self._model = MapModel(base.runs, tuple(zone if z.id == zone.id else z for z in base.zones), base.backdrop)
                self._save()
            model = self.model
            plan = ZonePlan(zone, model.run_map())
            if plan.n == 0:
                raise ZoneError(f"{zone.name} has no runs")
            if not any(self.controller(s.run.entry_id) for s in plan.slices):
                raise ZoneError(f"None of the lights in {zone.name} are set up")
            if old := self.runners.pop(zone.id, None):
                old.stop(keep={s.run.entry_id for s in plan.slices})
            await self._launch(plan, zone.speed)
        self._notify()

    async def async_stop_zone(self, ref: str) -> None:
        zone = self.zone(ref)
        if runner := self.runners.pop(zone.id, None):
            runner.stop()
            self._notify()

    def async_stop_all(self) -> None:
        for runner in list(self.runners.values()):
            runner.stop(restore=False)
        self.runners.clear()

    async def async_set_speed(self, ref: str, speed: int) -> None:
        """Change the speed of a zone, live when it is running; it is kept as the zone's speed."""
        zone = self.zone(ref)
        speed = max(1, min(100, int(speed)))
        if self._model is None:  # defaults have no zones, so zone() has already raised
            return
        self._model = MapModel(self._model.runs, tuple(replace(z, speed=speed) if z.id == zone.id else z
                                                       for z in self._model.zones), self._model.backdrop)
        self._save()
        if runner := self.runners.get(zone.id):
            runner.set_speed(speed)


def get_manager(hass: HomeAssistant) -> MapManager | None:
    return hass.data.get(MAP_KEY)
