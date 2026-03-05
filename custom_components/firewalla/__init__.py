"""The Firewalla integration."""
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FirewallaApiClient, FirewallaAuthError
from .const import (
    CONF_API_TOKEN,
    CONF_SUBDOMAIN,
    COORDINATOR,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SUBDOMAIN,
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)


class FirewallaData:
    """Class to hold Firewalla runtime data."""
    def __init__(self, client, coordinator):
        self.client = client
        self.coordinator = coordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Firewalla from a config entry."""
    session = async_get_clientsession(hass)

    client = FirewallaApiClient(
        session=session,
        api_token=entry.data.get(CONF_API_TOKEN),
        subdomain=entry.data.get(CONF_SUBDOMAIN, DEFAULT_SUBDOMAIN),
    )

    # NOTE: Do NOT call client.authenticate() here. It would hit GET /boxes,
    # which is immediately duplicated by the coordinator's first refresh.
    # Auth errors are instead caught inside async_update_data and translated
    # to ConfigEntryAuthFailed so HA can surface them correctly.

    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )

    async def async_update_data():
        """Fetch data from API based on user preferences."""
        from .const import (
            CONF_ENABLE_FLOWS, CONF_ENABLE_RULES,
            CONF_ENABLE_ALARMS, CONF_TRACK_DEVICES
        )

        opts = entry.options

        def is_enabled(key):
            return opts.get(key, entry.data.get(key, False))

        try:
            # Core data - always fetched
            boxes = await client.get_boxes()
            devices = await client.get_devices()

            # Conditional fetches
            results = {"rules": [], "alarms": [], "flows": []}
            calls = [
                ("rules",  is_enabled(CONF_ENABLE_RULES),  client.get_rules),
                ("alarms", is_enabled(CONF_ENABLE_ALARMS), client.get_alarms),
                ("flows",  is_enabled(CONF_ENABLE_FLOWS),  client.get_flows),
            ]

            for key, enabled, func in calls:
                if enabled:
                    try:
                        results[key] = await func()
                    except FirewallaAuthError as err:
                        raise ConfigEntryAuthFailed from err
                    except Exception as e:
                        _LOGGER.warning("Could not fetch %s: %s", key, e)

            last = getattr(async_update_data, "last_data", {}) or {}

            data = {
                "boxes":   boxes   or last.get("boxes", []),
                "devices": devices or last.get("devices", []),
                "rules":   results["rules"]  or last.get("rules", []),
                "alarms":  results["alarms"] or last.get("alarms", []),
                "flows":   results["flows"]  or last.get("flows", []),
            }

            async_update_data.last_data = data
            return data

        except FirewallaAuthError as err:
            # Translate to HA's auth-failed exception so the config entry is
            # flagged for re-auth rather than being retried indefinitely.
            raise ConfigEntryAuthFailed from err
        except Exception as err:
            if getattr(async_update_data, "last_data", None):
                _LOGGER.error("API error, using cached data: %s", err)
                return async_update_data.last_data
            raise UpdateFailed(f"Error communicating with API: {err}")

    async_update_data.last_data = None

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = FirewallaData(client, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update - reload the integration."""
    await hass.config_entries.async_reload(entry.entry_id)
