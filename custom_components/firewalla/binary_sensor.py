"""Binary sensor platform for Firewalla integration."""
import logging
from datetime import datetime
from typing import Any, Dict

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, 
    ATTR_ALARM_ID, 
    ATTR_DEVICE_ID, 
    ATTR_NETWORK_ID,
    ATTR_RULE_ID,
    CONF_ENABLE_ALARMS,
    CONF_ENABLE_RULES
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Set up Firewalla binary sensors based on runtime data."""
    # Modern Runtime Data access
    coordinator = entry.runtime_data.coordinator
    
    # Retrieve flags from options or data fallback
    enable_alarms = entry.options.get(CONF_ENABLE_ALARMS, entry.data.get(CONF_ENABLE_ALARMS, False))
    enable_rules = entry.options.get(CONF_ENABLE_RULES, entry.data.get(CONF_ENABLE_RULES, False))

    if not coordinator or not coordinator.data:
        return
    
    entities = []
    
    # 1. Device Connectivity Sensors (Always Enabled)
    if "devices" in coordinator.data:
        for device in coordinator.data["devices"]:
            if isinstance(device, dict) and "id" in device:
                entities.append(FirewallaOnlineSensor(coordinator, device))
    
    # 2. Box Status Sensors (Always Enabled)
    if "boxes" in coordinator.data:
        for box in coordinator.data["boxes"]:
            if isinstance(box, dict) and "id" in box:
                entities.append(FirewallaBoxOnlineSensor(coordinator, box))
    
    # 3. Rule Status Sensors (Conditional)
    if enable_rules and "rules" in coordinator.data:
        for rule in coordinator.data["rules"]:
            if isinstance(rule, dict) and "id" in rule:
                entities.append(FirewallaRuleStatusSensor(coordinator, rule))

    # 4. Individual Alarm Sensors (Conditional)
    if enable_alarms and "alarms" in coordinator.data:
        for alarm in coordinator.data["alarms"]:
            if isinstance(alarm, dict) and "id" in alarm:
                entities.append(FirewallaAlarmSensor(coordinator, alarm))
    
    async_add_entities(entities)


class FirewallaBaseBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Common base for all Firewalla binary sensors."""
    
    @property
    def entity_registry_enabled_default(self) -> bool:
        """Force entities to be enabled by default."""
        return True


class FirewallaOnlineSensor(FirewallaBaseBinarySensor):
    """Binary sensor for Firewalla device online status."""

    def __init__(self, coordinator, device):
        super().__init__(coordinator)
        self.device_id = device["id"]
        self._attr_name = f"{device.get('name', 'Unknown')} Online"
        self._attr_unique_id = f"{DOMAIN}_online_{self.device_id}"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        
        # Group with the specific device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.device_id)},
            name=device.get("name", f"Firewalla Device {self.device_id}"),
            manufacturer="Firewalla",
        )
        self._update_attributes(device)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update state from coordinator data."""
        device = next((d for d in self.coordinator.data.get("devices", []) 
                      if d.get("id") == self.device_id), None)
        if device:
            self._update_attributes(device)
            self.async_write_ha_state()

    def _update_attributes(self, device):
        self._attr_is_on = device.get("online", False)
        self._attr_extra_state_attributes = {
            "ip_address": device.get("ip"),
            "mac_address": device.get("mac"),
            "network": device.get("network", {}).get("name"),
        }


class FirewallaBoxOnlineSensor(FirewallaBaseBinarySensor):
    """Binary sensor for Firewalla box online status."""

    def __init__(self, coordinator, box):
        super().__init__(coordinator)
        self.box_id = box["id"]
        self._attr_name = f"Firewalla Box {box.get('name', 'Unknown')} Online"
        self._attr_unique_id = f"{DOMAIN}_box_online_{self.box_id}"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{self.box_id}")},
            name=f"Firewalla Box {box.get('name', self.box_id)}",
            manufacturer="Firewalla",
            model=box.get("model", "Firewalla Box"),
        )
        self._update_attributes(box)

    @callback
    def _handle_coordinator_update(self) -> None:
        box = next((b for b in self.coordinator.data.get("boxes", []) 
                   if b.get("id") == self.box_id), None)
        if box:
            self._update_attributes(box)
            self.async_write_ha_state()

    def _update_attributes(self, box):
        self._attr_is_on = box.get("online", False)
        self._attr_extra_state_attributes = {
            "version": box.get("version"),
            "last_seen": box.get("lastActiveTimestamp"),
        }


class FirewallaRuleStatusSensor(FirewallaBaseBinarySensor):
    """Binary sensor for Firewalla rule status."""

    def __init__(self, coordinator, rule):
        super().__init__(coordinator)
        self.rule_id = rule["id"]
        self._attr_name = f"Rule: {rule.get('action', 'Unknown')} {rule.get('direction', '')}"
        self._attr_unique_id = f"{DOMAIN}_rule_{self.rule_id}"
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        
        # Link to the Box device
        box_id = rule.get("boxId") or coordinator.data.get("boxes", [{}])[0].get("id")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{box_id}")},
        )
        self._update_attributes(rule)

    @callback
    def _handle_coordinator_update(self) -> None:
        rule = next((r for r in self.coordinator.data.get("rules", []) 
                    if r.get("id") == self.rule_id), None)
        if rule:
            self._update_attributes(rule)
            self.async_write_ha_state()

    def _update_attributes(self, rule):
        self._attr_is_on = rule.get("status") == "active"
        self._attr_extra_state_attributes = {
            ATTR_RULE_ID: self.rule_id,
            "notes": rule.get("notes"),
        }


class FirewallaAlarmSensor(FirewallaBaseBinarySensor):
    """Binary sensor for Firewalla individual alarms."""

    def __init__(self, coordinator, alarm):
        super().__init__(coordinator)
        self.alarm_id = alarm["id"]
        self._attr_name = f"Firewalla Alarm: {alarm.get('message', 'Alert')[:30]}"
        self._attr_unique_id = f"{DOMAIN}_alarm_{self.alarm_id}"
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        
        box_id = alarm.get("boxId") or coordinator.data.get("boxes", [{}])[0].get("id")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{box_id}")},
        )
        self._update_attributes(alarm)

    @callback
    def _handle_coordinator_update(self) -> None:
        alarm = next((a for a in self.coordinator.data.get("alarms", []) 
                     if a.get("id") == self.alarm_id), None)
        if alarm:
            self._update_attributes(alarm)
            self.async_write_ha_state()

    def _update_attributes(self, alarm):
        # Active if status is not 2 (cleared)
        self._attr_is_on = alarm.get("status", 1) != 2
        self._attr_extra_state_attributes = {
            ATTR_ALARM_ID: self.alarm_id,
            "message": alarm.get("message"),
        }
