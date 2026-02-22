"""Config flow for Firewalla integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FirewallaApiClient
from .const import (
    CONF_API_TOKEN,
    CONF_ENABLE_ALARMS,
    CONF_ENABLE_FLOWS,
    CONF_ENABLE_RULES,
    CONF_ENABLE_TRAFFIC,
    CONF_STALE_DAYS,
    CONF_SUBDOMAIN,
    CONF_TRACK_DEVICES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_DAYS,
    DEFAULT_SUBDOMAIN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class FirewallaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup config flow for Firewalla."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step - credentials and feature toggles."""
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = FirewallaApiClient(
                session=session,
                api_token=user_input[CONF_API_TOKEN],
                subdomain=user_input[CONF_SUBDOMAIN],
            )
            try:
                ok = await client.async_check_credentials()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error during Firewalla credential check")
                errors["base"] = "cannot_connect"
            else:
                if ok:
                    unique_id = (
                        f"{user_input[CONF_SUBDOMAIN]}_{user_input[CONF_API_TOKEN][-8:]}"
                    )
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Firewalla ({user_input[CONF_SUBDOMAIN]})",
                        data=user_input,
                    )
                else:
                    errors["base"] = "invalid_auth"

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SUBDOMAIN,
                    default=(user_input or {}).get(CONF_SUBDOMAIN, DEFAULT_SUBDOMAIN),
                ): str,
                vol.Required(CONF_API_TOKEN): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=(user_input or {}).get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.All(int, vol.Range(min=30, max=86400)),
                vol.Optional(CONF_ENABLE_ALARMS, default=False): bool,
                vol.Optional(CONF_ENABLE_RULES, default=False): bool,
                vol.Optional(CONF_ENABLE_FLOWS, default=False): bool,
                vol.Optional(CONF_ENABLE_TRAFFIC, default=False): bool,
                vol.Optional(CONF_TRACK_DEVICES, default=True): bool,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> FirewallaOptionsFlow:
        """Return the options flow handler."""
        return FirewallaOptionsFlow()


class FirewallaOptionsFlow(config_entries.OptionsFlow):
    """Handle Firewalla options - modifying settings after initial setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Display and handle the options form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        def _current(key: str, default: Any) -> Any:
            return self.config_entry.options.get(
                key, self.config_entry.data.get(key, default)
            )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=_current(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=30, max=86400)),
                vol.Optional(
                    CONF_ENABLE_ALARMS,
                    default=_current(CONF_ENABLE_ALARMS, False),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_RULES,
                    default=_current(CONF_ENABLE_RULES, False),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_FLOWS,
                    default=_current(CONF_ENABLE_FLOWS, False),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_TRAFFIC,
                    default=_current(CONF_ENABLE_TRAFFIC, False),
                ): bool,
                vol.Optional(
                    CONF_TRACK_DEVICES,
                    default=_current(CONF_TRACK_DEVICES, True),
                ): bool,
                vol.Optional(
                    CONF_STALE_DAYS,
                    default=_current(CONF_STALE_DAYS, DEFAULT_STALE_DAYS),
                ): vol.All(int, vol.Range(min=1, max=365)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
