"""Switch platform for Firewalla — firewall rule active/paused toggle."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_RULE_ID, CONF_ENABLE_RULES, DOMAIN
from .coordinator import FirewallaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Firewalla rule switches."""
    if not entry.options.get(CONF_ENABLE_RULES, entry.data.get(CONF_ENABLE_RULES, False)):
        return

    coordinator: FirewallaCoordinator = entry.runtime_data.coordinator

    if not coordinator.data:
        return

    entities = [
        FirewallaRuleSwitch(coordinator, rule)
        for rule in coordinator.data.get("rules", [])
        if isinstance(rule, dict) and "id" in rule
    ]

    async_add_entities(entities)


class FirewallaRuleSwitch(CoordinatorEntity[FirewallaCoordinator], SwitchEntity):
    """Switch representing a Firewalla firewall rule (On = Active, Off = Paused)."""

    _attr_has_entity_name = True
    _attr_translation_key = "rule_switch"

    def __init__(
        self, coordinator: FirewallaCoordinator, rule: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self._rule_id = rule["id"]
        self._attr_unique_id = f"{DOMAIN}_rule_switch_{self._rule_id}"

        action = rule.get("action", "Rule").capitalize()
        target = (
            rule.get("target", {}).get("value")
            or rule.get("scope", {}).get("value")
            or rule.get("notes")
            or self._rule_id
        )
        self._attr_name = f"{action}: {target}"

        box_id = rule.get("gid") or self._first_box_id(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{box_id}")},
        )

    @staticmethod
    def _first_box_id(coordinator: FirewallaCoordinator) -> str:
        boxes = coordinator.data.get("boxes", []) if coordinator.data else []
        return boxes[0].get("id", "unknown") if boxes else "unknown"

    def _get_rule(self) -> dict[str, Any] | None:
        """Return the latest rule data from the coordinator."""
        if not self.coordinator.data:
            return None
        return next(
            (
                r
                for r in self.coordinator.data.get("rules", [])
                if r.get("id") == self._rule_id
            ),
            None,
        )

    @property
    def entity_registry_enabled_default(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        """Return True if the rule is active."""
        rule = self._get_rule()
        return bool(rule and rule.get("status") == "active")

    @property
    def icon(self) -> str:
        """Contextual icon — shield when active, shield-off when paused."""
        return "mdi:shield-check" if self.is_on else "mdi:shield-off-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rule = self._get_rule()
        if not rule:
            return {}
        return {
            ATTR_RULE_ID: self._rule_id,
            "status": rule.get("status", "unknown"),
            "action": rule.get("action"),
            "direction": rule.get("direction"),
            "notes": rule.get("notes"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Resume (activate) the firewall rule."""
        client = self.coordinator.config_entry.runtime_data.client
        _LOGGER.debug("Resuming rule %s", self._rule_id)
        if await client.async_resume_rule(self._rule_id):
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to resume rule %s", self._rule_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pause (deactivate) the firewall rule."""
        client = self.coordinator.config_entry.runtime_data.client
        _LOGGER.debug("Pausing rule %s", self._rule_id)
        if await client.async_pause_rule(self._rule_id):
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to pause rule %s", self._rule_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
