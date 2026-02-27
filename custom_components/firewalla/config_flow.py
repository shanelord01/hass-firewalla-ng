"""Config flow for Firewalla integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import FirewallaApiClient
from .const import (
    CONF_API_TOKEN,
    CONF_BOX_FILTER,
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

    def __init__(self) -> None:
        """Initialise flow state."""
        self._user_input: dict[str, Any] = {}
        self._boxes: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: credentials and feature toggles."""
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
                    # Fetch boxes now so Step 2 can present them
                    try:
                        self._boxes = await client.get_boxes()
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception("Could not fetch box list")
                        self._boxes = []

                    self._user_input = user_input

                    # If only one box, skip selection step
                    if len(self._boxes) <= 1:
                        return await self._async_create_entry(box_filter=[])

                    return await self.async_step_select_boxes()
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

    async def async_step_select_boxes(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2: choose which boxes to include (multi-box accounts only)."""
        if user_input is not None:
            return await self._async_create_entry(
                box_filter=user_input.get(CONF_BOX_FILTER, [])
            )

        all_gids = [b["id"] for b in self._boxes if "id" in b]

        options = [
            {
                "value": box["id"],
                "label": (
                    f"{box.get('name', box['id'])}"
                    + (f" — {box.get('model', '')}" if box.get("model") else "")
                    + (f" ({box.get('location', '')})" if box.get("location") else "")
                ),
            }
            for box in self._boxes
            if "id" in box
        ]

        schema = vol.Schema(
            {
                vol.Optional(CONF_BOX_FILTER, default=all_gids): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="select_boxes",
            data_schema=schema,
        )

    async def _async_create_entry(
        self, box_filter: list[str]
    ) -> config_entries.ConfigFlowResult:
        """Finalise the config entry."""
        subdomain = self._user_input[CONF_SUBDOMAIN]
        token = self._user_input[CONF_API_TOKEN]

        unique_id = f"{subdomain}_{token[-8:]}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        data = {**self._user_input, CONF_BOX_FILTER: box_filter}

        return self.async_create_entry(
            title=f"Firewalla ({subdomain})",
            data=data,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> FirewallaOptionsFlow:
        """Return the options flow handler."""
        return FirewallaOptionsFlow()


class FirewallaOptionsFlow(config_entries.OptionsFlow):
    """Handle Firewalla options."""

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

        # Build box selector if we have boxes in the coordinator
        coordinator = self.config_entry.runtime_data.coordinator if hasattr(
            self.config_entry, "runtime_data"
        ) else None

        boxes = []
        if coordinator and coordinator.data:
            boxes = coordinator.data.get("boxes", [])

        schema_fields: dict = {
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

        # Only show box filter if there are multiple boxes
        if len(boxes) > 1:
            all_gids = [b["id"] for b in boxes if "id" in b]
            current_filter = _current(CONF_BOX_FILTER, all_gids)
            options = [
                {
                    "value": box["id"],
                    "label": (
                        f"{box.get('name', box['id'])}"
                        + (f" — {box.get('model', '')}" if box.get("model") else "")
                        + (f" ({box.get('location', '')})" if box.get("location") else "")
                    ),
                }
                for box in boxes
                if "id" in box
            ]
            schema_fields[
                vol.Optional(CONF_BOX_FILTER, default=current_filter)
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_fields),
        )
