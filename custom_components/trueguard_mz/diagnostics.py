"""Diagnostics for TrueGuard MZ."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

TO_REDACT = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME, "mac", "imei", "imsi", "account"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the current panel snapshot without credentials."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), {"receiver_account"}),
        "info": async_redact_data(asdict(coordinator.info), TO_REDACT),
        "alarm_state": data.alarm_state,
        "active_faults": list(data.active_faults),
        "gsm_status": data.gsm_status,
        "commands": list(data.commands),
        "settings": asdict(data.settings) if data.settings else None,
        "panel_settings": asdict(data.panel_settings) if data.panel_settings else None,
        "gsm_details": dict(data.gsm_details),
        "captures": [asdict(capture) for capture in data.captures],
        "receiver": {
            "listening": entry.runtime_data.receiver is not None,
            "last_contact": coordinator.last_push_at.isoformat()
            if coordinator.last_push_at
            else None,
            "last_report": async_redact_data(
                asdict(coordinator.last_push), {"account", "raw"}
            )
            if coordinator.last_push
            else None,
        },
        "devices": [asdict(device) for device in data.devices.values()],
        "history": [asdict(entry) for entry in data.history],
        "reports": [asdict(report) for report in data.reports],
    }
