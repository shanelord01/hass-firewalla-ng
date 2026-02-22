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

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any] | None:
        """Make an authenticated API request. Returns None on any failure."""
        url = f"{self._base_url}/{endpoint}"
        _LOGGER.debug("%s %s params=%s", method, url, params)

        try:
            async with async_timeout.timeout(DEFAULT_TIMEOUT):
                response = await self._session.request(
                    method, url, headers=self._headers, params=params
                )

            # Detect HTML error pages (e.g. 302 to login page)
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
                _LOGGER.error("Invalid JSON from %s: %s - %.200s", url, exc, body)
                return None

            # The MSP API may wrap lists in a {"results": [...]} envelope.
            if isinstance(result, dict):
                for key in ("results", "data"):
                    if key in result:
                        return result[key]
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
                dev["id"] = dev.get("mac") or dev.get("ip") or f"device_{i}"

            if "mac" in dev and dev["mac"].startswith("mac:"):
                dev["mac"] = dev["mac"][4:]

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
        """Return active alarms."""
        raw = await self._api_request("GET", "alarms")
        if raw is None:
            return []
        if not isinstance(raw, list):
            _LOGGER.warning("get_alarms: unexpected type %s", type(raw))
            return []

        alarms: list[dict[str, Any]] = []
        for i, alarm in enumerate(raw):
            if not isinstance(alarm, dict):
                continue
            alarm.setdefault("id", str(alarm.get("aid", f"alarm_{i}")))
            alarms.append(alarm)

        _LOGGER.debug("Retrieved %d alarm(s)", len(alarms))
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
        params: dict[str, Any] = {"count": limit}
        if cursor:
            params["cursor"] = cursor

        raw = await self._api_request("GET", "flows", params=params)
        if not isinstance(raw, list):
            return []
        return raw
