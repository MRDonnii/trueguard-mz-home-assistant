"""Cover platform for TrueGuard MZ blind/shutter modules."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import TrueGuardEntity
from .events import is_cover


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a cover per blind or shutter module."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        TrueGuardCover(coordinator, entry, zone)
        for zone, device in coordinator.data.devices.items()
        if is_cover(device.device_type)
    )


class TrueGuardCover(TrueGuardEntity, CoverEntity):
    """A blind module driven by the panel's Op / Ned / Stop buttons."""

    _attr_name = None
    _attr_assumed_state = True
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(self, coordinator, entry: ConfigEntry, zone: int) -> None:
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_cover"

    @property
    def is_closed(self) -> bool | None:
        status = self.device.status.casefold()
        if "ned" in status or "lukket" in status:
            return True
        if "op" in status or "åben" in status:
            return False
        return None

    async def _send(self, command: str) -> None:
        await self.coordinator.client.async_cover_command(self.zone, command)
        await self.coordinator.async_request_refresh()

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._send("Op / lås")

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._send("Ned / lås op")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._send("Stop")
