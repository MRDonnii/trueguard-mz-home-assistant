"""Local network discovery for TrueGuard MZ WebPanel devices."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from typing import Any
from urllib.parse import urljoin

import aiohttp

WEBPANEL_REALM = 'realm="webpanel"'
DISCOVERY_TIMEOUT = 1.25
DISCOVERY_CONCURRENCY = 64
MAX_DISCOVERY_HOSTS = 1024


def local_ipv4_hosts(adapters: Iterable[Mapping[str, Any]]) -> list[str]:
    """Return bounded private IPv4 candidates from enabled HA adapters."""
    networks: set[IPv4Network] = set()
    own_addresses: set[IPv4Address] = set()

    for adapter in adapters:
        if not adapter.get("enabled", False):
            continue
        for ip_info in adapter.get("ipv4", []):
            address = ip_address(ip_info["address"])
            if not isinstance(address, IPv4Address):
                continue
            if not address.is_private or address.is_loopback or address.is_link_local:
                continue
            own_addresses.add(address)
            prefix = max(int(ip_info["network_prefix"]), 24)
            networks.add(ip_network(f"{address}/{prefix}", strict=False))

    hosts: list[str] = []
    for network in sorted(
        networks, key=lambda item: (int(item.network_address), item.prefixlen)
    ):
        for host in network.hosts():
            if host in own_addresses:
                continue
            hosts.append(str(host))
            if len(hosts) >= MAX_DISCOVERY_HOSTS:
                return hosts
    return hosts


async def async_is_webpanel(
    session: aiohttp.ClientSession,
    host: str,
    *,
    timeout: float = DISCOVERY_TIMEOUT,
) -> bool:
    """Check for the unauthenticated fingerprint exposed by a WebPanel."""
    url = urljoin(f"{host.rstrip('/')}/", "control.htm")
    try:
        async with session.get(
            url,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            challenge = response.headers.get("WWW-Authenticate", "").casefold()
            if response.status in (401, 403):
                return WEBPANEL_REALM in challenge
            if response.status != 200:
                return False
            body = await response.text(encoding="utf-8", errors="replace")
            return "panel tilstand" in body.casefold()
    except (aiohttp.ClientError, TimeoutError, UnicodeError):
        return False


async def async_discover_hosts(
    session: aiohttp.ClientSession,
    adapters: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Discover compatible WebPanel hosts on selected local networks."""
    candidates = local_ipv4_hosts(adapters)
    semaphore = asyncio.Semaphore(DISCOVERY_CONCURRENCY)

    async def _probe(address: str) -> str | None:
        host = f"http://{address}"
        async with semaphore:
            return host if await async_is_webpanel(session, host) else None

    discovered = await asyncio.gather(*(_probe(address) for address in candidates))
    return sorted(host for host in discovered if host is not None)
