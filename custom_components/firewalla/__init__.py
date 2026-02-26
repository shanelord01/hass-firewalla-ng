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

from .api import FirewallaApiClient
from .const import (
    CONF_API_TOKEN,
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
