"""Binärsensor „Problem“ – an, sobald irgendein Problem aktiv ist."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import callback

from .entity import device_info


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ProblemSensor(entry.runtime_data.coordinator.problems, entry)])


class ProblemSensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_should_poll = False

    def __init__(self, tracker, entry) -> None:
        self.tracker = tracker
        self._attr_unique_id = f"{entry.entry_id}_problem"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.tracker.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return bool(self.tracker.active)

    @property
    def extra_state_attributes(self):
        return {"probleme": [f"{v['label']}: {v['detail']} (seit {v['seit']})"
                             for v in self.tracker.active.values()]}
