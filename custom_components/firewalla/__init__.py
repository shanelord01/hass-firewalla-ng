"""The Firewalla integration."""
import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.helpers.config_validation as cv
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
)
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    DOMAIN,
    CONF_API_TOKEN,
    CONF_SUBDOMAIN,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SUBDOMAIN,
    COORDINATOR,
    API_CLIENT,
    PLATFORMS,
)
from .api import FirewallaApiClient

_LOGGER = logging.getLogger(__name__)

# Core setup remains the same
async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Firewalla component."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Firewalla from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    session = async_get_clientsession(hass)
    subdomain = entry.data.get(CONF_SUBDOMAIN, DEFAULT_SUBDOMAIN)
    
    client = FirewallaApiClient(
        session=session,
        api_token=entry.data.get(CONF_API_TOKEN),
        subdomain=subdomain,
    )
    
    if not await client.authenticate():
        raise ConfigEntryNotReady("Failed to authenticate with Firewalla API")
    
    # 1. Initialize the entry-specific storage with an empty cache
    hass.data[DOMAIN][entry.entry_id] = {
        API_CLIENT: client,
        "last_data": None  # This is our new persistent cache location
    }

    async def async_update_data():
        """Fetch data from API based on user preferences."""
        from .const import CONF_ENABLE_FLOWS, CONF_ENABLE_RULES, CONF_ENABLE_ALARMS
        
        opts = entry.options
        data_src = entry.data
        
        enable_flows = opts.get(CONF_ENABLE_FLOWS, data_src.get(CONF_ENABLE_FLOWS, False))
        enable_rules = opts.get(CONF_ENABLE_RULES, data_src.get(CONF_ENABLE_RULES, False))
        enable_alarms = opts.get(CONF_ENABLE_ALARMS, data_src.get(CONF_ENABLE_ALARMS, False))

        try:
            # Fetch fresh data
            devices = await client.get_devices()
            boxes = await client.get_boxes()
            rules = await client.get_rules() if enable_rules else []
            alarms = await client.get_alarms() if enable_alarms else []
            flows = await client.get_flows() if enable_flows else []

            # 2. Retrieve the cache from hass.data
            last = hass.data[DOMAIN][entry.entry_id].get("last_data")

            # 3. Merge Logic: If an endpoint fails (returns empty), use the cache
            if last:
                if not boxes: boxes = last.get("boxes", [])
                if not devices: devices = last.get("devices", [])
                if not rules: rules = last.get("rules", [])
                if not alarms: alarms = last.get("alarms", [])
                if not flows: flows = last.get("flows", [])

            data = {
                "boxes": boxes,
                "devices": devices,
                "rules": rules,
                "alarms": alarms,
                "flows": flows
            }

            # 4. Save the merged result back to the cache
            hass.data[DOMAIN][entry.entry_id]["last_data"] = data
            return data

        except Exception as err:
            _LOGGER.error("Error communicating with API: %s", err)
            # 5. Fail-safe: Return the entire cache if the whole request fails
            last = hass.data[DOMAIN][entry.entry_id].get("last_data")
            if last:
                _LOGGER.info("Using cached data due to API error")
                return last
            raise UpdateFailed(f"Error communicating with API: {err}")

    # Standard Coordinator Setup
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL, 
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )
    
    await coordinator.async_config_entry_first_refresh()
    
    # Add the coordinator to our storage
    hass.data[DOMAIN][entry.entry_id][COORDINATOR] = coordinator
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    
    return True

# Unload and Update Options methods remain the same...
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok

async def async_update_options(hass: HomeAssistant, entry: ConfigEntry):
    """Update options."""
    # Update the scan interval in the coordinator
    coordinator = hass.data[DOMAIN][entry.entry_id].get(COORDINATOR)
    if coordinator:
        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        coordinator.update_interval = timedelta(seconds=scan_interval)
        
    # Reload the config entry to apply changes
    await hass.config_entries.async_reload(entry.entry_id)

