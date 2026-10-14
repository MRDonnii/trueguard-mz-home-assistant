"""HTTP client for TrueGuard MZ alarm panels."""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlparse

import aiohttp

from .const import COMMAND_ARM_AWAY, COMMAND_ARM_HOME, COMMAND_DISARM
from .parser import (
    AreaSettings,
    Capture,
    LogEntry,
    PanelData,
    PanelInfo,
    PanelSettings,
    ReportEvent,
    parse_control_page,
    parse_device_page,
    parse_gsm_page,
    parse_history_page,
    parse_area_page,
    parse_capture_page,
    parse_control_buttons,
    parse_gsm_details,
    parse_info_page,
    parse_panel_clock,
    parse_panel_page,
    parse_report_page,
)

RETRY_DELAY = 0.3


class TrueGuardError(Exception):
    """Base API error."""


class TrueGuardAuthError(TrueGuardError):
    """Authentication failed."""


class TrueGuardConnectionError(TrueGuardError):
    """Panel communication failed."""


def normalize_host(host: str) -> str:
    """Normalize a user-provided panel address."""
    host = host.strip()
    if not urlparse(host).scheme:
        host = f"http://{host}"
    parsed = urlparse(host)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Invalid panel address")
    return f"{parsed.scheme}://{parsed.netloc}"


class TrueGuardClient:
    """Async client using the panel's HTTP Basic authentication."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
    ) -> None:
        self.host = normalize_host(host)
        self._session = session
        self._auth = aiohttp.BasicAuth(username, password)

    async def _request(
        self, method: str, path: str, data: dict[str, str] | None = None
    ) -> str:
        """Request a page; read-only GETs are retried once on network errors."""
        try:
            return await self._request_once(method, path, data)
        except TrueGuardConnectionError:
            if method != "GET":
                raise
            await asyncio.sleep(RETRY_DELAY)
            return await self._request_once(method, path, data)

    async def async_fetch(self, path: str) -> tuple[bytes, str]:
        """Download a file from the panel (picture, backup) with its content type."""
        url = urljoin(f"{self.host}/", path)
        try:
            async with asyncio.timeout(30):
                response = await self._session.get(url, auth=self._auth)
                if response.status in (401, 403):
                    raise TrueGuardAuthError("The panel rejected the login")
                if response.status >= 400:
                    raise TrueGuardConnectionError(
                        f"Panel returned HTTP {response.status} for {path}"
                    )
                return await response.read(), response.headers.get(
                    "Content-Type", ""
                )
        except TrueGuardError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TrueGuardConnectionError(str(err)) from err

    async def _request_once(
        self, method: str, path: str, data: dict[str, str] | None = None
    ) -> str:
        url = urljoin(f"{self.host}/", path)
        try:
            async with asyncio.timeout(10):
                response = await self._session.request(
                    method, url, auth=self._auth, data=data
                )
                if response.status in (401, 403):
                    raise TrueGuardAuthError("The panel rejected the login")
                if response.status >= 400:
                    raise TrueGuardConnectionError(
                        f"Panel returned HTTP {response.status} for {path}"
                    )
                return await response.text(encoding="utf-8", errors="replace")
        except TrueGuardError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TrueGuardConnectionError(str(err)) from err

    async def async_validate(self) -> None:
        """Validate address, credentials, and panel identity."""
        html = await self._request("GET", "control.htm")
        if "Panel tilstand" not in html:
            raise TrueGuardConnectionError(
                "This does not look like a TrueGuard MZ panel"
            )
        parse_control_page(html)

    async def async_get_events(
        self,
    ) -> tuple[tuple[LogEntry, ...], tuple[ReportEvent, ...]]:
        """Read the event log and the Contact ID report list."""
        history_html, report_html = await asyncio.gather(
            self._request("GET", "history.htm"),
            self._request("GET", "rptevent.htm"),
        )
        return parse_history_page(history_html), parse_report_page(report_html)

    async def async_get_info(self) -> PanelInfo:
        """Read firmware and module versions."""
        return parse_info_page(await self._request("GET", "index.htm"))

    async def async_get_settings(
        self,
    ) -> tuple[AreaSettings, str | None, PanelSettings]:
        """Read area times (area.htm), the clock and check-in/temperature (panel.htm)."""
        area_html = await self._request("GET", "area.htm")
        panel_html = await self._request("GET", "panel.htm")
        return (
            parse_area_page(area_html),
            parse_panel_clock(panel_html),
            parse_panel_page(panel_html),
        )

    async def async_get_captures(self) -> tuple[Capture, ...]:
        """Read the newest PIR-camera pictures and videos (capevent.htm)."""
        return parse_capture_page(await self._request("GET", "capevent.htm"))

    async def async_get_backup(self) -> bytes:
        """Download the panel's configuration backup (cfg.bin)."""
        data, _ = await self.async_fetch("cfg.bin")
        return data

    async def async_get_gsm(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        """Read the GSM module status and its other rows (signal, operator, ...)."""
        html = await self._request("GET", "gsm.htm")
        return parse_gsm_page(html), parse_gsm_details(html)

    async def async_get_data(self) -> PanelData:
        """Read alarm state and all device states."""
        control_html, device_html = await asyncio.gather(
            self._request("GET", "control.htm"),
            self._request("GET", "device.htm"),
        )
        state, faults = parse_control_page(control_html)
        return PanelData(
            alarm_state=state,
            active_faults=faults,
            devices=parse_device_page(device_html),
            commands=parse_control_buttons(control_html),
        )

    async def async_set_alarm_state(
        self, command: str, allowed: tuple[str, ...] = ()
    ) -> None:
        """Send one of the panel's arm/disarm buttons on control.htm."""
        known = (COMMAND_DISARM, COMMAND_ARM_AWAY, COMMAND_ARM_HOME, *allowed)
        if command not in known:
            raise ValueError(f"Unsupported alarm command: {command}")
        await self._request("POST", "control.htm", {"S": command})

    async def _device_command(self, zone: int, command: str, **extra: str) -> None:
        """Press a button on the device page for one zone."""
        await self._request(
            "POST", "device.htm", {f"Z{zone}": "1", "S": command, **extra}
        )

    async def async_take_picture(self, zone: int, flash: bool = True) -> None:
        """Ask a PIR camera for a picture."""
        await self._device_command(
            zone, "Tag billede/video" if flash else "Tag billede (uden blitz)"
        )

    async def async_set_dimmer(self, zone: int, level: int) -> None:
        """Set a dimmer module to 0-100 % (steps of 10)."""
        await self._device_command(zone, "Dæmper niveau", B=str(level))

    async def async_cover_command(self, zone: int, command: str) -> None:
        """Send 'Op / lås', 'Ned / lås op', or 'Stop' to a module."""
        if command not in ("Op / lås", "Ned / lås op", "Stop"):
            raise ValueError(f"Unsupported module command: {command}")
        await self._device_command(zone, command)

    async def async_set_switch(self, zone: int, turn_on: bool) -> None:
        """Control a panel switch module."""
        command = "Modul tændt" if turn_on else "Modul slukket"
        await self._request("POST", "device.htm", {f"Z{zone}": "1", "S": command})
