"""Alarm control panel platform for TrueGuard MZ."""

from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import COMMAND_ARM_AWAY, COMMAND_ARM_HOME, COMMAND_DISARM, DOMAIN
from .coordinator import TrueGuardCoordinator
from .entity import panel_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the alarm entity."""
    async_add_entities([TrueGuardAlarm(entry.runtime_data.coordinator, entry)])


class TrueGuardAlarm(CoordinatorEntity[TrueGuardCoordinator], AlarmControlPanelEntity):
    """The TrueGuard panel arm state and controls."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_HOME
    )

    def __init__(self, coordinator: TrueGuardCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_alarm"
        self._attr_device_info = panel_device_info(entry)

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        value = self.coordinator.data.alarm_state.casefold()
        if "frakoblet" in value:
            return AlarmControlPanelState.DISARMED
        if "deltilkobling" in value:
            return AlarmControlPanelState.ARMED_HOME
        if "fuldsikring" in value:
            return AlarmControlPanelState.ARMED_AWAY
        if "alarm" in value or "udløst" in value:
            return AlarmControlPanelState.TRIGGERED
        if "tilkobler" in value or "udgang" in value:
            return AlarmControlPanelState.ARMING
        if "indgang" in value:
            return AlarmControlPanelState.PENDING
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str | list[str]]:
        return {
            "panel_state": self.coordinator.data.alarm_state,
            "active_faults": list(self.coordinator.data.active_faults),
        }

    async def _send(self, command: str) -> None:
        await self.coordinator.client.async_set_alarm_state(command)
        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._send(COMMAND_DISARM)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._send(COMMAND_ARM_AWAY)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._send(COMMAND_ARM_HOME)
