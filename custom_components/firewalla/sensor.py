"""Sensor platform for Firewalla."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .binary_sensor import _box_display_name
from .const import (
    CONF_ENABLE_ALARMS,
    CONF_ENABLE_FLOWS,
    CONF_ENABLE_TRAFFIC,
    DOMAIN,
)
from .coordinator import FirewallaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Firewalla sensors."""
    coordinator: FirewallaCoordinator = entry.runtime_data.coordinator

    if not coordinator.data:
        return

    def _opt(key: str) -> bool:
        return entry.options.get(key, entry.data.get(key, False))

    enable_traffic = _opt(CONF_ENABLE_TRAFFIC)
    enable_flows = _opt(CONF_ENABLE_FLOWS)
    enable_alarms = _opt(CONF_ENABLE_ALARMS)

    entities: list[SensorEntity] = []
    devices = coordinator.data.get("devices", [])

    for device in devices:
        if not isinstance(device, dict) or "id" not in device:
            continue

        # Core identity sensors (always on)
        entities.append(FirewallaIpAddressSensor(coordinator, device))
        entities.append(FirewallaMacAddressSensor(coordinator, device))
        entities.append(FirewallaNetworkNameSensor(coordinator, device))

        # Bandwidth sensors (optional)
        if enable_traffic:
            entities.append(FirewallaTotalDownloadSensor(coordinator, device))
            entities.append(FirewallaTotalUploadSensor(coordinator, device))

    # Flow sensors (optional)
    if enable_flows:
        for flow in coordinator.data.get("flows", []):
            if isinstance(flow, dict) and "id" in flow:
                dev_id = (
                    (flow.get("device") or {}).get("id")
                    or (flow.get("source") or {}).get("id")
                )
                device = next(
                    (d for d in devices if d.get("id") == dev_id), None
                )
                entities.append(FirewallaFlowSensor(coordinator, flow, device))

    # Alarm summary sensor (optional)
    if enable_alarms:
        entities.append(FirewallaAlarmCountSensor(coordinator))

    if entities:
        async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _FirewallaSensor(CoordinatorEntity[FirewallaCoordinator], SensorEntity):
    """Shared base for all Firewalla sensors."""

    _attr_has_entity_name = True

    @property
    def entity_registry_enabled_default(self) -> bool:
        return True

    def __init__(
        self,
        coordinator: FirewallaCoordinator,
        device: dict[str, Any],
        translation_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device["id"]
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{translation_key}_{self._device_id}"
        box_gid = device.get("gid") or device.get("boxId")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device.get("name", f"Device {self._device_id}"),
            manufacturer=device.get("macVendor") or device.get("vendor") or "Firewalla",
            connections=(
                {("mac", device["mac"])} if device.get("mac") else set()
            ),
            via_device=(DOMAIN, f"box_{box_gid}") if box_gid else None,
        )

    def _get_device(self) -> dict[str, Any] | None:
        return next(
            (
                d
                for d in self.coordinator.data.get("devices", [])
                if d.get("id") == self._device_id
            ),
            None,
        )


# ---------------------------------------------------------------------------
# Identity sensors
# ---------------------------------------------------------------------------


class FirewallaIpAddressSensor(_FirewallaSensor):
    """Current IP address of a network device."""

    _attr_icon = "mdi:ip-network"
    _attr_translation_key = "ip_address"

    def __init__(self, coordinator: FirewallaCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device, "ip_address")

    @property
    def native_value(self) -> str | None:
        device = self._get_device()
        return device.get("ip") if device else None


class FirewallaMacAddressSensor(_FirewallaSensor):
    """MAC address of a network device."""

    _attr_icon = "mdi:ethernet"
    _attr_translation_key = "mac_address"

    def __init__(self, coordinator: FirewallaCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device, "mac_address")

    @property
    def native_value(self) -> str | None:
        device = self._get_device()
        if not device:
            return None
        mac = device.get("mac", "")
        return mac[4:] if mac.startswith("mac:") else mac or None


class FirewallaNetworkNameSensor(_FirewallaSensor):
    """Name of the network the device belongs to."""

    _attr_icon = "mdi:lan"
    _attr_translation_key = "network_name"

    def __init__(self, coordinator: FirewallaCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device, "network_name")

    @property
    def native_value(self) -> str | None:
        device = self._get_device()
        if not device:
            return None
        return device.get("network", {}).get("name")


# ---------------------------------------------------------------------------
# Bandwidth sensors
# ---------------------------------------------------------------------------


class FirewallaTotalDownloadSensor(_FirewallaSensor):
    """Total download bytes (converted to kB) for a device."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.KILOBYTES
    _attr_translation_key = "total_download"

    def __init__(self, coordinator: FirewallaCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device, "total_download")

    @property
    def native_value(self) -> float | None:
        device = self._get_device()
        if not device:
            return None
        return round(device.get("totalDownload", 0) / 1024, 2)


class FirewallaTotalUploadSensor(_FirewallaSensor):
    """Total upload bytes (converted to kB) for a device."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfInformation.KILOBYTES
    _attr_translation_key = "total_upload"

    def __init__(self, coordinator: FirewallaCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device, "total_upload")

    @property
    def native_value(self) -> float | None:
        device = self._get_device()
        if not device:
            return None
        return round(device.get("totalUpload", 0) / 1024, 2)


# ---------------------------------------------------------------------------
# Alarm count
# ---------------------------------------------------------------------------


class FirewallaAlarmCountSensor(CoordinatorEntity[FirewallaCoordinator], SensorEntity):
    """Summary sensor: number of active alarms across the MSP account."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:shield-alert"
    _attr_translation_key = "alarm_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FirewallaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_alarm_count_{coordinator.config_entry.entry_id}"

        boxes = coordinator.data.get("boxes", []) if coordinator.data else []
        box_id = boxes[0].get("id", "global") if boxes else "global"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{box_id}")},
            name=_box_display_name(boxes[0]) if boxes else "Firewalla",
            manufacturer="Firewalla",
        )

    @property
    def entity_registry_enabled_default(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        if not self.coordinator.data:
            return 0
        alarms = self.coordinator.data.get("alarms", [])
        return sum(1 for a in alarms if isinstance(a, dict) and a.get("status", 1) != 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        alarms = self.coordinator.data.get("alarms", [])
        active = [
            {
                "id": a.get("id"),
                "message": a.get("message"),
                "type": a.get("type"),
                "ts": a.get("ts"),
            }
            for a in alarms[:10]
            if isinstance(a, dict) and a.get("status", 1) != 2
        ]
        return {
            "total_alarms": len(alarms),
            "active_alarms": active,
        }


# ---------------------------------------------------------------------------
# Flow sensor
# ---------------------------------------------------------------------------


class FirewallaFlowSensor(CoordinatorEntity[FirewallaCoordinator], SensorEntity):
    """Sensor for a single traffic flow (download + upload in kB)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.KILOBYTES
    _attr_translation_key = "flow_transfer"

    def __init__(
        self,
        coordinator: FirewallaCoordinator,
        flow: dict[str, Any],
        device: dict[str, Any] | None,
    ) -> None:
        super().__init__(coordinator)
        self._flow_id = flow["id"]
        self._attr_unique_id = f"{DOMAIN}_flow_{self._flow_id}"

        if device:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, device["id"])},
            )
        else:
            boxes = coordinator.data.get("boxes", []) if coordinator.data else []
            box_id = boxes[0].get("id", "global") if boxes else "global"
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"box_{box_id}")},
            )

    @property
    def entity_registry_enabled_default(self) -> bool:
        return True

    @property
    def native_value(self) -> float | None:
        flow = next(
            (
                f
                for f in self.coordinator.data.get("flows", [])
                if f.get("id") == self._flow_id
            ),
            None,
        )
        if not flow:
            return None
        return round(
            (flow.get("download", 0) + flow.get("upload", 0)) / 1024, 2
        )
