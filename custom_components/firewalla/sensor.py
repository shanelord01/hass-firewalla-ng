"""Sensor platform for Firewalla integration."""
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ATTR_DEVICE_ID,
    ATTR_DEVICE_NAME,
    ATTR_NETWORK_ID,
    CONF_ENABLE_FLOWS,
    CONF_ENABLE_TRAFFIC,
    CONF_ENABLE_ALARMS
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Set up sensors for Firewalla devices using runtime_data."""
    # Modern access via runtime_data
    coordinator = entry.runtime_data.coordinator

    # Retrieve flags
    enable_flows = entry.options.get(CONF_ENABLE_FLOWS, entry.data.get(CONF_ENABLE_FLOWS, False))
    enable_traffic = entry.options.get(CONF_ENABLE_TRAFFIC, entry.data.get(CONF_ENABLE_TRAFFIC, False))
    enable_alarms = entry.options.get(CONF_ENABLE_ALARMS, entry.data.get(CONF_ENABLE_ALARMS, False))
    
    if not coordinator or not coordinator.data:
        return
    
    entities = []
    
    # 1. Process devices
    if "devices" in coordinator.data:
        for device in coordinator.data["devices"]:
            if not isinstance(device, dict) or "id" not in device:
                continue

            # Core Identity Sensors (Always Enabled)
            entities.append(FirewallaMacAddressSensor(coordinator, device))
            entities.append(FirewallaIpAddressSensor(coordinator, device))
            entities.append(FirewallaNetworkNameSensor(coordinator, device))
            
            # Bandwidth Sensors (Conditional)
            if enable_traffic:
                if "totalDownload" in device:
                    entities.append(FirewallaTotalDownloadSensor(coordinator, device))
                if "totalUpload" in device:
                    entities.append(FirewallaTotalUploadSensor(coordinator, device))

    # 2. Process Flows (Conditional - Highly resource intensive)
    if enable_flows and "flows" in coordinator.data:
        for flow in coordinator.data["flows"]:
            # Find associated device for grouping
            device_id = flow.get("device", {}).get("id") or flow.get("source", {}).get("id")
            device = next((d for d in coordinator.data.get("devices", []) if d["id"] == device_id), None)
            entities.append(FirewallaFlowSensor(coordinator, flow, device))

    # 3. Process Alarms (Summary Sensor)
    if enable_alarms:
        entities.append(FirewallaRecentAlarmsSensor(coordinator))
    
    async_add_entities(entities)

class FirewallaBaseSensor(CoordinatorEntity, SensorEntity):
    """Base sensor to ensure entities are enabled by default."""
    
    @property
    def entity_registry_enabled_default(self) -> bool:
        """Force sensors to be enabled on discovery."""
        return True

    def __init__(self, coordinator, device, suffix: str):
        super().__init__(coordinator)
        self.device_id = device["id"]
        self._attr_name = f"{device.get('name', 'Unknown')} {suffix}"
        self._attr_unique_id = f"{DOMAIN}_{suffix.lower().replace(' ', '_')}_{self.device_id}"
        
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.device_id)},
            name=device.get("name", f"Firewalla Device {self.device_id}"),
            manufacturer="Firewalla",
        )

class FirewallaMacAddressSensor(FirewallaBaseSensor):
    """Sensor for MAC Address."""
    def __init__(self, coordinator, device):
        super().__init__(coordinator, device, "MAC Address")

    @property
    def native_value(self):
        device = next((d for d in self.coordinator.data.get("devices", []) if d["id"] == self.device_id), {})
        mac = device.get("mac", self.device_id)
        return mac[4:] if mac.startswith("mac:") else mac

class FirewallaIpAddressSensor(FirewallaBaseSensor):
    """Sensor for IP Address."""
    def __init__(self, coordinator, device):
        super().__init__(coordinator, device, "IP Address")

    @property
    def native_value(self):
        device = next((d for d in self.coordinator.data.get("devices", []) if d["id"] == self.device_id), {})
        return device.get("ip", "Unknown")

class FirewallaNetworkNameSensor(FirewallaBaseSensor):
    """Sensor for Network Name."""
    def __init__(self, coordinator, device):
        super().__init__(coordinator, device, "Network Name")

    @property
    def native_value(self):
        device = next((d for d in self.coordinator.data.get("devices", []) if d["id"] == self.device_id), {})
        return device.get("network", {}).get("name", "Unknown")

class FirewallaTotalDownloadSensor(FirewallaBaseSensor):
    """Sensor for Total Download."""
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.KILOBYTES

    def __init__(self, coordinator, device):
        super().__init__(coordinator, device, "Total Download")

    @property
    def native_value(self):
        device = next((d for d in self.coordinator.data.get("devices", []) if d["id"] == self.device_id), {})
        return round(device.get("totalDownload", 0) / 1024, 2)

class FirewallaTotalUploadSensor(FirewallaBaseSensor):
    """Sensor for Total Upload."""
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.KILOBYTES

    def __init__(self, coordinator, device):
        super().__init__(coordinator, device, "Total Upload")

    @property
    def native_value(self):
        device = next((d for d in self.coordinator.data.get("devices", []) if d["id"] == self.device_id), {})
        return round(device.get("totalUpload", 0) / 1024, 2)

class FirewallaRecentAlarmsSensor(CoordinatorEntity, SensorEntity):
    """Summary sensor for security events."""
    _attr_icon = "mdi:shield-alert"
    
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_name = "Firewalla Recent Alarms"
        self._attr_unique_id = f"{DOMAIN}_recent_alarms_summary_v2"
        
        # Link to first box found
        if coordinator.data.get("boxes"):
            box_id = coordinator.data["boxes"][0].get("id")
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"box_{box_id}")})

    @property
    def native_value(self):
        alarms = self.coordinator.data.get("alarms", [])
        return alarms[0].get("message", "No Alarms") if alarms else "No Alarms"

    @property
    def extra_state_attributes(self):
        alarms = self.coordinator.data.get("alarms", [])
        return {"total_alarms": len(alarms), "recent_events": alarms[:5]}

class FirewallaFlowSensor(CoordinatorEntity, SensorEntity):
    """Individual flow sensor - Warning: Can create many entities."""
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.KILOBYTES

    def __init__(self, coordinator, flow, device=None):
        super().__init__(coordinator)
        self.flow_id = flow["id"]
        dst = flow.get("destination", {}).get("name") or flow.get("destination", {}).get("ip", "unknown")
        self._attr_name = f"Flow to {dst}"
        self._attr_unique_id = f"{DOMAIN}_flow_{self.flow_id}"
        
        if device:
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device["id"])})

    @property
    def native_value(self):
        flow = next((f for f in self.coordinator.data.get("flows", []) if f["id"] == self.flow_id), {})
        return round((flow.get("download", 0) + flow.get("upload", 0)) / 1024, 2)
