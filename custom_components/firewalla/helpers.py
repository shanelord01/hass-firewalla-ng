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
