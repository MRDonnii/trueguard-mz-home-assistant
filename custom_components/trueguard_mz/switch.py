"""Switch platform for TrueGuard MZ modules."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import TrueGuardEntity


def _is_switch(device_type: str) -> bool:
    value = device_type.casefold()
    return "tænd/sluk" in value or "switch" in value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create controllable switch modules."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        TrueGuardSwitch(coordinator, entry, zone)
        for zone, device in coordinator.data.devices.items()
        if _is_switch(device.device_type)
    )


class TrueGuardSwitch(TrueGuardEntity, SwitchEntity):
    """A TrueGuard on/off module."""

    _attr_name = None

    def __init__(self, coordinator, entry: ConfigEntry, zone: int) -> None:
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_switch"

    @property
    def is_on(self) -> bool:
        return self.device.status.casefold().strip() in {"tænd", "tændt", "on", "1"}

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.client.async_set_switch(self.zone, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.client.async_set_switch(self.zone, False)
        await self.coordinator.async_request_refresh()
