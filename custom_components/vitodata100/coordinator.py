"""Datenabruf (Coordinator) und Zeitprogramm-Übertragung (Manager)."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import DAY_CKB, VitodataAuthError, VitodataClient, VitodataError
from .const import (
    CONF_CIRCUITS, CONF_DEVICE_ID, DEFAULT_PREHEAT, DEFAULT_SCAN_INTERVAL, DEFAULT_STOP_BEFORE,
    DOMAIN, MODE_CALENDAR, MODE_FIXED, MODE_NONE, OPT_CALENDARS, OPT_FIXED, OPT_MODE, OPT_PREHEAT,
    OPT_SCAN_INTERVAL, OPT_STOP_BEFORE, WATCH_HORIZON_DAYS,
)
from .planner import Program, async_calendar_program, parse_fixed_plan
from .problems import ProblemTracker, parse_portal_timestamp
from .const import DEFAULT_STALE_HOURS, OPT_STALE_HOURS, PORTAL_FAILS_BEFORE_ALARM

_LOGGER = logging.getLogger(__name__)


class VitodataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Liest regelmäßig die Statuswerte aus dem Portal."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        minutes = int(entry.options.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(minutes=max(1, minutes)))
        self.entry = entry
        self.lock = asyncio.Lock()          # nur eine Portal-Sitzung gleichzeitig
        self.first = True
        self.problems: ProblemTracker | None = None

    def client(self) -> VitodataClient:
        return VitodataClient(self.entry.data[CONF_USERNAME], self.entry.data[CONF_PASSWORD])

    def _fetch(self, refresh: bool) -> dict[str, Any]:
        c = self.client()
        try:
            c.connect(self.entry.data[CONF_DEVICE_ID])
            if refresh:
                c.refresh_from_device()
            return c.read_status()
        finally:
            c.close()

    async def _async_update_data(self) -> dict[str, Any]:
        # Beim Start nur lesen (schnell); danach jeweils mit „Aktualisieren“.
        refresh = not self.first
        self.first = False
        async with self.lock:
            try:
                data = await self.hass.async_add_executor_job(self._fetch, refresh)
            except Exception as err:  # noqa: BLE001
                msg = f"Login fehlgeschlagen: {err}" if isinstance(err, VitodataAuthError) else str(err)
                if self.problems:
                    self.problems.portal_fails += 1
                    n = self.problems.portal_fails
                    if n >= PORTAL_FAILS_BEFORE_ALARM:
                        await self.problems.async_set("portal", f"{msg} ({n}× in Folge)")
                raise UpdateFailed(msg) from err
        if self.problems:
            self.problems.portal_fails = 0
            await self.problems.async_set("portal", None)
            await self._check_stale(data)
        return data

    async def _check_stale(self, data: dict) -> None:
        hours = int(self.entry.options.get(OPT_STALE_HOURS, DEFAULT_STALE_HOURS) or 0)
        ts = parse_portal_timestamp(data.get("timestamp"))
        if hours <= 0 or ts is None:
            await self.problems.async_set("stale", None)
            return
        age = (dt_util.now() - ts).total_seconds() / 3600
        await self.problems.async_set(
            "stale", f"letzter Datenstand {data.get('timestamp')} ({age:.0f} h alt)" if age > hours else None)


class ScheduleManager:
    """Berechnet Wochenprogramme und überträgt Änderungen ins Portal."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: VitodataCoordinator):
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
        self.written: dict[str, dict[str, list]] = {}
        self.plans: dict[str, Program] = {}
        self.warnings: dict[str, list[str]] = {}
        self.auto_enabled = False
        self.last_transfer = None
        self.last_result = "noch keine Übertragung"
        self.last_error: str | None = None
        self.busy = False
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------ Infrastruktur
    async def async_load(self) -> None:
        data = await self.store.async_load() or {}
        self.written = data.get("written", {})
        self.last_result = data.get("last_result", self.last_result)
        ts = data.get("last_transfer")
        self.last_transfer = dt_util.parse_datetime(ts) if ts else None

    async def _async_save(self) -> None:
        await self.store.async_save({
            "written": self.written, "last_result": self.last_result,
            "last_transfer": self.last_transfer.isoformat() if self.last_transfer else None})

    @callback
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def circuits(self) -> list[dict[str, str]]:
        return self.entry.data.get(CONF_CIRCUITS, [])

    def mode(self, cid: str) -> str:
        return self.entry.options.get(OPT_MODE.format(cid), MODE_NONE)

    def managed(self) -> list[str]:
        return [c["id"] for c in self.circuits() if self.mode(c["id"]) != MODE_NONE]

    def written_program(self, cid: str) -> Program | None:
        raw = self.written.get(cid)
        if raw is None:
            return None
        return {int(k): [list(p) for p in v] for k, v in raw.items()}

    # ------------------------------------------------------------ Berechnung
    async def async_compute(self) -> dict[str, dict]:
        """Berechnet alle Programme. Rückgabe je Kreis: {'program', 'dates'}."""
        result = {}
        opts = self.entry.options
        for cid in self.managed():
            mode = self.mode(cid)
            dates = {}
            warnings: list[str] = []
            try:
                if mode == MODE_CALENDAR:
                    prog, warnings, dates = await async_calendar_program(
                        self.hass, list(opts.get(OPT_CALENDARS.format(cid), [])),
                        int(opts.get(OPT_PREHEAT.format(cid), DEFAULT_PREHEAT)),
                        int(opts.get(OPT_STOP_BEFORE.format(cid), DEFAULT_STOP_BEFORE)))
                elif mode == MODE_FIXED:
                    prog = parse_fixed_plan(opts.get(OPT_FIXED.format(cid), ""))
                else:
                    continue
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Programm für %s nicht berechenbar: %s", cid, err)
                self.warnings[cid] = [f"Fehler: {err}"]
                await self.coordinator.problems.async_set(f"plan:{cid}", f"{cid}: {err}")
                continue
            await self.coordinator.problems.async_set(f"plan:{cid}", None)
            self.plans[cid] = prog
            _LOGGER.debug("Programm %s: %s", cid, prog)
            self.warnings[cid] = warnings
            result[cid] = {"program": prog, "dates": dates}
        self._notify()
        return result

    async def _async_refresh_calendars(self) -> None:
        """Abonnierte Kalender neu laden (z. B. Remote Calendar lädt sonst nur alle 24 h)."""
        cals = sorted({c for cid in self.managed() if self.mode(cid) == MODE_CALENDAR
                       for c in self.entry.options.get(OPT_CALENDARS.format(cid), [])})
        if not cals:
            return
        try:
            await self.hass.services.async_call(
                "homeassistant", "update_entity", {"entity_id": cals}, blocking=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Kalender-Aktualisierung fehlgeschlagen: %s", err)

    def in_sync(self, cid: str) -> bool:
        return cid in self.plans and self.written_program(cid) == self.plans[cid]

    # ------------------------------------------------------------ Übertragung
    async def async_run(self, *, full: bool = False, horizon_days: int | None = None,
                        reason: str = "") -> None:
        """Programme berechnen und – falls Automatik aktiv – geänderte Tage schreiben."""
        await self._async_refresh_calendars()
        computed = await self.async_compute()
        if not self.auto_enabled:
            _LOGGER.debug("Automatik aus – nur Vorschau (%s)", reason)
            return

        today = dt_util.now().date()
        jobs: dict[str, dict[int, list[list[int]]]] = {}
        for cid, info in computed.items():
            prog, dates = info["program"], info["dates"]
            old = None if full else self.written_program(cid)
            days = []
            for wd in range(7):
                if old is not None and old.get(wd) == prog[wd]:
                    continue
                if horizon_days is not None and dates:
                    if (dates[wd] - today).days >= horizon_days:
                        continue
                days.append(wd)
            if days:
                jobs[cid] = {wd: prog[wd] for wd in days}
        if not jobs:
            return

        self.busy = True
        self._notify()
        async with self.coordinator.lock:
            try:
                done, err = await self.hass.async_add_executor_job(self._write_jobs, jobs)
            finally:
                self.busy = False
        for cid, wds in done.items():
            entry = self.written.setdefault(cid, {})
            for wd in wds:
                entry[str(wd)] = jobs[cid][wd]
        self.last_transfer = dt_util.now()
        n = sum(len(v) for v in done.values())
        self.last_error = err
        self.last_result = (f"{n} Tag(e) übertragen ({reason})" if not err
                            else f"Teilweise fehlgeschlagen nach {n} Tag(en): {err}")
        if err:
            _LOGGER.error("Übertragung fehlgeschlagen: %s", err)
        else:
            _LOGGER.info("Vitodata: %s", self.last_result)
        await self.coordinator.problems.async_set("transfer", err)
        await self._async_save()
        self._notify()

    def _write_jobs(self, jobs: dict[str, dict[int, list[list[int]]]]):
        done: dict[str, list[int]] = {}
        c = self.coordinator.client()
        try:
            c.connect(self.entry.data[CONF_DEVICE_ID])
            for cid, days in jobs.items():
                # ganze Woche identisch → ein Speichervorgang „Montag-Sonntag“
                if len(days) == 7 and all(days[d] == days[0] for d in range(7)):
                    c.write_day_program(cid, ["MondayTillSunday"], _hours(days[0]))
                    done[cid] = list(range(7))
                    continue
                for wd, phases in sorted(days.items()):
                    c.write_day_program(cid, [DAY_CKB[wd]], _hours(phases))
                    done.setdefault(cid, []).append(wd)
            return done, None
        except Exception as err:  # noqa: BLE001
            return done, str(err)
        finally:
            c.close()

    async def async_watch(self) -> None:
        """Kalenderüberwachung: nur dringende Änderungen (heute/morgen) sofort übertragen."""
        if self.busy:
            return
        await self.async_run(horizon_days=WATCH_HORIZON_DAYS, reason="Kalenderänderung")


def _hours(phases: list[list[int]]) -> list[tuple[float, float]]:
    return [(a / 60, b / 60) for a, b in phases]
