"""Constants for the TrueGuard MZ integration."""

from typing import Final

DOMAIN: Final = "trueguard_mz"

CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 10
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 3600

PLATFORMS: Final = ["alarm_control_panel", "binary_sensor", "sensor", "switch"]

COMMAND_DISARM: Final = "Frakoblet"
COMMAND_ARM_AWAY: Final = "Fuldsikring"
COMMAND_ARM_HOME: Final = "Deltilkobling"
