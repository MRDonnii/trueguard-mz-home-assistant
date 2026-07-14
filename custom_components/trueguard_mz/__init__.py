"""TrueGuard MZ integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TrueGuardClient
from .const import PLATFORMS
from .coordinator import TrueGuardCoordinator


@dataclass(slots=True)
class TrueGuardRuntimeData:
    """Runtime objects associated with a config entry."""

    client: TrueGuardClient
    coordinator: TrueGuardCoordinator


async def async_migrate_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Migrate legacy entries to the current config-entry format."""
    if entry.version == 1:
        unique_id = entry.unique_id
        if unique_id and unique_id.startswith(("http://", "https://")):
            unique_id = None
        hass.config_entries.async_update_entry(
            entry,
            unique_id=unique_id,
            version=2,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TrueGuard MZ from a config entry."""
    client = TrueGuardClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = TrueGuardCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = TrueGuardRuntimeData(client, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
