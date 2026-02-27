"""The Firewalla integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import FirewallaApiClient
from .const import (
    CONF_API_TOKEN,
    CONF_ENABLE_ALARMS,
    CONF_ENABLE_FLOWS,
    CONF_ENABLE_RULES,
    CONF_SCAN_INTERVAL,
    CONF_SUBDOMAIN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SUBDOMAIN,
    DOMAIN,
    PLATFORMS,
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

    # Remove entity registry entries for any features that have been disabled.
    # This prevents previously-created entities from lingering as "Unavailable"
    # when the user toggles off Alarms, Rules, or Flows in the options flow.
    await _async_cleanup_disabled_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: FirewallaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: FirewallaConfigEntry
) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_cleanup_disabled_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Remove entity registry entries for features that have been disabled.

    When a user disables Alarms, Rules, or Flows via the options flow, the
    platform setup functions simply skip creating those entities — but any
    entries already registered from a previous run remain in the entity
    registry and show as Unavailable. This function removes them cleanly.
    """

    def _opt(key: str) -> bool:
        return entry.options.get(key, entry.data.get(key, False))

    # Map each optional feature flag to the unique_id prefixes it generates.
    # These must match the unique_id patterns set in binary_sensor.py and sensor.py.
    feature_prefixes: dict[str, list[str]] = {
        CONF_ENABLE_ALARMS: [
            f"{DOMAIN}_alarm_",        # FirewallaAlarmSensor (binary_sensor)
            f"{DOMAIN}_alarm_count_",  # FirewallaAlarmCountSensor (sensor)
        ],
        CONF_ENABLE_RULES: [
            f"{DOMAIN}_rule_",         # FirewallaRuleActiveSensor (binary_sensor)
        ],
        CONF_ENABLE_FLOWS: [
            f"{DOMAIN}_flow_",         # FirewallaFlowSensor (sensor)
        ],
    }

    ent_registry = er.async_get(hass)
    all_entries = er.async_entries_for_config_entry(ent_registry, entry.entry_id)

    for feature_key, prefixes in feature_prefixes.items():
        if _opt(feature_key):
            # Feature is enabled — leave its entities alone
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
    """Allow manual deletion of any device that is not currently online.

    Devices actively reporting as online via the API cannot be removed.
    Offline or stale devices can be cleaned up manually by the user.
    """
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
