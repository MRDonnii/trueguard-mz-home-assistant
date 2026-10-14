"""Binary sensor platform for TrueGuard MZ."""

from __future__ import annotations

from collections.abc import Callable
import re

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import TrueGuardEntity, TrueGuardPanelEntity
from .events import (
    panel_ac_failure,
    panel_battery_low,
    panel_jamming,
    panel_tamper,
    zone_battery_low,
    zone_supervision_lost,
    zone_tamper,
)
from .parser import PanelData

# Device types without a battery-powered radio part.
_WIRED_TYPES = ("tænd/sluk", "switch", "strømstyring", "dæmper")


def _kind(device_type: str) -> str | None:
    value = device_type.casefold()
    if "dørkontakt" in value or "door contact" in value:
        return "opening"
    if "røg" in value or "smoke" in value:
        return "smoke"
    if re.search(r"\bco\b|kulilte|carbon monoxide", value):
        return "co"
    if "varme" in value or "heat" in value:
        return "heat"
    if "gas" in value:
        return "gas"
    if "glasbrud" in value or "glass" in value or "vibration" in value or "rystelse" in value:
        return "vibration"
    if "pir" in value or "bevæg" in value or "motion" in value:
        return "motion"
    if "vand" in value or "water" in value:
        return "moisture"
    return None


_KIND_CLASSES = {
    "smoke": BinarySensorDeviceClass.SMOKE,
    "co": BinarySensorDeviceClass.CO,
    "heat": BinarySensorDeviceClass.HEAT,
    "gas": BinarySensorDeviceClass.GAS,
    "vibration": BinarySensorDeviceClass.VIBRATION,
    "motion": BinarySensorDeviceClass.MOTION,
    "moisture": BinarySensorDeviceClass.MOISTURE,
}


def _is_wireless(device_type: str) -> bool:
    value = device_type.casefold()
    return not any(word in value for word in _WIRED_TYPES)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create zone and panel binary sensors."""
    coordinator = entry.runtime_data.coordinator
    entities: list[BinarySensorEntity] = [
        TrueGuardPanelBinarySensor(coordinator, entry, *description)
        for description in PANEL_SENSORS
    ]
    for zone, device in coordinator.data.devices.items():
        if (kind := _kind(device.device_type)) is not None:
            entities.append(TrueGuardBinarySensor(coordinator, entry, zone, kind))
        if _is_wireless(device.device_type):
            entities.extend(
                TrueGuardZoneDiagnostic(coordinator, entry, zone, *description)
                for description in ZONE_DIAGNOSTICS
            )
    async_add_entities(entities)


class TrueGuardBinarySensor(TrueGuardEntity, BinarySensorEntity):
    """A contact, smoke, CO, heat, gas, glass-break, motion, or moisture zone."""

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
        return _KIND_CLASSES[self.kind]

    @property
    def is_on(self) -> bool:
        status = self.device.status.casefold().strip()
        if self.kind == "opening":
            return status in {"åben", "open", "opened"}
        inactive = {"", "normal", "ok", "luk", "lukket", "closed", "off", "0"}
        return status not in inactive


ZoneCheck = Callable[[PanelData, int], bool]

# key, name, device class, check
ZONE_DIAGNOSTICS: tuple[tuple[str, str, BinarySensorDeviceClass, ZoneCheck], ...] = (
    ("battery", "Batteri", BinarySensorDeviceClass.BATTERY, zone_battery_low),
    ("tamper", "Sabotage", BinarySensorDeviceClass.TAMPER, zone_tamper),
    (
        "connection",
        "Forbindelse",
        BinarySensorDeviceClass.CONNECTIVITY,
        lambda data, zone: not zone_supervision_lost(data, zone),
    ),
)


class TrueGuardZoneDiagnostic(TrueGuardEntity, BinarySensorEntity):
    """Battery, tamper, or supervision state for one wireless zone."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        zone: int,
        key: str,
        name: str,
        device_class: BinarySensorDeviceClass,
        check: ZoneCheck,
    ) -> None:
        super().__init__(coordinator, entry, zone)
        self._check = check
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_{key}"

    @property
    def is_on(self) -> bool:
        return self._check(self.coordinator.data, self.zone)

    @property
    def extra_state_attributes(self) -> None:
        return None


PanelCheck = Callable[[PanelData], bool]

# key, name, device class, check
PANEL_SENSORS: tuple[tuple[str, str, BinarySensorDeviceClass, PanelCheck], ...] = (
    ("tamper", "Sabotage", BinarySensorDeviceClass.TAMPER, panel_tamper),
    ("ac_failure", "230 V-fejl", BinarySensorDeviceClass.PROBLEM, panel_ac_failure),
    ("battery", "Batteri", BinarySensorDeviceClass.BATTERY, panel_battery_low),
    ("jamming", "Radiostøj", BinarySensorDeviceClass.PROBLEM, panel_jamming),
)


class TrueGuardPanelBinarySensor(TrueGuardPanelEntity, BinarySensorEntity):
    """Tamper, power, battery, or jamming state for the panel itself."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        device_class: BinarySensorDeviceClass,
        check: PanelCheck,
    ) -> None:
        super().__init__(coordinator, entry, key)
        self._check = check
        self._attr_name = name
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool:
        return self._check(self.coordinator.data)
