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
        # Explicitly set the name so it's easy to find in the People list
        self._attr_name = device.get("name", f"Firewalla {self.device_id}")
        
        # We need a stable unique_id for the Entity Registry
        self._attr_unique_id = f"{DOMAIN}_tracker_{self.device_id}"
        
        # source_type ROUTER is required for the People integration
        self._attr_source_type = SourceType.ROUTER

        # For the 1.0 release, let's keep it simple to ensure they appear
        # Grouping them under the box to match your previous success
        box_id = "firewalla_hub"
        if coordinator.data.get("boxes"):
            box_id = coordinator.data["boxes"][0].get("id")

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{box_id}")},
            name="Firewalla Box",
            manufacturer="Firewalla",
        )

    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return self._attr_source_type

    @property
    def is_connected(self) -> bool:
        """Return true if connected."""
        device = self._get_device_data()
        return device.get("online", False)

    @property
    def ip_address(self) -> str:
        """Return IP."""
        device = self._get_device_data()
        return device.get("ip")

    @property
    def mac_address(self) -> str:
        """Return MAC."""
        device = self._get_device_data()
        return device.get("mac")

    def _get_device_data(self) -> dict:
        """Helper to find device."""
        devices = self.coordinator.data.get("devices", []) if self.coordinator.data else []
        return next((d for d in devices if d.get("id") == self.device_id), {})

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update state."""
        self.async_write_ha_state()
