"""Schalter „Automatik aktiv“ (standardmäßig aus)."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import device_info


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AutoSwitch(entry.runtime_data.manager, entry)])


class AutoSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "Automatik aktiv"
    _attr_icon = "mdi:robot"

    def __init__(self, mgr, entry) -> None:
        self.mgr = mgr
        self._attr_unique_id = f"{entry.entry_id}_auto"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        self.mgr.auto_enabled = bool(last and last.state == "on")
        self.mgr._notify()

    @property
    def is_on(self) -> bool:
        return self.mgr.auto_enabled

    async def async_turn_on(self, **kwargs) -> None:
        self.mgr.auto_enabled = True
        self.async_write_ha_state()
        self.mgr._notify()

    async def async_turn_off(self, **kwargs) -> None:
        self.mgr.auto_enabled = False
        self.async_write_ha_state()
        self.mgr._notify()
