"""HTTP client for TrueGuard MZ alarm panels."""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlparse

import aiohttp

from .const import COMMAND_ARM_AWAY, COMMAND_ARM_HOME, COMMAND_DISARM
from .parser import PanelData, parse_control_page, parse_device_page


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
        )

    async def async_set_alarm_state(self, command: str) -> None:
        """Send one of the panel's supported alarm commands."""
        if command not in (COMMAND_DISARM, COMMAND_ARM_AWAY, COMMAND_ARM_HOME):
            raise ValueError(f"Unsupported alarm command: {command}")
        await self._request("POST", "control.htm", {"S": command})

    async def async_set_switch(self, zone: int, turn_on: bool) -> None:
        """Control a panel switch module."""
        command = "Modul tændt" if turn_on else "Modul slukket"
        await self._request("POST", "device.htm", {f"Z{zone}": "1", "S": command})
