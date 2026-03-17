"""Shared helper utilities for the Firewalla integration."""
from __future__ import annotations

import ipaddress
import logging

_LOGGER = logging.getLogger(__name__)


def box_display_name(box: dict) -> str:
    """Return a clean box display name without duplicating 'Firewalla'.

    The Firewalla MSP API sometimes returns box names that already contain
    the word 'Firewalla' (e.g. 'name Firewalla'). This helper prevents
    the UI from showing 'Firewalla name Firewalla'.
    """
    name = box.get("name") or box.get("id", "Box")
    return name if "firewalla" in name.lower() else f"Firewalla {name}"


def first_box_id(coordinator_data: dict | None) -> str:
    """Return the ID of the first known box, or 'unknown' if none.

    Used as a fallback device attachment point for rules and alarms
    that don't carry a gid field.  Accepts coordinator.data directly
    to avoid importing FirewallaCoordinator and creating a circular dep.
    """
    boxes = coordinator_data.get("boxes", []) if coordinator_data else []
    return boxes[0].get("id", "unknown") if boxes else "unknown"


def safe_configuration_url(raw_ip: str | None) -> str | None:
    """Return an https URL for the box's public IP, or None if invalid.

    Validates the value is a real IP address before embedding it in a URL
    shown on the HA device card.  Rejects hostnames, empty strings, and
    malformed values that could construct an unexpected URL.
    IPv6 addresses are wrapped in brackets per RFC 2732.
    """
    if not raw_ip:
        return None
    try:
        addr = ipaddress.ip_address(raw_ip)
    except ValueError:
        _LOGGER.debug("Ignoring invalid publicIP value: %s", raw_ip)
        return None
    if isinstance(addr, ipaddress.IPv6Address):
        return f"https://[{addr}]"
    return f"https://{addr}"


def rule_display_name(
    rule: dict,
    devices: list[dict] | None = None,
) -> str:
    """Build a human-readable display name for a Firewalla rule.

    Priority:
    1. notes field — user-defined label, used as-is with action prefix.
    2. Composite built from action + target + scope, resolving device MACs
       against the coordinator device list where possible.

    Examples:
      notes="Test User"                     → "Block: Test User"
      target=internet, scope=group 67       → "Block: Internet on group 67"
      target=domain deb.debian.org, scope=device pi4nut (MAC resolved)
                                            → "Allow: deb.debian.org on pi4nut"
      target=ip 71.6.167.142, no scope      → "Block: 71.6.167.142"
    """
    action = rule.get("action", "rule").capitalize()
    notes = (rule.get("notes") or "").strip()

    if notes:
        return f"{action}: {notes}"

    target_label = _target_label(rule.get("target") or {})
    scope_label = _scope_label(rule.get("scope") or {}, devices or [])

    if scope_label:
        return f"{action}: {target_label} on {scope_label}"
    return f"{action}: {target_label}"


def _target_label(target: dict) -> str:
    """Return a human-readable label for a rule target."""
    t_type = target.get("type", "")
    t_value = target.get("value", "")

    if t_type == "internet":
        return "Internet"
    if t_type == "intranet":
        # Value is a network-segment UUID — not resolvable without an extra
        # API call. Use a generic label; notes should be used for clarity.
        return "Intranet"
    if t_value:
        return str(t_value)
    return t_type or "Unknown"


def _scope_label(scope: dict, devices: list[dict]) -> str:
    """Return a human-readable label for a rule scope.

    Device scopes are resolved against the coordinator device list by MAC
    address. Network and group scopes return a generic label because their
    UUIDs / numeric IDs are not resolvable without additional API endpoints.
    """
    if not scope:
        return ""

    s_type = scope.get("type", "")
    s_value = scope.get("value", "")

    if not s_value:
        return ""

    if s_type == "device":
        mac_upper = s_value.upper()
        device = next(
            (d for d in devices if (d.get("mac") or "").upper() == mac_upper),
            None,
        )
        if device and device.get("name"):
            return device["name"]
        return s_value  # Fall back to raw MAC

    if s_type == "network":
        # UUID — not resolvable without a /networks endpoint
        return "network"

    if s_type == "group":
        return f"group {s_value}"

    return s_type
