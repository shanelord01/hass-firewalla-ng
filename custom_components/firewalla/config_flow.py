"""Config flow for Firewalla integration."""
from __future__ import annotations

import hashlib
import logging
import re
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
    CONF_ENABLE_TARGET_LISTS,
    CONF_ENABLE_TRAFFIC,
    CONF_STALE_DAYS,
    CONF_SUBDOMAIN,
    CONF_TRACK_DEVICES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_DAYS,
    DEFAULT_SUBDOMAIN,
    DOMAIN,
    FirewallaAuthError,
)

_LOGGER = logging.getLogger(__name__)

# RFC-952/1123 subdomain pattern — prevents control characters, spaces,
# and URL-significant characters from reaching the URL constructor.
_SUBDOMAIN_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")


def _validate_subdomain(value: str) -> str:
    """Validate and normalise a subdomain value.

    Must be called in the handler, not embedded in the voluptuous schema —
    voluptuous_serialize cannot serialise bare Python functions and will raise
    ValueError when HA tries to render the config flow form (HTTP 500).
    """
    value = value.strip().lower()
    if not _SUBDOMAIN_RE.match(value):
        raise vol.Invalid(
            "Subdomain must be 1-63 alphanumeric characters or hyphens, "
            "starting and ending with a letter or digit."
        )
    return value


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
            # Validate subdomain manually — cannot be done in the schema because
            # voluptuous_serialize rejects bare Python callables and raises a
            # ValueError that surfaces as HTTP 500 before the form renders.
            try:
                user_input[CONF_SUBDOMAIN] = _validate_subdomain(
                    user_input[CONF_SUBDOMAIN]
                )
            except vol.Invalid:
                errors[CONF_SUBDOMAIN] = "invalid_subdomain"

            if not errors:
                session = async_get_clientsession(self.hass)
                client = FirewallaApiClient(
                    session=session,
                    api_token=user_input[CONF_API_TOKEN],
                    subdomain=user_input[CONF_SUBDOMAIN],
                )
                try:
                    # Use get_boxes() directly — a non-empty result confirms valid
                    # credentials without a separate async_check_credentials() call
                    # that would hit GET /boxes twice in rapid succession.
                    boxes = await client.get_boxes()
                except FirewallaAuthError:
                    errors["base"] = "invalid_auth"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Error during Firewalla credential check")
                    errors["base"] = "cannot_connect"
                else:
                    # get_boxes() returns None on API failure (network error,
                    # server error, unexpected response) and [] on a genuine
                    # empty-but-valid account. Distinguish the two so the user
                    # sees the correct error message.
                    if boxes is None:
                        errors["base"] = "cannot_connect"
                    elif boxes:
                        self._boxes = boxes
                        self._user_input = user_input

                        # If only one box, skip selection step
                        if len(self._boxes) <= 1:
                            return await self._async_create_entry(box_filter=[])

                        return await self.async_step_select_boxes()
                    else:
                        # Genuine empty list — valid credentials but zero boxes
                        errors["base"] = "no_boxes"

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
                vol.Optional(CONF_ENABLE_TARGET_LISTS, default=False): bool,
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

        # Use a SHA-256 hash prefix instead of raw token characters so the
        # persistent unique_id in .storage/core.config_entries does not leak
        # any part of the API token to filesystem readers.
        token_hash = hashlib.sha256(token.encode()).hexdigest()[:12]
        unique_id = f"{subdomain}_{token_hash}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        # Backwards compatibility: entries created before v2.5 used the last
        # 8 characters of the raw token as the unique_id suffix.  Check for
        # an existing entry with the old format to prevent duplicate config
        # entries when the user re-adds the same account after upgrading.
        old_unique_id = f"{subdomain}_{token[-8:]}"
        for existing in self.hass.config_entries.async_entries(DOMAIN):
            if existing.unique_id == old_unique_id:
                return self.async_abort(reason="already_configured")

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
                CONF_ENABLE_TARGET_LISTS,
                default=_current(CONF_ENABLE_TARGET_LISTS, False),
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
