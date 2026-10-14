"""TrueGuard MZ integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from urllib.parse import urlparse

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TrueGuardClient
from .const import (
    CONF_RECEIVER,
    CONF_RECEIVER_ACCOUNT,
    CONF_RECEIVER_PORT,
    DEFAULT_RECEIVER_PORT,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import TrueGuardCoordinator
from .receiver import ReportReceiver

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TrueGuardRuntimeData:
    """Runtime objects associated with a config entry."""

    client: TrueGuardClient
    coordinator: TrueGuardCoordinator
    receiver: ReportReceiver | None = None


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
    if entry.version == 2:
        # Version 3 was used by early pre-release builds with the same data
        # (host, username, password); entries saved by them load unchanged.
        hass.config_entries.async_update_entry(entry, version=3)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TrueGuard MZ from a config entry."""
    if CONF_HOST not in entry.data:
        raise ConfigEntryError("The panel address is missing; reconfigure the entry")
    if CONF_USERNAME not in entry.data or CONF_PASSWORD not in entry.data:
        raise ConfigEntryAuthFailed("The panel login is missing")
    client = TrueGuardClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = TrueGuardCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = TrueGuardRuntimeData(client, coordinator)
    if entry.options.get(CONF_RECEIVER):
        entry.runtime_data.receiver = await _async_start_receiver(
            hass, entry, coordinator
        )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    known_zones = set(coordinator.data.devices)

    @callback
    def _async_follow_panel() -> None:
        """Keep zones and device names in step with the panel's device table."""
        devices = coordinator.data.devices
        if set(devices) != known_zones:
            # A zone was added or removed on the panel: rebuild the entities.
            hass.config_entries.async_schedule_reload(entry.entry_id)
            return
        registry = dr.async_get(hass)
        for zone, device in devices.items():
            device_entry = registry.async_get_device(
                identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone}")}
            )
            if device_entry is None:
                continue
            if device_entry.name != device.name or device_entry.model != device.device_type:
                registry.async_update_device(
                    device_entry.id, name=device.name, model=device.device_type
                )

    entry.async_on_unload(coordinator.async_add_listener(_async_follow_panel))
    return True


async def _async_start_receiver(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: TrueGuardCoordinator
) -> ReportReceiver | None:
    """Start the push receiver; only the panel's own address may connect."""
    host = urlparse(entry.data[CONF_HOST]).hostname or ""
    allowed = {host}
    try:
        infos = await hass.loop.getaddrinfo(host, None)
        allowed |= {info[4][0] for info in infos}
    except OSError as err:
        _LOGGER.warning("Could not resolve the TrueGuard panel %s: %s", host, err)
    receiver = ReportReceiver(
        port=entry.options.get(CONF_RECEIVER_PORT, DEFAULT_RECEIVER_PORT),
        allowed_hosts=allowed,
        account=entry.options.get(CONF_RECEIVER_ACCOUNT, ""),
        on_report=coordinator.async_handle_push,
    )
    try:
        await receiver.async_start()
    except OSError as err:
        _LOGGER.error(
            "TrueGuard report receiver could not listen on port %s: %s",
            receiver.port,
            err,
        )
        return None
    return receiver


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and (receiver := entry.runtime_data.receiver) is not None:
        await receiver.async_stop()
    return unloaded
