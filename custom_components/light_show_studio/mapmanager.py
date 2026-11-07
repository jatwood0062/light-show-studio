"""The map of areas, runs and zones: stored, edited, and played (one ZoneRunner per running zone), and the
camera scan sessions that borrow a light to flash its pixel codes."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from . import mapping, patterns, scan_codes
from .const import CONF_BULBS, CONF_SEGMENTED, CONF_TYPE, DEFAULT_BULBS, DOMAIN, MAP_KEY, MAP_STORE_KEY, TYPE_PLAYER, TYPE_TWINKLY, TYPE_WLED
from .controller import C9Controller
from .mapped_patterns import ZonePlan
from .mapping import Area, LightSpec, MapError, MapModel, Zone
from .zoneplayer import ZoneRunner

_LOGGER = logging.getLogger(__name__)
SAVE_DELAY = 1.0
AD_HOC = "area:"  # id prefix of the zone made on the fly for "this area" (never saved)
SCAN_IDLE = 120.0  # seconds without a frame (or a stop) after which a scan lets the light go


class ZoneError(HomeAssistantError):
    """A zone can't be started or found; the message is shown to the person."""


class _Scan:
    """One light lent to a camera scan."""

    def __init__(self, ctl: C9Controller, first: int, count: int, token: object, mode: str = scan_codes.DEFAULT_MODE) -> None:
        self.ctl, self.first, self.count, self.token, self.mode = ctl, first, count, token, mode
        self.frames = scan_codes.frames_for(count, mode)
        self.handle: asyncio.TimerHandle | None = None


def _kind(entry: ConfigEntry) -> str:
    return {TYPE_TWINKLY: "twinkly", TYPE_WLED: "wled"}.get(entry.data.get(CONF_TYPE), "govee")


class MapManager:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        # the store's own version stays 1; the map inside carries its version (see mapping.migrate)
        self._store: Store[dict[str, Any]] = Store(hass, 1, MAP_STORE_KEY)
        self._backup: Store[dict[str, Any]] = Store(hass, 1, f"{MAP_STORE_KEY}.v1")  # a version 1 map, kept when migrated
        self._scans: dict[str, _Scan] = {}
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

    def _ha_area(self, entry: ConfigEntry) -> tuple[str | None, str | None]:
        """(area id, name) of the light's Home Assistant area: its device's, else one of its entities'."""
        try:
            area_id = next((d.area_id for d in dr.async_entries_for_config_entry(dr.async_get(self.hass), entry.entry_id)
                            if d.area_id), None)
            if area_id is None:
                area_id = next((x.area_id for x in er.async_entries_for_config_entry(er.async_get(self.hass), entry.entry_id)
                                if x.area_id), None)
            if area_id is None:
                return None, None
            area = ar.async_get(self.hass).async_get_area(area_id)
            return area_id, (area.name if area else area_id)
        except Exception:  # noqa: BLE001  a registry hiccup must not hide the map
            _LOGGER.debug("Could not look up the area of %s", entry.entry_id, exc_info=True)
            return None, None

    def light_specs(self) -> list[LightSpec]:
        out = []
        for e in self._entries():
            o = {**e.data, **e.options}
            ctl = self.controller(e.entry_id)
            layout = getattr(ctl, "layout", None) if ctl else None
            area_id, area_name = self._ha_area(e)
            out.append(LightSpec(e.entry_id, e.title, ctl.bulbs if ctl else int(o.get(CONF_BULBS, DEFAULT_BULBS)),
                                 _kind(e), layout, area_id, area_name))
        return out

    def default_areas(self) -> dict[str, Area]:
        """Where a run that names no area goes: its light's Home Assistant area, else an area named after it."""
        return {s.entry_id: mapping.light_area(s) for s in self.light_specs()}

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
            area = mapping.light_area(spec)
            row["area"] = {"id": area.id, "name": area.name, **({"ha_area_id": area.ha_area_id} if area.ha_area_id else {})}
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
            if data.get("version", mapping.VERSION) == 1:  # keep the old map as it was, then move it to version 2
                await self._backup.async_save(data)
                migrated = True
            else:
                migrated = False
            # lights may not be set up yet, so only config entries that are gone count as missing
            self._model = mapping.from_dict(data, {s.entry_id: s.bulbs for s in self.light_specs()}, strict=False,
                                            default_areas=self.default_areas())
            if migrated:
                self._save()
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
        model = mapping.from_dict(data, {s.entry_id: s.bulbs for s in self.light_specs()},
                                  default_areas=self.default_areas())
        async with self._lock:
            self._model = model
            self._save()
            for zone_id in list(self.runners):
                old = self.runners.pop(zone_id)
                if zone_id.startswith(AD_HOC):  # the zone made for an area follows that area's runs
                    zone = old.plan.zone if model.area(zone_id[len(AD_HOC):]) else None
                else:
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
                self._model = replace(base, zones=tuple(zone if z.id == zone.id else z for z in base.zones))
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

    async def async_start_area(self, ref: str, pattern: str | None = None, effect: str | None = None,
                               speed: int | None = None, mode: str = "string", axis: Any = None) -> None:
        """Start (or restart) a pattern on every run in an area, without saving a zone. Stopped with the zone
        id "area:<area id>"."""
        if pattern is not None and pattern not in patterns.PATTERNS:
            raise ZoneError(f"Unknown pattern {pattern}")
        if effect is not None and effect not in patterns.PATTERN_EFFECTS:
            raise ZoneError(f"Unknown effect {effect}")
        if mode not in mapping.MODES:
            raise ZoneError("Mode must be string or spatial")
        try:
            ax = mapping.parse_axis(axis if axis is not None else mapping.default_axis())
        except MapError as err:
            raise ZoneError(str(err)) from err
        async with self._lock:
            model = self.model
            if (area := model.area(ref)) is None:
                raise ZoneError(f"No area called {ref}")
            zone = Zone(AD_HOC + area.id, area.name, (), mode, ax, pattern or patterns.DEFAULT_PATTERN,
                        effect or patterns.CHASE, max(1, min(100, int(speed))) if speed is not None else patterns.DEFAULT_SPEED,
                        (area.id,))
            plan = ZonePlan(zone, model.run_map())
            if plan.n == 0:
                raise ZoneError(f"{area.name} has no runs")
            if not any(self.controller(s.run.entry_id) for s in plan.slices):
                raise ZoneError(f"None of the lights in {area.name} are set up")
            if old := self.runners.pop(zone.id, None):
                old.stop(keep={s.run.entry_id for s in plan.slices})
            await self._launch(plan, zone.speed)
        self._notify()

    async def async_stop_zone(self, ref: str) -> None:
        if ref not in self.runners and not ref.startswith(AD_HOC):
            ref = self.zone(ref).id
        if runner := self.runners.pop(ref, None):
            runner.stop()
            self._notify()

    async def async_stop_area(self, ref: str) -> None:
        area = self.model.area(ref)
        await self.async_stop_zone(AD_HOC + (area.id if area else ref))

    def async_stop_all(self) -> None:
        for runner in list(self.runners.values()):
            runner.stop(restore=False)
        self.runners.clear()
        for entry_id in list(self._scans):
            self.scan_stop(entry_id, restore=False)

    async def async_set_speed(self, ref: str, speed: int) -> None:
        """Change the speed of a zone, live when it is running; it is kept as the zone's speed."""
        speed = max(1, min(100, int(speed)))
        if ref.startswith(AD_HOC):  # a zone made for an area isn't saved: just the running one
            if (runner := self.runners.get(ref)) is None:
                raise ZoneError(f"No zone called {ref}")
            runner.set_speed(speed)
            return
        zone = self.zone(ref)
        if self._model is None:  # defaults have no zones, so zone() has already raised
            return
        self._model = replace(self._model, zones=tuple(replace(z, speed=speed) if z.id == zone.id else z
                                                       for z in self._model.zones))
        self._save()
        if runner := self.runners.get(zone.id):
            runner.set_speed(speed)


    # --- camera scan --------------------------------------------------------------------------
    @property
    def scanning(self) -> list[str]:
        return list(self._scans)

    async def async_scan_start(self, entry_id: str, token: object, first: int = 0, count: int | None = None,
                               mode: str = scan_codes.DEFAULT_MODE) -> dict[str, Any]:
        """Take a light for a scan: any effect or zone on it stops and it goes dark, ready for code frames. Only
        pixels first..first+count-1 are coded (the rest stay dark); the whole light by default. mode: "colour"
        (default: red/green/blue frames) or "white" (binary white flashes). Returns the number of pixels coded, of
        frames, the first pixel, the level of a lit channel and the mode. The scan ends on scan_stop, SCAN_IDLE seconds without a frame,
        or when something else takes the light."""
        if mode not in scan_codes.MODES:
            raise ZoneError(f"Scan mode must be one of {', '.join(scan_codes.MODES)}")
        ctl = self.controller(entry_id)
        if ctl is None:
            raise ZoneError("That light isn't set up")
        if not ctl.segmented:
            raise ZoneError("This light can't show separate bulbs, so it can't be scanned")
        if not 0 <= first < ctl.bulbs:
            raise ZoneError(f"First pixel must be between 0 and {ctl.bulbs - 1}")
        count = ctl.bulbs - first if count is None else count
        if not 1 <= count <= ctl.bulbs - first:
            raise ZoneError(f"Pixel count must be between 1 and {ctl.bulbs - first}")
        if old := self._scans.pop(entry_id, None):
            self._cancel_idle(old)
        scan = _Scan(ctl, first, count, token, mode)
        info = {"bulbs": count, "frames": scan.frames, "first": first, "level": scan_codes.level_for(mode), "mode": mode}
        self._scans[entry_id] = scan
        self._arm_idle(entry_id, scan)  # from now on, whatever happens, the light is let go
        try:
            await ctl.async_claim_scan()
            if self._scans.get(entry_id) is not scan:  # stopped or replaced while the light was turning on
                return info
            ctl.push_scan_frame([scan_codes.OFF] * ctl.bulbs)
        except BaseException:
            self.scan_stop(entry_id, token)
            raise
        return info

    async def async_scan_frame(self, entry_id: str, frame: int) -> None:
        scan = self._scans.get(entry_id)
        if scan is None:
            raise ZoneError("That light isn't being scanned")
        if not 0 <= frame < scan.frames:
            raise ZoneError(f"Frame must be between 0 and {scan.frames - 1}")
        colours = [scan_codes.OFF] * scan.ctl.bulbs
        colours[scan.first : scan.first + scan.count] = scan_codes.frame_colours_for(scan.count, frame, scan.mode)
        if not scan.ctl.push_scan_frame(colours):  # a person took the light
            self.scan_stop(entry_id, restore=False)
            raise ZoneError("The light was taken by something else, so the scan stopped")
        self._arm_idle(entry_id, scan)

    async def async_scan_segment(self, entry_id: str, start: int | None = None, count: int | None = None,
                                 colour: Sequence[int] | None = None, segments: Sequence[dict[str, Any]] | None = None) -> None:
        """Glow scan: light only the given stretch(es) of the light, everything else off. Either start/count (one
        stretch, in `colour`, white at the scan level by default) or `segments`: up to 3 {start, count, colour}
        stretches lit at once (colour defaults to red, green, blue in that order), which lets the phone find three
        anchors per picture. start is a pixel of the light, not of the scan, and every stretch must lie inside the
        scanned pixels and not overlap another."""
        scan = self._scans.get(entry_id)
        if scan is None:
            raise ZoneError("That light isn't being scanned")
        single = segments is None  # the old call: one white stretch
        if segments is None:
            if start is None or count is None:
                raise ZoneError("Give a start and count, or segments")
            segments = [{"start": start, "count": count, "colour": colour}]
        if not 1 <= len(segments) <= 3:
            raise ZoneError("Light between 1 and 3 stretches at a time")
        colours = [scan_codes.OFF] * scan.ctl.bulbs
        used: set[int] = set()
        for k, seg in enumerate(segments):
            s, c = seg["start"], seg["count"]
            if c < 1 or s < scan.first or s + c > scan.first + scan.count:
                raise ZoneError(f"The lit stretch must lie within pixels {scan.first} to {scan.first + scan.count - 1}")
            if used & set(range(s, s + c)):
                raise ZoneError("The lit stretches must not overlap")
            used.update(range(s, s + c))
            col = seg.get("colour")
            if col is None:
                col = scan_codes.SCAN_COLOUR if single else scan_codes.primary(k)
            col = tuple(col)
            if len(col) != 3 or not all(isinstance(v, int) and 0 <= v <= 255 for v in col):
                raise ZoneError("A colour is three numbers from 0 to 255")
            colours[s : s + c] = [col] * c
        if not scan.ctl.push_scan_frame(colours):  # a person took the light
            self.scan_stop(entry_id, restore=False)
            raise ZoneError("The light was taken by something else, so the scan stopped")
        self._arm_idle(entry_id, scan)

    @callback
    def scan_stop(self, entry_id: str, token: object | None = None, restore: bool = True) -> None:
        """End a scan and put the light back. With a token, only the scan that token started (a connection
        that closes late must not end a newer scan). Safe to call when nothing is scanned."""
        scan = self._scans.get(entry_id)
        if scan is None or (token is not None and scan.token is not token):
            return
        del self._scans[entry_id]
        self._cancel_idle(scan)
        scan.ctl.release_scan(restore)

    def _arm_idle(self, entry_id: str, scan: _Scan) -> None:
        self._cancel_idle(scan)
        scan.handle = self.hass.loop.call_later(SCAN_IDLE, self._scan_idle, entry_id, scan)

    @staticmethod
    def _cancel_idle(scan: _Scan) -> None:
        if scan.handle:
            scan.handle.cancel()
            scan.handle = None

    def _scan_idle(self, entry_id: str, scan: _Scan) -> None:
        scan.handle = None
        if self._scans.get(entry_id) is scan:
            _LOGGER.debug("Scan of %s timed out; letting the light go", entry_id)
            self.scan_stop(entry_id, scan.token)


def get_manager(hass: HomeAssistant) -> MapManager | None:
    return hass.data.get(MAP_KEY)
