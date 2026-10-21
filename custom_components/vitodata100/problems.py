"""Problemüberwachung: Reparaturhinweis, Benachrichtigung, Push – nur bei Zustandswechsel."""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Callable

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import DOMAIN, OPT_NOTIFY

_LOGGER = logging.getLogger(__name__)

LABELS = {
    "portal": "Portal nicht erreichbar / Login oder Auslesen fehlgeschlagen",
    "transfer": "Übertragung der Zeitprogramme fehlgeschlagen",
    "stale": "Anlage meldet keine aktuellen Daten",
    "plan": "Zeitprogramm konnte nicht berechnet werden",
}


class ProblemTracker:
    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.active: dict[str, dict] = {}
        self.portal_fails = 0     # bleibt über Neuladen/Setup-Wiederholungen erhalten
        self._listeners: list[Callable[[], None]] = []

    @callback
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def _services(self) -> list[str]:
        raw = str(self.entry.options.get(OPT_NOTIFY, "") or "")
        out = []
        for part in raw.replace(";", ",").split(","):
            p = part.strip()
            if p:
                out.append(p.split(".", 1)[1] if p.startswith("notify.") else p)
        return out

    async def async_send(self, title: str, message: str) -> list[str]:
        """Push an alle eingetragenen notify-Dienste. Rückgabe: Fehlermeldungen."""
        errors = []
        for svc in self._services():
            try:
                await self.hass.services.async_call(
                    "notify", svc, {"title": title, "message": message}, blocking=True)
            except Exception as err:  # noqa: BLE001
                errors.append(f"notify.{svc}: {err}")
                _LOGGER.error("Benachrichtigung über notify.%s fehlgeschlagen: %s", svc, err)
        return errors

    async def async_set(self, key: str, detail: str | None) -> None:
        """detail=None → Problem behoben."""
        kind = key.split(":")[0]
        label = LABELS.get(kind, key)
        issue_id = f"{self.entry.entry_id}_{key.replace(':', '_')}"
        if detail:
            if key in self.active:
                self.active[key]["detail"] = detail
                return
            self.active[key] = {"seit": dt_util.now().isoformat(timespec="seconds"),
                                "label": label, "detail": detail}
            msg = f"{label}: {detail}"
            _LOGGER.error("Vitodata 100 – %s", msg)
            ir.async_create_issue(self.hass, DOMAIN, issue_id, is_fixable=False,
                                  severity=ir.IssueSeverity.ERROR, translation_key="problem",
                                  translation_placeholders={"message": msg})
            persistent_notification.async_create(
                self.hass, msg + "\n\nDie zuletzt übertragenen Zeitprogramme laufen in der Anlage weiter.",
                title="⚠️ Vitodata 100 – Heizung prüfen", notification_id=issue_id)
            await self.async_send("⚠️ Kirchenheizung: Problem", msg)
        elif key in self.active:
            info = self.active.pop(key)
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            persistent_notification.async_dismiss(self.hass, issue_id)
            await self.async_send("✅ Kirchenheizung: wieder OK", f"Behoben: {info['label']}")
        else:
            return
        for cb in list(self._listeners):
            cb()


def parse_portal_timestamp(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        naive = datetime.strptime(text.strip(), "%d.%m.%Y %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
