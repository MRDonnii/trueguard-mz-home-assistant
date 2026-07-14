"""Config flow for TrueGuard MZ."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import network
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .api import (
    TrueGuardAuthError,
    TrueGuardClient,
    TrueGuardConnectionError,
    normalize_host,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .discovery import async_discover_hosts, async_is_webpanel


def _scan_interval() -> vol.All:
    """Return the shared polling interval validator."""
    return vol.All(
        vol.Coerce(int),
        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
    )


async def _validate(hass: HomeAssistant, data: dict[str, Any]) -> str:
    """Validate credentials and return a normalized panel URL."""
    host = normalize_host(data[CONF_HOST])
    client = TrueGuardClient(
        async_get_clientsession(hass),
        host,
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
    )
    await client.async_validate()
    return host


class TrueGuardConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup, discovery, reconfiguration, and reauthentication."""

    VERSION = 2

    def __init__(self) -> None:
        self._discovered_hosts: list[str] = []
        self._dhcp_host: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Let the user choose automatic discovery or manual setup."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["automatic", "manual"],
        )

    async def async_step_automatic(self, user_input: dict[str, Any] | None = None):
        """Scan selected local interfaces for the WebPanel fingerprint."""
        adapters = await network.async_get_adapters(self.hass)
        self._discovered_hosts = await async_discover_hosts(
            async_get_clientsession(self.hass), adapters
        )
        if not self._discovered_hosts:
            return self.async_show_form(
                step_id="automatic",
                data_schema=vol.Schema({}),
                errors={"base": "no_devices_found"},
            )
        return await self.async_step_credentials()

    def _credentials_schema(self) -> vol.Schema:
        """Return a schema for credentials after active discovery."""
        fields: dict[Any, Any] = {}
        if len(self._discovered_hosts) > 1:
            fields[vol.Required(CONF_HOST, default=self._discovered_hosts[0])] = vol.In(
                self._discovered_hosts
            )
        fields.update(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): _scan_interval(),
            }
        )
        return vol.Schema(fields)

    async def async_step_credentials(self, user_input: dict[str, Any] | None = None):
        """Collect credentials for a panel found by active discovery."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = dict(user_input)
            if len(self._discovered_hosts) == 1:
                data[CONF_HOST] = self._discovered_hosts[0]
            return await self._async_create_panel_entry(data, errors, "credentials")

        return self.async_show_form(
            step_id="credentials",
            data_schema=self._credentials_schema(),
            errors=errors,
            description_placeholders={"count": str(len(self._discovered_hosts))},
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        """Set up a panel by hostname or IP address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            return await self._async_create_panel_entry(user_input, errors, "manual")
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                    ): _scan_interval(),
                }
            ),
            errors=errors,
        )

    async def _async_create_panel_entry(
        self,
        user_input: dict[str, Any],
        errors: dict[str, str],
        step_id: str,
    ):
        """Validate and create a panel config entry."""
        try:
            host = await _validate(self.hass, user_input)
        except TrueGuardAuthError:
            errors["base"] = "invalid_auth"
        except (TrueGuardConnectionError, ValueError):
            errors["base"] = "cannot_connect"
        else:
            self._async_abort_entries_match({CONF_HOST: host})
            data = dict(user_input)
            data[CONF_HOST] = host
            return self.async_create_entry(title="TrueGuard MZ", data=data)

        if step_id == "credentials":
            return self.async_show_form(
                step_id="credentials",
                data_schema=self._credentials_schema(),
                errors=errors,
                description_placeholders={
                    "count": str(len(self._discovered_hosts))
                },
            )
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "")): str,
                    vol.Required(
                        CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=user_input.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): _scan_interval(),
                }
            ),
            errors=errors,
        )

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo):
        """Handle passive DHCP discovery and future IP address changes."""
        host = normalize_host(discovery_info.ip)
        session = async_get_clientsession(self.hass)
        if not await async_is_webpanel(session, host):
            return self.async_abort(reason="not_trueguard")

        mac = format_mac(discovery_info.macaddress)
        for entry in self._async_current_entries():
            if (
                normalize_host(entry.data[CONF_HOST]) == host
                and entry.unique_id is None
            ):
                self.hass.config_entries.async_update_entry(entry, unique_id=mac)
                return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._dhcp_host = host
        self.context["title_placeholders"] = {"name": "TrueGuard MZ"}
        return await self.async_step_dhcp_confirm()

    async def async_step_dhcp_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Confirm a DHCP-discovered panel and collect its login."""
        errors: dict[str, str] = {}
        if user_input is not None and self._dhcp_host is not None:
            data = {**user_input, CONF_HOST: self._dhcp_host}
            try:
                host = await _validate(self.hass, data)
            except TrueGuardAuthError:
                errors["base"] = "invalid_auth"
            except (TrueGuardConnectionError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                data[CONF_HOST] = host
                return self.async_create_entry(title="TrueGuard MZ", data=data)

        return self.async_show_form(
            step_id="dhcp_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                    ): _scan_interval(),
                }
            ),
            errors=errors,
            description_placeholders={"host": self._dhcp_host or ""},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ):
        """Allow changing the panel address without removing entities."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            try:
                host = await _validate(self.hass, data)
            except TrueGuardAuthError:
                errors["base"] = "invalid_auth"
            except (TrueGuardConnectionError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                data[CONF_HOST] = host
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Update rejected WebPanel credentials."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**reauth_entry.data, **user_input}
            try:
                await _validate(self.hass, data)
            except TrueGuardAuthError:
                errors["base"] = "invalid_auth"
            except (TrueGuardConnectionError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data_updates=data
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Return the polling options flow."""
        return TrueGuardOptionsFlow()


class TrueGuardOptionsFlow(config_entries.OptionsFlowWithReload):
    """Configure polling without entering credentials again."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Set the coordinator polling interval."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): _scan_interval()
                }
            ),
        )
