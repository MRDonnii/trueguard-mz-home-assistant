"""Describe TrueGuard MZ log events in the Home Assistant logbook."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_TRUEGUARD


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    """Describe panel log rows."""

    @callback
    def async_describe(event: Event) -> dict[str, str]:
        data = event.data
        message = data.get("event") or ""
        if data.get("user"):
            message = f"{message} ({data['user']})"
        return {
            LOGBOOK_ENTRY_NAME: data.get("zone_name") or "TrueGuard MZ",
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    async_describe_event(DOMAIN, EVENT_TRUEGUARD, async_describe)
