"""Client für die Vitodata-100-Weboberfläche (ASP.NET/Telerik-Postbacks).

Synchron (requests) – wird von Home Assistant im Executor ausgeführt.
Jede Operation nutzt eine eigene, frische Sitzung (Login → Anlage öffnen → Aktion).
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)

HOST = "https://vitodata100.viessmann-climatesolutions.com"
BASE = HOST + "/VD100"
URL_LOGIN = BASE + "/VD100/Login/Login.aspx"
URL_DEVLIST = BASE + "/VD100/Overview/DeviceList.aspx"
URL_OVERVIEW = BASE + "/Service/Overview.aspx"
URL_OPERATION = BASE + "/Service/DeviceOperation.aspx?x=1"

ED = "ctl00$Content$EventGroupTreeControl$eventGroupEditControl$"
TE_N = ED + "EventRepeater2$ctl00$VsmCircuitTimesEditor$"
TE_C = TE_N.replace("$", "_")
CONTENT_PANEL = "ctl00$ctl00$Content$PanelContentPanel"
GRID = "ctl00$Content$UcDevices$RGDevices"
GRID_PANEL = "ctl00$ctl00$Content$UcDevices$RGDevicesPanel"
UPDATE_TIMER = "ctl00$Content$ActualizeButton$UpdateTimer"
UPDATE_BUTTON = "ctl00$Content$ActualizeButton$UpdateButton"

# Checkbox-Namen im Editor („Übernehmen für …“), Index = Python-Wochentag
DAY_CKB = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ALL_CKB = DAY_CKB + ["MondayTillSunday", "MondayTillFriday", "SaturdayTillSunday"]

POLLS_AFTER_SAVE = 2
POLL_DELAY = 5


class VitodataError(Exception):
    """Allgemeiner Fehler."""


class VitodataAuthError(VitodataError):
    """Login fehlgeschlagen."""


def _cs_textbox(value: str) -> str:
    return json.dumps({"enabled": True, "emptyMessage": "", "validationText": value,
                       "valueAsString": value, "lastSetTextBoxValue": value},
                      separators=(",", ":"), ensure_ascii=False)


class VitodataClient:
    """Eine Portal-Sitzung."""

    def __init__(self, username: str, password: str) -> None:
        self._user = username
        self._pw = password
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
        self.soup: BeautifulSoup | None = None
        self.url: str | None = None
        self.last_text = ""

    # ------------------------------------------------------------ Grundlagen
    def close(self) -> None:
        self.s.close()

    def _get(self, url: str) -> BeautifulSoup:
        r = self.s.get(url, timeout=60)
        r.raise_for_status()
        if "Login/Login.aspx" in r.url and url != URL_LOGIN:
            raise VitodataAuthError("Sitzung abgelaufen")
        self.soup = BeautifulSoup(r.text, "html.parser")
        self.url = r.url
        self.last_text = r.text
        return self.soup

    def _form_data(self) -> dict[str, str]:
        data: dict[str, str] = {}
        for el in self.soup.find_all(["input", "select", "textarea"]):
            name = el.get("name")
            if not name or el.has_attr("disabled"):
                continue
            if el.name == "input":
                typ = (el.get("type") or "text").lower()
                if typ in ("submit", "button", "image", "reset", "file"):
                    continue
                if typ in ("checkbox", "radio"):
                    if el.has_attr("checked"):
                        data[name] = el.get("value") or "on"
                    continue
                data[name] = el.get("value", "")
            elif el.name == "select":
                opt = el.find("option", selected=True) or el.find("option")
                data[name] = opt.get("value", opt.text) if opt else ""
            else:
                data[name] = el.text
        return data

    @staticmethod
    def _parse_delta(text: str) -> list[tuple[str, str, str]]:
        if not re.match(r"\d+\|", text):
            raise VitodataError("Keine AJAX-Antwort vom Server: " + text[:150].strip())
        out, p = [], 0
        while p < len(text):
            a = text.find("|", p)
            if a < 0:
                break
            n = int(text[p:a])
            b = text.find("|", a + 1)
            c = text.find("|", b + 1)
            out.append((text[a + 1:b], text[b + 1:c], text[c + 1:c + 1 + n]))
            p = c + 1 + n + 1
        return out

    def _apply_delta(self, text: str) -> str | None:
        redirect = None
        for typ, id_, content in self._parse_delta(text):
            if typ == "updatePanel":
                el = self.soup.find(id=id_)
                if el is not None:
                    el.clear()
                    frag = BeautifulSoup(content, "html.parser")
                    for child in list(frag.contents):
                        el.append(child)
            elif typ == "hiddenField":
                el = self.soup.find("input", attrs={"name": id_})
                if el is None:
                    el = self.soup.new_tag("input", attrs={"type": "hidden", "name": id_, "id": id_})
                    self.soup.find("form").append(el)
                el["value"] = content
            elif typ == "pageRedirect":
                redirect = urllib.parse.urljoin(self.url, urllib.parse.unquote(content))
            elif typ == "error":
                raise VitodataError("Serverfehler: " + content[:300])
        return redirect

    def _postback(self, target: str, argument: str = "", *, sm_field: str = "ctl00$RSMViessmannPage",
                  panel: str = CONTENT_PANEL, ajax_ctrl: str | None = None,
                  set_fields: dict | None = None, remove: list | None = None,
                  event_target: str | None = None) -> str | None:
        data = self._form_data()
        for k in remove or []:
            data.pop(k, None)
        data.update(set_fields or {})
        data["__EVENTTARGET"] = target if event_target is None else event_target
        data["__EVENTARGUMENT"] = argument
        data[sm_field] = f"{panel}|{target}"
        data["__ASYNCPOST"] = "true"
        if ajax_ctrl:
            data["RadAJAXControlID"] = ajax_ctrl
        r = self.s.post(self.url, data=data, timeout=120, headers={
            "X-MicrosoftAjax": "Delta=true", "X-Requested-With": "XMLHttpRequest",
            "Origin": HOST, "Referer": self.url, "Accept": "*/*", "Cache-Control": "no-cache",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
        r.raise_for_status()
        self.last_text = r.text
        return self._apply_delta(r.text)

    # ------------------------------------------------------------ Ablauf
    def login(self) -> None:
        self._get(URL_LOGIN)
        tz = int(-time.timezone / 60 + (60 if time.localtime().tm_isdst else 0))
        red = self._postback(
            "ctl00$ACPHRight$RBLogin", sm_field="ctl00$RSMViessmannLogin",
            panel="ctl00$ctl00$ACPHRight$DivLoginRightPanel", ajax_ctrl="ctl00_RAMViessmannLogin",
            set_fields={
                "__CLIENTTIMEZONEOFFSET__": str(tz),
                "ctl00$ACPHRight$RTBUser": self._user,
                "ctl00_ACPHRight_RTBUser_ClientState": _cs_textbox(self._user),
                "ctl00$ACPHRight$RTBPassword": self._pw,
                "ctl00_ACPHRight_RTBPassword_ClientState": _cs_textbox(self._pw)})
        if not red or "DeviceList" not in red:
            raise VitodataAuthError("Login fehlgeschlagen")

    def list_devices(self) -> list[int]:
        """Anlagen-IDs aus der Anlagenliste (nach Login)."""
        self._get(URL_DEVLIST)
        self._postback(GRID, f"FireCommand:{GRID}$ctl00;RebindGrid;", panel=GRID_PANEL,
                       ajax_ctrl="ctl00_RAMViessmannPage")
        ids = re.findall(r'DeviceId(?:&quot;|")\s*:\s*(\d+)', self.last_text)
        return list(dict.fromkeys(int(i) for i in ids))

    def open_device(self, device_id: int) -> None:
        if self.url is None or "DeviceList" not in self.url:
            self.list_devices()
        grid_cs = json.dumps({"selectedIndexes": ["0"], "selectedCellsIndexes": [],
                              "unselectableItemsIndexes": [], "reorderedColumns": [],
                              "expandedItems": [], "expandedGroupItems": [], "expandedFilterItems": [],
                              "deletedItems": [], "hidedColumns": [], "showedColumns": [],
                              "groupColsState": {}, "hierarchyState": {}, "scrolledPosition": "0,0",
                              "popUpLocations": {}, "draggedItemsIndexes": []}, separators=(",", ":"))
        arg = json.dumps({"Sender": "ctl00_Content_UcDevices_RGDevices", "Function": "entryopen",
                          "ValueType": "Int32", "Value": int(device_id), "Arguments": []},
                         separators=(",", ":"))
        red = self._postback(GRID, f"FireCommand:{GRID}$ctl00;entryopen;{arg}", panel=GRID_PANEL,
                             ajax_ctrl="ctl00_RAMViessmannPage",
                             set_fields={"ctl00_Content_UcDevices_RGDevices_ClientState": grid_cs})
        if not red or "Overview" not in red:
            raise VitodataError(f"Anlage {device_id} konnte nicht geöffnet werden")

    def connect(self, device_id: int) -> None:
        self.login()
        self.open_device(device_id)

    # ------------------------------------------------------------ Lesen
    def _parse_groups(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        group = None
        for el in self.soup.find_all(["span", "tr"]):
            if el.name == "span" and re.search(r"LblEventGroupName(Link)?$", el.get("id", "")):
                group = el.get_text(strip=True)
                result.setdefault(group, {})
            elif el.name == "tr" and "TableRow_EventType" in el.get("id", "") and group:
                cells = el.find_all("td", recursive=False)
                if len(cells) >= 2:
                    own = cells[1].find(string=True, recursive=False)
                    val = " ".join(own.split()) if own and own.strip() else cells[1].get_text(" ", strip=True)
                    result[group][cells[0].get_text(" ", strip=True)] = val
        return result

    def _data_timestamp(self) -> str | None:
        m = re.search(r"Letzte Aktualisierung:\s*([\d.]+\s+[\d:]+)", self.soup.get_text(" "))
        return m.group(1) if m else None

    def refresh_from_device(self) -> None:
        """„Ansicht aktualisieren“ – holt frische Werte von der Anlage (best effort)."""
        self._get(URL_OVERVIEW)
        try:
            self._postback(UPDATE_BUTTON, event_target="", ajax_ctrl="ctl00_RAMViessmannPage",
                           set_fields={UPDATE_BUTTON + ".x": "8", UPDATE_BUTTON + ".y": "8"})
            for _ in range(3):
                time.sleep(POLL_DELAY)
                self._postback(UPDATE_TIMER, ajax_ctrl="ctl00_RAMViessmannPage")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Aktualisieren nicht erfolgreich: %s", err)

    def read_status(self) -> dict:
        self._get(URL_OVERVIEW)
        return {"groups": self._parse_groups(), "timestamp": self._data_timestamp()}

    def discover_circuits(self) -> list[dict[str, str]]:
        """Heizkreise mit Zeitprogramm, z.B. [{'id': 'M2', 'name': 'Foyer Kirchenkaffee'}]."""
        self._get(URL_OPERATION)
        out = []
        for span in self.soup.find_all("span", id=re.compile(r"LblEventGroupNameLink$")):
            m = re.match(r"Schaltzeiten\s+(\w+)\s*(?:\((.*)\))?", span.get_text(strip=True))
            if m:
                out.append({"id": m.group(1), "name": (m.group(2) or m.group(1)).strip()})
        return out

    # ------------------------------------------------------------ Schreiben
    def _group_edit_target(self, circuit: str) -> str:
        for span in self.soup.find_all("span", id=re.compile(r"LblEventGroupNameLink$")):
            if re.match(rf"Schaltzeiten\s+{re.escape(circuit)}\b", span.get_text(strip=True)):
                a = span.find_parent("a")
                return re.search(r"__doPostBack\('([^']+)'", a["href"].replace("&#39;", "'")).group(1)
        raise VitodataError(f"Gruppe 'Schaltzeiten {circuit}' nicht gefunden")

    def write_day_program(self, circuit: str, ckb_names: list[str],
                          phases: list[tuple[float, float]]) -> None:
        """Schreibt bis zu 4 Heizphasen (Stunden als float) für die angegebenen Tage."""
        if len(phases) > 4:
            raise VitodataError("Maximal 4 Phasen")
        self._get(URL_OPERATION)
        self._postback(self._group_edit_target(circuit))
        if self.soup.find("input", attrs={"name": TE_N + "HfEventValueId"}) is None:
            raise VitodataError("Zeitprogramm-Editor wurde nicht geladen")

        fields: dict[str, str] = {}
        for i in range(4):
            if i < len(phases):
                fields.update(self._phase_fields(i, phases[i][0], phases[i][1], True))
            else:
                fields.update(self._phase_fields(i, None, None, False))
        name_el = self.soup.find("input", attrs={"name": ED + "tbName"})
        if name_el is not None:
            fields[ED.replace("$", "_") + "tbName_ClientState"] = _cs_textbox(name_el.get("value", ""))
        remove = [TE_N + f"Ckb{d}Inherit" for d in ALL_CKB]
        for d in ckb_names:
            fields[TE_N + f"Ckb{d}Inherit"] = "on"

        self._postback(ED + "SaveButton", set_fields=fields, remove=remove)
        for _ in range(POLLS_AFTER_SAVE):
            time.sleep(POLL_DELAY)
            self._postback(UPDATE_TIMER, ajax_ctrl="ctl00_RAMViessmannPage")
        self._postback(ED + "BackButton")

    @staticmethod
    def _num(h: float) -> str:
        return str(int(h)) if float(h).is_integer() else f"{h:.6g}"

    @staticmethod
    def _hhmm(h: float) -> str:
        m = round(h * 60)
        return f"{m // 60}:{m % 60:02d}"

    def _phase_fields(self, i: int, st, en, heat: bool) -> dict[str, str]:
        f: dict[str, str] = {}
        for key, val in (("Start", st), ("End", en)):
            txt = self._hhmm(val) if val is not None else "--:--"
            num = self._num(val) if val is not None else "0"
            f[TE_N + f"RTBCircuitTimes{key}_{i}"] = txt
            f[TE_C + f"RTBCircuitTimes{key}_{i}_ClientState"] = json.dumps(
                {"enabled": True, "emptyMessage": "", "validationText": num, "valueAsString": num,
                 "minValue": 0, "maxValue": 24, "lastSetTextBoxValue": txt}, separators=(",", ":"))
            if val is not None:
                mm = round(val * 60)
                mask = f"{mm // 60:02d}:" + (f"{mm % 60:02d}" if mm % 60 else "__")
                vt = mask.replace("__", "")
            else:
                mask = vt = "00:50"
            f[TE_N + f"RMTBCircuitTimes{key}Mask_{i}"] = mask
            f[TE_C + f"RMTBCircuitTimes{key}Mask_{i}_ClientState"] = json.dumps(
                {"enabled": True, "emptyMessage": "", "validationText": vt, "valueAsString": mask,
                 "valueWithPromptAndLiterals": mask, "lastSetTextBoxValue": mask}, separators=(",", ":"))
        typ_txt, typ_val = ("Heizbetrieb", "1") if heat else ("Abschaltbetrieb", "0")
        f[TE_N + f"RCBCircuitTimesType_{i}"] = typ_txt
        f[TE_C + f"RCBCircuitTimesType_{i}_ClientState"] = json.dumps(
            {"logEntries": [] if heat else None, "value": typ_val, "text": typ_txt, "enabled": False,
             "checkedIndices": [], "checkedItemsTextOverflows": False}, separators=(",", ":"))

        def n(v):
            return int(v) if float(v).is_integer() else float(v)

        f[TE_N + f"ClientExchangeField_{i}"] = json.dumps(
            {"StartTime": n(st) if heat else None, "EndTime": n(en) if heat else None,
             "Type": typ_val if heat else 0, "Index": 0}, separators=(",", ":"))
        return f
