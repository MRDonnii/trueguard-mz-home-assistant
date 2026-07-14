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


@dataclass(frozen=True, slots=True)
class PanelData:
    """A complete polling snapshot."""

    alarm_state: str
    active_faults: tuple[str, ...] = ()
    devices: dict[int, PanelDevice] = field(default_factory=dict)


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


def parse_control_page(html: str) -> tuple[str, tuple[str, ...]]:
    """Parse alarm state and active faults from control.htm."""
    text = visible_text(html)
    state_match = re.search(r"Panel tilstand:\s*([^\n]+)", text, re.IGNORECASE)
    if not state_match:
        raise ValueError("Panel state was not found in control.htm")

    state = _clean(state_match.group(1))
    faults: tuple[str, ...] = ()
    fault_match = re.search(
        r"Aktive fejl\s*(.*?)(?:©|$)", text, re.IGNORECASE | re.DOTALL
    )
    if fault_match:
        values = [
            line.strip()
            for line in fault_match.group(1).splitlines()
            if line.strip() and "Ingen enheder fundet" not in line
        ]
        faults = tuple(values)
    return state, faults


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
