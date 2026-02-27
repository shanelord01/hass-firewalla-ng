"""Shared helper utilities for the Firewalla integration."""
from __future__ import annotations


def _box_display_name(box: dict) -> str:
    """Return a clean box display name without duplicating 'Firewalla'.

    The Firewalla MSP API sometimes returns box names that already contain
    the word 'Firewalla' (e.g. 'name Firewalla'). This helper prevents
    the UI from showing 'Firewalla name Firewalla'.
    """
    name = box.get("name") or box.get("id", "Box")
    return name if "firewalla" in name.lower() else f"Firewalla {name}"
