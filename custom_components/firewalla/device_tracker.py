"""Device tracker platform for Firewalla."""
import logging
from homeassistant.components.device_tracker import SourceType, ScannerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, COORDINATOR

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up Firewalla device trackers."""
    coordinator = hass.data[DOMAIN][entry.entry_id].get(COORDINATOR)
    if not coordinator or "devices" not in coordinator.data:
        return

    entities = []
    for device in coordinator.data["devices"]:
        if isinstance(device, dict) and "id" in device:
            entities.append(FirewallaDeviceTracker(coordinator, device))
    
    async_add_entities(entities)

class FirewallaDeviceTracker(CoordinatorEntity, ScannerEntity):
    """Firewalla Device Tracker entity."""

    def __init__(self, coordinator, device):
        """Initialize the tracker."""
        super().__init__(coordinator)
        self.device_id = device["id"]
        self._attr_name = device.get("name", f"Firewalla Device {self.device_id}")
        self._attr_unique_id = f"{DOMAIN}_tracker_{self.device_id}"
        
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.device_id)},
            name=self._attr_name,
            manufacturer="Firewalla",
        )

    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.ROUTER

    @property
    def is_connected(self) -> bool:
        """Return true if the device is connected to the network."""
        for device in self.coordinator.data.get("devices", []):
            if device["id"] == self.device_id:
                return device.get("online", False)
        return False

    @property
    def ip_address(self) -> str:
        """Return the primary IP address."""
        for device in self.coordinator.data.get("devices", []):
            if device["id"] == self.device_id:
                return device.get("ip")
        return None

    @property
    def mac_address(self) -> str:
        """Return the MAC address."""
        for device in self.coordinator.data.get("devices", []):
            if device["id"] == self.device_id:
                return device.get("mac")
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update state on coordinator refresh."""
        self.async_write_ha_state()
