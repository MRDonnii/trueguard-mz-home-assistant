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
from .entity import panel_device_info
from .events import is_camera, is_cover, is_dimmer
from .receiver import ReportReceiver

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TrueGuardRuntimeData:
    """Runtime objects associated with a config entry."""

    client: TrueGuardClient
    coordinator: TrueGuardCoordinator
    receiver: ReportReceiver | None = None
    panel_device_id: str | None = None
    platforms: tuple[str, ...] = ()


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
    # Register the panel first, so zone devices can point at it by device id.
    entry.runtime_data.panel_device_id = (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=entry.entry_id, **panel_device_info(entry, coordinator)
        )
        .id
    )
    await coordinator.async_load_last_backup()
    if entry.options.get(CONF_RECEIVER):
        entry.runtime_data.receiver = await _async_start_receiver(
            hass, entry, coordinator
        )
    entry.runtime_data.platforms = _platforms(coordinator)
    await hass.config_entries.async_forward_entry_setups(
        entry, entry.runtime_data.platforms
    )
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
            device_entry = _zone_device(registry, entry, zone)
            if device_entry is None:
                continue
            if device_entry.name != device.name or device_entry.model != device.device_type:
                registry.async_update_device(
                    device_entry.id, name=device.name, model=device.device_type
                )

    entry.async_on_unload(coordinator.async_add_listener(_async_follow_panel))
    return True


def _platforms(coordinator: TrueGuardCoordinator) -> tuple[str, ...]:
    """Load camera, light, and cover only when the panel has such devices."""
    types = [device.device_type for device in coordinator.data.devices.values()]
    optional = {
        "camera": any(is_camera(kind) for kind in types),
        "light": any(is_dimmer(kind) for kind in types),
        "cover": any(is_cover(kind) for kind in types),
    }
    return tuple(
        platform for platform in PLATFORMS if optional.get(platform, True)
    )


def _zone_device(
    registry: dr.DeviceRegistry, entry: ConfigEntry, zone: int
) -> dr.DeviceEntry | None:
    """Find a zone device; async_get_device is deprecated from HA 2026.8."""
    identifier = (DOMAIN, f"{entry.entry_id}_zone_{zone}")
    if hasattr(registry, "async_get_device_by_identifier"):
        return registry.async_get_device_by_identifier(identifier, entry.entry_id)
    return registry.async_get_device(identifiers={identifier})


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
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms
    )
    if unloaded and (receiver := entry.runtime_data.receiver) is not None:
        await receiver.async_stop()
    return unloaded
