"""DataUpdateCoordinator for Firewalla with stale-device management."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import FirewallaApiClient
from .const import (
    CONF_BOX_FILTER,
    CONF_ENABLE_ALARMS,
    CONF_ENABLE_FLOWS,
    CONF_ENABLE_RULES,
    CONF_ENABLE_TARGET_LISTS,
    CONF_STALE_DAYS,
    DEFAULT_STALE_DAYS,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
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
            config_entry=entry,
        )
        self._client = client
        self._entry = entry
        # Maps device id -> last datetime we saw it in the API response
        self._device_last_seen: dict[str, datetime] = {}
        # Tracks which device ids have been present across all updates
        self._known_device_ids: set[str] = set()
        # Persistent store — keyed per config entry so multi-account installs don't collide
        self._store: Store = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY}_{entry.entry_id}",
        )

    async def async_load_store(self) -> None:
        """Load persisted device-seen timestamps from disk.

        Called once during async_setup_entry before the first refresh so that
        stale-device cleanup survives HA restarts. Timestamps older than
        2 × stale_days are pruned on load to prevent unbounded growth.
        """
        stored = await self._store.async_load()
        if not stored or not isinstance(stored, dict):
            return

        stale_days = self._opt(CONF_STALE_DAYS, DEFAULT_STALE_DAYS)
        cutoff = datetime.now() - timedelta(days=stale_days * 2)
        loaded = 0

        for dev_id, iso_ts in stored.items():
            try:
                ts = datetime.fromisoformat(iso_ts)
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                # Too old to be useful — discard silently
                continue
            self._device_last_seen[dev_id] = ts
            self._known_device_ids.add(dev_id)
            loaded += 1

        _LOGGER.debug(
            "Loaded %d device-seen timestamp(s) from persistent store", loaded
        )

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
            "target_lists": [],
        }

        optional_fetches = [
            ("rules", CONF_ENABLE_RULES, self._client.get_rules),
            ("alarms", CONF_ENABLE_ALARMS, self._client.get_alarms),
            ("flows", CONF_ENABLE_FLOWS, self._client.get_flows),
            ("target_lists", CONF_ENABLE_TARGET_LISTS, self._client.get_target_lists),
        ]

        for key, flag, func in optional_fetches:
            if self._opt(flag):
                try:
                    results[key] = await func() or []
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("Could not fetch %s: %s", key, exc)
                    if self.data:
                        results[key] = self.data.get(key, [])

        # Always fetch simple stats — single lightweight call, no toggle needed
        stats_simple: dict[str, Any] = {}
        try:
            stats_simple = await self._client.get_simple_stats()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Could not fetch stats/simple: %s", exc)
            if self.data:
                stats_simple = self.data.get("stats_simple", {})

        data = {
            "boxes": boxes,
            "devices": devices,
            "stats_simple": stats_simple,
            **results,
        }

        # Update the seen-timestamp for every device in this poll
        now = datetime.now()
        current_ids = {d["id"] for d in devices if isinstance(d, dict) and "id" in d}
        newly_absent = self._known_device_ids - current_ids  # devices missing this poll
        for dev_id in current_ids:
            self._device_last_seen[dev_id] = now
        self._known_device_ids.update(current_ids)

        # Persist timestamps when devices go absent so stale tracking survives restarts.
        # Intentionally NOT written on every poll — many HA installs run on SD cards
        # where per-poll writes (e.g. every 60s) would cause significant write-wear.
        # Writing only on the Present→Absent transition keeps I/O minimal while still
        # ensuring counters are durable across restarts.
        if newly_absent:
            await self._async_persist_timestamps()

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

        if not boxes and not devices:
            raise UpdateFailed("Both boxes and devices endpoints returned empty — possible API failure")

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

    async def _async_persist_timestamps(self) -> None:
        """Write current device-seen timestamps to persistent storage.

        Only called when a device transitions to absent, keeping disk writes
        minimal while ensuring stale-day counters survive HA restarts.
        """
        payload = {
            dev_id: ts.isoformat()
            for dev_id, ts in self._device_last_seen.items()
        }
        await self._store.async_save(payload)
        _LOGGER.debug(
            "Persisted %d device-seen timestamp(s) to store", len(payload)
        )
