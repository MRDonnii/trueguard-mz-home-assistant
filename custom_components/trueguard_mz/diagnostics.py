"""Diagnostics for TrueGuard MZ."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

TO_REDACT = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME, "mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the current panel snapshot without credentials."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "info": async_redact_data(asdict(coordinator.info), TO_REDACT),
        "alarm_state": data.alarm_state,
        "active_faults": list(data.active_faults),
        "gsm_status": data.gsm_status,
        "devices": [asdict(device) for device in data.devices.values()],
        "history": [asdict(entry) for entry in data.history],
        "reports": [asdict(report) for report in data.reports],
    }
