"""Binary sensor platform for Firewalla."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_ALARM_ID,
    ATTR_RULE_ID,
    CONF_ENABLE_ALARMS,
    CONF_ENABLE_RULES,
    DOMAIN,
)
from .coordinator import FirewallaCoordinator
from .helpers import box_display_name, first_box_id, safe_configuration_url

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Firewalla binary sensors."""
    coordinator: FirewallaCoordinator = entry.runtime_data.coordinator

    if not coordinator.data:
        return

    def _opt(key: str) -> bool:
        return entry.options.get(key, entry.data.get(key, False))

    enable_rules = _opt(CONF_ENABLE_RULES)
    enable_alarms = _opt(CONF_ENABLE_ALARMS)

    known_box_ids: set[str] = set()
    known_device_ids: set[str] = set()
    known_rule_ids: set[str] = set()
    known_alarm_ids: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        """Discover and register new binary sensor entities on each coordinator update."""
        if not coordinator.data:
            return

        new_entities: list[BinarySensorEntity] = []

        # Box connectivity (always enabled)
        for box in coordinator.data.get("boxes", []):
            if not isinstance(box, dict) or "id" not in box:
                continue
            box_id = str(box["id"])
            if box_id not in known_box_ids:
                known_box_ids.add(box_id)
                new_entities.append(FirewallaBoxOnlineSensor(coordinator, box))

        # Device connectivity (always enabled)
        for device in coordinator.data.get("devices", []):
            if not isinstance(device, dict) or "id" not in device:
                continue
            device_id = str(device["id"])
            if device_id not in known_device_ids:
                known_device_ids.add(device_id)
                new_entities.append(FirewallaDeviceOnlineSensor(coordinator, device))

        # Rules (optional)
        if enable_rules:
            for rule in coordinator.data.get("rules", []):
                if not isinstance(rule, dict) or "id" not in rule:
                    continue
                rule_id = str(rule["id"])
                if rule_id not in known_rule_ids:
                    known_rule_ids.add(rule_id)
                    new_entities.append(FirewallaRuleActiveSensor(coordinator, rule))

        # Individual alarm sensors (optional)
        if enable_alarms:
            for alarm in coordinator.data.get("alarms", []):
                if not isinstance(alarm, dict) or "id" not in alarm:
                    continue
                alarm_id = str(alarm["id"])
                if alarm_id not in known_alarm_ids:
                    known_alarm_ids.add(alarm_id)
                    new_entities.append(FirewallaAlarmSensor(coordinator, alarm))

        if new_entities:
            async_add_entities(new_entities)

    # Register entities already present at setup time.
    _async_add_new_entities()

    # Re-run on every subsequent coordinator refresh to pick up new entries.
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _FirewallaBinarySensor(CoordinatorEntity[FirewallaCoordinator], BinarySensorEntity):
    """Shared base for all Firewalla binary sensors."""

    _attr_has_entity_name = True


# ---------------------------------------------------------------------------
# Box online
# ---------------------------------------------------------------------------


class FirewallaBoxOnlineSensor(_FirewallaBinarySensor):
    """Connectivity sensor for a Firewalla box."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "box_online"

    def __init__(
        self, coordinator: FirewallaCoordinator, box: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self._box_id = box["id"]
        self._attr_unique_id = f"{DOMAIN}_box_online_{self._box_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{self._box_id}")},
            name=box_display_name(box),
            manufacturer="Firewalla",
            model=box.get("model", "Firewalla Box"),
            sw_version=box.get("version"),
            configuration_url=safe_configuration_url(box.get("publicIP")),
        )
        self._update_state(box)

    def _get_box(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        return next(
            (
                b
                for b in self.coordinator.data.get("boxes", [])
                if b.get("id") == self._box_id
            ),
            None,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        box = self._get_box()
        if box:
            self._update_state(box)
            self.async_write_ha_state()

    def _update_state(self, box: dict[str, Any]) -> None:
        self._attr_is_on = box.get("online", False)
        self._attr_extra_state_attributes = {
            "gid": box.get("gid"),
            "version": box.get("version"),
            "mode": box.get("mode"),
            "public_ip": box.get("publicIP"),
            "location": box.get("location"),
            "last_seen": box.get("lastSeen"),
            "device_count": box.get("deviceCount"),
            "alarm_count": box.get("alarmCount"),
            "rule_count": box.get("ruleCount"),
        }


# ---------------------------------------------------------------------------
# Device online
# ---------------------------------------------------------------------------


class FirewallaDeviceOnlineSensor(_FirewallaBinarySensor):
    """Connectivity sensor for a network device seen by Firewalla."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "device_online"

    def __init__(
        self, coordinator: FirewallaCoordinator, device: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device["id"]
        self._attr_unique_id = f"{DOMAIN}_online_{self._device_id}"
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
        self._update_state(device)

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

    @callback
    def _handle_coordinator_update(self) -> None:
        device = self._get_device()
        if device:
            self._update_state(device)
            self.async_write_ha_state()

    def _update_state(self, device: dict[str, Any]) -> None:
        self._attr_is_on = device.get("online", False)
        self._attr_extra_state_attributes = {
            "ip_address": device.get("ip"),
            "mac_address": device.get("mac"),
            "network": device.get("network", {}).get("name"),
            "last_active": device.get("lastActiveTimestamp"),
        }


# ---------------------------------------------------------------------------
# Rule active
# ---------------------------------------------------------------------------


class FirewallaRuleActiveSensor(_FirewallaBinarySensor):
    """Indicates whether a Firewalla firewall rule is active."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "rule_active"

    def __init__(
        self, coordinator: FirewallaCoordinator, rule: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self._rule_id = rule["id"]
        self._attr_unique_id = f"{DOMAIN}_rule_{self._rule_id}"

        action = rule.get("action", "Rule").capitalize()
        target = (
            rule.get("target", {}).get("value")
            or rule.get("scope", {}).get("value")
            or rule.get("notes")
            or self._rule_id
        )
        self._attr_name = f"{action}: {target}"

        box_id = rule.get("gid") or first_box_id(coordinator.data)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{box_id}")},
        )
        self._update_state(rule)

    @callback
    def _handle_coordinator_update(self) -> None:
        if not self.coordinator.data:
            return
        rule = next(
            (
                r
                for r in self.coordinator.data.get("rules", [])
                if r.get("id") == self._rule_id
            ),
            None,
        )
        if rule:
            self._update_state(rule)
            self.async_write_ha_state()

    def _update_state(self, rule: dict[str, Any]) -> None:
        self._attr_is_on = rule.get("status") == "active"
        self._attr_extra_state_attributes = {
            ATTR_RULE_ID: self._rule_id,
            "action": rule.get("action"),
            "direction": rule.get("direction"),
            "notes": rule.get("notes"),
        }


# ---------------------------------------------------------------------------
# Alarm active
# ---------------------------------------------------------------------------


class FirewallaAlarmSensor(_FirewallaBinarySensor):
    """Binary sensor representing a single Firewalla alarm."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "alarm_active"

    def __init__(
        self, coordinator: FirewallaCoordinator, alarm: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self._alarm_id = alarm["id"]
        self._attr_unique_id = f"{DOMAIN}_alarm_{self._alarm_id}"

        msg = alarm.get("message") or alarm.get("type") or self._alarm_id
        self._attr_name = f"Alarm: {msg[:40]}"

        box_id = alarm.get("gid") or first_box_id(coordinator.data)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{box_id}")},
        )
        self._update_state(alarm)

    @callback
    def _handle_coordinator_update(self) -> None:
        if not self.coordinator.data:
            return
        alarm = next(
            (
                a
                for a in self.coordinator.data.get("alarms", [])
                if a.get("id") == self._alarm_id
            ),
            None,
        )
        if alarm:
            self._update_state(alarm)
            self.async_write_ha_state()

    def _update_state(self, alarm: dict[str, Any]) -> None:
        self._attr_is_on = alarm.get("status", 1) != 2

        device_id = (alarm.get("device") or {}).get("id")
        device_name: str | None = None
        if device_id and self.coordinator.data:
            matched = next(
                (
                    d for d in self.coordinator.data.get("devices", [])
                    if d.get("id") == device_id
                ),
                None,
            )
            device_name = matched.get("name") if matched else device_id

        self._attr_extra_state_attributes = {
            ATTR_ALARM_ID: self._alarm_id,
            "message": alarm.get("message"),
            "type": alarm.get("type"),
            "timestamp": alarm.get("ts"),
            "device_id": device_id,
            "device_name": device_name,
        }
