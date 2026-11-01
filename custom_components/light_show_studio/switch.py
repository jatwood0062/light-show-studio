"""A switch per schedule: on = the schedule is enabled. Created and removed as schedules are."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SCHEDULER_KEY
from .scheduler import Scheduler


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if (scheduler := hass.data.get(SCHEDULER_KEY)) is not None:
        scheduler.attach_platform(async_add_entities)  # adds a switch for every schedule, and for later ones
        entry.async_on_unload(scheduler.detach_platform)


class ScheduleSwitch(SwitchEntity):
    _attr_should_poll = False

    def __init__(self, scheduler: Scheduler, schedule_id: str) -> None:
        self._scheduler = scheduler
        self.schedule_id = schedule_id
        self._attr_unique_id = f"{DOMAIN}_schedule_{schedule_id}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, "show_player")}, name="Light Show Player",
                                            manufacturer="Light Show Studio")
        self._attr_name = self._title()

    def _title(self) -> str:
        s = self._scheduler.get_schedule(self.schedule_id)
        return f"Light Show Schedule {s['name']}" if s else "Light Show Schedule"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._scheduler.add_listener(self._changed))

    def _changed(self) -> None:
        self._attr_name = self._title()
        if self.hass is not None and self.entity_id:
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        s = self._scheduler.get_schedule(self.schedule_id)
        return bool(s and s["enabled"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._scheduler.get_schedule(self.schedule_id)
        return {"schedule_id": self.schedule_id, "state": self._scheduler.schedule_state(self.schedule_id),
                "playlist_id": s["playlist_id"] if s else None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._scheduler.async_set_enabled(self.schedule_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._scheduler.async_set_enabled(self.schedule_id, False)
