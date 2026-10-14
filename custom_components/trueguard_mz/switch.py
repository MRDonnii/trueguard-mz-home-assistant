"""Switch platform for TrueGuard MZ modules and zone activation."""

from __future__ import annotations

from homeassistant.components.logbook import async_log_entry
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .binary_sensor import _kind
from .const import DOMAIN
from .entity import TrueGuardEntity
from .events import is_camera


def _is_switch(device_type: str) -> bool:
    value = device_type.casefold()
    if "dæmper" in value or "dimmer" in value:
        return False  # dimmers are lights (light.py)
    return "tænd/sluk" in value or "switch" in value or "strømstyring" in value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create switch modules and the (disabled by default) zone switches."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SwitchEntity] = []
    for zone, device in coordinator.data.devices.items():
        if _is_switch(device.device_type):
            entities.append(TrueGuardSwitch(coordinator, entry, zone))
        if _kind(device.device_type) is not None or is_camera(device.device_type):
            entities.append(TrueGuardZoneActiveSwitch(coordinator, entry, zone))
    async_add_entities(entities)


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


class TrueGuardZoneActiveSwitch(TrueGuardEntity, SwitchEntity):
    """Whether a detector zone is active; off deactivates it on the panel.

    Disabled by default: enable it per zone in the entity settings. Each change
    is written to the logbook, and the "Udkoblet" sensor shows the result.
    """

    _attr_name = "Aktiv"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry: ConfigEntry, zone: int) -> None:
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_active"

    @property
    def is_on(self) -> bool:
        return not self.device.disabled.strip()

    @property
    def icon(self) -> str:
        return "mdi:shield-check" if self.is_on else "mdi:shield-off-outline"

    async def _set(self, active: bool) -> None:
        await self.coordinator.client.async_set_zone_active(self.zone, active)
        async_log_entry(
            self.hass,
            self.device.name,
            "aktiveret i alarmen" if active else "deaktiveret i alarmen",
            DOMAIN,
            self.entity_id,
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)
