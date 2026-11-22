"""Alarm control panel platform for TrueGuard MZ."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import COMMAND_ARM_AWAY, COMMAND_ARM_HOME, COMMAND_DISARM
from .coordinator import TrueGuardCoordinator
from .entity import TrueGuardPanelEntity
from .events import (
    ALARM_HOLD,
    alarm_is_triggered,
    arm_commands,
    arm_mode,
    exit_delay,
    last_alarm,
    last_changed_by,
    zone_name,
)


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
    if "deltilkobling 2" in value:
        return AlarmControlPanelState.ARMED_NIGHT
    if "deltilkobling 3" in value:
        return AlarmControlPanelState.ARMED_CUSTOM_BYPASS
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

    def __init__(self, coordinator: TrueGuardCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "alarm")
        self._cancel_timer: CALLBACK_TYPE | None = None

    def _commands(self) -> dict[str, str]:
        """Arm modes the panel offers, from the buttons on control.htm."""
        commands = arm_commands(self.coordinator.data.commands)
        if not commands:  # older snapshot without buttons: the classic three
            commands = {
                "disarm": COMMAND_DISARM,
                "away": COMMAND_ARM_AWAY,
                "home": COMMAND_ARM_HOME,
            }
        return commands

    @property
    def supported_features(self) -> AlarmControlPanelEntityFeature:
        commands = self._commands()
        features = AlarmControlPanelEntityFeature(0)
        for mode, feature in (
            ("away", AlarmControlPanelEntityFeature.ARM_AWAY),
            ("home", AlarmControlPanelEntityFeature.ARM_HOME),
            ("night", AlarmControlPanelEntityFeature.ARM_NIGHT),
            ("custom", AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS),
        ):
            if mode in commands:
                features |= feature
        return features

    def _hold(self) -> timedelta:
        """How long a reported alarm counts as triggered: the panel's siren time."""
        settings = self.coordinator.data.settings
        if settings and settings.siren:
            return timedelta(seconds=settings.siren)
        return ALARM_HOLD

    def _countdown(self) -> tuple[AlarmControlPanelState, datetime] | None:
        """Return the running exit or entry countdown, if any."""
        now = dt_util.utcnow()
        if (until := self.coordinator.arming_until) and now < until:
            return AlarmControlPanelState.ARMING, until
        if (until := self.coordinator.entry_until) and now < until:
            return AlarmControlPanelState.PENDING, until
        return None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        data = self.coordinator.data
        state = _text_state(data.alarm_state)
        if state is AlarmControlPanelState.TRIGGERED:
            return state
        armed = state in (
            AlarmControlPanelState.ARMED_AWAY,
            AlarmControlPanelState.ARMED_HOME,
            AlarmControlPanelState.ARMED_NIGHT,
            AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
            AlarmControlPanelState.PENDING,
        )
        now = dt_util.now().replace(tzinfo=None)
        if alarm_is_triggered(data, now, armed, self._hold()):
            return AlarmControlPanelState.TRIGGERED
        if armed and (countdown := self._countdown()):
            return countdown[0]
        return state

    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        self._schedule_countdown_end()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_countdown_timer)

    @callback
    def _cancel_countdown_timer(self) -> None:
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def _schedule_countdown_end(self) -> None:
        """Write the state again when a countdown or a siren hold runs out."""
        self._cancel_countdown_timer()
        delays = []
        if countdown := self._countdown():
            delays.append((countdown[1] - dt_util.utcnow()).total_seconds())
        if self.alarm_state is AlarmControlPanelState.TRIGGERED and (
            alarm := last_alarm(self.coordinator.data)
        ):
            when = dt_util.parse_datetime(alarm.time)
            if when is not None:
                end = when + self._hold() - dt_util.now().replace(tzinfo=None)
                delays.append(end.total_seconds())
        delays = [delay for delay in delays if delay > 0]
        if delays:
            self._cancel_timer = async_call_later(
                self.hass, min(delays) + 0.5, self._async_timer_done
            )

    @callback
    def _async_timer_done(self, _now: datetime) -> None:
        self._cancel_timer = None
        self.async_write_ha_state()
        self._schedule_countdown_end()

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
        if (settings := data.settings) is not None:
            mode = arm_mode(data.alarm_state) or "away"
            attributes.update(
                {
                    "exit_delay": exit_delay(data, mode),
                    "entry_delay": settings.entry1_away
                    if mode == "away"
                    else settings.entry1_home,
                    "siren_time": settings.siren,
                    "supervision": settings.supervision,
                }
            )
        if countdown := self._countdown():
            attributes["countdown_until"] = countdown[1].isoformat()
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

    async def _send(self, mode: str) -> None:
        commands = self._commands()
        if mode not in commands:
            raise HomeAssistantError(f"The panel has no button for {mode}")
        await self.coordinator.client.async_set_alarm_state(
            commands[mode], tuple(commands.values())
        )
        await self.coordinator.async_request_refresh()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._send("disarm")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._send("away")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._send("home")

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._send("night")

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        await self._send("custom")
