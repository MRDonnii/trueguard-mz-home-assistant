"""Constants for the TrueGuard MZ integration."""

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

# Fired on the event bus for every new row in the panel's event log.
EVENT_TRUEGUARD: Final = "trueguard_mz_event"

PLATFORMS: Final = ["alarm_control_panel", "binary_sensor", "sensor", "switch"]

COMMAND_DISARM: Final = "Frakoblet"
COMMAND_ARM_AWAY: Final = "Fuldsikring"
COMMAND_ARM_HOME: Final = "Deltilkobling"
