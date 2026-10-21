"""Buttons: Zeitprogramme jetzt übertragen, Werte aktualisieren."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .entity import device_info


async def async_setup_entry(hass, entry, async_add_entities):
    d = entry.runtime_data
    async_add_entities([TransferButton(d.manager, entry), RefreshButton(d.coordinator, entry),
                        TestNotifyButton(d.coordinator.problems, entry)])


class TransferButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Zeitprogramme jetzt übertragen"
    _attr_icon = "mdi:upload-multiple"

    def __init__(self, mgr, entry) -> None:
        self.mgr = mgr
        self._attr_unique_id = f"{entry.entry_id}_transfer_now"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        if not self.mgr.auto_enabled:
            raise HomeAssistantError("„Automatik aktiv“ ist ausgeschaltet – es wird nichts übertragen.")
        if self.mgr.busy:
            raise HomeAssistantError("Es läuft bereits eine Übertragung.")
        self.hass.async_create_task(self.mgr.async_run(full=True, reason="manuell, vollständig"))


class RefreshButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Werte aktualisieren"
    _attr_icon = "mdi:refresh"

    def __init__(self, coord, entry) -> None:
        self.coord = coord
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        await self.coord.async_request_refresh()


class TestNotifyButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Test-Benachrichtigung senden"
    _attr_icon = "mdi:bell-ring-outline"

    def __init__(self, tracker, entry) -> None:
        self.tracker = tracker
        self._attr_unique_id = f"{entry.entry_id}_test_notify"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        if not self.tracker._services():
            raise HomeAssistantError("Kein Benachrichtigungsdienst eingetragen (Konfigurieren → Allgemein).")
        errors = await self.tracker.async_send(
            "🔔 Kirchenheizung: Test", "Test-Benachrichtigung der Vitodata-100-Integration.")
        if errors:
            raise HomeAssistantError("; ".join(errors))
