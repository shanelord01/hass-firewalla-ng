"""Firewalla API Client."""
import logging
import aiohttp
import asyncio
import async_timeout
from datetime import datetime
from typing import List, Dict, Any, Optional, Union
import json

from .const import (
    DEFAULT_TIMEOUT,
    DEFAULT_API_URL,
    CONF_API_TOKEN,
    CONF_SUBDOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class FirewallaAuthError(Exception):
    """Raised when the API returns a 401 Unauthorized response."""


class FirewallaApiClient:
    """Firewalla API client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_token: str,
        subdomain: Optional[str] = None,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._api_token = api_token
        self._subdomain = subdomain

        if subdomain:
            self._base_url = f"https://{subdomain}.firewalla.net/v2"
            _LOGGER.debug("Using API URL: %s", self._base_url)
        else:
            self._base_url = DEFAULT_API_URL
            _LOGGER.debug("Using default API URL: %s", self._base_url)

        _LOGGER.debug("Initialized Firewalla API client with token authentication")

    @property
    def _headers(self) -> Dict[str, str]:
        """Get the headers for API requests."""
        headers = {"Content-Type": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Token {self._api_token}"
        return headers

    async def authenticate(self) -> bool:
        """Verify authentication with the Firewalla API.

        NOTE: Only call this from the config flow credential check.
        __init__.py must NOT call this on setup - the coordinator's first
        refresh already hits GET /boxes, so calling this here would double
        the request count and trigger 429s.
        """
        result = await self._api_request("GET", "boxes")
        if result is not None:
            _LOGGER.info("Authentication successful")
            return True
        _LOGGER.error("Failed to authenticate with Firewalla API")
        return False

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """Make an API request.

        Raises:
            FirewallaAuthError: On HTTP 401.
        Returns:
            Parsed response data, or None on non-auth errors.
        """
        url = f"{self._base_url}/{endpoint}"
        _LOGGER.debug("%s %s", method, url)

        try:
            async with async_timeout.timeout(DEFAULT_TIMEOUT):
                response = await self._session.request(
                    method,
                    url,
                    headers=self._headers,
                    params=params,
                )

                # All response body reads are inside the timeout context.

                if response.status == 401:
                    response_text = await response.text()
                    _LOGGER.error("Unauthorized (401) from Firewalla API: %s", response_text)
                    raise FirewallaAuthError(f"Unauthorized: {response_text}")

                if response.status == 429:
                    retry_after = response.headers.get("Retry-After")
                    response_text = await response.text()
                    _LOGGER.error(
                        "HTTP 429 from %s: %s",
                        url,
                        response_text,
                    )
                    if retry_after:
                        try:
                            wait = int(retry_after)
                            _LOGGER.warning(
                                "Rate limited. Retry-After=%s seconds. Backing off.", wait
                            )
                            await asyncio.sleep(min(wait, 60))
                        except ValueError:
                            pass
                    return None

                # Check for unexpected HTML (e.g. login redirect pages)
                content_type = response.headers.get("Content-Type", "")
                if "text/html" in content_type:
                    response_text = await response.text()
                    if "<html" in response_text:
                        _LOGGER.error(
                            "Received HTML response instead of JSON from %s", url
                        )
                        return None

                if response.status not in (200, 204):
                    response_text = await response.text()
                    _LOGGER.error(
                        "Unexpected HTTP %s from %s: %s",
                        response.status,
                        url,
                        response_text,
                    )
                    return None

                # HTTP 204 No Content - successful action, no body
                if response.status == 204:
                    return True

                try:
                    result = await response.json()
                    _LOGGER.debug("API request successful: %s", url)
                except aiohttp.ContentTypeError:
                    response_text = await response.text()
                    _LOGGER.error("Invalid JSON response from %s: %s", url, response_text)
                    return None

                # Unwrap {"data": [...]} envelope if present
                if isinstance(result, dict) and "data" in result:
                    return result["data"]

                return result

        except FirewallaAuthError:
            raise
        except asyncio.TimeoutError:
            _LOGGER.error("Request to %s timed out after %ss", url, DEFAULT_TIMEOUT)
            return None
        except aiohttp.ClientError as err:
            _LOGGER.error("Client error making request to %s: %s", url, err)
            return None
        except Exception as exc:
            _LOGGER.error("Unexpected error making request to %s: %s", url, exc)
            return None

    async def async_check_credentials(self) -> bool:
        """Check if credentials are valid (used by config flow only).

        Does a single GET /boxes call. Does not fall back to GET /devices
        to avoid unnecessary double-requests during setup.
        """
        try:
            result = await self._api_request("GET", "boxes")
        except FirewallaAuthError:
            return False

        if result is not None:
            _LOGGER.info("Credential check successful")
            return True

        _LOGGER.error("Failed to validate credentials")
        return False

    async def get_boxes(self) -> List[Dict[str, Any]]:
        """Get all Firewalla boxes."""
        try:
            boxes = await self._api_request("GET", "boxes")

            if boxes is None:
                _LOGGER.warning("get_boxes: no response from API")
                return []

            if not isinstance(boxes, list):
                _LOGGER.warning(
                    "get_boxes: unexpected response type %s", type(boxes)
                )
                if isinstance(boxes, dict) and "data" in boxes:
                    boxes = boxes["data"]
                    if not isinstance(boxes, list):
                        return []
                else:
                    return []

            processed = []
            for box in boxes:
                if isinstance(box, dict):
                    if "id" not in box:
                        box["id"] = box.get("uuid") or box.get("gid") or box.get("name") or f"box_{len(processed)}"
                    processed.append(box)

            _LOGGER.debug("Retrieved %d boxes", len(processed))
            return processed

        except FirewallaAuthError:
            raise
        except Exception as exc:
            _LOGGER.error("Error getting boxes: %s", exc)
            return []

    async def get_devices(self) -> List[Dict[str, Any]]:
        """Get all devices across all networks."""
        try:
            devices = await self._api_request("GET", "devices")

            if devices is None:
                _LOGGER.warning("get_devices: no response from API")
                return []

            if not isinstance(devices, list):
                _LOGGER.warning(
                    "get_devices: unexpected response type %s", type(devices)
                )
                if isinstance(devices, dict) and "data" in devices:
                    devices = devices["data"]
                    if not isinstance(devices, list):
                        return []
                else:
                    return []

            processed = []
            for device in devices:
                if isinstance(device, dict):
                    if "id" not in device:
                        device["id"] = device.get("mac") or device.get("ip") or f"device_{len(processed)}"

                    if "online" not in device:
                        last_active = device.get("lastActiveTimestamp")
                        if last_active:
                            now = datetime.now().timestamp() * 1000
                            device["online"] = (now - last_active) < (15 * 60 * 1000)
                        else:
                            device["online"] = False

                    if "networkId" not in device:
                        device["networkId"] = "default"

                    processed.append(device)

            _LOGGER.debug("Retrieved %d devices", len(processed))
            return processed

        except FirewallaAuthError:
            raise
        except Exception as exc:
            _LOGGER.error("Error getting devices: %s", exc)
            return []

    async def get_rules(self) -> List[Dict[str, Any]]:
        """Get all rules."""
        result = await self._api_request("GET", "rules")
        return result if isinstance(result, list) else []

    async def get_alarms(self) -> List[Dict[str, Any]]:
        """Get all alarms from the API."""
        try:
            alarms_response = await self._api_request("GET", "alarms")

            if not alarms_response:
                _LOGGER.warning("No alarms found or endpoint not available")
                return []

            if isinstance(alarms_response, dict) and "results" in alarms_response:
                alarms = alarms_response["results"]
            else:
                alarms = alarms_response

            if not isinstance(alarms, list):
                _LOGGER.warning("Alarms data is not a list: %s", alarms)
                return []

            processed = []
            for alarm in alarms:
                if isinstance(alarm, dict):
                    if "id" not in alarm:
                        alarm["id"] = f"alarm_{alarm.get('aid', len(processed))}"
                    processed.append(alarm)

            return processed

        except FirewallaAuthError:
            raise
        except Exception as exc:
            _LOGGER.error("Error getting alarms: %s", exc)
            return []

    async def get_flows(self) -> List[Dict[str, Any]]:
        """Get all flows."""
        result = await self._api_request("GET", "flows")
        return result if isinstance(result, list) else []
