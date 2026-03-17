"""Sensor platform for Firewalla."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfInformation
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ENABLE_ALARMS,
    CONF_ENABLE_FLOWS,
    CONF_ENABLE_TARGET_LISTS,
    CONF_ENABLE_TRAFFIC,
    DOMAIN,
)
from .coordinator import FirewallaCoordinator
from .helpers import box_display_name

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
    enable_target_lists = _opt(CONF_ENABLE_TARGET_LISTS)

    # MSP summary sensors and alarm count are singletons — add them once
    # at setup time; they reference coordinator.data directly and never
    # need dynamic entity creation.
    static_entities: list[SensorEntity] = [
        FirewallaMspOnlineBoxesSensor(coordinator),
        FirewallaMspOfflineBoxesSensor(coordinator),
        FirewallaMspTotalAlarmsSensor(coordinator),
        FirewallaMspTotalRulesSensor(coordinator),
    ]
    if enable_alarms:
        static_entities.append(FirewallaAlarmCountSensor(coordinator))

    async_add_entities(static_entities)

    # --- Dynamic entity sets (grow as the coordinator discovers new items) ---

    known_device_ids: set[str] = set()
    known_flow_ids: set[str] = set()
    known_target_list_ids: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        """Discover and register new sensor entities on each coordinator update."""
        if not coordinator.data:
            return

        new_entities: list[SensorEntity] = []

        # Per-device identity and bandwidth sensors
        for device in coordinator.data.get("devices", []):
            if not isinstance(device, dict) or "id" not in device:
                continue
            device_id = str(device["id"])
            if device_id not in known_device_ids:
                known_device_ids.add(device_id)
                new_entities.append(FirewallaIpAddressSensor(coordinator, device))
                new_entities.append(FirewallaMacAddressSensor(coordinator, device))
                new_entities.append(FirewallaNetworkNameSensor(coordinator, device))
                if enable_traffic:
                    new_entities.append(FirewallaTotalDownloadSensor(coordinator, device))
                    new_entities.append(FirewallaTotalUploadSensor(coordinator, device))

        # Flow sensors
        if enable_flows:
            # Pre-build a lookup dict keyed by uppercased device ID so
            # flow→device matching is O(1) per flow instead of O(N).
            device_by_id: dict[str, dict[str, Any]] = {
                d["id"].upper(): d
                for d in coordinator.data.get("devices", [])
                if isinstance(d, dict) and "id" in d
            }
            for flow in coordinator.data.get("flows", []):
                if not isinstance(flow, dict) or "id" not in flow:
                    continue
                flow_id = str(flow["id"])
                if flow_id not in known_flow_ids:
                    known_flow_ids.add(flow_id)
                    flow_device_id = (flow.get("device") or {}).get("id", "").upper()
                    flow_device = device_by_id.get(flow_device_id) if flow_device_id else None
                    new_entities.append(FirewallaFlowSensor(coordinator, flow, flow_device))

        # Target list sensors
        if enable_target_lists:
            for tl in coordinator.data.get("target_lists", []):
                if not isinstance(tl, dict) or "id" not in tl:
                    continue
                tl_id = str(tl["id"])
                if tl_id not in known_target_list_ids:
                    known_target_list_ids.add(tl_id)
                    new_entities.append(FirewallaTargetListSensor(coordinator, tl))

        if new_entities:
            async_add_entities(new_entities)

    # Register entities already present at setup time.
    _async_add_new_entities()

    # Re-run on every subsequent coordinator refresh to pick up new items.
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _FirewallaSensor(CoordinatorEntity[FirewallaCoordinator], SensorEntity):
    """Shared base for all Firewalla per-device sensors."""

    _attr_has_entity_name = True

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
        if not self.coordinator.data:
            return None
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

    def __init__(self, coordinator: FirewallaCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device, "ip_address")

    @property
    def native_value(self) -> str | None:
        device = self._get_device()
        return device.get("ip") if device else None


class FirewallaMacAddressSensor(_FirewallaSensor):
    """MAC address of a network device."""

    def __init__(self, coordinator: FirewallaCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device, "mac_address")

    @property
    def native_value(self) -> str | None:
        device = self._get_device()
        if not device:
            return None
        return device.get("mac")


class FirewallaNetworkNameSensor(_FirewallaSensor):
    """Name of the network the device belongs to."""

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
    """Total download bytes (converted to kB) for a device.

    Uses TOTAL rather than TOTAL_INCREASING because Firewalla's totalDownload
    counter is a rolling/resettable accumulated value, not a monotonically
    increasing lifetime counter. TOTAL supports accumulated values that can
    decrease (e.g. when a rolling window expires) without triggering HA
    recorder warnings. MEASUREMENT is intentionally avoided as it implies
    an instantaneous point-in-time reading rather than an accumulated total.
    """

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfInformation.KILOBYTES

    def __init__(self, coordinator: FirewallaCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device, "total_download")

    @property
    def native_value(self) -> float | None:
        device = self._get_device()
        if not device:
            return None
        return round(device.get("totalDownload", 0) / 1024, 2)


class FirewallaTotalUploadSensor(_FirewallaSensor):
    """Total upload bytes (converted to kB) for a device.

    Uses TOTAL for the same reason as FirewallaTotalDownloadSensor — the
    Firewalla API returns a rolling accumulated value, not a lifetime counter.
    """

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfInformation.KILOBYTES

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
    _attr_translation_key = "alarm_count"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: FirewallaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_alarm_count_{coordinator.config_entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"msp_global_{coordinator.config_entry.entry_id}")},
            name="Firewalla MSP",
            manufacturer="Firewalla",
            model="MSP",
            entry_type=DeviceEntryType.SERVICE,
        )

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
            for a in alarms
            if isinstance(a, dict) and a.get("status", 1) != 2
        ][:10]
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
            flow_box_gid = flow.get("gid") or flow.get("boxId")
            fallback_box_id = (
                flow_box_gid
                if flow_box_gid
                else (boxes[0].get("id", "global") if boxes else "global")
            )
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"box_{fallback_box_id}")},
            )

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
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


# ---------------------------------------------------------------------------
# MSP Simple Stats Sensors
# ---------------------------------------------------------------------------


class FirewallaMspBaseSensor(CoordinatorEntity[FirewallaCoordinator], SensorEntity):
    """Base class for MSP-wide summary sensors."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: FirewallaCoordinator,
        key: str,
        translation_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_msp_{key}_{coordinator.config_entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"msp_global_{coordinator.config_entry.entry_id}")},
            name="Firewalla MSP",
            manufacturer="Firewalla",
            model="MSP",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> int | None:
        stats = self.coordinator.data.get("stats_simple", {}) if self.coordinator.data else {}
        return stats.get(self._key)


class FirewallaMspOnlineBoxesSensor(FirewallaMspBaseSensor):
    """Number of Firewalla boxes currently online across the MSP account."""

    def __init__(self, coordinator: FirewallaCoordinator) -> None:
        super().__init__(coordinator, "onlineBoxes", "msp_online_boxes")


class FirewallaMspOfflineBoxesSensor(FirewallaMspBaseSensor):
    """Number of Firewalla boxes currently offline across the MSP account."""

    def __init__(self, coordinator: FirewallaCoordinator) -> None:
        super().__init__(coordinator, "offlineBoxes", "msp_offline_boxes")


class FirewallaMspTotalAlarmsSensor(FirewallaMspBaseSensor):
    """Total active alarms across the MSP account."""

    def __init__(self, coordinator: FirewallaCoordinator) -> None:
        super().__init__(coordinator, "alarms", "msp_total_alarms")


class FirewallaMspTotalRulesSensor(FirewallaMspBaseSensor):
    """Total firewall rules across the MSP account."""

    def __init__(self, coordinator: FirewallaCoordinator) -> None:
        super().__init__(coordinator, "rules", "msp_total_rules")


# ---------------------------------------------------------------------------
# Target List Sensor
# ---------------------------------------------------------------------------


class FirewallaTargetListSensor(CoordinatorEntity[FirewallaCoordinator], SensorEntity):
    """Sensor representing a single Firewalla target list.

    State = number of entries in the list.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "target_list"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "entries"

    def __init__(
        self,
        coordinator: FirewallaCoordinator,
        tl: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._tl_id: str = tl["id"]
        self._attr_unique_id = (
            f"{DOMAIN}_target_list_{self._tl_id}_{coordinator.config_entry.entry_id}"
        )
        self._attr_name = tl.get("name", self._tl_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"msp_global_{coordinator.config_entry.entry_id}")},
            name="Firewalla MSP",
            manufacturer="Firewalla",
            model="MSP",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _get_tl(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        return next(
            (
                tl
                for tl in self.coordinator.data.get("target_lists", [])
                if tl.get("id") == self._tl_id
            ),
            None,
        )

    @property
    def native_value(self) -> int | None:
        tl = self._get_tl()
        if tl is None:
            return None
        if "count" in tl:
            return tl["count"]
        return len(tl.get("targets", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tl = self._get_tl()
        if not tl:
            return {}

        last_updated_raw = tl.get("lastUpdated")
        last_updated_iso: str | None = None
        if last_updated_raw is not None:
            try:
                last_updated_iso = datetime.fromtimestamp(
                    float(last_updated_raw), tz=timezone.utc
                ).isoformat()
            except (ValueError, TypeError, OSError):
                last_updated_iso = str(last_updated_raw)

        return {
            "owner": tl.get("owner"),
            "category": tl.get("category"),
            "notes": tl.get("notes"),
            "targets": tl.get("targets", []),
            "last_updated": last_updated_iso,
        }
