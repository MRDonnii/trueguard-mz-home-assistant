"""Constants for the TrueGuard MZ integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "trueguard_mz"

CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 10
MIN_SCAN_INTERVAL: Final = 0.5
MAX_SCAN_INTERVAL: Final = 3600

CONF_EVENT_INTERVAL: Final = "event_interval"
DEFAULT_EVENT_INTERVAL: Final = 10
MIN_EVENT_INTERVAL: Final = 2
MAX_EVENT_INTERVAL: Final = 3600

# The GSM page rarely changes and the panel's web server is small.
GSM_INTERVAL_SECONDS: Final = 300

# Entry/exit/siren times and the panel clock (area.htm, panel.htm).
SETTINGS_INTERVAL_SECONDS: Final = 3600

# Push reports from the panel's report slots (SIA DC-09 / Contact ID over IP).
CONF_RECEIVER: Final = "receiver_enabled"
CONF_RECEIVER_PORT: Final = "receiver_port"
CONF_RECEIVER_ACCOUNT: Final = "receiver_account"
DEFAULT_RECEIVER_PORT: Final = 52010
EVENT_TRUEGUARD_REPORT: Final = "trueguard_mz_report"

# PIR camera pictures and configuration backups.
CONF_SAVE_IMAGES: Final = "save_images"
CONF_AUTO_BACKUP: Final = "auto_backup"
EVENT_TRUEGUARD_IMAGE: Final = "trueguard_mz_image"
AUTO_BACKUP_EVERY: Final = timedelta(days=30)
BACKUPS_TO_KEEP: Final = 12

# Fired on the event bus for every new row in the panel's event log.
EVENT_TRUEGUARD: Final = "trueguard_mz_event"

PLATFORMS: Final = [
    "alarm_control_panel",
    "binary_sensor",
    "button",
    "camera",
    "cover",
    "light",
    "sensor",
    "switch",
]

COMMAND_DISARM: Final = "Frakoblet"
COMMAND_ARM_AWAY: Final = "Fuldsikring"
COMMAND_ARM_HOME: Final = "Deltilkobling"
