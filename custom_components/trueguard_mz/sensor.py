"""Sensor platform for TrueGuard MZ."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .binary_sensor import _is_wireless, _kind
from .entity import TrueGuardEntity, TrueGuardPanelEntity
from .events import describe, last_alarm, zone_last_event, zone_name

# Number of log rows exposed as an attribute on the panel's event sensor.
RECENT_EVENTS = 25
# Home Assistant limits state strings to 255 characters.
MAX_STATE = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create panel sensors and per-zone signal, status, and event sensors."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        TrueGuardLastEventSensor(coordinator, entry),
        TrueGuardLastAlarmSensor(coordinator, entry),
        TrueGuardGsmSensor(coordinator, entry),
    ]
    for zone, device in coordinator.data.devices.items():
        type_name = device.device_type.casefold()
        if _is_wireless(device.device_type) or device.signal is not None:
            entities.append(TrueGuardSignalSensor(coordinator, entry, zone))
        if (
            _kind(device.device_type) is None
            and "tænd/sluk" not in type_name
            and "switch" not in type_name
        ):
            entities.append(TrueGuardStatusSensor(coordinator, entry, zone))
        entities.append(TrueGuardZoneEventSensor(coordinator, entry, zone))
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


class TrueGuardZoneEventSensor(TrueGuardEntity, SensorEntity):
    """The newest event-log row for one zone."""

    _attr_name = "Sidste hændelse"
    _attr_icon = "mdi:history"
    _unrecorded_attributes = frozenset({"events"})

    def __init__(self, coordinator, entry: ConfigEntry, zone: int) -> None:
        super().__init__(coordinator, entry, zone)
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone}_last_event"

    @property
    def native_value(self) -> str | None:
        entry = zone_last_event(self.coordinator.data, self.zone)
        return entry.event[:MAX_STATE] if entry else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        events = [
            {"time": entry.time, "event": entry.event, "user": entry.user}
            for entry in self.coordinator.data.history
            if entry.zone == self.zone
        ]
        return {
            "zone": self.zone,
            "time": events[0]["time"] if events else None,
            "events": events,
        }


class TrueGuardLastEventSensor(TrueGuardPanelEntity, SensorEntity):
    """The newest row in the panel's event log, with recent rows as attribute."""

    _attr_name = "Sidste hændelse"
    _attr_icon = "mdi:history"
    _unrecorded_attributes = frozenset({"events", "reports"})

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_event")

    @property
    def native_value(self) -> str | None:
        history = self.coordinator.data.history
        return describe(history[0])[:MAX_STATE] if history else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        history = data.history
        latest = history[0] if history else None
        return {
            "time": latest.time if latest else None,
            "device": latest.device if latest else None,
            "user": latest.user if latest else None,
            "event": latest.event if latest else None,
            "events": [
                {**asdict(entry), "zone_name": zone_name(data, entry.zone)}
                for entry in history[:RECENT_EVENTS]
            ],
            "reports": [
                {
                    "time": report.time,
                    "event": report.event,
                    "cid": report.cid,
                    "zone": report.zone,
                    "status": report.status,
                }
                for report in data.reports[:RECENT_EVENTS]
            ],
        }


class TrueGuardLastAlarmSensor(TrueGuardPanelEntity, SensorEntity):
    """The newest reported alarm with zone and Contact ID code."""

    _attr_name = "Seneste alarm"
    _attr_icon = "mdi:alarm-light"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_alarm")

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        alarm = last_alarm(data)
        if alarm is None:
            return "Ingen"
        return f"{alarm.event} – {zone_name(data, alarm.zone)}"[:MAX_STATE]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        alarm = last_alarm(self.coordinator.data)
        if alarm is None:
            return {}
        return {
            "time": alarm.time,
            "zone": alarm.zone,
            "group": alarm.group,
            "cid": alarm.cid,
            "report_status": alarm.status,
        }


class TrueGuardGsmSensor(TrueGuardPanelEntity, SensorEntity):
    """The GSM module status, for example a missing SIM card."""

    _attr_name = "GSM"
    _attr_icon = "mdi:sim"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "gsm")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.gsm_status or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"module": self.coordinator.info.gsm_module or None}
