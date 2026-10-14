"""Data update coordinator for TrueGuard MZ."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, replace
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TrueGuardAuthError, TrueGuardClient, TrueGuardConnectionError
from .const import (
    CONF_EVENT_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_EVENT_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    EVENT_TRUEGUARD,
    GSM_INTERVAL_SECONDS,
)
from .events import zone_name
from .parser import LogEntry, PanelData, PanelInfo

_LOGGER = logging.getLogger(__name__)


class TrueGuardCoordinator(DataUpdateCoordinator[PanelData]):
    """Poll the panel pages once for all entities.

    control.htm and device.htm are read on every poll. The event log and the
    report list are read every event interval, and at once when the arm
    state or a zone changes, so the cause of a change shows up quickly.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: TrueGuardClient
    ) -> None:
        self.client = client
        self.info = PanelInfo()
        interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        self._event_interval = entry.options.get(
            CONF_EVENT_INTERVAL, DEFAULT_EVENT_INTERVAL
        )
        self._events_read_at: float | None = None
        self._gsm_read_at: float | None = None
        self._seen_log: Counter[LogEntry] | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="TrueGuard MZ",
            update_interval=timedelta(seconds=interval),
            always_update=False,
        )

    async def _async_setup(self) -> None:
        """Read static panel information once."""
        try:
            self.info = await self.client.async_get_info()
        except (TrueGuardConnectionError, ValueError) as err:
            _LOGGER.debug("Could not read TrueGuard panel info: %s", err)

    async def _async_update_data(self) -> PanelData:
        try:
            data = await self.client.async_get_data()
        except TrueGuardAuthError as err:
            raise ConfigEntryAuthFailed("TrueGuard login was rejected") from err
        except (TrueGuardConnectionError, ValueError) as err:
            raise UpdateFailed(f"Error communicating with TrueGuard: {err}") from err

        previous = self.data
        now = time.monotonic()
        history = previous.history if previous else ()
        reports = previous.reports if previous else ()
        gsm_status = previous.gsm_status if previous else None

        changed = previous is None or (
            data.alarm_state,
            data.active_faults,
            data.devices,
        ) != (previous.alarm_state, previous.active_faults, previous.devices)
        if (
            changed
            or self._events_read_at is None
            or now - self._events_read_at >= self._event_interval
        ):
            try:
                history, reports = await self.client.async_get_events()
            except TrueGuardAuthError as err:
                raise ConfigEntryAuthFailed("TrueGuard login was rejected") from err
            except (TrueGuardConnectionError, ValueError) as err:
                _LOGGER.debug("Could not read the TrueGuard event log: %s", err)
            else:
                self._events_read_at = now

        if self._gsm_read_at is None or now - self._gsm_read_at >= GSM_INTERVAL_SECONDS:
            try:
                gsm_status = await self.client.async_get_gsm_status()
            except (TrueGuardConnectionError, ValueError) as err:
                _LOGGER.debug("Could not read the TrueGuard GSM status: %s", err)
            else:
                self._gsm_read_at = now

        data = replace(data, history=history, reports=reports, gsm_status=gsm_status)
        self._fire_new_log_entries(data)
        return data

    def _fire_new_log_entries(self, data: PanelData) -> None:
        """Fire one bus event per log row that was not in the previous read."""
        if self._events_read_at is None:
            return
        current = Counter(data.history)
        if self._seen_log is None:
            self._seen_log = current
            return
        new = current - self._seen_log
        self._seen_log = current
        # The log is newest first; fire the oldest new row first.
        for entry in reversed(data.history):
            if new[entry] <= 0:
                continue
            new[entry] -= 1
            self.hass.bus.async_fire(
                EVENT_TRUEGUARD,
                {
                    **asdict(entry),
                    "zone_name": zone_name(data, entry.zone)
                    if entry.zone is not None
                    else None,
                    "config_entry_id": self.config_entry.entry_id,
                },
            )
