"""Sensors for the scheduler: when the next show starts, and what the scheduler is doing."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SCHEDULER_KEY
from .scheduler import STATUSES, Scheduler


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if (scheduler := hass.data.get(SCHEDULER_KEY)) is not None:
        async_add_entities([NextShowSensor(scheduler), ScheduleStatusSensor(scheduler)])


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
        super().__init__(scheduler, "next_show", "Light Show Next Show")

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
        super().__init__(scheduler, "schedule_status", "Light Show Schedule Status")

    @property
    def native_value(self) -> str:
        return self._scheduler.status()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active = self._scheduler.active_info() or {}
        return {"schedule": active.get("schedule"), "ends": active.get("end")}
