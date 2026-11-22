"""Light platform for TrueGuard MZ dimmer modules."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import TrueGuardEntity
from .events import is_dimmer


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a light per dimmer module."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        TrueGuardDimmer(coordinator, entry, zone)
        for zone, device in coordinator.data.devices.items()
        if is_dimmer(device.device_type)
    )


class TrueGuardDimmer(TrueGuardEntity, LightEntity):
    """A dimmer module; the panel accepts 0-100 % in steps of 10."""

    _attr_name = None
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator, entry: ConfigEntry, zone: int) -> None:
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_light"

    def _level(self) -> int | None:
        if match := re.search(r"(\d{1,3})\s*%", self.device.status):
            return int(match.group(1))
        return None

    @property
    def is_on(self) -> bool:
        if (level := self._level()) is not None:
            return level > 0
        return self.device.status.casefold().strip() in {"tænd", "tændt", "on", "1"}

    @property
    def brightness(self) -> int | None:
        level = self._level()
        return round(level * 255 / 100) if level is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            level = max(10, min(100, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255, -1)))
            await self.coordinator.client.async_set_dimmer(self.zone, int(level))
        else:
            await self.coordinator.client.async_set_switch(self.zone, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.async_set_switch(self.zone, False)
        await self.coordinator.async_request_refresh()
