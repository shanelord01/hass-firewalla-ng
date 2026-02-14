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
    
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL, 
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    
    # --- Optimized Coordinator Logic ---
    async def async_update_data():
        """Fetch data from API based on user preferences."""
        from .const import CONF_ENABLE_FLOWS, CONF_ENABLE_RULES, CONF_ENABLE_ALARMS
        
        # Access entry directly (no 'self' needed here)
        opts = entry.options
        data_src = entry.data
        
        enable_flows = opts.get(CONF_ENABLE_FLOWS, data_src.get(CONF_ENABLE_FLOWS, False))
        enable_rules = opts.get(CONF_ENABLE_RULES, data_src.get(CONF_ENABLE_RULES, False))
        enable_alarms = opts.get(CONF_ENABLE_ALARMS, data_src.get(CONF_ENABLE_ALARMS, False))

        try:
            # 1. Always fetch core data
            devices = await client.get_devices()
            boxes = await client.get_boxes()
            
            # 2. Conditional Fetching
            rules = []
            if enable_rules:
                try:
                    rules = await client.get_rules()
                except Exception as e:
                    _LOGGER.warning("Failed to get rules: %s", e)

            alarms = []
            if enable_alarms:
                try:
                    alarms = await client.get_alarms()
                except Exception as e:
                    _LOGGER.warning("Failed to get alarms: %s", e)

            flows = []
            if enable_flows:
                try:
                    flows = await client.get_flows()
                except Exception as e:
                    _LOGGER.warning("Failed to get flows: %s", e)

            # 3. Cache Merging Logic
            if hasattr(async_update_data, "last_data") and async_update_data.last_data:
                last = async_update_data.last_data
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
            async_update_data.last_data = data
            return data

        except Exception as err:
            _LOGGER.error("Error communicating with API: %s", err)
            if hasattr(async_update_data, "last_data") and async_update_data.last_data:
                _LOGGER.info("Using cached data due to API error")
                return async_update_data.last_data
            raise UpdateFailed(f"Error communicating with API: {err}")

    # Initialize last_data
    async_update_data.last_data = None
    
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )
    
    await coordinator.async_config_entry_first_refresh()
    
    # Store data
    hass.data[DOMAIN][entry.entry_id] = {
        API_CLIENT: client,
        COORDINATOR: coordinator,
    }
    
    # Set up platforms
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
