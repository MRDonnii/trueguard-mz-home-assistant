"""Parse the HTML pages exposed by TrueGuard MZ alarm panels."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re


@dataclass(frozen=True, slots=True)
class PanelDevice:
    """A device row returned by device.htm."""

    zone: int
    device_type: str
    name: str
    attribute: str = ""
    condition: str = ""
    battery: str = ""
    tamper: str = ""
    disabled: str = ""
    signal: int | None = None
    status: str = ""




class _TableParser(HTMLParser):
    """Small tolerant table parser for the panel's HTML 4 pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def visible_text(html: str) -> str:
    """Return normalized visible text."""
    parser = _TextParser()
    parser.feed(html)
    return "\n".join(_clean(part) for part in parser.parts if _clean(part))


def _fault_rows(html: str) -> tuple[str, ...] | None:
    """Return one text per row of the "Aktive fejl" table, or None if absent.

    A row is a fault and the zone it belongs to, for example
    "Zone18 (Garageport): Sensortjek fejl".
    """
    parser = _TableParser()
    parser.feed(html)
    for table in parser.tables:
        if not table or not table[0] or table[0][0].casefold() != "aktive fejl":
            continue
        faults = []
        for row in table[1:]:
            cells = [cell for cell in row if cell]
            if not cells or "ingen enheder fundet" in " ".join(cells).casefold():
                continue
            zones = [cell for cell in cells if re.match(r"zone\s*\d+", cell, re.I)]
            others = [cell for cell in cells if cell not in zones]
            if zones and others:
                faults.append(f"{zones[0]}: {' – '.join(others)}")
            else:
                faults.append(" – ".join(cells))
        return tuple(faults)
    return None


def parse_control_page(html: str) -> tuple[str, tuple[str, ...]]:
    """Parse alarm state and active faults from control.htm."""
    text = visible_text(html)
    state_match = re.search(r"Panel tilstand:\s*([^\n]+)", text, re.IGNORECASE)
    if not state_match:
        raise ValueError("Panel state was not found in control.htm")

    state = _clean(state_match.group(1))
    faults = _fault_rows(html)
    if faults is None:
        faults = ()
        fault_match = re.search(
            r"Aktive fejl\s*(.*?)(?:©|$)", text, re.IGNORECASE | re.DOTALL
        )
        if fault_match:
            faults = tuple(
                line.strip()
                for line in fault_match.group(1).splitlines()
                if line.strip() and "Ingen enheder fundet" not in line
            )
    return state, faults


def parse_control_buttons(html: str) -> tuple[str, ...]:
    """Return the arm/disarm button texts on control.htm, e.g. 'Deltilkobling 2'."""
    return tuple(
        _clean(value)
        for value in re.findall(
            r'<input[^>]*name="S"[^>]*value="([^"]+)"[^>]*type=submit',
            html,
            re.IGNORECASE,
        )
    )


def parse_device_page(html: str) -> dict[int, PanelDevice]:
    """Parse all device rows from device.htm."""
    parser = _TableParser()
    parser.feed(html)

    for table in parser.tables:
        if not table:
            continue
        header = [cell.casefold() for cell in table[0]]
        if "index" not in header or "type" not in header or "status" not in header:
            continue

        devices: dict[int, PanelDevice] = {}
        for row in table[1:]:
            if len(row) < 10:
                continue
            zone_match = re.search(r"\d+", row[0])
            if not zone_match:
                continue
            zone = int(zone_match.group())
            signal_match = re.search(r"-?\d+", row[8])
            devices[zone] = PanelDevice(
                zone=zone,
                device_type=row[1],
                name=row[2] or f"Zone {zone}",
                attribute=row[3],
                condition=row[4],
                battery=row[5],
                tamper=row[6],
                disabled=row[7],
                signal=int(signal_match.group()) if signal_match else None,
                status=row[9],
            )
        if devices:
            return devices

    raise ValueError("Device table was not found in device.htm")


@dataclass(frozen=True, slots=True)
class LogEntry:
    """A row from the panel's event log (history.htm)."""

    time: str
    device: str = ""
    user: str = ""
    event: str = ""
    zone: int | None = None


@dataclass(frozen=True, slots=True)
class ReportEvent:
    """A row from the panel's report list (rptevent.htm) with its Contact ID."""

    time: str
    event: str = ""
    cid: str = ""
    group: int | None = None
    zone: int | None = None
    status: str = ""

    @property
    def qualifier(self) -> str:
        """Contact ID qualifier: 1 = new event/disarm, 3 = restore/arm."""
        return self.cid[:1] if len(self.cid) == 4 else ""

    @property
    def code(self) -> str:
        """Three-digit Contact ID event code."""
        return self.cid[1:] if len(self.cid) == 4 else ""


@dataclass(frozen=True, slots=True)
class PanelInfo:
    """Static panel information from index.htm."""

    firmware: str = ""
    zigbee: str = ""
    gsm_module: str = ""
    mac: str = ""


@dataclass(frozen=True, slots=True)
class PanelData:
    """A complete polling snapshot."""

    alarm_state: str
    active_faults: tuple[str, ...] = ()
    devices: dict[int, PanelDevice] = field(default_factory=dict)
    history: tuple[LogEntry, ...] = ()
    reports: tuple[ReportEvent, ...] = ()
    gsm_status: str | None = None
    settings: AreaSettings | None = None
    panel_clock: str | None = None
    clock_offset: int | None = None
    commands: tuple[str, ...] = ()
    captures: tuple[Capture, ...] = ()
    panel_settings: PanelSettings | None = None
    gsm_details: tuple[tuple[str, str], ...] = ()


def _find_table(html: str, *required: str) -> list[list[str]] | None:
    """Return the first table whose header row contains all required labels."""
    parser = _TableParser()
    parser.feed(html)
    for table in parser.tables:
        if not table:
            continue
        header = [cell.casefold() for cell in table[0]]
        if all(label in header for label in required):
            return table
    return None


def _int_or_none(value: str) -> int | None:
    match = re.search(r"-?\d+", value)
    return int(match.group()) if match else None


def _zone_from_device(device: str) -> int | None:
    match = re.match(r"\s*zone\s*(\d+)", device, re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_history_page(html: str) -> tuple[LogEntry, ...]:
    """Parse the event log from history.htm, newest first."""
    table = _find_table(html, "tid", "enhed", "hændelse")
    if table is None:
        raise ValueError("Event table was not found in history.htm")
    entries = []
    for row in table[1:]:
        if len(row) < 4 or not re.match(r"\d{4}-\d{2}-\d{2}", row[0]):
            continue
        entries.append(
            LogEntry(
                time=row[0],
                device=row[1],
                user=row[2],
                event=row[3],
                zone=_zone_from_device(row[1]),
            )
        )
    return tuple(entries)


def parse_report_page(html: str) -> tuple[ReportEvent, ...]:
    """Parse reported events with Contact ID codes from rptevent.htm."""
    table = _find_table(html, "tid", "hændelse", "cid hændelse")
    if table is None:
        raise ValueError("Report table was not found in rptevent.htm")
    events = []
    for row in table[1:]:
        if len(row) < 6 or not re.match(r"\d{4}-\d{2}-\d{2}", row[0]):
            continue
        events.append(
            ReportEvent(
                time=row[0],
                event=row[1],
                cid=row[2].strip(),
                group=_int_or_none(row[3]),
                zone=_int_or_none(row[4]),
                status=row[5],
            )
        )
    return tuple(events)


def _key_values(html: str) -> dict[str, str]:
    parser = _TableParser()
    parser.feed(html)
    values: dict[str, str] = {}
    for table in parser.tables:
        for row in table:
            if len(row) >= 2 and row[0].endswith(":"):
                values.setdefault(row[0][:-1].strip().casefold(), row[1])
    return values


def parse_info_page(html: str) -> PanelInfo:
    """Parse firmware and module versions from index.htm."""
    values = _key_values(html)
    return PanelInfo(
        firmware=values.get("firmware revision", ""),
        zigbee=values.get("zigbee revision", ""),
        gsm_module=values.get("gsm revision", ""),
        mac=values.get("mac adresse", ""),
    )


def parse_gsm_page(html: str) -> str:
    """Return the GSM module status text from gsm.htm."""
    return _key_values(html).get("status", "")


@dataclass(frozen=True, slots=True)
class AreaSettings:
    """Timing settings from area.htm, in seconds (0 = not active)."""

    entry1_away: int = 0
    entry1_home: int = 0
    entry2_away: int = 0
    entry2_home: int = 0
    exit_away: int = 0
    exit_home: int = 0
    siren: int = 0
    supervision: int = 0


_UNIT_SECONDS = {"sek": 1, "min": 60, "time": 3600, "timer": 3600}


def _duration(text: str) -> int:
    """Convert option text like '10 sek', '3 min', or '4 timer' to seconds."""
    match = re.search(r"(\d+)\s*(sek|min|timer|time)", text.casefold())
    if not match:
        return 0
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)]


def _selected_texts(row_html: str) -> list[str]:
    """Return the selected option text of every select in a table row."""
    texts = []
    for select in re.findall(r"<select[^>]*>(.*?)</select>", row_html, re.S | re.I):
        match = re.search(r"<option[^>]*selected[^>]*>([^<]*)", select, re.I)
        texts.append(_clean(match.group(1)) if match else "")
    return texts


def parse_area_page(html: str) -> AreaSettings:
    """Parse entry, exit, siren, and supervision times from area.htm."""
    values: dict[str, list[int]] = {}
    for row in re.findall(r"<tr>(.*?)</tr>", html, re.S | re.I):
        label = _clean(re.sub(r"<[^>]+>", "", row.split("</td>", 1)[0])).casefold()
        values[label.rstrip(":")] = [_duration(text) for text in _selected_texts(row)]

    def pick(label: str, index: int = 0) -> int:
        row = values.get(label, [])
        return row[index] if len(row) > index else 0

    if "sirene tid" not in values:
        raise ValueError("Area settings were not found in area.htm")
    return AreaSettings(
        entry1_away=pick("indgangstid1"),
        entry1_home=pick("indgangstid1", 1),
        entry2_away=pick("indgangstid2"),
        entry2_home=pick("indgangstid2", 1),
        exit_away=pick("udgangstid"),
        exit_home=pick("udgangstid", 1),
        siren=pick("sirene tid"),
        supervision=pick("supervision timer"),
    )


def parse_panel_clock(html: str) -> str | None:
    """Return the panel clock from panel.htm as 'YYYY-MM-DD HH:MM', if present."""
    parts = []
    for name in ("Y", "M", "D", "E", "F"):
        select = re.search(
            rf'<select name="{name}">(.*?)</select>', html, re.S | re.I
        )
        if not select:
            return None
        match = re.search(r'<option value="(\d+)"[^>]*selected', select.group(1), re.I)
        if not match:
            return None
        parts.append(int(match.group(1)))
    year, month, day, hour, minute = parts
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


@dataclass(frozen=True, slots=True)
class Capture:
    """A row on Billeder/Video (capevent.htm) from a PIR camera."""

    time: str
    device: str = ""
    kind: str = ""
    status: str = ""
    zone: int | None = None
    links: tuple[str, ...] = ()


class _RowLinkParser(HTMLParser):
    """Collect cell texts plus link and image addresses for every table row."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[list[str], list[str]]] = []
        self._cells: list[str] | None = None
        self._links: list[str] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = dict(attrs)
        if tag == "tr":
            self._cells, self._links = [], []
        elif tag in ("td", "th") and self._cells is not None:
            self._cell = []
        elif self._cells is not None and tag in ("a", "img", "source", "video", "embed"):
            for key in ("href", "src", "data"):
                if values.get(key):
                    self._links.append(values[key])

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th") and self._cell is not None and self._cells is not None:
            self._cells.append(_clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._cells is not None:
            self.rows.append((self._cells, self._links))
            self._cells = None


def parse_capture_page(html: str) -> tuple[Capture, ...]:
    """Parse the newest pictures/videos from capevent.htm, newest first."""
    parser = _RowLinkParser()
    parser.feed(html)
    captures = []
    for cells, links in parser.rows:
        if not cells or not re.match(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue
        cells = cells + [""] * (4 - len(cells))
        captures.append(
            Capture(
                time=cells[0],
                device=cells[1],
                kind=cells[2],
                status=cells[3],
                zone=_zone_from_device(cells[1]),
                links=tuple(dict.fromkeys(link for link in links if link)),
            )
        )
    return tuple(captures)


def parse_gsm_details(html: str) -> tuple[tuple[str, str], ...]:
    """Return every 'label: value' row on gsm.htm (status, signal, operator, ...)."""
    return tuple(_key_values(html).items())


@dataclass(frozen=True, slots=True)
class PanelSettings:
    """Settings from panel.htm used by the integration."""

    checkin_interval: int = 0  # seconds, 0 = not active
    temperature_high: int | None = None  # °C, None = not active
    temperature_low: int | None = None


def _selected_row(html: str, label: str) -> list[str]:
    for row in re.findall(r"<tr>(.*?)</tr>", html, re.S | re.I):
        cell = _clean(re.sub(r"<[^>]+>", "", row.split("</td>", 1)[0])).casefold()
        if cell.startswith(label):
            return _selected_texts(row)
    return []


def _temperature_limit(texts: list[str]) -> int | None:
    match = re.search(r"(-?\d+)\s*°", texts[0]) if texts else None
    return int(match.group(1)) if match else None


def parse_panel_page(html: str) -> PanelSettings:
    """Parse the auto check-in interval and temperature alarm limits."""
    checkin = _selected_row(html, "auto check-in")
    return PanelSettings(
        checkin_interval=_duration(checkin[0]) if checkin else 0,
        temperature_high=_temperature_limit(_selected_row(html, "høj temperatur")),
        temperature_low=_temperature_limit(_selected_row(html, "lav temperatur")),
    )
