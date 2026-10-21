"""Einrichtung und Optionen für Vitodata 100."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import VitodataAuthError, VitodataClient, VitodataError
from .const import (
    CONF_CIRCUITS, CONF_DEVICE_ID, DEFAULT_PREHEAT, DEFAULT_SCAN_INTERVAL, DEFAULT_STOP_BEFORE,
    DEFAULT_WATCH, DEFAULT_WATCH_INTERVAL, DEFAULT_WRITE_TIME, DOMAIN, MODE_CALENDAR, MODE_FIXED,
    MODE_NONE, OPT_CALENDARS, OPT_FIXED, OPT_MODE, OPT_PREHEAT, OPT_SCAN_INTERVAL, OPT_STOP_BEFORE,
    OPT_WATCH, OPT_WATCH_INTERVAL, OPT_WRITE_TIME, OPT_NOTIFY, OPT_STALE_HOURS, DEFAULT_STALE_HOURS,
)
from .planner import parse_fixed_plan

_LOGGER = logging.getLogger(__name__)


def _minutes(min_v: int, max_v: int, step: int = 1) -> selector.NumberSelector:
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=min_v, max=max_v, step=step, unit_of_measurement="min",
        mode=selector.NumberSelectorMode.BOX))


class VitodataConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._user: str = ""
        self._pw: str = ""
        self._devices: list[int] = []

    def _probe(self, device_id: int | None):
        c = VitodataClient(self._user, self._pw)
        try:
            c.login()
            devices = c.list_devices()
            if device_id is None:
                if len(devices) != 1:
                    return devices, None
                device_id = devices[0]
            c.open_device(device_id)
            return [device_id], c.discover_circuits()
        finally:
            c.close()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._user = user_input[CONF_USERNAME].strip()
            self._pw = user_input[CONF_PASSWORD]
            try:
                devices, circuits = await self.hass.async_add_executor_job(self._probe, None)
            except VitodataAuthError:
                errors["base"] = "invalid_auth"
            except (VitodataError, OSError) as err:
                _LOGGER.warning("Vitodata: %s", err)
                errors["base"] = "cannot_connect"
            else:
                if not devices:
                    errors["base"] = "no_devices"
                elif circuits is None:
                    self._devices = devices
                    return await self.async_step_device()
                else:
                    return await self._async_finish(devices[0], circuits)
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_USERNAME, default=self._user): str,
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }))

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            dev = int(user_input[CONF_DEVICE_ID])
            try:
                _, circuits = await self.hass.async_add_executor_job(self._probe, dev)
            except (VitodataError, OSError):
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish(dev, circuits)
        return self.async_show_form(step_id="device", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_DEVICE_ID): selector.SelectSelector(selector.SelectSelectorConfig(
                options=[str(d) for d in self._devices])),
        }))

    async def _async_finish(self, device_id: int, circuits: list[dict]) -> ConfigFlowResult:
        await self.async_set_unique_id(f"{self._user.lower()}_{device_id}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Vitodata 100 ({device_id})",
            data={CONF_USERNAME: self._user, CONF_PASSWORD: self._pw,
                  CONF_DEVICE_ID: device_id, CONF_CIRCUITS: circuits},
            options={OPT_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL, OPT_WRITE_TIME: DEFAULT_WRITE_TIME,
                     OPT_WATCH: DEFAULT_WATCH, OPT_WATCH_INTERVAL: DEFAULT_WATCH_INTERVAL})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return VitodataOptionsFlow()


class VitodataOptionsFlow(OptionsFlow):
    """Menü: Allgemein | Heizkreis wählen → Heizkreis-Einstellungen."""

    def __init__(self) -> None:
        self._cid: str | None = None

    def _circuits(self) -> list[dict]:
        return self.config_entry.data.get(CONF_CIRCUITS, [])

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["general", "select_circuit"])

    async def async_step_general(self, user_input=None) -> ConfigFlowResult:
        o = self.config_entry.options
        if user_input is not None:
            data = {**o, **user_input}
            for k in (OPT_SCAN_INTERVAL, OPT_WATCH_INTERVAL, OPT_STALE_HOURS):
                data[k] = int(data[k])
            data[OPT_NOTIFY] = (data.get(OPT_NOTIFY) or "").strip()
            return self.async_create_entry(data=data)
        return self.async_show_form(step_id="general", data_schema=vol.Schema({
            vol.Required(OPT_SCAN_INTERVAL, default=o.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)):
                _minutes(5, 10080),
            vol.Required(OPT_WRITE_TIME, default=o.get(OPT_WRITE_TIME, DEFAULT_WRITE_TIME)):
                selector.TimeSelector(),
            vol.Required(OPT_WATCH, default=o.get(OPT_WATCH, DEFAULT_WATCH)): selector.BooleanSelector(),
            vol.Required(OPT_WATCH_INTERVAL, default=o.get(OPT_WATCH_INTERVAL, DEFAULT_WATCH_INTERVAL)):
                _minutes(5, 240),
            vol.Optional(OPT_NOTIFY, default=o.get(OPT_NOTIFY, "")): selector.TextSelector(),
            vol.Required(OPT_STALE_HOURS, default=o.get(OPT_STALE_HOURS, DEFAULT_STALE_HOURS)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=168, step=1, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)),
        }))

    async def async_step_select_circuit(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._cid = user_input["circuit"]
            return await self.async_step_circuit()
        opts = [selector.SelectOptionDict(value=c["id"], label=f'{c["id"]} – {c["name"]}')
                for c in self._circuits()]
        return self.async_show_form(step_id="select_circuit", data_schema=vol.Schema({
            vol.Required("circuit"): selector.SelectSelector(selector.SelectSelectorConfig(options=opts)),
        }))

    async def async_step_circuit(self, user_input=None) -> ConfigFlowResult:
        cid = self._cid
        o = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None and "mode" in user_input:
            mode = user_input["mode"]
            fixed = user_input.get("fixed", "") or ""
            if mode == MODE_FIXED:
                try:
                    parse_fixed_plan(fixed)
                except ValueError:
                    errors["fixed"] = "invalid_plan"
            if mode == MODE_CALENDAR and not user_input.get("calendars"):
                errors["calendars"] = "no_calendar"
            if not errors:
                data = dict(o)
                data[OPT_MODE.format(cid)] = mode
                data[OPT_CALENDARS.format(cid)] = list(user_input.get("calendars", []))
                data[OPT_PREHEAT.format(cid)] = int(user_input["preheat"])
                data[OPT_STOP_BEFORE.format(cid)] = int(user_input["stop_before"])
                data[OPT_FIXED.format(cid)] = fixed
                return self.async_create_entry(data=data)

        cur = user_input or {}
        name = next((c["name"] for c in self._circuits() if c["id"] == cid), cid)
        schema = vol.Schema({
            vol.Required("mode", default=cur.get("mode", o.get(OPT_MODE.format(cid), MODE_NONE))):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=[MODE_CALENDAR, MODE_FIXED, MODE_NONE], translation_key="mode")),
            vol.Optional("calendars", default=cur.get("calendars", o.get(OPT_CALENDARS.format(cid), []))):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="calendar", multiple=True)),
            vol.Required("preheat", default=cur.get("preheat", o.get(OPT_PREHEAT.format(cid), DEFAULT_PREHEAT))):
                _minutes(0, 1440, 10),
            vol.Required("stop_before", default=cur.get(
                "stop_before", o.get(OPT_STOP_BEFORE.format(cid), DEFAULT_STOP_BEFORE))): _minutes(0, 600, 10),
            vol.Optional("fixed", default=cur.get("fixed", o.get(OPT_FIXED.format(cid), ""))):
                selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
        })
        return self.async_show_form(step_id="circuit", data_schema=schema, errors=errors,
                                    description_placeholders={"circuit": f"{cid} – {name}"})
