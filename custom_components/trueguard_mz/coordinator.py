"""Data update coordinator for TrueGuard MZ."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TrueGuardAuthError, TrueGuardClient, TrueGuardConnectionError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
from .parser import PanelData

_LOGGER = logging.getLogger(__name__)


class TrueGuardCoordinator(DataUpdateCoordinator[PanelData]):
    """Poll both panel pages once for all entities."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: TrueGuardClient
    ) -> None:
        self.client = client
        interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="TrueGuard MZ",
            update_interval=timedelta(seconds=interval),
            always_update=False,
        )

    async def _async_update_data(self) -> PanelData:
        try:
            return await self.client.async_get_data()
        except TrueGuardAuthError as err:
            raise ConfigEntryAuthFailed("TrueGuard login was rejected") from err
        except (TrueGuardConnectionError, ValueError) as err:
            raise UpdateFailed(f"Error communicating with TrueGuard: {err}") from err
