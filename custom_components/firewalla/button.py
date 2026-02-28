"""Button platform for Firewalla — rule toggle (pause/resume)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ENABLE_RULES, DOMAIN
from .coordinator import FirewallaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Firewalla button entities."""
    if not entry.options.get(CONF_ENABLE_RULES, entry.data.get(CONF_ENABLE_RULES, False)):
        return

    coordinator: FirewallaCoordinator = entry.runtime_data.coordinator

    if not coordinator.data:
        return

    entities = [
        FirewallaRuleToggleButton(coordinator, rule)
        for rule in coordinator.data.get("rules", [])
        if isinstance(rule, dict) and "id" in rule
    ]

    async_add_entities(entities)


class FirewallaRuleToggleButton(CoordinatorEntity[FirewallaCoordinator], ButtonEntity):
    """Button that toggles a Firewalla firewall rule between active and paused."""

    _attr_has_entity_name = True
    _attr_translation_key = "rule_toggle"

    def __init__(
        self, coordinator: FirewallaCoordinator, rule: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self._rule_id = rule["id"]
        self._attr_unique_id = f"{DOMAIN}_rule_toggle_{self._rule_id}"

        action = rule.get("action", "Rule").capitalize()
        target = (
            rule.get("target", {}).get("value")
            or rule.get("scope", {}).get("value")
            or rule.get("notes")
            or self._rule_id
        )
        self._attr_name = f"{action}: {target} Toggle"

        box_id = rule.get("boxId") or self._first_box_id(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{box_id}")},
        )

    @staticmethod
    def _first_box_id(coordinator: FirewallaCoordinator) -> str:
        boxes = coordinator.data.get("boxes", []) if coordinator.data else []
        return boxes[0].get("id", "unknown") if boxes else "unknown"

    def _get_rule(self) -> dict[str, Any] | None:
        """Return the current rule data from coordinator."""
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
    def icon(self) -> str:
        """Return contextual icon based on current rule state."""
        rule = self._get_rule()
        if rule and rule.get("status") == "active":
            return "mdi:pause-circle"
        return "mdi:play-circle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rule = self._get_rule()
        if not rule:
            return {}
        return {
            "rule_id": self._rule_id,
            "current_status": rule.get("status", "unknown"),
            "action": rule.get("action"),
            "direction": rule.get("direction"),
        }

    async def async_press(self) -> None:
        """Toggle the rule — pause if active, resume if paused."""
        rule = self._get_rule()
        client = self.coordinator.config_entry.runtime_data.client

        if rule and rule.get("status") == "active":
            _LOGGER.debug("Pausing rule %s", self._rule_id)
            success = await client.async_pause_rule(self._rule_id)
        else:
            _LOGGER.debug("Resuming rule %s", self._rule_id)
            success = await client.async_resume_rule(self._rule_id)

        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to toggle rule %s", self._rule_id)
