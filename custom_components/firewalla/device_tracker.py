"""Device tracker platform for Firewalla."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SourceType, ScannerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_TRACK_DEVICES, DOMAIN
from .coordinator import FirewallaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Firewalla device trackers."""
    # Honour the CONF_TRACK_DEVICES toggle
    if not entry.options.get(
        CONF_TRACK_DEVICES, entry.data.get(CONF_TRACK_DEVICES, True)
    ):
        return

    coordinator: FirewallaCoordinator = entry.runtime_data.coordinator

    if not coordinator.data or "devices" not in coordinator.data:
        return

    entities = [
        FirewallaDeviceTracker(coordinator, device)
        for device in coordinator.data["devices"]
        if isinstance(device, dict) and "id" in device
    ]

    async_add_entities(entities)


class FirewallaDeviceTracker(CoordinatorEntity[FirewallaCoordinator], ScannerEntity):
    """Represent a network device tracked by Firewalla."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FirewallaCoordinator, device: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device["id"]

        # Cache MAC at instantiation so automations referencing this tracker
        # by MAC continue to work even when the device is offline.
        raw_mac = device.get("mac", "")
        self._mac = raw_mac[4:] if raw_mac.startswith("mac:") else raw_mac

        self._attr_unique_id = f"{DOMAIN}_tracker_{self._device_id}"
        self._attr_name = device.get("name", f"Device {self._device_id}")

        # Attach to the Firewalla Box device card
        boxes = coordinator.data.get("boxes", []) if coordinator.data else []
        box_id = boxes[0].get("id", "firewalla_hub") if boxes else "firewalla_hub"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{box_id}")},
            name=f"Firewalla {boxes[0].get('name', box_id)}" if boxes else "Firewalla",
            manufacturer="Firewalla",
        )

    @property
    def entity_registry_enabled_default(self) -> bool:
        return True

    @property
    def source_type(self) -> SourceType:
        return SourceType.ROUTER

    @property
    def is_connected(self) -> bool:
        return self._current_device().get("online", False)

    @property
    def ip_address(self) -> str | None:
        return self._current_device().get("ip")

    @property
    def mac_address(self) -> str | None:
        """Return the MAC address cached at entity creation.

        Using a cached value means automations that reference this tracker
        by MAC continue to work even when the device is offline and the
        coordinator returns no data for it.
        """
        return self._mac or None

    @property
    def hostname(self) -> str | None:
        return self._current_device().get("name")

    def _current_device(self) -> dict[str, Any]:
        """Look up the latest data for this device from the coordinator."""
        if not self.coordinator.data:
            return {}
        return next(
            (
                d
                for d in self.coordinator.data.get("devices", [])
                if d.get("id") == self._device_id
            ),
            {},
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
