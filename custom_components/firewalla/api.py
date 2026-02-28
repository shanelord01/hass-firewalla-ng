"""Firewalla MSP API Client."""
import asyncio
import logging
from datetime import datetime
from typing import Any

import aiohttp
import async_timeout

from .const import DEFAULT_API_URL, DEFAULT_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class FirewallaApiClient:
    """Firewalla MSP API client using Token authentication."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_token: str,
        subdomain: str | None = None,
    ) -> None:
        """Initialise the API client."""
        self._session = session
        self._api_token = api_token

        if subdomain:
            self._base_url = f"https://{subdomain}.firewalla.net/v2"
        else:
            self._base_url = DEFAULT_API_URL

        _LOGGER.debug("Firewalla API base URL: %s", self._base_url)

    @property
    def _headers(self) -> dict[str, str]:
        """Return request headers."""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Token {self._api_token}",
        }

    async def _api_request_raw(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list | None:
        """Make an authenticated API request and return the raw parsed JSON.

        Unlike _api_request, this does NOT unwrap {results, next_cursor} envelopes.
        Use this when you need access to pagination cursors or the full response shape.
        Returns None on any network/auth failure.
        """
        url = f"{self._base_url}/{endpoint}"
        _LOGGER.debug("%s %s params=%s json=%s", method, url, params, json)

        try:
            async with async_timeout.timeout(DEFAULT_TIMEOUT):
                response = await self._session.request(
                    method, url, headers=self._headers, params=params, json=json
                )

            content_type = response.headers.get("Content-Type", "")
            if "text/html" in content_type:
                body = await response.text()
                _LOGGER.error(
                    "Received HTML response from %s (likely auth failure): %.200s",
                    url, body,
                )
                return None

            if response.status == 401:
                _LOGGER.error("Unauthorised - check API token")
                return None

            if response.status != 200:
                body = await response.text()
                _LOGGER.error("HTTP %s from %s: %.200s", response.status, url, body)
                return None

            try:
                result = await response.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError) as exc:
                body = await response.text()
                if not body.strip():
                    return {}
                _LOGGER.error("Invalid JSON from %s: %s - %.200s", url, exc, body)
                return None

            return result

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout calling %s", url)
            return None
        except aiohttp.ClientError as exc:
            _LOGGER.error("Client error calling %s: %s", url, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("Unexpected error calling %s: %s", url, exc)
            return None

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any] | None:
        """Make an authenticated API request. Returns None on any failure.

        Automatically unwraps {results, data} envelope responses into the inner list.
        For paginated endpoints where you need next_cursor, use _api_request_raw instead.
        """
        result = await self._api_request_raw(method, endpoint, params=params, json=json)
        if result is None:
            return None
        # Unwrap {results: [...]} and {data: [...]} envelopes
        if isinstance(result, dict):
            for key in ("results", "data"):
                if key in result:
                    return result[key]
        return result

    # ------------------------------------------------------------------
    # Credential check
    # ------------------------------------------------------------------

    async def async_check_credentials(self) -> bool:
        """Return True if the token grants access to the boxes endpoint."""
        result = await self._api_request("GET", "boxes")
        if result is not None:
            _LOGGER.debug("Credential check passed via /boxes")
            return True
        _LOGGER.error("Credential check failed")
        return False

    async def authenticate(self) -> bool:
        """Alias for async_check_credentials."""
        return await self.async_check_credentials()

    # ------------------------------------------------------------------
    # Data endpoints
    # ------------------------------------------------------------------

    async def get_boxes(self) -> list[dict[str, Any]]:
        """Return all Firewalla boxes for this MSP account."""
        raw = await self._api_request("GET", "boxes")
        if not isinstance(raw, list):
            _LOGGER.warning("get_boxes: unexpected response type %s", type(raw))
            return []

        boxes: list[dict[str, Any]] = []
        for i, box in enumerate(raw):
            if not isinstance(box, dict):
                continue
            if "gid" in box:
                box.setdefault("id", box["gid"])
            elif "id" not in box:
                box["id"] = box.get("name", f"box_{i}")
            boxes.append(box)

        _LOGGER.debug("Retrieved %d box(es)", len(boxes))
        return boxes

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return all network devices from the MSP API."""
        raw = await self._api_request("GET", "devices")
        if not isinstance(raw, list):
            _LOGGER.warning("get_devices: unexpected response type %s", type(raw))
            return []

        now_ms = datetime.now().timestamp() * 1000
        devices: list[dict[str, Any]] = []
        for i, dev in enumerate(raw):
            if not isinstance(dev, dict):
                continue

            if "id" not in dev:
                dev["id"] = dev.get("ip") or f"device_{i}"

            # The API returns the MAC as the device id field (e.g. AA:BB:CC:DD:EE:FF).
            # There is no separate mac field in the response. Synthesise one from id
            # so downstream code (connections, MAC sensor, device tracker) can use it
            # consistently without needing to know this API quirk.
            if "mac" not in dev and ":" in dev["id"]:
                dev["mac"] = dev["id"]

            if "online" not in dev:
                last_active = dev.get("lastActiveTimestamp", 0)
                dev["online"] = bool(
                    last_active and (now_ms - last_active) < (15 * 60 * 1000)
                )

            dev.setdefault("networkId", dev.get("network", {}).get("id", "default"))
            devices.append(dev)

        _LOGGER.debug("Retrieved %d device(s)", len(devices))
        return devices

    async def get_alarms(self) -> list[dict[str, Any]]:
        """Return all active alarms, following pagination cursors until exhausted.

        Uses query=status:active so cleared/dismissed alarms are excluded.
        Paginates fully using next_cursor — an MSP with >200 active alarms will
        no longer silently drop records.

        Safety cap: MAX_ALARM_PAGES prevents runaway loops on misconfigured accounts.
        """
        MAX_ALARM_PAGES = 20  # 20 × 200 = 4000 alarms max; ample for any real deployment
        params: dict[str, Any] = {"query": "status:active"}
        alarms: list[dict[str, Any]] = []
        page = 0

        while page < MAX_ALARM_PAGES:
            page += 1
            envelope = await self._api_request_raw("GET", "alarms", params=params)

            if envelope is None:
                _LOGGER.warning("get_alarms: page %d returned None", page)
                break

            # Handle both paginated envelope and bare list (API version differences)
            if isinstance(envelope, list):
                page_results = envelope
                next_cursor = None
            elif isinstance(envelope, dict):
                page_results = envelope.get("results", [])
                next_cursor = envelope.get("next_cursor")
            else:
                _LOGGER.warning("get_alarms: unexpected response type %s", type(envelope))
                break

            offset = len(alarms)
            for i, alarm in enumerate(page_results):
                if not isinstance(alarm, dict):
                    continue
                alarm.setdefault("id", str(alarm.get("aid", f"alarm_{offset + i}")))
                alarms.append(alarm)

            if not next_cursor:
                break

            params = {"query": "status:active", "cursor": next_cursor}

        _LOGGER.debug("Retrieved %d active alarm(s) across %d page(s)", len(alarms), page)
        return alarms

    async def get_rules(self) -> list[dict[str, Any]]:
        """Return all firewall rules."""
        raw = await self._api_request("GET", "rules")
        if not isinstance(raw, list):
            return []
        return raw

    async def get_flows(
        self,
        limit: int = 100,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent traffic flows (first page only for HA purposes)."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        raw = await self._api_request("GET", "flows", params=params)
        if not isinstance(raw, list):
            return []
        return raw

    async def get_simple_stats(self) -> dict[str, Any]:
        """Return MSP-wide summary statistics (onlineBoxes, offlineBoxes, alarms, rules).

        Endpoint: GET /v2/stats/simple
        Response shape: {onlineBoxes: int, offlineBoxes: int, alarms: int, rules: int}
        This is a single lightweight call — always fetched regardless of user toggles.
        """
        raw = await self._api_request("GET", "stats/simple")
        if not isinstance(raw, dict):
            _LOGGER.warning("stats/simple returned unexpected type: %s", type(raw))
            return {}
        return raw

    async def get_target_lists(self) -> list[dict[str, Any]]:
        """Return all target lists visible to this MSP account.

        Endpoint: GET /v2/target-lists
        Response shape: [{id, name, owner, targets, category, notes, lastUpdated}, ...]
        The 'targets' field is a list of domain/IP strings.
        """
        raw = await self._api_request("GET", "target-lists")
        if not isinstance(raw, list):
            _LOGGER.warning("target-lists returned unexpected type: %s", type(raw))
            return []
        return raw

    # ------------------------------------------------------------------
    # Search / query methods (return data to callers, used by HA service responses)
    # ------------------------------------------------------------------

    async def search_alarms(
        self,
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search alarms using Firewalla query syntax.

        Endpoint: GET /v2/alarms?query=<query>&limit=<limit>
        Returns all matching alarms across pages up to a safety cap.

        Query examples (URL encoding handled automatically by aiohttp params):
          status:active device.name:iphone
          transfer.total:>50MB remote.category:game
          ts:>1695196894 box.id:00000000-0000-0000-0000-000000000000

        Args:
            query: Firewalla search query string (not URL-encoded — aiohttp handles that)
            limit: Results per page (default 50; max 200 per API)
        """
        MAX_PAGES = 10
        params: dict[str, Any] = {"query": query, "limit": limit}
        results: list[dict[str, Any]] = []
        page = 0

        while page < MAX_PAGES:
            page += 1
            envelope = await self._api_request_raw("GET", "alarms", params=params)
            if envelope is None:
                break
            if isinstance(envelope, list):
                page_results, next_cursor = envelope, None
            elif isinstance(envelope, dict):
                page_results = envelope.get("results", [])
                next_cursor = envelope.get("next_cursor")
            else:
                break
            for i, alarm in enumerate(page_results):
                if isinstance(alarm, dict):
                    alarm.setdefault("id", str(alarm.get("aid", f"alarm_{len(results) + i}")))
                    results.append(alarm)
            if not next_cursor:
                break
            params = {"query": query, "limit": limit, "cursor": next_cursor}

        _LOGGER.debug("search_alarms(%r): %d result(s) across %d page(s)", query, len(results), page)
        return results

    async def search_flows(
        self,
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search flows using Firewalla query syntax.

        Endpoint: GET /v2/flows?query=<query>&limit=<limit>
        Returns all matching flows across pages up to a safety cap.

        Query examples:
          device.name:iphone direction:outbound
          total:>10MB domain:*youtube*
          ts:>1695196894 category:game

        Args:
            query: Firewalla search query string (not URL-encoded)
            limit: Results per page (default 50)
        """
        MAX_PAGES = 10
        params: dict[str, Any] = {"query": query, "limit": limit}
        results: list[dict[str, Any]] = []
        page = 0

        while page < MAX_PAGES:
            page += 1
            envelope = await self._api_request_raw("GET", "flows", params=params)
            if envelope is None:
                break
            if isinstance(envelope, list):
                page_results, next_cursor = envelope, None
            elif isinstance(envelope, dict):
                page_results = envelope.get("results", [])
                next_cursor = envelope.get("next_cursor")
            else:
                break
            for item in page_results:
                if isinstance(item, dict):
                    results.append(item)
            if not next_cursor:
                break
            params = {"query": query, "limit": limit, "cursor": next_cursor}

        _LOGGER.debug("search_flows(%r): %d result(s) across %d page(s)", query, len(results), page)
        return results

    # ------------------------------------------------------------------
    # Action endpoints
    # ------------------------------------------------------------------

    async def async_pause_rule(self, rule_id: str) -> bool:
        """Pause an active firewall rule. Returns True on success."""
        result = await self._api_request("POST", f"rules/{rule_id}/pause")
        return result is not None

    async def async_resume_rule(self, rule_id: str) -> bool:
        """Resume a paused firewall rule. Returns True on success."""
        result = await self._api_request("POST", f"rules/{rule_id}/resume")
        return result is not None

    async def async_delete_alarm(self, gid: str, aid: str) -> bool:
        """Delete/dismiss an alarm by box GID and alarm ID. Returns True on success."""
        result = await self._api_request("DELETE", f"alarms/{gid}/{aid}")
        return result is not None

    async def async_rename_device(self, gid: str, device_id: str, name: str) -> bool:
        """Rename a network device (requires MSP 2.9+). Returns True on success."""
        result = await self._api_request(
            "PATCH",
            f"boxes/{gid}/devices/{device_id}",
            json={"name": name},
        )
        return result is not None
