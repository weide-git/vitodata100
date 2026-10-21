"""Sensoren: Portalwerte, geplante Zeitprogramme, Übertragungsstatus."""
from __future__ import annotations

import re

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import WEEKDAYS_DE
from .entity import device_info
from .planner import fmt_phases

NUM_RE = re.compile(r"^(-?\d+(?:,\d+)?)\s*(°C|%|K)?$")
UNITS = {"°C": UnitOfTemperature.CELSIUS, "%": "%", "K": UnitOfTemperature.KELVIN}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback):
    data = entry.runtime_data
    coord, mgr = data.coordinator, data.manager
    ents: list[SensorEntity] = []
    groups = (coord.data or {}).get("groups", {})
    counts: dict[str, int] = {}
    for values in groups.values():
        for key in values:
            counts[key] = counts.get(key, 0) + 1
    for group, values in groups.items():
        for key, val in values.items():
            name = key if counts[key] == 1 else f"{key} ({group})"
            ents.append(PortalValueSensor(coord, entry, group, key, val, name))
    ents.append(PortalTimestampSensor(coord, entry))
    for c in mgr.circuits():
        if c["id"] in mgr.managed():
            ents.append(PlanSensor(mgr, entry, c["id"], c["name"]))
    ents.append(TransferSensor(mgr, entry))
    async_add_entities(ents)


class PortalValueSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coord, entry, group: str, key: str, sample: str, name: str) -> None:
        super().__init__(coord)
        self._group, self._key = group, key
        self._attr_unique_id = f"{entry.entry_id}_{slugify(group)}_{slugify(key)}"
        self._attr_name = name
        self._attr_device_info = device_info(entry)
        m = NUM_RE.match(sample or "")
        self._numeric = bool(m)
        if m:
            unit = m.group(2)
            if unit:
                self._attr_native_unit_of_measurement = UNITS[unit]
            if unit == "°C":
                self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_extra_state_attributes = {"gruppe": group}

    @property
    def native_value(self):
        raw = (self.coordinator.data or {}).get("groups", {}).get(self._group, {}).get(self._key)
        if raw is None:
            return None
        if self._numeric:
            m = NUM_RE.match(raw)
            return float(m.group(1).replace(",", ".")) if m else None
        return raw[:255]


class PortalTimestampSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Datenstand Portal"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coord, entry) -> None:
        super().__init__(coord)
        self._attr_unique_id = f"{entry.entry_id}_datenstand"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("timestamp")


class _ManagerEntity(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, mgr, entry) -> None:
        self.mgr = mgr
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.mgr.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()


class PlanSensor(_ManagerEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, mgr, entry, cid: str, name: str) -> None:
        super().__init__(mgr, entry)
        self.cid = cid
        self._attr_name = f"Zeitprogramm {cid} ({name})"
        self._attr_unique_id = f"{entry.entry_id}_plan_{cid}"

    @property
    def native_value(self):
        if any(str(w).startswith("Fehler") for w in self.mgr.warnings.get(self.cid, [])):
            return "Fehler"
        if self.cid not in self.mgr.plans:
            return "noch nicht berechnet"
        if self.mgr.in_sync(self.cid):
            return "übertragen"
        return "ausstehend" if self.mgr.auto_enabled else "Vorschau"

    @property
    def extra_state_attributes(self):
        plan = self.mgr.plans.get(self.cid, {})
        written = self.mgr.written_program(self.cid) or {}
        attrs = {"modus": self.mgr.mode(self.cid)}
        for wd, name in enumerate(WEEKDAYS_DE):
            attrs[name] = fmt_phases(plan.get(wd, []))
        attrs["im Portal (zuletzt geschrieben)"] = {
            WEEKDAYS_DE[wd]: fmt_phases(written[wd]) for wd in sorted(written)} or "–"
        attrs["hinweise"] = self.mgr.warnings.get(self.cid) or []
        return attrs


class TransferSensor(_ManagerEntity):
    _attr_name = "Letzte Übertragung"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:upload"

    def __init__(self, mgr, entry) -> None:
        super().__init__(mgr, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_transfer"

    @property
    def native_value(self):
        return self.mgr.last_transfer

    @property
    def extra_state_attributes(self):
        return {"ergebnis": self.mgr.last_result, "fehler": self.mgr.last_error,
                "läuft gerade": self.mgr.busy, "automatik aktiv": self.mgr.auto_enabled}
