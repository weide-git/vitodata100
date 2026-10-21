"""Gemeinsame Basis."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import CONF_DEVICE_ID, DOMAIN


def device_info(entry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, str(entry.data[CONF_DEVICE_ID]))},
        name="Vitodata 100", manufacturer="Viessmann", model="Vitocom 100 LAN1 (Cloud)",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://vitodata100.viessmann-climatesolutions.com/")
