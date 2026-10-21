"""Berechnung der Wochenprogramme (Kalender oder fester Plan).

Ein Programm ist ein dict  Wochentag (0=Mo … 6=So) -> Liste von [start_min, end_min]
(Minuten seit Mitternacht, 10-Minuten-Raster, max. 4 Phasen, end_min bis 1440).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
import re

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

STEP = 10
MAX_PHASES = 4
DAY_TOKENS = {"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6}

Program = dict[int, list[list[int]]]


def empty_program() -> Program:
    return {d: [] for d in range(7)}


def fmt_phases(phases: list[list[int]]) -> str:
    if not phases:
        return "–"

    def t(m):
        return f"{m // 60:02d}:{m % 60:02d}"

    return ", ".join(f"{t(a)}–{t(b)}" for a, b in phases)


def _merge_and_limit(windows: list[list[int]]) -> tuple[list[list[int]], bool]:
    """Sortieren, Überlappungen zusammenlegen, auf max. 4 Phasen reduzieren."""
    ws = sorted([list(w) for w in windows if w[1] > w[0]])
    merged: list[list[int]] = []
    for w in ws:
        if merged and w[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], w[1])
        else:
            merged.append(w)
    reduced = False
    while len(merged) > MAX_PHASES:
        gaps = [merged[i + 1][0] - merged[i][1] for i in range(len(merged) - 1)]
        i = gaps.index(min(gaps))
        merged[i] = [merged[i][0], merged[i + 1][1]]
        del merged[i + 1]
        reduced = True
    return merged, reduced


# ------------------------------------------------------------------ fester Plan
def parse_fixed_plan(text: str) -> Program:
    """Format je Zeile: '<Tage> HH:MM-HH:MM[, HH:MM-HH:MM …]'
    Tage: Mo,Di,Mi,Do,Fr,Sa,So, Bereiche wie Mo-Fr, Listen wie Mo,Mi. Spätere Zeilen überschreiben."""
    prog = empty_program()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z,\-\s]+?)\s+(\d.*)$", line)
        if not m:
            raise ValueError(f"Zeile nicht verständlich: {line}")
        days = _parse_days(m.group(1))
        phases = []
        for part in re.split(r"[,;]\s*", m.group(2).strip()):
            pm = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", part.strip())
            if not pm:
                raise ValueError(f"Zeitraum nicht verständlich: {part}")
            a = int(pm[1]) * 60 + int(pm[2])
            b = int(pm[3]) * 60 + int(pm[4])
            if a % STEP or b % STEP:
                raise ValueError(f"Nur 10-Minuten-Schritte erlaubt: {part}")
            if not 0 <= a < b <= 1440:
                raise ValueError(f"Ungültiger Zeitraum: {part}")
            phases.append([a, b])
        merged, reduced = _merge_and_limit(phases)
        if reduced:
            raise ValueError(f"Mehr als 4 Phasen: {line}")
        for d in days:
            prog[d] = [list(p) for p in merged]
    return prog


def _parse_days(spec: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"\s*,\s*", spec.strip().lower()):
        if "-" in part:
            a, b = [x.strip() for x in part.split("-", 1)]
            if a not in DAY_TOKENS or b not in DAY_TOKENS:
                raise ValueError(f"Unbekannter Tag: {part}")
            i, j = DAY_TOKENS[a], DAY_TOKENS[b]
            rng = list(range(i, j + 1)) if i <= j else list(range(i, 7)) + list(range(0, j + 1))
            out.extend(rng)
        elif part in DAY_TOKENS:
            out.append(DAY_TOKENS[part])
        else:
            raise ValueError(f"Unbekannter Tag: {part}")
    return out


# ------------------------------------------------------------------ Kalender
def _to_local(value) -> tuple[datetime | None, bool]:
    """(datetime lokal, ganztägig?) aus einem Event-Feld."""
    if isinstance(value, datetime):
        return dt_util.as_local(value), False
    if isinstance(value, date):
        return None, True
    if isinstance(value, str):
        dtv = dt_util.parse_datetime(value)
        if dtv is not None and ("T" in value or " " in value):
            if dtv.tzinfo is None:
                dtv = dtv.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            return dt_util.as_local(dtv), False
        return None, True
    return None, True


async def async_fetch_events(hass: HomeAssistant, calendars: list[str],
                             start: datetime, end: datetime) -> list[tuple[datetime, datetime, str]]:
    if not calendars:
        return []
    resp = await hass.services.async_call(
        "calendar", "get_events",
        {"entity_id": calendars, "start_date_time": start.isoformat(), "end_date_time": end.isoformat()},
        blocking=True, return_response=True)
    events = []
    for cal in calendars:
        for ev in (resp or {}).get(cal, {}).get("events", []):
            s, allday = _to_local(ev.get("start"))
            e, _ = _to_local(ev.get("end"))
            if allday or s is None or e is None:
                continue  # ganztägige Termine werden ignoriert
            events.append((s, e, ev.get("summary", "")))
    return events


def program_from_events(events, today: date, preheat_min: int, stop_before_min: int
                        ) -> tuple[Program, list[str], dict[int, date]]:
    """Wochenprogramm für die 7 Tage ab heute. Liefert auch Wochentag→Datum."""
    windows = []
    for s, e, _ in events:
        start = s - timedelta(minutes=preheat_min)
        end = max(e - timedelta(minutes=stop_before_min), s)
        if end > start:
            windows.append((start, end))

    prog = empty_program()
    warnings: list[str] = []
    day_dates: dict[int, date] = {}
    for offset in range(7):
        d = today + timedelta(days=offset)
        day_dates[d.weekday()] = d
        day_start = dt_util.start_of_local_day(d)
        day_end = dt_util.start_of_local_day(d + timedelta(days=1))
        day_ws = []
        for ws, we in windows:
            a, b = max(ws, day_start), min(we, day_end)
            if b <= a:
                continue
            am = int((a - day_start).total_seconds() // 60)
            bm = int(-(-(b - day_start).total_seconds() // 60))
            am = am - am % STEP                       # Beginn abrunden
            bm = min(1440, bm + (-bm % STEP))         # Ende aufrunden
            day_ws.append([am, bm])
        merged, reduced = _merge_and_limit(day_ws)
        if reduced:
            warnings.append(f"{d.strftime('%d.%m.')}: mehr als 4 Heizfenster – zusammengefasst")
        prog[d.weekday()] = merged
    return prog, warnings, day_dates


async def async_calendar_program(hass: HomeAssistant, calendars: list[str], preheat_min: int,
                                 stop_before_min: int) -> tuple[Program, list[str], dict[int, date]]:
    today = dt_util.now().date()
    start = dt_util.start_of_local_day(today) - timedelta(days=1)
    end = dt_util.start_of_local_day(today + timedelta(days=8)) + timedelta(minutes=preheat_min)
    events = await async_fetch_events(hass, calendars, start, end)
    return program_from_events(events, today, preheat_min, stop_before_min)
