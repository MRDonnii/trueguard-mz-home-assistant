"""Sensor platform for TrueGuard MZ."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .binary_sensor import _kind
from .entity import TrueGuardEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create diagnostic signal sensors and generic device-state sensors."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = []
    for zone, device in coordinator.data.devices.items():
        if device.signal is not None:
            entities.append(TrueGuardSignalSensor(coordinator, entry, zone))
        type_name = device.device_type.casefold()
        if (
            _kind(device.device_type) is None
            and "tænd/sluk" not in type_name
            and "switch" not in type_name
        ):
            entities.append(TrueGuardStatusSensor(coordinator, entry, zone))
    async_add_entities(entities)


class TrueGuardSignalSensor(TrueGuardEntity, SensorEntity):
    """Raw panel radio signal quality, normally 0-9."""

    _attr_name = "Signal"
    _attr_icon = "mdi:signal"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, entry: ConfigEntry, zone: int) -> None:
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_signal"

    @property
    def native_value(self) -> int | None:
        return self.device.signal


class TrueGuardStatusSensor(TrueGuardEntity, SensorEntity):
    """State for keypads, remotes, sirens, and other generic devices."""

    _attr_name = "Status"

    def __init__(self, coordinator, entry: ConfigEntry, zone: int) -> None:
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_status"

    @property
    def native_value(self) -> str:
        if self.device.status:
            return self.device.status
        if self.device.disabled:
            return self.device.disabled
        return "OK" if self.device.signal is not None else "Ukendt"
