"""DataUpdateCoordinator for Firewalla with stale-device management."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FirewallaApiClient
from .const import (
    CONF_BOX_FILTER,
    CONF_ENABLE_ALARMS,
    CONF_ENABLE_FLOWS,
    CONF_ENABLE_RULES,
    CONF_ENABLE_TRAFFIC,
    CONF_STALE_DAYS,
    DEFAULT_STALE_DAYS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class FirewallaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage Firewalla data fetching and stale-device cleanup."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: FirewallaApiClient,
        entry: ConfigEntry,
        update_interval: timedelta,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=update_interval,
        )
        self._client = client
        self._entry = entry
        # Maps device id -> last datetime we saw it in the API response
        self._device_last_seen: dict[str, datetime] = {}
        # Tracks which device ids have been present across all updates
        self._known_device_ids: set[str] = set()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _opt(self, key: str, default: Any = False) -> Any:
        """Read a config option, falling back to config entry data."""
        return self._entry.options.get(key, self._entry.data.get(key, default))

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all enabled data from the Firewalla MSP API."""
        try:
            boxes, devices = await self._fetch_core_data()
        except Exception as exc:
            if self.data:
                _LOGGER.warning(
                    "API error - using cached data: %s", exc
                )
                return self.data
            raise UpdateFailed(f"Cannot reach Firewalla API: {exc}") from exc

        results: dict[str, list] = {
            "rules": [],
            "alarms": [],
            "flows": [],
        }

        optional_fetches = [
            ("rules", CONF_ENABLE_RULES, self._client.get_rules),
            ("alarms", CONF_ENABLE_ALARMS, self._client.get_alarms),
            ("flows", CONF_ENABLE_FLOWS, self._client.get_flows),
        ]

        for key, flag, func in optional_fetches:
            if self._opt(flag):
                try:
                    results[key] = await func() or []
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Could not fetch %s: %s", key, exc)
                    if self.data:
                        results[key] = self.data.get(key, [])

        data = {
            "boxes": boxes,
            "devices": devices,
            **results,
        }

        # Update the seen-timestamp for every device in this poll
        now = datetime.now()
        current_ids = {d["id"] for d in devices if isinstance(d, dict) and "id" in d}
        for dev_id in current_ids:
            self._device_last_seen[dev_id] = now
        self._known_device_ids.update(current_ids)

        # Stale-device cleanup
        await self._async_remove_stale_devices(current_ids)

        return data

    async def _fetch_core_data(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetch boxes and devices, raising on total failure.

        If box_filter is configured, only boxes and devices belonging to
        the selected box GIDs are returned.
        """
        boxes = await self._client.get_boxes()
        devices = await self._client.get_devices()

        if boxes is None and devices is None:
            raise UpdateFailed("Both boxes and devices endpoints failed")

        boxes = boxes or []
        devices = devices or []

        # Apply box filter if configured
        box_filter: list[str] = self._opt(CONF_BOX_FILTER, [])
        if box_filter:
            boxes = [b for b in boxes if b.get("id") in box_filter]
            allowed_gids = {b["id"] for b in boxes}
            devices = [
                d for d in devices
                if d.get("gid") in allowed_gids or d.get("boxId") in allowed_gids
            ]

        return boxes, devices

    # ------------------------------------------------------------------
    # Stale-device management
    # ------------------------------------------------------------------

    async def _async_remove_stale_devices(
        self, current_ids: set[str]
    ) -> None:
        """Remove HA device registry entries for long-absent Firewalla devices.

        A device is only removed when:
        1. It has not appeared in any API response for stale_days days.
        2. It is not anchored to the config entry by automations/scenes.

        Devices referenced in automations are NOT automatically removed -
        HA enforces this via async_remove_config_entry_device.
        """
        stale_days = self._opt(CONF_STALE_DAYS, DEFAULT_STALE_DAYS)
        threshold = timedelta(days=stale_days)
        now = datetime.now()

        absent_ids = self._known_device_ids - current_ids
        if not absent_ids:
            return

        dev_registry = dr.async_get(self.hass)

        for dev_id in absent_ids:
            last_seen = self._device_last_seen.get(dev_id)
            if last_seen is None:
                continue
            if (now - last_seen) < threshold:
                continue

            device_entry = dev_registry.async_get_device(
                identifiers={(DOMAIN, dev_id)}
            )
            if device_entry is None:
                self._known_device_ids.discard(dev_id)
                continue

            _LOGGER.info(
                "Removing stale Firewalla device '%s' (last seen %s, threshold %d days)",
                device_entry.name_by_user or device_entry.name or dev_id,
                last_seen.isoformat(),
                stale_days,
            )
            dev_registry.async_update_device(
                device_entry.id,
                remove_config_entry_id=self._entry.entry_id,
            )
            self._known_device_ids.discard(dev_id)
            self._device_last_seen.pop(dev_id, None)
