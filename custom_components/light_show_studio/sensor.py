"""Sensors for the scheduler (when the next show starts, what the scheduler is doing), and for each light how well
its frames are getting out (diagnostic)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_TYPE, DOMAIN, SCHEDULER_KEY, TYPE_PLAYER
from .controller import C9Controller
from .scheduler import STATUSES, Scheduler


STATS_EVERY = timedelta(seconds=10)  # the stats change on every frame; the recorder gets them this often at most

# key in LinkStats.snapshot(), unique id suffix, name, unit, state class, enabled by default
LINK_SENSORS = (
    ("fps", "frame_rate", "Frame rate", "fps", SensorStateClass.MEASUREMENT, True),
    ("gap_ms", "longest_gap", "Longest gap", UnitOfTime.MILLISECONDS, SensorStateClass.MEASUREMENT, True),
    ("failed", "failed_sends", "Failed sends", None, SensorStateClass.TOTAL_INCREASING, True),
    ("late", "late_frames", "Late frames", None, SensorStateClass.TOTAL_INCREASING, False),
    ("merged", "merged_frames", "Merged frames", None, SensorStateClass.TOTAL_INCREASING, False),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if entry.data.get(CONF_TYPE) == TYPE_PLAYER:
        if (scheduler := hass.data.get(SCHEDULER_KEY)) is not None:
            async_add_entities([NextShowSensor(scheduler), ScheduleStatusSensor(scheduler)])
        return
    ctl: C9Controller = entry.runtime_data
    uid = entry.unique_id or entry.data[CONF_HOST]  # as light.py: the same device, and it stays put when the IP changes
    sensors = [LinkSensor(ctl, uid, *spec) for spec in LINK_SENSORS]
    async_add_entities(sensors)

    @callback
    def update(_now: datetime) -> None:  # one timer and one snapshot for the light's five sensors
        snap = ctl.stats.snapshot()
        for sensor in sensors:
            sensor.show(snap)

    entry.async_on_unload(async_track_time_interval(hass, update, STATS_EVERY))


class LinkSensor(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, ctl: C9Controller, uid: str, field: str, key: str, name: str, unit: str | None,
                 state_class: SensorStateClass, enabled: bool) -> None:
        self._field = field
        self._attr_unique_id = f"{uid}_{key}"
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_entity_registry_enabled_default = enabled
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, uid)})
        self._attr_native_value = ctl.stats.snapshot()[field]

    @callback
    def show(self, snap: dict[str, Any]) -> None:
        self._attr_native_value = snap[self._field]
        if self.hass is not None and self.enabled:  # a disabled sensor is never added
            self.async_write_ha_state()


class _Base(SensorEntity):
    _attr_should_poll = False

    def __init__(self, scheduler: Scheduler, key: str, name: str) -> None:
        self._scheduler = scheduler
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, "show_player")}, name="Light Show Player",
                                            manufacturer="Light Show Studio")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._scheduler.add_listener(self.async_write_ha_state))


class NextShowSensor(_Base):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, scheduler: Scheduler) -> None:
        super().__init__(scheduler, "next_show", "Next show")

    @property
    def native_value(self) -> datetime | None:
        nxt = self._scheduler.next_info
        return nxt["start"] if nxt else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        nxt = self._scheduler.next_info or {}
        end = nxt.get("end")
        return {"schedule": nxt.get("schedule"), "playlist": nxt.get("playlist"),
                "ends": end.isoformat() if end else None}


class ScheduleStatusSensor(_Base):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES

    def __init__(self, scheduler: Scheduler) -> None:
        super().__init__(scheduler, "schedule_status", "Schedule status")

    @property
    def native_value(self) -> str:
        return self._scheduler.status()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active = self._scheduler.active_info() or {}
        return {"schedule": active.get("schedule"), "ends": active.get("end")}
