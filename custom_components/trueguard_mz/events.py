"""Derive alarm, battery, tamper, and fault state from the panel's event lists.

The report list (rptevent.htm) carries Contact ID codes. The qualifier is the
first digit (1 = new event or disarm, 3 = restore or arm) and the next three
digits are the event code. Both lists are ordered newest first.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .parser import LogEntry, PanelData, ReportEvent

# Alarm codes that mean the panel is (or was) in alarm. Tamper codes (137,
# 144, 145, 383) are reported as tamper instead of as an alarm.
BURGLARY_CODES = frozenset(
    {"130", "131", "132", "133", "134", "135", "136", "139", "140", "141", "142", "143"}
)
TWENTY_FOUR_HOUR_CODES = frozenset(
    {"100", "101", "102"}  # medical
    | {f"{code}" for code in range(110, 119)}  # fire
    | {"120", "121", "122", "123"}  # panic, duress
    | {f"{code}" for code in range(150, 170)}  # water, temperature, CO, ...
)
ALARM_CODES = BURGLARY_CODES | TWENTY_FOUR_HOUR_CODES
ARM_DISARM_CODES = frozenset({f"{code}" for code in range(400, 410)} | {"441", "442"})

CODE_PANEL_TAMPER = "137"
CODE_ZONE_TAMPER = "383"
CODE_ZONE_LOW_BATTERY = "384"
CODE_ZONE_SUPERVISION = "381"
CODE_AC_LOSS = "301"
CODE_PANEL_LOW_BATTERY = "302"
CODE_JAMMING = "344"

# How long a reported alarm keeps the alarm entity in "triggered" when the
# panel itself is never disarmed or re-armed afterwards.
ALARM_HOLD = timedelta(hours=1)

_OK_WORDS = frozenset({"", "ok", "normal", "nej", "no", "-", "0", "off"})


def parse_panel_time(value: str) -> datetime | None:
    """Parse a panel timestamp (local panel time, no zone)."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def last_power_up(data: PanelData) -> str:
    """Return the time of the newest panel start ("Enhed tændt"), or ""."""
    for entry in data.history:
        if not entry.device and entry.event.casefold().startswith("enhed tændt"):
            return entry.time
    return ""


def _latest_report(
    reports: tuple[ReportEvent, ...], code: str, zone: int | None, since: str = ""
) -> tuple[str, bool] | None:
    """Return (time, active) for the newest report with this code after `since`."""
    for event in reports:
        if event.time <= since:
            break
        if event.code != code:
            continue
        if zone is not None and event.zone != zone:
            continue
        if event.qualifier in ("1", "3"):
            return event.time, event.qualifier == "1"
    return None


def _latest_log(
    history: tuple[LogEntry, ...],
    zone: int | None,
    words: tuple[str, ...],
    since: str = "",
) -> tuple[str, bool] | None:
    """Return (time, active) for the newest log row; '<text> ok' means restored."""
    for entry in history:
        if entry.time <= since:
            break
        if entry.zone != zone:
            continue
        text = entry.event.casefold()
        if any(word in text for word in words):
            return entry.time, not text.endswith(" ok")
    return None


def _newest(*candidates: tuple[str, bool] | None) -> bool | None:
    """Pick the newest candidate; earlier candidates win a tie."""
    best: tuple[str, bool] | None = None
    for candidate in candidates:
        if candidate is not None and (best is None or candidate[0] > best[0]):
            best = candidate
    return best[1] if best else None


def _latest_status(
    reports: tuple[ReportEvent, ...], code: str, zone: int | None, since: str = ""
) -> bool | None:
    """Return True if the newest report with this code is active, False if restored."""
    latest = _latest_report(reports, code, zone, since)
    return latest[1] if latest else None


def _column_flag(value: str) -> bool | None:
    """Interpret device.htm's battery/tamper columns; empty means no flag."""
    value = value.strip().casefold()
    if not value:
        return None
    return value not in _OK_WORDS


def _fault_mentions(data: PanelData, names: tuple[str, ...], words: tuple[str, ...]) -> bool:
    """Return True when the panel's active-fault list names one of `names`."""
    for fault in data.active_faults:
        text = fault.casefold()
        if any(name and name.casefold() in text for name in names) and any(
            word in text for word in words
        ):
            return True
    return False


def _zone_flag(
    data: PanelData, zone: int, column: str | None, code: str, words: tuple[str, ...]
) -> bool:
    """Combine the panel's live view with events since the last panel start.

    The device table and the active-fault list are the panel's current view.
    Log and report rows only count after the newest panel start, because the
    panel forgets old battery and tamper states when it restarts.
    """
    if (flag := _column_flag(column or "")) is not None:
        return flag
    device = data.devices.get(zone)
    names = (f"zone{zone} ", f"zone {zone} ", device.name if device else "")
    if _fault_mentions(data, names, words):
        return True
    since = last_power_up(data)
    newest = _newest(
        _latest_report(data.reports, code, zone, since),
        _latest_log(data.history, zone, words, since),
    )
    return bool(newest)


def zone_battery_low(data: PanelData, zone: int) -> bool:
    """Return True when a zone device reports low battery."""
    device = data.devices.get(zone)
    return _zone_flag(
        data, zone, device.battery if device else None, CODE_ZONE_LOW_BATTERY, ("batteri",)
    )


def zone_tamper(data: PanelData, zone: int) -> bool:
    """Return True when a zone device reports tamper."""
    device = data.devices.get(zone)
    return _zone_flag(
        data, zone, device.tamper if device else None, CODE_ZONE_TAMPER, ("sabotage",)
    )


def zone_supervision_lost(data: PanelData, zone: int) -> bool:
    """Return True when the panel has lost contact with a zone device."""
    return bool(
        _latest_status(data.reports, CODE_ZONE_SUPERVISION, zone, last_power_up(data))
    )


def panel_tamper(data: PanelData) -> bool:
    """Return True when the panel's own tamper switch is open."""
    since = last_power_up(data)
    return _fault_text(data, "panel sabotage") or bool(
        _newest(
            _latest_report(data.reports, CODE_PANEL_TAMPER, None, since),
            _latest_log(data.history, None, ("panel sabotage",), since),
        )
    )


def _fault_text(data: PanelData, *words: str) -> bool:
    return any(
        word in fault.casefold() for fault in data.active_faults for word in words
    )


def panel_ac_failure(data: PanelData) -> bool:
    """Return True when the panel runs without 230 V mains power."""
    status = _latest_status(data.reports, CODE_AC_LOSS, None, last_power_up(data))
    if status is not None:
        return status
    return _fault_text(data, "230", "strøm", "ac ")


def panel_battery_low(data: PanelData) -> bool:
    """Return True when the panel's backup battery is low."""
    status = _latest_status(data.reports, CODE_PANEL_LOW_BATTERY, None, last_power_up(data))
    if status is not None:
        return status
    return _fault_text(data, "panel batteri", "lavt batteri")


def panel_jamming(data: PanelData) -> bool:
    """Return True when the panel detects radio jamming."""
    status = _latest_status(data.reports, CODE_JAMMING, None, last_power_up(data))
    if status is not None:
        return status
    return _fault_text(data, "jamming", "støj")


def last_alarm(data: PanelData) -> ReportEvent | None:
    """Return the newest reported alarm event."""
    for event in data.reports:
        if event.qualifier == "1" and event.code in ALARM_CODES:
            return event
    return None


def alarm_is_triggered(data: PanelData, now: datetime, armed: bool) -> bool:
    """Return True when a reported alarm is still active.

    A burglary alarm stays active until the panel is armed or disarmed again.
    A 24-hour alarm (fire, water, panic, ...) also ends when its zone restores.
    Either way it ends after ALARM_HOLD, so a missed disarm report cannot keep
    the entity triggered forever.
    """
    since = last_power_up(data)
    for index, event in enumerate(data.reports):
        if event.time <= since:
            return False
        if event.code in ARM_DISARM_CODES:
            return False
        if event.qualifier != "1" or event.code not in ALARM_CODES:
            continue
        when = parse_panel_time(event.time)
        if when is None or now - when > ALARM_HOLD:
            return False
        if event.code in BURGLARY_CODES:
            return armed
        restored = any(
            newer.qualifier == "3"
            and newer.code == event.code
            and newer.zone == event.zone
            for newer in data.reports[:index]
        )
        return not restored
    return False


def zone_last_event(data: PanelData, zone: int) -> LogEntry | None:
    """Return the newest log entry for a zone."""
    return next((entry for entry in data.history if entry.zone == zone), None)


def describe(entry: LogEntry) -> str:
    """Return a one-line description of a log entry."""
    parts = [entry.event]
    if entry.device:
        parts.insert(0, f"{entry.device}:")
    if entry.user:
        parts.append(f"({entry.user})")
    return " ".join(part for part in parts if part)


def zone_name(data: PanelData, zone: int | None) -> str:
    """Return the configured name for a zone number."""
    if zone is None or zone == 0:
        return "Panel"
    device = data.devices.get(zone)
    return device.name if device else f"Zone {zone}"


def last_changed_by(data: PanelData) -> str | None:
    """Return the user behind the newest arm/disarm log entry."""
    for entry in data.history:
        text = entry.event.casefold()
        if any(word in text for word in ("frakobl", "fuldsikring", "deltilkobl", "tilkobl")):
            return entry.user or entry.device or None
    return None
