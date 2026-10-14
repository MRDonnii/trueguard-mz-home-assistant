"""Derive alarm, battery, tamper, and fault state from the panel's event lists.

The report list (rptevent.htm) carries Contact ID codes. The qualifier is the
first digit (1 = new event or disarm, 3 = restore or arm) and the next three
digits are the event code. Both lists are ordered newest first.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re

from .parser import LogEntry, PanelData, ReportEvent

# Alarm codes that mean the panel is (or was) in alarm. Tamper codes (137,
# 144, 145, 383) are reported as tamper instead of as an alarm.
BURGLARY_CODES = frozenset(
    {"130", "131", "132", "133", "134", "135", "136", "139", "140", "141", "142", "143"}
)
TWENTY_FOUR_HOUR_CODES = frozenset(
    {"100", "101", "102"}  # medical
    | {f"{code}" for code in range(110, 119)}  # fire
    | {"120", "122", "123"}  # panic (121 duress is silent, see CODE_DURESS)
    | {f"{code}" for code in range(150, 170)}  # water, temperature, CO, ...
)
ALARM_CODES = BURGLARY_CODES | TWENTY_FOUR_HOUR_CODES
# Open/close codes. The TrueGuard MZ reports partial arm as 3456 and remote
# disarm as 1401.
ARM_DISARM_CODES = frozenset(
    {f"{code}" for code in range(400, 410)} | {"441", "442", "456"}
)

CODE_PANEL_TAMPER = "137"
CODE_ZONE_TAMPER = ("383", "144")
CODE_ZONE_LOW_BATTERY = "384"
CODE_ZONE_SUPERVISION = ("381", "147")
CODE_AC_LOSS = "301"
CODE_PANEL_LOW_BATTERY = ("302", "309", "311")
CODE_JAMMING = "344"
CODE_TEST_REPORT = "602"
CODE_DURESS = "121"
CODE_ZONE_FAULT = "380"
CODE_ZONE_BYPASS = "570"
CODE_WALK_TEST = "607"
CODE_PROGRAMMING = ("627", "628")
DISARM_CODES = frozenset({f"{code}" for code in range(400, 410)})

# Short Danish texts for codes received by push before the panel logs them.
CODE_TEXT = {
    "110": "Brand", "111": "Røg", "120": "Panik", "121": "Tvangskode",
    "130": "Indbrud", "131": "Indbrud (skalsikring)", "132": "Indbrud (indvendig)",
    "134": "Indbrud (ind-/udgang)", "137": "Panel sabotage", "144": "Sabotage",
    "147": "Mistet forbindelse", "151": "Gas", "154": "Vandudslip", "162": "CO",
    "301": "230 V-fejl", "302": "Lavt batteri i central", "344": "Radiostøj",
    "381": "Mistet forbindelse", "383": "Sabotage", "384": "Lavt batteri",
    "374": "Tilkoblet med fejl", "380": "Sensorfejl",
    "401": "Til/frakobling", "441": "Deltilkobling", "456": "Deltilkobling",
    "461": "Forkert kode", "570": "Zone bypass", "602": "Testrapport",
    "607": "Gå-test", "627": "Programmering startet", "628": "Programmering afsluttet",
}

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
    reports: tuple[ReportEvent, ...],
    code: str | tuple[str, ...],
    zone: int | None,
    since: str = "",
) -> tuple[str, bool] | None:
    """Return (time, active) for the newest report with this code after `since`."""
    codes = (code,) if isinstance(code, str) else code
    for event in reports:
        if event.time <= since:
            break
        if event.code not in codes:
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
    reports: tuple[ReportEvent, ...],
    code: str | tuple[str, ...],
    zone: int | None,
    since: str = "",
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
    data: PanelData,
    zone: int,
    column: str | None,
    code: str | tuple[str, ...],
    words: tuple[str, ...],
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
    since = last_power_up(data)
    return bool(
        _newest(
            _latest_report(data.reports, CODE_ZONE_SUPERVISION, zone, since),
            _latest_log(data.history, zone, ("supervision",), since),
        )
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


def alarm_is_triggered(
    data: PanelData, now: datetime, armed: bool, hold: timedelta = ALARM_HOLD
) -> bool:
    """Return True when a reported alarm is still active.

    A burglary alarm stays active until the panel is armed or disarmed again.
    A 24-hour alarm (fire, water, panic, ...) also ends when its zone restores.
    Either way it ends after `hold` (the panel's siren time when set), so a
    missed disarm report cannot keep the entity triggered forever.
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
        if when is None or now - when > hold:
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


_INACTIVE_STATUS = frozenset({"", "normal", "ok", "luk", "lukket", "closed", "off", "0"})


def arm_mode(state_text: str) -> str | None:
    """Return 'away', 'home', or None (disarmed/unknown) for the panel state text."""
    value = state_text.casefold()
    if "fuldsikring" in value:
        return "away"
    if "deltilkobl" in value:
        return "home"
    return None


def zone_is_open(data: PanelData, zone: int) -> bool:
    """Return True when a zone reports open or active."""
    device = data.devices.get(zone)
    return device is not None and device.status.casefold().strip() not in _INACTIVE_STATUS


def entry_delay(data: PanelData, zone: int, mode: str) -> int:
    """Return the entry delay in seconds for an entry zone, 0 for instant zones."""
    device = data.devices.get(zone)
    settings = data.settings
    if device is None or settings is None:
        return 0
    attribute = device.attribute.casefold().replace(" ", "")
    if "indgang2" in attribute:
        return settings.entry2_away if mode == "away" else settings.entry2_home
    if "indgang" in attribute:
        return settings.entry1_away if mode == "away" else settings.entry1_home
    return 0


def exit_delay(data: PanelData, mode: str) -> int:
    """Return the exit delay in seconds for an arm mode."""
    if data.settings is None:
        return 0
    return data.settings.exit_away if mode == "away" else data.settings.exit_home


def zone_fault(data: PanelData, zone: int) -> str | None:
    """Return why a zone is faulty, or None.

    Sources: the device table's "Tilstand" column (e.g. "Funktionsfejl"), the
    panel's active-fault list, and sensor-trouble reports (CID 380).
    """
    device = data.devices.get(zone)
    if device and (condition := device.condition.strip()):
        if condition.casefold() not in _OK_WORDS:
            return condition
    names = (f"zone{zone} ", f"zone{zone}:", f"zone {zone} ", device.name if device else "")
    for fault in data.active_faults:
        text = fault.casefold()
        if any(name and name.casefold() in text for name in names):
            return fault
    if _latest_status(data.reports, CODE_ZONE_FAULT, zone, last_power_up(data)):
        return "Sensorfejl"
    return None


def zone_bypassed(data: PanelData, zone: int) -> str | None:
    """Return how a zone is bypassed, or None.

    "Deaktiver enhed" in the device table is a lasting bypass (for example
    "Permanent deaktiver"). A zone bypass report (CID 570) lasts until the
    panel is disarmed or the zone is restored.
    """
    device = data.devices.get(zone)
    if device and (disabled := device.disabled.strip()):
        if disabled.casefold() not in _OK_WORDS:
            return disabled
    since = last_power_up(data)
    for event in data.reports:
        if event.time <= since:
            break
        if event.code in DISARM_CODES and event.qualifier == "1":
            return None
        if event.code == CODE_ZONE_BYPASS and event.zone == zone:
            return "Zone bypass" if event.qualifier == "1" else None
    return None


def reporting_failed(data: PanelData) -> bool:
    """Return True when the newest report could not be delivered."""
    for event in data.reports:
        if event.status == "Push":
            continue
        status = event.status.casefold()
        if "venter" in status:
            continue
        return "ingen forbindelse" in status or "fejl" in status
    return False


def walk_test_active(data: PanelData) -> bool:
    """Return True while the panel is in walk-test mode (CID 607)."""
    return bool(_latest_status(data.reports, CODE_WALK_TEST, None, last_power_up(data)))


def zone_temperature(device_status: str) -> float | None:
    """Return a temperature in °C from a zone status like '21,5 °C'."""
    match = re.search(r"(-?\d+(?:[.,]\d+)?)\s*°\s*C", device_status)
    return float(match.group(1).replace(",", ".")) if match else None


def arm_commands(commands: tuple[str, ...]) -> dict[str, str]:
    """Map Home Assistant arm modes to the panel's control.htm buttons.

    Panels with one partial-arm button use it for "home". Panels with
    numbered partial arms use 1 for home, 2 for night, and 3 for custom.
    """
    found = {command.casefold(): command for command in commands}
    mapping: dict[str, str] = {}
    for command in commands:
        value = command.casefold()
        if value.startswith("frakobl"):
            mapping["disarm"] = command
        elif value.startswith("fuldsikring"):
            mapping["away"] = command
    numbered = {
        number: found[key]
        for number in ("1", "2", "3")
        if (key := f"deltilkobling {number}") in found
    }
    if numbered:
        if "1" in numbered:
            mapping["home"] = numbered["1"]
        if "2" in numbered:
            mapping["night"] = numbered["2"]
        if "3" in numbered:
            mapping["custom"] = numbered["3"]
    elif "deltilkobling" in found:
        mapping["home"] = found["deltilkobling"]
    return mapping


def is_camera(device_type: str) -> bool:
    """Return True for PIR cameras ('PIR m. kamera')."""
    value = device_type.casefold()
    return "kamera" in value or "camera" in value


def is_dimmer(device_type: str) -> bool:
    value = device_type.casefold()
    return "dæmper" in value or "dimmer" in value


def is_cover(device_type: str) -> bool:
    value = device_type.casefold()
    return any(word in value for word in ("gardin", "rulle", "jalousi", "markise", "shutter"))
