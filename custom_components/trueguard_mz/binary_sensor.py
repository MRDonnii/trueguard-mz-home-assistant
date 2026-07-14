"""Binary sensor platform for TrueGuard MZ."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import TrueGuardEntity


def _kind(device_type: str) -> str | None:
    value = device_type.casefold()
    if "dørkontakt" in value or "door contact" in value:
        return "opening"
    if "røg" in value or "smoke" in value:
        return "smoke"
    if "pir" in value or "bevæg" in value or "motion" in value:
        return "motion"
    if "vand" in value or "water" in value:
        return "moisture"
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one primary binary sensor for each compatible zone."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        TrueGuardBinarySensor(coordinator, entry, zone, kind)
        for zone, device in coordinator.data.devices.items()
        if (kind := _kind(device.device_type)) is not None
    )


class TrueGuardBinarySensor(TrueGuardEntity, BinarySensorEntity):
    """A contact, smoke, motion, or moisture zone."""

    _attr_name = None

    def __init__(self, coordinator, entry: ConfigEntry, zone: int, kind: str) -> None:
        super().__init__(coordinator, entry, zone)
        self.kind = kind
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}"

    @property
    def device_class(self) -> BinarySensorDeviceClass:
        if self.kind == "opening":
            name = self.device.name.casefold()
            if "vindue" in name or "window" in name:
                return BinarySensorDeviceClass.WINDOW
            return BinarySensorDeviceClass.DOOR
        return {
            "smoke": BinarySensorDeviceClass.SMOKE,
            "motion": BinarySensorDeviceClass.MOTION,
            "moisture": BinarySensorDeviceClass.MOISTURE,
        }[self.kind]

    @property
    def is_on(self) -> bool:
        status = self.device.status.casefold().strip()
        if self.kind == "opening":
            return status in {"åben", "open", "opened"}
        inactive = {"", "normal", "ok", "luk", "lukket", "closed", "off", "0"}
        return status not in inactive
