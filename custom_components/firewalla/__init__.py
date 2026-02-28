"""The Firewalla integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import FirewallaApiClient
from .const import (
    CONF_API_TOKEN,
    CONF_ENABLE_ALARMS,
    CONF_ENABLE_FLOWS,
    CONF_ENABLE_RULES,
    CONF_ENABLE_TRAFFIC,
    CONF_SCAN_INTERVAL,
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

_LOGGER = logging.getLogger(__name__)


@dataclass
class FirewallaData:
    """Runtime data stored on the config entry."""

    client: FirewallaApiClient
    coordinator: FirewallaCoordinator


type FirewallaConfigEntry = ConfigEntry[FirewallaData]


async def async_setup_entry(hass: HomeAssistant, entry: FirewallaConfigEntry) -> bool:
    """Set up Firewalla from a config entry."""
    session = async_get_clientsession(hass)

    client = FirewallaApiClient(
        session=session,
        api_token=entry.data[CONF_API_TOKEN],
        subdomain=entry.data.get(CONF_SUBDOMAIN, DEFAULT_SUBDOMAIN),
    )

    if not await client.authenticate():
        raise ConfigEntryNotReady("Authentication with Firewalla API failed")

    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )

    coordinator = FirewallaCoordinator(
        hass=hass,
        client=client,
        entry=entry,
        update_interval=timedelta(seconds=scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = FirewallaData(client=client, coordinator=coordinator)

    await _async_cleanup_disabled_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if not hass.services.has_service(DOMAIN, SERVICE_DELETE_ALARM):
        _async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: FirewallaConfigEntry) -> bool:
    """Unload a config entry."""
    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if result and not remaining:
        for service in (SERVICE_DELETE_ALARM, SERVICE_RENAME_DEVICE, SERVICE_SEARCH_ALARMS, SERVICE_SEARCH_FLOWS):
            hass.services.async_remove(DOMAIN, service)

    return result


async def _async_update_listener(
    hass: HomeAssistant, entry: FirewallaConfigEntry
) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register Firewalla action services.

    Rules (pause/resume) are handled natively by the switch platform via
    switch.turn_on / switch.turn_off — no custom services required.

    delete_alarm and rename_device use HA target selectors so users pick
    from dropdowns rather than manually entering internal Firewalla IDs.
    """

    def _get_entries() -> list[FirewallaConfigEntry]:
        return [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if hasattr(entry, "runtime_data") and entry.runtime_data is not None
        ]

    async def _handle_delete_alarm(call: ServiceCall) -> None:
        """Delete an alarm identified by its HA entity_id (from target selector)."""
        ent_registry = er.async_get(hass)
        entity_ids: list[str] = call.data.get("entity_id", [])

        if not entity_ids:
            raise ServiceValidationError(
                "No alarm entity selected. Pick an alarm binary sensor from the target."
            )

        for entity_id in entity_ids:
            entity_entry = ent_registry.async_get(entity_id)
            if not entity_entry:
                _LOGGER.warning("Entity %s not found in registry", entity_id)
                continue

            # unique_id format: firewalla_alarm_{alarm_id}
            prefix = f"{DOMAIN}_alarm_"
            if not entity_entry.unique_id.startswith(prefix):
                raise ServiceValidationError(
                    f"Entity '{entity_id}' is not a Firewalla alarm sensor."
                )
            alarm_id = entity_entry.unique_id[len(prefix):]

            matched = False
            for entry in _get_entries():
                data: FirewallaData = entry.runtime_data
                if not data.coordinator.data:
                    continue
                alarms = data.coordinator.data.get("alarms", [])
                alarm = next(
                    (a for a in alarms if isinstance(a, dict) and a.get("id") == alarm_id),
                    None,
                )
                if alarm is None:
                    continue

                gid = str(alarm.get("gid") or "")
                aid = str(alarm.get("aid") or alarm_id)
                if not gid:
                    raise ServiceValidationError(
                        f"Cannot determine box GID for alarm '{alarm_id}'."
                    )
                if await data.client.async_delete_alarm(gid, aid):
                    await data.coordinator.async_request_refresh()
                    matched = True
                    break
                raise ServiceValidationError(
                    f"API rejected deletion of alarm '{alarm_id}'."
                )

            if not matched:
                raise ServiceValidationError(
                    f"Alarm '{alarm_id}' not found in coordinator data. "
                    "Ensure 'Enable Alarm Sensors' is on in the integration options."
                )

    async def _handle_rename_device(call: ServiceCall) -> None:
        """Rename a device identified by its HA device_id (from target selector)."""
        dev_registry = dr.async_get(hass)
        name: str = call.data["name"]
        ha_device_ids: list[str] = call.data.get("device_id", [])

        if not ha_device_ids:
            raise ServiceValidationError(
                "No device selected. Pick a Firewalla client device from the target."
            )

        for ha_device_id in ha_device_ids:
            ha_device = dev_registry.async_get(ha_device_id)
            if not ha_device:
                _LOGGER.warning("HA device %s not found in registry", ha_device_id)
                continue

            # Box devices use identifier "box_{id}" — exclude those.
            # Client devices use the raw Firewalla device ID (MAC or similar).
            firewalla_device_id = next(
                (
                    identifier[1]
                    for identifier in ha_device.identifiers
                    if identifier[0] == DOMAIN and not identifier[1].startswith("box_")
                ),
                None,
            )

            if not firewalla_device_id:
                raise ServiceValidationError(
                    "Selected device is not a Firewalla client device "
                    "(it may be a Firewalla box rather than a network device)."
                )

            matched = False
            for entry in _get_entries():
                data: FirewallaData = entry.runtime_data
                if not data.coordinator.data:
                    continue
                devices = data.coordinator.data.get("devices", [])
                device = next(
                    (
                        d for d in devices
                        if isinstance(d, dict) and d.get("id") == firewalla_device_id
                    ),
                    None,
                )
                if device is None:
                    continue

                gid = str(device.get("gid") or device.get("boxId") or "")
                if not gid:
                    raise ServiceValidationError(
                        f"Cannot determine box GID for device '{firewalla_device_id}'."
                    )
                if await data.client.async_rename_device(gid, firewalla_device_id, name):
                    await data.coordinator.async_request_refresh()
                    matched = True
                    break
                raise ServiceValidationError(
                    f"API rejected rename of device '{firewalla_device_id}'."
                )

            if not matched:
                raise ServiceValidationError(
                    f"Device '{firewalla_device_id}' not found in coordinator data."
                )

    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_ALARM,
        _handle_delete_alarm,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): vol.All(cv.ensure_list, [cv.entity_id]),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RENAME_DEVICE,
        _handle_rename_device,
        schema=vol.Schema(
            {
                vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
                vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=32)),
            }
        ),
    )

    # ------------------------------------------------------------------
    # Search services — return data to the calling automation
    # ------------------------------------------------------------------

    async def _handle_search_alarms(call: ServiceCall) -> dict[str, Any]:
        """Search alarms using Firewalla query syntax.

        Returns a dict with a 'results' key containing the matched alarms so the
        caller can use response_variable in an automation action.

        Example automation step:
          action: firewalla.search_alarms
          data:
            query: "device.name:Kids_iPad transfer.total:>50MB"
            limit: 20
          response_variable: alarm_results

          Then: alarm_results.results contains the list of matching alarms.
        """
        query: str = call.data["query"]
        limit: int = call.data.get("limit", 50)

        # Use any loaded config entry to reach the client
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError("No Firewalla integration is configured.")

        client: FirewallaApiClient = entries[0].runtime_data.client
        results = await client.search_alarms(query=query, limit=limit)
        return {"results": results, "count": len(results)}

    async def _handle_search_flows(call: ServiceCall) -> dict[str, Any]:
        """Search flows using Firewalla query syntax.

        Returns a dict with a 'results' key containing the matched flows so the
        caller can use response_variable in an automation action.

        Example automation step:
          action: firewalla.search_flows
          data:
            query: "device.name:Kids_iPad category:game"
            limit: 20
          response_variable: flow_results

          Then: flow_results.results contains the list of matching flows.
        """
        query: str = call.data["query"]
        limit: int = call.data.get("limit", 50)

        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError("No Firewalla integration is configured.")

        client: FirewallaApiClient = entries[0].runtime_data.client
        results = await client.search_flows(query=query, limit=limit)
        return {"results": results, "count": len(results)}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_ALARMS,
        _handle_search_alarms,
        schema=vol.Schema(
            {
                vol.Required("query"): cv.string,
                vol.Optional("limit", default=50): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=200)
                ),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_FLOWS,
        _handle_search_flows,
        schema=vol.Schema(
            {
                vol.Required("query"): cv.string,
                vol.Optional("limit", default=50): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=200)
                ),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )


async def _async_cleanup_disabled_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Remove entity registry entries for features that have been disabled."""

    def _opt(key: str, default: bool = False) -> bool:
        return entry.options.get(key, entry.data.get(key, default))

    feature_prefixes: dict[str, list[str]] = {
        CONF_ENABLE_ALARMS: [
            f"{DOMAIN}_alarm_",
            f"{DOMAIN}_alarm_count_",
        ],
        CONF_ENABLE_RULES: [
            f"{DOMAIN}_rule_",      # covers rule_ (binary_sensor) and rule_switch_ (switch)
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
    }

    feature_defaults: dict[str, bool] = {
        CONF_ENABLE_ALARMS: False,
        CONF_ENABLE_RULES: False,
        CONF_ENABLE_FLOWS: False,
        CONF_ENABLE_TRAFFIC: False,
        CONF_TRACK_DEVICES: True,
    }

    ent_registry = er.async_get(hass)
    all_entries = er.async_entries_for_config_entry(ent_registry, entry.entry_id)

    for feature_key, prefixes in feature_prefixes.items():
        if _opt(feature_key, feature_defaults[feature_key]):
            continue
        for entity_entry in all_entries:
            if any(entity_entry.unique_id.startswith(p) for p in prefixes):
                _LOGGER.debug(
                    "Removing orphaned entity %s (feature %s is disabled)",
                    entity_entry.entity_id,
                    feature_key,
                )
                ent_registry.async_remove(entity_entry.entity_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: FirewallaConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow manual deletion of any device that is not currently online."""
    coordinator = config_entry.runtime_data.coordinator
    if coordinator.data is None:
        return True

    online_ids: set[str] = set()
    for device in coordinator.data.get("devices", []):
        if isinstance(device, dict) and "id" in device and device.get("online", False):
            online_ids.add(device["id"])
    for box in coordinator.data.get("boxes", []):
        if isinstance(box, dict) and "id" in box and box.get("online", False):
            online_ids.add(f"box_{box['id']}")

    return not any(
        identifier
        for identifier in device_entry.identifiers
        if identifier[0] == DOMAIN and identifier[1] in online_ids
    )
