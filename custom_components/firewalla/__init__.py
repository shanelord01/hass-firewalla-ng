"""The Firewalla integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FirewallaApiClient
from .const import (
    ATTR_ALARM_ID,
    CONF_API_TOKEN,
    CONF_DEBUG_LOGGING,
    CONF_ENABLE_ALARMS,
    CONF_ENABLE_FLOWS,
    CONF_ENABLE_RULES,
    CONF_ENABLE_TARGET_LISTS,
    CONF_ENABLE_TRAFFIC,
    CONF_SUBDOMAIN,
    CONF_TRACK_DEVICES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SUBDOMAIN,
    DOMAIN,
    PLATFORMS,
    SERVICE_DELETE_ALARM,
    SERVICE_RENAME_DEVICE,
    SERVICE_SEARCH_ALARMS,
    SERVICE_SEARCH_FLOWS,
)
from .coordinator import FirewallaCoordinator
from .helpers import box_display_name, safe_configuration_url

_LOGGER = logging.getLogger(__name__)


# -----------------------------------------------------------------------
# Runtime data container
# -----------------------------------------------------------------------

class FirewallaData:
    """Container for per-entry runtime objects."""

    def __init__(
        self, client: FirewallaApiClient, coordinator: FirewallaCoordinator
    ) -> None:
        self.client = client
        self.coordinator = coordinator


# -----------------------------------------------------------------------
# Setup / unload
# -----------------------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Firewalla from a config entry."""
    session = async_get_clientsession(hass)

    client = FirewallaApiClient(
        session=session,
        api_token=entry.data.get(CONF_API_TOKEN, ""),
        subdomain=entry.data.get(CONF_SUBDOMAIN, DEFAULT_SUBDOMAIN),
    )

    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )

    coordinator = FirewallaCoordinator(
        hass,
        client=client,
        entry=entry,
        update_interval=timedelta(seconds=scan_interval),
    )

    # Load persisted device-seen timestamps so stale-device tracking
    # survives HA restarts.
    await coordinator.async_load_store()

    # First refresh — also acts as the implicit credential check.
    # FirewallaAuthError is translated to ConfigEntryAuthFailed inside
    # the coordinator so HA surfaces a re-auth notification.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = FirewallaData(client, coordinator)

    # Apply debug logging preference before platforms load so any setup
    # debug messages are captured if the user has the toggle enabled.
    _async_apply_debug_logging(entry)

    # Pre-register box devices in the HA device registry before any platform
    # sets up entities.  Sensor, device_tracker, switch, and binary_sensor
    # platforms all reference box devices via `via_device`.  If the box device
    # doesn't exist in the registry when those entities are created, HA logs a
    # warning and will refuse the reference from 2025.12 onwards.
    # Pre-registering here guarantees the box device exists regardless of
    # platform setup order.
    _async_preregister_boxes(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    # Register domain-level services once (idempotent across entries).
    _async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove domain services when last entry unloads.
    if unloaded:
        remaining = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id
        ]
        if not remaining:
            for svc in (
                SERVICE_DELETE_ALARM,
                SERVICE_RENAME_DEVICE,
                SERVICE_SEARCH_ALARMS,
                SERVICE_SEARCH_FLOWS,
            ):
                hass.services.async_remove(DOMAIN, svc)

    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Handle the 'Delete' button on a device page in the HA UI."""
    fw_device_id: str | None = None
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if identifier.startswith("box_") or identifier.startswith("msp_global_"):
            return False
        fw_device_id = identifier

    if not fw_device_id:
        return False

    coordinator: FirewallaCoordinator = config_entry.runtime_data.coordinator
    client: FirewallaApiClient = config_entry.runtime_data.client

    fw_box_id: str | None = None
    if coordinator.data:
        device_data = next(
            (
                d for d in coordinator.data.get("devices", [])
                if d.get("id") == fw_device_id
            ),
            None,
        )
        if device_data:
            fw_box_id = device_data.get("gid") or device_data.get("boxId")

    if fw_box_id:
        success = await client.async_delete_device(fw_box_id, fw_device_id)
        if success:
            _LOGGER.info(
                "Deleted device %s from Firewalla box %s", fw_device_id, fw_box_id
            )
        else:
            _LOGGER.warning(
                "Firewalla API could not delete device %s — "
                "removing from HA registry only",
                fw_device_id,
            )
    else:
        _LOGGER.warning(
            "Could not resolve box ID for device %s — removing from HA registry only",
            fw_device_id,
        )

    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — apply log level, clean up orphaned entities, then reload."""
    _async_apply_debug_logging(entry)
    _async_cleanup_disabled_entities(hass, entry)
    await hass.config_entries.async_reload(entry.entry_id)


# -----------------------------------------------------------------------
# Debug logging
# -----------------------------------------------------------------------

def _async_apply_debug_logging(entry: ConfigEntry) -> None:
    """Set the integration logger level based on the debug_logging option.

    Applies immediately without requiring an HA restart. Reverts to WARNING
    when the toggle is turned off, overriding any level set via the HA UI
    logger panel for this integration.
    """
    logger = logging.getLogger("custom_components.firewalla")
    if entry.options.get(CONF_DEBUG_LOGGING, entry.data.get(CONF_DEBUG_LOGGING, False)):
        logger.setLevel(logging.DEBUG)
        _LOGGER.debug("Debug logging enabled via integration options")
    else:
        logger.setLevel(logging.WARNING)


# -----------------------------------------------------------------------
# Box pre-registration
# -----------------------------------------------------------------------

def _async_preregister_boxes(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: FirewallaCoordinator,
) -> None:
    """Register box devices in the HA device registry before platforms load.

    Sensor, device_tracker, switch, and binary_sensor platforms all reference
    box devices via `via_device`. Platform setup order is not guaranteed, so
    the box device may not exist when a sensor tries to reference it.
    Pre-registering here ensures the device entry exists before any entity
    is created, eliminating the 'non existing via_device' warning introduced
    in HA 2025.4 and enforced from 2025.12.
    """
    if not coordinator.data:
        return

    dev_reg = dr.async_get(hass)

    for box in coordinator.data.get("boxes", []):
        if not isinstance(box, dict) or "id" not in box:
            continue
        box_id = box["id"]
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"box_{box_id}")},
            name=box_display_name(box),
            manufacturer="Firewalla",
            model=box.get("model", "Firewalla Box"),
            sw_version=box.get("version"),
            configuration_url=safe_configuration_url(box.get("publicIP")),
        )
        _LOGGER.debug("Pre-registered box device: box_%s", box_id)


# -----------------------------------------------------------------------
# Orphaned-entity cleanup
# -----------------------------------------------------------------------

def _async_cleanup_disabled_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Remove entities belonging to features that the user has disabled."""
    opts = entry.options

    def _opt(key: str) -> bool:
        return opts.get(key, entry.data.get(key, False))

    ent_reg = er.async_get(hass)

    cleanup_map: dict[str, list[str]] = {
        CONF_ENABLE_ALARMS: [
            f"{DOMAIN}_alarm_",
            f"{DOMAIN}_alarm_count_",
        ],
        CONF_ENABLE_RULES: [
            f"{DOMAIN}_rule_",
            f"{DOMAIN}_rule_switch_",
        ],
        CONF_ENABLE_FLOWS: [
            f"{DOMAIN}_flow_",
        ],
        CONF_ENABLE_TRAFFIC: [
            f"{DOMAIN}_total_download_",
            f"{DOMAIN}_total_upload_",
        ],
        CONF_TRACK_DEVICES: [
            f"{DOMAIN}_tracker_",
        ],
        CONF_ENABLE_TARGET_LISTS: [
            f"{DOMAIN}_target_list_",
        ],
    }

    for flag, prefixes in cleanup_map.items():
        if _opt(flag):
            continue
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if any(ent.unique_id.startswith(p) for p in prefixes):
                _LOGGER.debug("Removing orphaned entity %s", ent.entity_id)
                ent_reg.async_remove(ent.entity_id)


# -----------------------------------------------------------------------
# Service registration
# -----------------------------------------------------------------------

def _async_register_services(hass: HomeAssistant) -> None:
    """Register Firewalla domain services (idempotent)."""

    if hass.services.has_service(DOMAIN, SERVICE_DELETE_ALARM):
        return

    # -- delete_alarm ------------------------------------------------

    async def _handle_delete_alarm(call: ServiceCall) -> None:
        entity_ids = call.data.get("entity_id", [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]

        for entity_id in entity_ids:
            state = hass.states.get(entity_id)
            if not state:
                _LOGGER.error("Entity %s not found", entity_id)
                continue

            alarm_id = state.attributes.get(ATTR_ALARM_ID)
            gid = state.attributes.get("gid")

            if not alarm_id:
                _LOGGER.error("Entity %s has no alarm_id attribute", entity_id)
                continue

            if not gid:
                ent_reg = er.async_get(hass)
                ent_entry = ent_reg.async_get(entity_id)
                if ent_entry and ent_entry.config_entry_id:
                    cfg = hass.config_entries.async_get_entry(
                        ent_entry.config_entry_id
                    )
                    if cfg and hasattr(cfg, "runtime_data"):
                        coord = cfg.runtime_data.coordinator
                        alarm = next(
                            (
                                a
                                for a in coord.data.get("alarms", [])
                                if str(a.get("id")) == str(alarm_id)
                                or str(a.get("aid")) == str(alarm_id)
                            ),
                            None,
                        )
                        if alarm:
                            gid = alarm.get("gid")

            if not gid:
                _LOGGER.error(
                    "Cannot determine box GID for alarm %s", alarm_id
                )
                continue

            client = _client_for_entity(hass, entity_id)
            if not client:
                _LOGGER.error("No API client found for %s", entity_id)
                continue

            aid = alarm_id
            if await client.async_delete_alarm(gid, aid):
                _LOGGER.info("Deleted alarm %s/%s", gid, aid)
            else:
                _LOGGER.error("API rejected deletion of alarm %s/%s", gid, aid)

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_ALARM,
        _handle_delete_alarm,
        schema=vol.Schema({}),
    )

    # -- rename_device -----------------------------------------------

    async def _handle_rename_device(call: ServiceCall) -> None:
        device_ids = call.data.get("device_id", [])
        if isinstance(device_ids, str):
            device_ids = [device_ids]
        name = call.data.get("name", "")
        if not name or len(name) > 32:
            _LOGGER.error("Name must be 1-32 characters")
            return

        dev_reg = dr.async_get(hass)

        for ha_device_id in device_ids:
            device_entry = dev_reg.async_get(ha_device_id)
            if not device_entry:
                _LOGGER.error("Device %s not found", ha_device_id)
                continue

            fw_device_id: str | None = None
            for domain, identifier in device_entry.identifiers:
                if domain != DOMAIN:
                    continue
                if identifier.startswith("box_") or identifier.startswith("msp_global_"):
                    continue
                fw_device_id = identifier

            if not fw_device_id:
                _LOGGER.error(
                    "Cannot find Firewalla device ID for HA device %s",
                    ha_device_id,
                )
                continue

            client: FirewallaApiClient | None = None
            fw_box_id: str | None = None
            for cfg in hass.config_entries.async_entries(DOMAIN):
                if not hasattr(cfg, "runtime_data"):
                    continue
                coord = cfg.runtime_data.coordinator
                if not coord.data:
                    continue
                device_data = next(
                    (
                        d
                        for d in coord.data.get("devices", [])
                        if d.get("id") == fw_device_id
                    ),
                    None,
                )
                if device_data:
                    fw_box_id = device_data.get("gid") or device_data.get("boxId")
                    client = cfg.runtime_data.client
                    break

            if not fw_box_id or not client:
                _LOGGER.error(
                    "Cannot determine box ID for device %s", fw_device_id
                )
                continue

            if await client.async_rename_device(fw_box_id, fw_device_id, name):
                _LOGGER.info("Renamed device %s to '%s'", fw_device_id, name)
            else:
                _LOGGER.error("Failed to rename device %s", fw_device_id)

    hass.services.async_register(
        DOMAIN,
        SERVICE_RENAME_DEVICE,
        _handle_rename_device,
        schema=vol.Schema(
            {
                vol.Required("name"): vol.All(str, vol.Length(min=1, max=32)),
            }
        ),
    )

    # -- search_alarms -----------------------------------------------

    async def _handle_search_alarms(call: ServiceCall) -> dict[str, Any]:
        query = call.data.get("query", "")
        limit = call.data.get("limit", 50)
        all_results: list[dict[str, Any]] = []

        for cfg in hass.config_entries.async_entries(DOMAIN):
            if not hasattr(cfg, "runtime_data"):
                continue
            client = cfg.runtime_data.client
            try:
                result = await client.search_alarms(query, limit)
                all_results.extend(result.get("results", []))
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("search_alarms error for %s: %s", cfg.title, exc)

        return {"count": len(all_results), "results": all_results}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_ALARMS,
        _handle_search_alarms,
        schema=vol.Schema(
            {
                vol.Required("query"): str,
                vol.Optional("limit", default=50): vol.All(
                    int, vol.Range(min=1, max=200)
                ),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )

    # -- search_flows ------------------------------------------------

    async def _handle_search_flows(call: ServiceCall) -> dict[str, Any]:
        query = call.data.get("query", "")
        limit = call.data.get("limit", 50)
        all_results: list[dict[str, Any]] = []

        for cfg in hass.config_entries.async_entries(DOMAIN):
            if not hasattr(cfg, "runtime_data"):
                continue
            client = cfg.runtime_data.client
            try:
                result = await client.search_flows(query, limit)
                all_results.extend(result.get("results", []))
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("search_flows error for %s: %s", cfg.title, exc)

        return {"count": len(all_results), "results": all_results}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_FLOWS,
        _handle_search_flows,
        schema=vol.Schema(
            {
                vol.Required("query"): str,
                vol.Optional("limit", default=50): vol.All(
                    int, vol.Range(min=1, max=200)
                ),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _client_for_entity(
    hass: HomeAssistant, entity_id: str
) -> FirewallaApiClient | None:
    """Return the API client that owns a given entity."""
    ent_reg = er.async_get(hass)
    ent_entry = ent_reg.async_get(entity_id)
    if not ent_entry or not ent_entry.config_entry_id:
        return None
    cfg = hass.config_entries.async_get_entry(ent_entry.config_entry_id)
    if cfg and hasattr(cfg, "runtime_data"):
        return cfg.runtime_data.client
    return None
