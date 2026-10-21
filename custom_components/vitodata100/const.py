"""Konstanten für Vitodata 100."""
DOMAIN = "vitodata100"

CONF_DEVICE_ID = "device_id"
CONF_CIRCUITS = "circuits"

OPT_SCAN_INTERVAL = "scan_interval"        # Minuten
OPT_WRITE_TIME = "write_time"              # "HH:MM:SS"
OPT_WATCH = "watch_calendars"              # bool
OPT_WATCH_INTERVAL = "watch_interval"      # Minuten
OPT_NOTIFY = "notify_services"             # "mobile_app_x, mobile_app_y"
OPT_STALE_HOURS = "stale_hours"            # 0 = aus

OPT_MODE = "mode_{}"                       # calendar | fixed | none
OPT_CALENDARS = "calendars_{}"
OPT_PREHEAT = "preheat_{}"                 # Minuten
OPT_STOP_BEFORE = "stop_before_{}"         # Minuten
OPT_FIXED = "fixed_{}"                     # Text, z.B. "Mo-So 08:00-22:00"

MODE_CALENDAR = "calendar"
MODE_FIXED = "fixed"
MODE_NONE = "none"

DEFAULT_SCAN_INTERVAL = 240
DEFAULT_WRITE_TIME = "03:00:00"
DEFAULT_WATCH = True
DEFAULT_WATCH_INTERVAL = 15
DEFAULT_STALE_HOURS = 0
PORTAL_FAILS_BEFORE_ALARM = 2
DEFAULT_PREHEAT = 240
DEFAULT_STOP_BEFORE = 30

# Sofort-Übertragung durch die Kalenderüberwachung nur für heute und morgen,
# alles Weitere übernimmt die nächtliche Übertragung.
WATCH_HORIZON_DAYS = 2

WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
