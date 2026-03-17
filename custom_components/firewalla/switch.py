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
from .helpers import first_box_id

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

    known_rule_ids: set[str] = set()

    @callback
    def _async_add_new_rules() -> None:
        """Discover and register new rule switch entities on each coordinator update."""
        if not coordinator.data:
            return

        rules = coordinator.data.get("rules", [])
        _LOGGER.debug(
            "Rule listener fired — %d rules in coordinator data, %d already known",
            len(rules),
            len(known_rule_ids),
        )

        new_entities: list[SwitchEntity] = []
        for rule in rules:
            if not isinstance(rule, dict) or "id" not in rule:
                continue
            rule_id = str(rule["id"])
            if rule_id not in known_rule_ids:
                _LOGGER.debug("Registering new rule entity: %s", rule_id)
                known_rule_ids.add(rule_id)
                new_entities.append(FirewallaRuleSwitch(coordinator, rule))

        if new_entities:
            _LOGGER.debug("Adding %d new rule switch entities", len(new_entities))
            async_add_entities(new_entities)

    # Register entities already present at setup time.
    _async_add_new_rules()

    # Re-run on every subsequent coordinator refresh to pick up new rules.
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_rules))


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
            rule.get("notes")
            or rule.get("target", {}).get("value")
            or rule.get("scope", {}).get("value")
            or self._rule_id
        )
        self._attr_name = f"{action}: {target}"

        box_id = rule.get("gid") or first_box_id(coordinator.data)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"box_{box_id}")},
        )

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
    def is_on(self) -> bool:
        """Return True if the rule is active."""
        rule = self._get_rule()
        return bool(rule and rule.get("status") == "active")

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
