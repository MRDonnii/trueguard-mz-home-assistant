"""Button platform for TrueGuard MZ: camera pictures and configuration backup."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import TrueGuardEntity, TrueGuardPanelEntity
from .events import is_camera


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the backup button and picture buttons for PIR cameras."""
    coordinator = entry.runtime_data.coordinator
    entities: list[ButtonEntity] = [TrueGuardBackupButton(coordinator, entry)]
    for zone, device in coordinator.data.devices.items():
        if is_camera(device.device_type):
            entities.append(TrueGuardPictureButton(coordinator, entry, zone, True))
            entities.append(TrueGuardPictureButton(coordinator, entry, zone, False))
    async_add_entities(entities)


class TrueGuardPictureButton(TrueGuardEntity, ButtonEntity):
    """Ask a PIR camera for a picture, with or without flash."""

    _attr_icon = "mdi:camera"

    def __init__(self, coordinator, entry: ConfigEntry, zone: int, flash: bool) -> None:
        super().__init__(coordinator, entry, zone)
        self._flash = flash
        self._attr_name = "Tag billede" if flash else "Tag billede uden blitz"
        suffix = "picture" if flash else "picture_no_flash"
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_{suffix}"

    async def async_press(self) -> None:
        await self.coordinator.client.async_take_picture(self.zone, self._flash)
        # The picture shows up on Billeder/Video after a few seconds.
        self.coordinator.request_events_soon()


class TrueGuardBackupButton(TrueGuardPanelEntity, ButtonEntity):
    """Save the panel configuration (cfg.bin) under /config/trueguard_mz/backups."""

    _attr_name = "Tag backup"
    _attr_icon = "mdi:content-save-cog"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "backup")

    async def async_press(self) -> None:
        await self.coordinator.async_backup()
