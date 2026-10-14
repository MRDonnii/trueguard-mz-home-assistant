"""Shared TrueGuard MZ entity classes."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TrueGuardCoordinator
from .parser import PanelDevice


def panel_device_info(entry: ConfigEntry, coordinator: TrueGuardCoordinator) -> DeviceInfo:
    """Return device registry data for the alarm panel."""
    info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="TrueGuard / SecPro Sikring",
        model="MZ alarm panel",
        configuration_url=entry.data["host"],
    )
    if coordinator.info.firmware:
        info["sw_version"] = coordinator.info.firmware
    if coordinator.info.zigbee:
        info["hw_version"] = f"ZigBee {coordinator.info.zigbee}"
    return info


class TrueGuardPanelEntity(CoordinatorEntity[TrueGuardCoordinator]):
    """Base entity that belongs to the panel device itself."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: TrueGuardCoordinator, entry: ConfigEntry, key: str
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = panel_device_info(entry, coordinator)


class TrueGuardEntity(CoordinatorEntity[TrueGuardCoordinator]):
    """Base entity associated with one alarm zone."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: TrueGuardCoordinator, entry: ConfigEntry, zone: int
    ) -> None:
        super().__init__(coordinator, context=zone)
        self.entry = entry
        self.zone = zone

    @property
    def available(self) -> bool:
        """Zones removed from the panel become unavailable."""
        return super().available and self.zone in self.coordinator.data.devices

    @property
    def device(self) -> PanelDevice:
        """Return the current device snapshot."""
        return self.coordinator.data.devices[self.zone]

    @property
    def device_info(self) -> DeviceInfo:
        device = self.coordinator.data.devices.get(self.zone)
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.entry.entry_id}_zone_{self.zone}")},
            name=device.name if device else f"Zone {self.zone}",
            manufacturer="TrueGuard / SecPro Sikring",
            model=device.device_type if device else None,
            via_device=(DOMAIN, self.entry.entry_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        """Expose panel fields that do not warrant separate primary entities."""
        device = self.coordinator.data.devices.get(self.zone)
        if device is None:
            return {"zone": self.zone}
        return {
            "zone": device.zone,
            "type": device.device_type,
            "attribute": device.attribute,
            "condition": device.condition,
            "battery": device.battery,
            "tamper": device.tamper,
            "disabled": device.disabled,
            "signal": device.signal,
            "raw_status": device.status,
        }
