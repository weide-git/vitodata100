"""Vitodata 100 – Heizungssteuerung über das Viessmann-Portal."""
from __future__ import annotations

from datetime import timedelta
from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.start import async_at_started

from .const import (
    DOMAIN, DEFAULT_WATCH, DEFAULT_WATCH_INTERVAL, DEFAULT_WRITE_TIME, OPT_WATCH, OPT_WATCH_INTERVAL,
    OPT_WRITE_TIME,
)
from .coordinator import ScheduleManager, VitodataCoordinator
from .problems import ProblemTracker

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.BUTTON]


@dataclass
class VitodataData:
    coordinator: VitodataCoordinator
    manager: ScheduleManager


type VitodataConfigEntry = ConfigEntry[VitodataData]


async def async_setup_entry(hass: HomeAssistant, entry: VitodataConfigEntry) -> bool:
    coordinator = VitodataCoordinator(hass, entry)
    trackers = hass.data.setdefault(DOMAIN, {})
    tracker = trackers.get(entry.entry_id)
    if tracker is None:
        tracker = trackers[entry.entry_id] = ProblemTracker(hass, entry)
    tracker.entry = entry
    coordinator.problems = tracker
    manager = ScheduleManager(hass, entry, coordinator)
    await manager.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = VitodataData(coordinator, manager)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # nächtliche Übertragung zur eingestellten Uhrzeit
    h, m, s = (int(x) for x in str(entry.options.get(OPT_WRITE_TIME, DEFAULT_WRITE_TIME)).split(":")[:3])

    async def _nightly(_now) -> None:
        await manager.async_run(reason="nächtliche Übertragung")

    entry.async_on_unload(async_track_time_change(hass, _nightly, hour=h, minute=m, second=s))

    # Kalenderüberwachung (abschaltbar)
    if entry.options.get(OPT_WATCH, DEFAULT_WATCH):
        minutes = int(entry.options.get(OPT_WATCH_INTERVAL, DEFAULT_WATCH_INTERVAL))

        async def _watch(_now) -> None:
            await manager.async_watch()

        entry.async_on_unload(async_track_time_interval(hass, _watch, timedelta(minutes=max(5, minutes))))

    # Vorschau berechnen, sobald HA (und damit die Kalender) vollständig gestartet ist – schreibt nichts
    async def _preview(_hass=None) -> None:
        await manager._async_refresh_calendars()
        await manager.async_compute()

    entry.async_on_unload(async_at_started(hass, _preview))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: VitodataConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: VitodataConfigEntry) -> None:
    from homeassistant.helpers import issue_registry as ir
    tracker = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    for key in list(tracker.active) if tracker else []:
        ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_{key.replace(':', '_')}")


async def async_unload_entry(hass: HomeAssistant, entry: VitodataConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
