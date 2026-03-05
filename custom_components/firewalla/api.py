"""Firewalla MSP API Client.

Communicates with the Firewalla MSP API v2.
Endpoint reference: https://docs.firewalla.net/
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import aiohttp
import async_timeout

from .const import (
    DEFAULT_API_URL,
    DEFAULT_TIMEOUT,
    FirewallaAuthError,
)

_LOGGER = logging.getLogger(__name__)

# Maximum pages to follow when paginating cursored endpoints (safety cap).
_MAX_PAGES = 20


class FirewallaApiClient:
    """Firewalla MSP API v2 client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_token: str,
        subdomain: str | None = None,
    ) -> None:
        """Initialise the API client."""
        self._session = session
        self._api_token = api_token
        self._subdomain = subdomain

        if subdomain:
            self._base_url = f"https://{subdomain}.firewalla.net/v2"
        else:
            self._base_url = DEFAULT_API_URL

        _LOGGER.debug("Firewalla API client → %s", self._base_url)

    # ------------------------------------------------------------------
    # Low-level request helper
    # ------------------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        """Return headers for every API request."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Token {self._api_token}"
        return headers

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]] | bool | None:
        """Make an API request.

        Raises
        ------
        FirewallaAuthError
            On HTTP 401 — must be caught explicitly by callers.

        Returns
        -------
        Parsed JSON (dict or list), ``True`` for 204 No Content,
        or ``None`` on non-auth errors.
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
                    json=json_data,
                )

                # --- All body reads are inside the timeout context ---

                if response.status == 401:
                    body = await response.text()
                    _LOGGER.error("HTTP 401 from %s: %s", url, body)
                    raise FirewallaAuthError(f"Unauthorized: {body}")

                if response.status == 429:
                    body = await response.text()
                    _LOGGER.error("HTTP 429 from %s: %s", url, body)
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = min(int(retry_after), 60)
                            _LOGGER.warning("Rate limited — backing off %ss", wait)
                            await asyncio.sleep(wait)
                        except ValueError:
                            pass
                    return None

                # Detect unexpected HTML (e.g. login redirect)
                ct = response.headers.get("Content-Type", "")
                if "text/html" in ct:
                    body = await response.text()
                    if "<html" in body:
                        _LOGGER.error("HTML instead of JSON from %s", url)
                        return None

                if response.status not in (200, 201, 204):
                    body = await response.text()
                    _LOGGER.error("HTTP %s from %s: %s", response.status, url, body)
                    return None

                # 204 No Content — successful action, no body
                if response.status == 204:
                    return True

                try:
                    result = await response.json()
                except aiohttp.ContentTypeError:
                    body = await response.text()
                    _LOGGER.error("Invalid JSON from %s: %s", url, body)
                    return None

                # Unwrap {"data": [...]} envelope if present
                if isinstance(result, dict) and "data" in result and not isinstance(result.get("data"), (int, float, str, bool)):
                    return result["data"]

                return result

        except FirewallaAuthError:
            raise
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout after %ss: %s", DEFAULT_TIMEOUT, url)
            return None
        except aiohttp.ClientError as err:
            _LOGGER.error("Client error for %s: %s", url, err)
            return None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Unexpected error for %s: %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # Core data endpoints
    # ------------------------------------------------------------------

    async def get_boxes(self) -> list[dict[str, Any]]:
        """GET /v2/boxes — list all Firewalla boxes."""
        try:
            result = await self._api_request("GET", "boxes")
        except FirewallaAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error getting boxes: %s", exc)
            return []

        if result is None:
            return []

        if not isinstance(result, list):
            _LOGGER.warning("get_boxes: unexpected type %s", type(result))
            return []

        processed: list[dict[str, Any]] = []
        for box in result:
            if not isinstance(box, dict):
                continue
            if "id" not in box:
                box["id"] = (
                    box.get("uuid")
                    or box.get("gid")
                    or box.get("name")
                    or f"box_{len(processed)}"
                )
            processed.append(box)

        _LOGGER.debug("Retrieved %d boxes", len(processed))
        return processed

    async def get_devices(self) -> list[dict[str, Any]]:
        """GET /v2/devices — list all devices across all networks."""
        try:
            result = await self._api_request("GET", "devices")
        except FirewallaAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error getting devices: %s", exc)
            return []

        if result is None:
            return []

        if not isinstance(result, list):
            _LOGGER.warning("get_devices: unexpected type %s", type(result))
            return []

        processed: list[dict[str, Any]] = []
        for device in result:
            if not isinstance(device, dict):
                continue
            if "id" not in device:
                device["id"] = (
                    device.get("mac")
                    or device.get("ip")
                    or f"device_{len(processed)}"
                )
            # Synthesise 'mac' from 'id' — MSP device ID is the MAC address
            if "mac" not in device and ":" in device.get("id", ""):
                device["mac"] = device["id"]
            # Derive online status from lastActiveTimestamp if missing
            if "online" not in device:
                last_active = device.get("lastActiveTimestamp")
                if last_active:
                    now_ms = datetime.now().timestamp() * 1000
                    device["online"] = (now_ms - last_active) < (15 * 60 * 1000)
                else:
                    device["online"] = False
            processed.append(device)

        _LOGGER.debug("Retrieved %d devices", len(processed))
        return processed

    async def get_rules(self) -> list[dict[str, Any]]:
        """GET /v2/rules — list all firewall rules.

        The API returns ``{count, results}``; we return just the results list.
        """
        try:
            result = await self._api_request("GET", "rules")
        except FirewallaAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error getting rules: %s", exc)
            return []

        if isinstance(result, dict) and "results" in result:
            return result["results"] if isinstance(result["results"], list) else []
        return result if isinstance(result, list) else []

    async def get_alarms(self) -> list[dict[str, Any]]:
        """GET /v2/alarms — list active alarms with cursor pagination.

        Follows ``next_cursor`` to retrieve all active alarms (safety cap 4 000).
        """
        all_alarms: list[dict[str, Any]] = []
        params: dict[str, Any] = {"query": "status:active", "limit": 200}

        for _page in range(_MAX_PAGES):
            try:
                result = await self._api_request("GET", "alarms", params=params)
            except FirewallaAuthError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error("Error getting alarms: %s", exc)
                break

            if not result:
                break

            if isinstance(result, dict):
                results = result.get("results", [])
                next_cursor = result.get("next_cursor")
            elif isinstance(result, list):
                results = result
                next_cursor = None
            else:
                break

            for alarm in results:
                if isinstance(alarm, dict):
                    if "id" not in alarm:
                        alarm["id"] = f"alarm_{alarm.get('aid', len(all_alarms))}"
                    all_alarms.append(alarm)

            if not next_cursor or len(all_alarms) >= 4000:
                break
            params["cursor"] = next_cursor

        _LOGGER.debug("Retrieved %d alarms", len(all_alarms))
        return all_alarms

    async def get_flows(self) -> list[dict[str, Any]]:
        """GET /v2/flows — list recent network flows."""
        try:
            result = await self._api_request("GET", "flows")
        except FirewallaAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error getting flows: %s", exc)
            return []

        if isinstance(result, dict) and "results" in result:
            return result["results"] if isinstance(result["results"], list) else []
        return result if isinstance(result, list) else []

    async def get_target_lists(self) -> list[dict[str, Any]]:
        """GET /v2/target-lists — list all target lists."""
        try:
            result = await self._api_request("GET", "target-lists")
        except FirewallaAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error getting target lists: %s", exc)
            return []

        return result if isinstance(result, list) else []

    async def get_simple_stats(self) -> dict[str, Any]:
        """GET /v2/stats/simple — lightweight fleet health stats.

        Returns ``{onlineBoxes, offlineBoxes, alarms, rules}``.
        """
        try:
            result = await self._api_request("GET", "stats/simple")
        except FirewallaAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error getting stats/simple: %s", exc)
            return {}

        return result if isinstance(result, dict) else {}

    # ------------------------------------------------------------------
    # Action endpoints
    # ------------------------------------------------------------------

    async def async_delete_alarm(self, gid: str, aid: str | int) -> bool:
        """DELETE /v2/alarms/:gid/:aid — delete (dismiss) an alarm.

        Returns True on success (HTTP 200/204), False otherwise.
        """
        try:
            result = await self._api_request("DELETE", f"alarms/{gid}/{aid}")
        except FirewallaAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error deleting alarm %s/%s: %s", gid, aid, exc)
            return False
        return result is not None

    async def async_pause_rule(self, rule_id: str) -> bool:
        """POST /v2/rules/:id/pause — pause an active rule.

        Returns True on success (HTTP 200/204), False otherwise.
        """
        try:
            result = await self._api_request("POST", f"rules/{rule_id}/pause")
        except FirewallaAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error pausing rule %s: %s", rule_id, exc)
            return False
        return result is not None

    async def async_resume_rule(self, rule_id: str) -> bool:
        """POST /v2/rules/:id/resume — resume a paused rule.

        Returns True on success (HTTP 200/204), False otherwise.
        """
        try:
            result = await self._api_request("POST", f"rules/{rule_id}/resume")
        except FirewallaAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error resuming rule %s: %s", rule_id, exc)
            return False
        return result is not None

    async def async_rename_device(
        self, box_id: str, device_id: str, name: str
    ) -> bool:
        """PATCH /v2/boxes/:boxId/devices/:deviceId — rename a device.

        Requires MSP 2.9+.  Returns True on success, False otherwise.
        """
        try:
            result = await self._api_request(
                "PATCH",
                f"boxes/{box_id}/devices/{device_id}",
                json_data={"name": name},
            )
        except FirewallaAuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error renaming device %s: %s", device_id, exc)
            return False
        return result is not None

    # ------------------------------------------------------------------
    # Search endpoints (cursor-paginated, for services)
    # ------------------------------------------------------------------

    async def search_alarms(
        self,
        query: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        """GET /v2/alarms?query=…&limit=… — search alarms with pagination.

        Returns ``{count: int, results: list}`` aggregated across pages
        (up to 10 pages).
        """
        return await self._paginated_search("alarms", query, limit, max_pages=10)

    async def search_flows(
        self,
        query: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        """GET /v2/flows?query=…&limit=… — search flows with pagination.

        Returns ``{count: int, results: list}`` aggregated across pages
        (up to 10 pages).
        """
        return await self._paginated_search("flows", query, limit, max_pages=10)

    async def _paginated_search(
        self,
        endpoint: str,
        query: str,
        limit: int,
        max_pages: int,
    ) -> dict[str, Any]:
        """Generic cursor-paginated search for alarms/flows."""
        all_results: list[dict[str, Any]] = []
        total_count = 0
        params: dict[str, Any] = {"query": query, "limit": limit}

        for _page in range(max_pages):
            try:
                result = await self._api_request("GET", endpoint, params=params)
            except FirewallaAuthError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Search %s page %d error: %s", endpoint, _page, exc)
                break

            if not result or not isinstance(result, dict):
                break

            results = result.get("results", [])
            if isinstance(results, list):
                all_results.extend(results)
            total_count = result.get("count", total_count)

            next_cursor = result.get("next_cursor")
            if not next_cursor:
                break
            params["cursor"] = next_cursor

        return {"count": len(all_results), "results": all_results}
