"""The Firewalla integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import FirewallaApiClient
from .const import (
    ATTR_ALARM_ID,
    ATTR_DEVICE_ID,
    ATTR_RULE_ID,
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
    SERVICE_PAUSE_RULE,
    SERVICE_RENAME_DEVICE,
    SERVICE_RESUME_RULE,
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
    # when the user toggles off optional features in the options flow.
    await _async_cleanup_disabled_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Register services once — they operate across all loaded entries.
    if not hass.services.has_service(DOMAIN, SERVICE_PAUSE_RULE):
        _async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: FirewallaConfigEntry) -> bool:
    """Unload a config entry."""
    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove services when the last entry is unloaded.
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if result and not remaining:
        for service in (
            SERVICE_PAUSE_RULE,
            SERVICE_RESUME_RULE,
            SERVICE_DELETE_ALARM,
            SERVICE_RENAME_DEVICE,
        ):
            hass.services.async_remove(DOMAIN, service)

    return result


async def _async_update_listener(
    hass: HomeAssistant, entry: FirewallaConfigEntry
) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register Firewalla action services (called once for all config entries)."""

    def _get_entries() -> list[FirewallaConfigEntry]:
        return [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if hasattr(entry, "runtime_data") and entry.runtime_data is not None
        ]

    async def _handle_pause_rule(call: ServiceCall) -> None:
        rule_id: str = call.data[ATTR_RULE_ID]
        entries = _get_entries()
        if not entries:
            raise ServiceValidationError("No Firewalla account is configured.")
        for entry in entries:
            data: FirewallaData = entry.runtime_data
            if await data.client.async_pause_rule(rule_id):
                await data.coordinator.async_request_refresh()
                return
        raise ServiceValidationError(
            f"Could not pause rule '{rule_id}'. "
            "Verify the rule ID is correct and belongs to a configured account."
        )

    async def _handle_resume_rule(call: ServiceCall) -> None:
        rule_id: str = call.data[ATTR_RULE_ID]
        entries = _get_entries()
        if not entries:
            raise ServiceValidationError("No Firewalla account is configured.")
        for entry in entries:
            data: FirewallaData = entry.runtime_data
            if await data.client.async_resume_rule(rule_id):
                await data.coordinator.async_request_refresh()
                return
        raise ServiceValidationError(
            f"Could not resume rule '{rule_id}'. "
            "Verify the rule ID is correct and belongs to a configured account."
        )

    async def _handle_delete_alarm(call: ServiceCall) -> None:
        alarm_id: str = call.data[ATTR_ALARM_ID]
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
            gid = str(alarm.get("boxId") or alarm.get("gid") or "")
            aid = str(alarm.get("aid") or alarm_id)
            if not gid:
                raise ServiceValidationError(
                    f"Cannot determine box GID for alarm '{alarm_id}'."
                )
            if await data.client.async_delete_alarm(gid, aid):
                await data.coordinator.async_request_refresh()
                return
            raise ServiceValidationError(f"API rejected deletion of alarm '{alarm_id}'.")
        raise ServiceValidationError(
            f"Alarm '{alarm_id}' not found. "
            "Enable 'Alarm Sensors' in the integration options so alarms are fetched."
        )

    async def _handle_rename_device(call: ServiceCall) -> None:
        device_id: str = call.data[ATTR_DEVICE_ID]
        name: str = call.data["name"]
        for entry in _get_entries():
            data: FirewallaData = entry.runtime_data
            if not data.coordinator.data:
                continue
            devices = data.coordinator.data.get("devices", [])
            device = next(
                (d for d in devices if isinstance(d, dict) and d.get("id") == device_id),
                None,
            )
            if device is None:
                continue
            gid = str(device.get("gid") or device.get("boxId") or "")
            if not gid:
                raise ServiceValidationError(
                    f"Cannot determine box GID for device '{device_id}'."
                )
            if await data.client.async_rename_device(gid, device_id, name):
                await data.coordinator.async_request_refresh()
                return
            raise ServiceValidationError(f"API rejected rename of device '{device_id}'.")
        raise ServiceValidationError(
            f"Device '{device_id}' not found in coordinator data."
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_PAUSE_RULE,
        _handle_pause_rule,
        schema=vol.Schema({vol.Required(ATTR_RULE_ID): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESUME_RULE,
        _handle_resume_rule,
        schema=vol.Schema({vol.Required(ATTR_RULE_ID): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_ALARM,
        _handle_delete_alarm,
        schema=vol.Schema({vol.Required(ATTR_ALARM_ID): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RENAME_DEVICE,
        _handle_rename_device,
        schema=vol.Schema({
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=32)),
        }),
    )


async def _async_cleanup_disabled_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Remove entity registry entries for features that have been disabled.

    When a user disables an optional feature via the options flow, the platform
    setup functions simply skip creating those entities — but any entries already
    registered from a previous run remain in the entity registry and show as
    Unavailable. This function removes them cleanly on every setup/reload.

    Prefixes must exactly match the unique_id patterns assigned in
    binary_sensor.py, sensor.py, and device_tracker.py.
    """

    def _opt(key: str, default: bool = False) -> bool:
        return entry.options.get(key, entry.data.get(key, default))

    # Each feature flag maps to the unique_id prefix(es) of the entities it creates.
    # CONF_TRACK_DEVICES defaults True so we pass that through correctly.
    feature_prefixes: dict[str, list[str]] = {
        CONF_ENABLE_ALARMS: [
            f"{DOMAIN}_alarm_",         # FirewallaAlarmSensor (binary_sensor)
            f"{DOMAIN}_alarm_count_",   # FirewallaAlarmCountSensor (sensor)
        ],
        CONF_ENABLE_RULES: [
            f"{DOMAIN}_rule_",          # FirewallaRuleActiveSensor (binary_sensor) + FirewallaRuleToggleButton (button)
        ],
        CONF_ENABLE_FLOWS: [
            f"{DOMAIN}_flow_",          # FirewallaFlowSensor (sensor)
        ],
        CONF_ENABLE_TRAFFIC: [
            f"{DOMAIN}_total_download_",  # FirewallaTotalDownloadSensor (sensor)
            f"{DOMAIN}_total_upload_",    # FirewallaTotalUploadSensor (sensor)
        ],
        CONF_TRACK_DEVICES: [
            f"{DOMAIN}_tracker_",       # FirewallaDeviceTracker (device_tracker)
        ],
    }

    # Default values must match the defaults used in config_flow and sensor/tracker setup.
    feature_defaults: dict[str, bool] = {
        CONF_ENABLE_ALARMS: False,
        CONF_ENABLE_RULES: False,
        CONF_ENABLE_FLOWS: False,
        CONF_ENABLE_TRAFFIC: False,
        CONF_TRACK_DEVICES: True,   # Device tracker is on by default
    }

    ent_registry = er.async_get(hass)
    all_entries = er.async_entries_for_config_entry(ent_registry, entry.entry_id)

    for feature_key, prefixes in feature_prefixes.items():
        if _opt(feature_key, feature_defaults[feature_key]):
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
