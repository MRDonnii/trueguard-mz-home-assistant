"""Alarm control panel platform for TrueGuard MZ."""

from __future__ import annotations

from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import COMMAND_ARM_AWAY, COMMAND_ARM_HOME, COMMAND_DISARM
from .coordinator import TrueGuardCoordinator
from .entity import TrueGuardPanelEntity
from .events import alarm_is_triggered, last_alarm, last_changed_by, zone_name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the alarm entity."""
    async_add_entities([TrueGuardAlarm(entry.runtime_data.coordinator, entry)])


def _text_state(value: str) -> AlarmControlPanelState | None:
    """Map the panel's own state text to an alarm state."""
    value = value.casefold()
    if "alarm" in value or "udløst" in value:
        return AlarmControlPanelState.TRIGGERED
    if "tilkobler" in value or "udgang" in value:
        return AlarmControlPanelState.ARMING
    if "indgang" in value:
        return AlarmControlPanelState.PENDING
    if "frakoblet" in value:
        return AlarmControlPanelState.DISARMED
    if "deltilkobling" in value:
        return AlarmControlPanelState.ARMED_HOME
    if "fuldsikring" in value:
        return AlarmControlPanelState.ARMED_AWAY
    return None


class TrueGuardAlarm(TrueGuardPanelEntity, AlarmControlPanelEntity):
    """The TrueGuard panel arm state and controls."""

    _attr_name = None
    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
    )

    def __init__(self, coordinator: TrueGuardCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "alarm")

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        data = self.coordinator.data
        state = _text_state(data.alarm_state)
        if state is AlarmControlPanelState.TRIGGERED:
            return state
        armed = state in (
            AlarmControlPanelState.ARMED_AWAY,
            AlarmControlPanelState.ARMED_HOME,
            AlarmControlPanelState.PENDING,
        )
        now = dt_util.now().replace(tzinfo=None)
        if alarm_is_triggered(data, now, armed):
            return AlarmControlPanelState.TRIGGERED
        return state

    @property
    def changed_by(self) -> str | None:
        return last_changed_by(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        attributes: dict[str, Any] = {
            "panel_state": data.alarm_state,
            "active_faults": list(data.active_faults),
        }
        if (alarm := last_alarm(data)) is not None:
            attributes.update(
                {
                    "last_alarm": alarm.event,
                    "last_alarm_time": alarm.time,
                    "last_alarm_zone": alarm.zone,
                    "last_alarm_zone_name": zone_name(data, alarm.zone),
                    "last_alarm_cid": alarm.cid,
                }
            )
        return attributes

    async def _send(self, command: str) -> None:
        await self.coordinator.client.async_set_alarm_state(command)
        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._send(COMMAND_DISARM)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._send(COMMAND_ARM_AWAY)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._send(COMMAND_ARM_HOME)
