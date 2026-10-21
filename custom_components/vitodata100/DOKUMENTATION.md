# Vitodata 100 – Home-Assistant-Integration

Steuert Viessmann-Anlagen mit **Vitocom 100 LAN1** über das Cloud-Portal **Vitodata 100**
(vitodata100.viessmann-climatesolutions.com) – ohne zusätzliches Gateway.
Die Integration bedient die Weboberfläche des Portals automatisiert (inoffiziell, keine Viessmann-API).

## Funktionen
- **Sensoren** aus der Portal-Übersicht (Außentemperatur, Vorlauf, Betriebsarten, Sollwerte …).
- **Zeitprogramme je Heizkreis** (A1, M2, M3 …) in drei Modi:
  - **Kalender** – aus einem oder mehreren HA-Kalendern berechnet (Vorheizen / Abschalten vor Ende einstellbar),
  - **Fester Plan** – z. B. `Mo-So 08:00-22:00`,
  - **Nicht verwalten** – Kreis wird nicht angefasst (Standard).
- **Nächtliche Übertragung** zur einstellbaren Uhrzeit (Standard 03:00).
- **Kalenderüberwachung** (abschaltbar): lädt die gewählten Kalender alle 15 min neu und prüft lokal; ändert sich
  etwas für **heute oder morgen**, wird sofort übertragen. Ohne Änderung kein Portalzugriff.
  Kurzfristige Termine: Liegt der Vorheizbeginn schon in der Vergangenheit, heizt die Anlage ab der Übertragung
  (Vorheizzeit entsprechend verkürzt).
- Es werden **nur geänderte Tage** geschrieben; ist die ganze Woche gleich, genügt ein Speichervorgang.
- **Schalter „Automatik aktiv“** – standardmäßig **aus**. Aus = nur Vorschau, es wird nichts ins Portal geschrieben.

## Problemüberwachung
Die zuletzt übertragenen Zeitprogramme liegen **in der Anlage** und laufen bei einem Ausfall der Integration
weiter – es fehlen dann nur neue/geänderte Termine. Damit ein Ausfall nicht unbemerkt bleibt:

| Problem | Wann |
|---|---|
| Portal nicht erreichbar / Login oder Auslesen fehlgeschlagen | 2 Abrufe in Folge fehlgeschlagen (auch beim Start) |
| Übertragung der Zeitprogramme fehlgeschlagen | sofort |
| Zeitprogramm nicht berechenbar (z. B. Kalender gelöscht) | sofort |
| Anlage meldet keine aktuellen Daten | optional: „Datenstand Portal“ älter als X Stunden (Standard aus) |

Bei jedem **neuen** Problem: Eintrag unter **Einstellungen → Reparaturen**, HA-Benachrichtigung und – falls
eingetragen – Push über die gewählten `notify`-Dienste. Bei Behebung: Eintrag verschwindet, Push „wieder OK“.
Jedes Problem meldet sich nur einmal (keine Wiederholungen bei jedem Abruf).
Zusätzlich: `binary_sensor.vitodata_100_problem` (für eigene Automationen) und
Button „Test-Benachrichtigung senden“.

Grenze: Ist Home Assistant selbst aus oder offline, kann es nicht warnen.

## Installation
1. Ordner `custom_components/vitodata100` nach `/config/custom_components/vitodata100` kopieren
   (z. B. per Samba-Freigabe oder File-Editor-App).
2. Home Assistant neu starten.
3. **Einstellungen → Geräte & Dienste → Integration hinzufügen → „Vitodata 100“**,
   Zugangsdaten eingeben. Anlage und Heizkreise werden automatisch erkannt.
4. **Konfigurieren → Heizkreis einstellen**: Modus, Kalender, Vorheizen/Abschalten bzw. festen Plan je Kreis festlegen.
5. Ein paar Tage die Vorschau prüfen (Sensor „Zeitprogramm …“, Attribute je Wochentag),
   dann **„Automatik aktiv“** einschalten.

## Einstellungen
| Einstellung | Standard | Hinweis |
|---|---|---|
| Abfrage-Intervall | 240 min | 5 – 10080 min; jeder Abruf = Login + „Aktualisieren“ im Portal |
| Uhrzeit nächtliche Übertragung | 03:00 | |
| Kalenderüberwachung | an | wirkt nur bei aktiver Automatik |
| Prüfintervall Kalenderüberwachung | 15 min | rein lokal |
| Benachrichtigungsdienste | leer | z. B. `notify.mobile_app_handy`, mehrere mit Komma |
| Alarm bei alten Anlagendaten | 0 h (aus) | erst einschalten, wenn „Datenstand Portal“ sich zuverlässig aktualisiert |
| Vorheizen | 240 min | je Heizkreis |
| Abschalten vor Ende | 30 min | je Heizkreis; Heizfenster endet nie vor Terminbeginn |

## Regeln der Berechnung (Modus Kalender)
- Zeitraum: heute + 6 Tage (jeder Wochentag genau einmal).
- Heizfenster = Terminbeginn − Vorheizen bis Terminende − Abschaltvorlauf.
- Beginn wird auf 10 min abgerundet, Ende aufgerundet.
- Überlappende Fenster werden zusammengelegt; über Mitternacht wird auf zwei Tage verteilt.
- Mehr als 4 Fenster pro Tag (Grenze des Reglers): die Fenster mit der kleinsten Lücke werden
  zusammengefasst, Hinweis im Sensor-Attribut `hinweise`.
- **Ganztägige Termine werden ignoriert.**

## Format „Fester Plan“
```
Mo-So 08:00-22:00
Sa,So 09:00-12:00, 17:00-21:30
```
Tage: `Mo Di Mi Do Fr Sa So`, Bereiche (`Mo-Fr`) und Listen (`Mo,Mi`). Spätere Zeilen überschreiben frühere.
Max. 4 Zeiträume pro Tag, 10-Minuten-Schritte.

## Entitäten
- `sensor.vitodata_100_*` – Portalwerte
- `sensor.vitodata_100_datenstand_portal` – Zeitstempel „Letzte Aktualisierung“ des Portals
- `sensor.vitodata_100_zeitprogramm_<kreis>_…` – Status `Vorschau` / `ausstehend` / `übertragen` / `Fehler`,
  Attribute: geplantes Programm je Wochentag, zuletzt ins Portal geschriebenes Programm, Hinweise
- `sensor.vitodata_100_letzte_ubertragung` – Zeitpunkt, Ergebnis, Fehler („Unbekannt“, solange noch nie übertragen wurde)
- `binary_sensor.vitodata_100_problem` – an bei aktivem Problem, Details im Attribut `probleme`
- `switch.vitodata_100_automatik_aktiv`
- `button.vitodata_100_zeitprogramme_jetzt_ubertragen` – schreibt **alle** Tage aller verwalteten Kreise neu
  (z. B. nach manuellen Änderungen im Portal)
- `button.vitodata_100_werte_aktualisieren`
- `button.vitodata_100_test_benachrichtigung_senden`

## Bekannte Grenzen
- Inoffiziell: Ändert Viessmann das Portal, kann die Integration ausfallen → „Automatik aktiv“ ausschalten.
- Das Portal ist langsam: ein Speichervorgang dauert ca. 15–30 s.
- Bestehende Zeitprogramme können nicht zuverlässig ausgelesen werden. Die Integration merkt sich,
  was sie selbst geschrieben hat. Nach Änderungen direkt im Portal: Button „jetzt übertragen“.
- „Werte aktualisieren“ (frische Werte von der Anlage) ist nachgebildet, aber noch nicht verifiziert –
  prüfbar über `sensor.…_datenstand_portal`.

## Changelog
### 0.2.0 (2026-10-20)
- Neu: Problemüberwachung mit Reparaturhinweis, HA-Benachrichtigung und optionalem Push (notify-Dienste),
  Entwarnung bei Behebung, Binärsensor „Problem“, Button „Test-Benachrichtigung senden“.
- Neu (optional, Standard aus): Alarm, wenn die Anlagendaten im Portal älter als X Stunden sind.
### 0.1.2 (2026-10-20)
- Vorschau wird erst berechnet, wenn Home Assistant vollständig gestartet ist (Kalender sind dann verfügbar);
  vorher blieb der Zeitprogramm-Sensor bis zur ersten Kalenderprüfung auf „unbekannt“.
- Zeitprogramm-Sensor zeigt „Fehler“ (Details im Attribut `hinweise`) bzw. „noch nicht berechnet“ statt „unbekannt“.
### 0.1.1 (2026-10-20)
- Vor jeder Prüfung/Übertragung werden die gewählten Kalender neu geladen (`homeassistant.update_entity`).
  Hintergrund: Die Integration „Remote Calendar“ lädt iCal-Abos sonst nur alle 24 h – kurzfristige Termine
  wären erst am nächsten Tag sichtbar gewesen.

### 0.1.0 (2026-10-20)
- Erste Version: Login, Anlagen-/Heizkreiserkennung, Portalwerte als Sensoren, Zeitprogramme aus
  Kalender oder festem Plan, nächtliche Übertragung, Kalenderüberwachung, Schalter „Automatik aktiv“,
  Buttons „jetzt übertragen“ / „Werte aktualisieren“.
