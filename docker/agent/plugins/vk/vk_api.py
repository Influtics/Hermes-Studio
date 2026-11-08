"""VK API transport for the Hermes VK platform plugin.

Two egress paths, deliberately separated:

* **Token-bearing method calls** (``groups.getLongPollServer``,
  ``messages.send``, ``users.get``, …) go through the Influtics VK proxy
  (``https://vk-proxy.influtics.com/vk/api-call``) by default. The proxy
  runs on the RU VPS, so every call egresses from a single static IP —
  which is what VK requires for IP-locked tokens, and what the rest of
  the Influtics stack already does (``vk-worker`` → VPS proxy → VK API).

* **Long Poll polling** is a tokenless GET to ``lp.vk.com``: the longpoll
  key in the URL is the only credential and VK does not bind it to an IP,
  so it goes direct from wherever the agent runs.

Both paths are implemented here so the adapter never has to know which one
is in use; it just calls ``api_call()``.

Only httpx is used — it is already a Hermes dependency.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover — httpx ships with Hermes
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_PROXY_URL = "https://vk-proxy.influtics.com"
DEFAULT_API_URL = "https://api.vk.com"
DEFAULT_API_VERSION = "5.199"

# VK Long Poll v2 — the version that carries ``message_new`` events for
# community conversations. v3 adds extra fields we do not need.
LONGPOLL_VERSION = 2
LONGPOLL_DEFAULT_WAIT = 25
LONGPOLL_MIN_WAIT = 10
LONGPOLL_MAX_WAIT = 90

# VK error codes that mean "this token will never work from here" — the
# poll loop must stop instead of retrying forever.
#   5     — auth failed (wrong / revoked token)
#   1130  — "access_token was given to another ip address" (IP-locked token
#           seen from an egress VK does not recognise)
#   900   — invalid access token
FATAL_ERROR_CODES = frozenset({5, 1130, 900})


class VkApiError(Exception):
    """A VK API call failed.

    ``error_code`` carries VK's numeric code when one is available
    (5 / 6 / 1130 / 900 …). ``fatal`` forces the outcome for failures
    that VK never reports a code for — a rejected proxy key, for
    instance, is a configuration error that no retry can fix.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: Optional[int] = None,
        method: Optional[str] = None,
        fatal: Optional[bool] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.method = method
        self._fatal = fatal

    @property
    def is_fatal(self) -> bool:
        """True when retrying cannot help (bad token / wrong egress IP)."""
        if self._fatal is not None:
            return self._fatal
        return self.error_code in FATAL_ERROR_CODES

    @property
    def is_rate_limited(self) -> bool:
        """True when VK asked us to slow down (error code 6)."""
        return self.error_code == 6

    def __str__(self) -> str:
        if self.error_code is None:
            return self.message
        return f"{self.message} (VK error {self.error_code})"


def _random_id() -> int:
    """Generate the ``random_id`` VK requires on every ``messages.send``.

    VK uses it as the server-side dedup key: two sends with the same
    ``random_id`` are collapsed into one message. It must be a positive
    integer, so we take 32 bits of a uuid.
    """
    return int(uuid.uuid4().hex[:8], 16)


class VkApi:
    """Thin async client for the VK methods the adapter needs."""

    def __init__(
        self,
        token: str,
        *,
        group_id: str = "",
        api_mode: str = "proxy",
        proxy_url: str = DEFAULT_PROXY_URL,
        proxy_api_key: str = "",
        http_client: Optional["httpx.AsyncClient"] = None,
        timeout: float = 15.0,
    ) -> None:
        self.token = (token or "").strip()
        self.group_id = (group_id or "").strip()
        self.api_mode = (api_mode or "proxy").strip().lower()
        self.proxy_url = (proxy_url or DEFAULT_PROXY_URL).rstrip("/")
        self.proxy_api_key = (proxy_api_key or "").strip()
        self.timeout = timeout
        self._http_client = http_client
        self._owns_client = http_client is None

    # -- Plumbing -----------------------------------------------------------

    def _client(self) -> "httpx.AsyncClient":
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self.timeout)
            self._owns_client = True
        return self._http_client

    async def aclose(self) -> None:
        """Close the HTTP client if this instance created it."""
        if self._http_client is not None and self._owns_client:
            await self._http_client.aclose()
            self._http_client = None

    def _proxy_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.proxy_api_key:
            headers["Authorization"] = f"Bearer {self.proxy_api_key}"
        return headers

    # -- Method calls -------------------------------------------------------

    async def api_call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Call a VK API method and return its ``response`` payload.

        Raises :class:`VkApiError` on any VK-level or transport failure.
        """
        params = {k: v for k, v in (params or {}).items() if v is not None}

        if self.api_mode == "direct":
            return await self._api_call_direct(method, params)
        return await self._api_call_via_proxy(method, params)

    async def _api_call_direct(self, method: str, params: Dict[str, Any]) -> Any:
        """Call VK directly (``https://api.vk.com/method/…``)."""
        query = dict(params)
        query["access_token"] = self.token
        query["v"] = DEFAULT_API_VERSION
        url = f"{DEFAULT_API_URL}/method/{method}"

        try:
            response = await self._client().get(url, params=query, timeout=self.timeout)
        except httpx.TimeoutException as e:
            raise VkApiError(f"VK request timed out: {e}", method=method) from e
        except httpx.HTTPError as e:
            raise VkApiError(f"VK request failed: {e}", method=method) from e

        try:
            payload = response.json()
        except ValueError as e:
            raise VkApiError(
                f"VK returned non-JSON (HTTP {response.status_code})", method=method
            ) from e

        error = payload.get("error")
        if error:
            raise VkApiError(
                error.get("error_msg") or "VK API error",
                error_code=error.get("error_code"),
                method=method,
            )
        return payload.get("response")

    async def _api_call_via_proxy(self, method: str, params: Dict[str, Any]) -> Any:
        """Call VK through the Influtics VPS proxy.

        The proxy owns the egress IP and takes the caller's access token as
        a parameter, so the community token is used as-is while the request
        itself leaves from the RU VPS.
        """
        url = f"{self.proxy_url}/vk/api-call"
        body = {"method": method, "params": params, "access_token": self.token}

        try:
            response = await self._client().post(
                url, json=body, headers=self._proxy_headers(), timeout=self.timeout
            )
        except httpx.TimeoutException as e:
            raise VkApiError(f"VK proxy request timed out: {e}", method=method) from e
        except httpx.HTTPError as e:
            raise VkApiError(f"VK proxy request failed: {e}", method=method) from e

        try:
            payload = response.json()
        except ValueError as e:
            raise VkApiError(
                f"VK proxy returned non-JSON (HTTP {response.status_code})", method=method
            ) from e

        if response.status_code >= 300 or not payload.get("success", False):
            error_code = payload.get("error_code")
            message = payload.get("error") or f"VK proxy HTTP {response.status_code}"
            raise VkApiError(
                message,
                error_code=error_code if isinstance(error_code, int) else None,
                method=method,
                # A rejected proxy key is a configuration error, not a
                # transient VK failure — retrying it forever would just
                # hide the misconfiguration. Only ever *raise* fatality
                # here: passing False would mask the error-code-derived
                # verdict (a VK auth error, code 5, is fatal too).
                fatal=True if response.status_code in (401, 403) else None,
            )
        return payload.get("data")

    # -- Long Poll ----------------------------------------------------------

    async def get_longpoll_server(self) -> Dict[str, str]:
        """Fetch the Long Poll server + key for this community.

        Returns ``{"server": ..., "key": ..., "ts": ...}``.
        """
        response = await self.api_call(
            "groups.getLongPollServer",
            {"group_id": self.group_id, "lp_version": LONGPOLL_VERSION},
        )
        if not isinstance(response, dict):
            raise VkApiError("groups.getLongPollServer returned no server", method="groups.getLongPollServer")

        server = response.get("server")
        key = response.get("key")
        ts = response.get("ts")
        if not server or not key:
            raise VkApiError(
                "groups.getLongPollServer returned no server/key", method="groups.getLongPollServer"
            )
        return {"server": str(server), "key": str(key), "ts": str(ts) if ts is not None else ""}

    async def get_longpoll_settings(self) -> Dict[str, Any]:
        """Read the community's Long Poll event flags.

        Returns the raw settings object: ``{"api_version", "events",
        "is_enabled"}``, where ``events`` maps an event name to
        ``{"in", "out", "enabled"}``.

        This is a read. Enabling an event flag would mean calling
        ``groups.setLongPollSettings``, which changes a persistent setting
        on the community — not something this client does on its own.
        """
        response = await self.api_call(
            "groups.getLongPollSettings", {"group_id": self.group_id}
        )
        return response if isinstance(response, dict) else {}

    async def poll(
        self, server: str, key: str, ts: str, *, wait: int = LONGPOLL_DEFAULT_WAIT
    ) -> Dict[str, Any]:
        """Poll the Long Poll server for new events.

        The poll URL carries no access token — only the longpoll key — so
        this call is always direct, even when method calls go through the
        proxy.

        Returns the raw payload: ``{"ts": ..., "updates": [...]}`` on
        success, or ``{"failed": 1|2, "ts": ...}`` when VK wants us to
        re-fetch the longpoll server.
        """
        wait = max(LONGPOLL_MIN_WAIT, min(LONGPOLL_MAX_WAIT, int(wait)))
        # VK returns `server` as a bare host+path ("lp.vk.com/wh228928227"),
        # with no scheme, and the longpoll key is a QUERY parameter — the
        # request is `{server}?act=a_check&key={key}&ts={ts}&wait={wait}`.
        # Appending the key to the path (and skipping the scheme) builds a
        # URL httpx cannot dispatch, so the poll fails on every cycle.
        base = server.rstrip("/")
        if not base.startswith(("http://", "https://")):
            base = f"https://{base}"
        url = base
        params = {
            "act": "a_check",
            "key": key,
            "ts": ts or "",
            "wait": wait,
            "lp_version": LONGPOLL_VERSION,
        }

        try:
            response = await self._client().get(
                url,
                params=params,
                timeout=httpx.Timeout(self.timeout, read=wait + 15.0),
            )
        except httpx.TimeoutException as e:
            raise VkApiError(f"Long Poll timed out: {e}", method="longpoll") from e
        except httpx.HTTPError as e:
            raise VkApiError(f"Long Poll request failed: {e}", method="longpoll") from e

        if response.status_code in (401, 403):
            # The longpoll key was rotated or revoked — the caller re-fetches
            # the server instead of treating this as a hard failure.
            raise VkApiError(
                f"Long Poll rejected (HTTP {response.status_code})", method="longpoll"
            )
        if response.status_code >= 300:
            raise VkApiError(
                f"Long Poll HTTP {response.status_code}", method="longpoll"
            )

        try:
            payload = response.json()
        except ValueError as e:
            raise VkApiError("Long Poll returned non-JSON", method="longpoll") from e

        if not isinstance(payload, dict):
            raise VkApiError("Long Poll returned an unexpected payload", method="longpoll")
        return payload

    async def poll_via_proxy(
        self, server: str, key: str, ts: str, *, wait: int = LONGPOLL_DEFAULT_WAIT
    ) -> Dict[str, Any]:
        """Poll the Long Poll through the proxy instead of directly.

        Every other VK call this stack makes goes through the proxy, so
        that the egress IP is a single static one. The Long Poll was the
        odd one out — it went straight to VK's Long Poll CDN from whatever
        host the agent runs on, and from there it times out before VK
        answers. Relaying it through the proxy puts it on the same footing
        as the rest of the VK traffic.

        The Long Poll request carries no access token — only the
        longpoll key — so relaying it does not expose the community
        token.
        """
        wait = max(LONGPOLL_MIN_WAIT, min(LONGPOLL_MAX_WAIT, int(wait)))
        url = f"{self.proxy_url}/vk/longpoll"
        body = {"server": server, "key": key, "ts": ts, "wait": wait}

        try:
            response = await self._client().post(
                url,
                json=body,
                headers=self._proxy_headers(),
                # The proxy holds the request open for `wait` seconds, so
                # the read timeout has to clear that plus the proxy's own
                # overhead.
                timeout=httpx.Timeout(self.timeout, read=wait + 30.0),
            )
        except httpx.TimeoutException as e:
            raise VkApiError(f"Long Poll via proxy timed out: {e}", method="longpoll") from e
        except httpx.HTTPError as e:
            raise VkApiError(f"Long Poll via proxy failed: {e}", method="longpoll") from e

        try:
            payload = response.json()
        except ValueError as e:
            raise VkApiError("Long Poll proxy returned non-JSON", method="longpoll") from e

        if not isinstance(payload, dict):
            raise VkApiError("Long Poll proxy returned an unexpected payload", method="longpoll")
        if not payload.get("success"):
            raise VkApiError(
                f"Long Poll proxy error: {payload.get('error') or 'unknown error'}",
                method="longpoll",
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise VkApiError("Long Poll proxy returned no payload", method="longpoll")
        return data

    # -- Messages -----------------------------------------------------------

    async def send_message(
        self, peer_id: int, text: str, *, random_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Send a text message to ``peer_id``.

        ``peer_id`` is the conversation the message came from: a personal
        dialog is ``2000000001 + user_id``, a community conversation is the
        conversation id itself.
        """
        response = await self.api_call(
            "messages.send",
            {
                "peer_id": peer_id,
                "message": text,
                "random_id": random_id if random_id is not None else _random_id(),
            },
        )
        return response if isinstance(response, dict) else {}

    async def get_user(self, user_id: int) -> Dict[str, Any]:
        """Fetch a user's public profile (best effort — name lookup only)."""
        response = await self.api_call("users.get", {"user_ids": user_id})
        if isinstance(response, list) and response:
            return response[0] if isinstance(response[0], dict) else {}
        if isinstance(response, dict):
            return response
        return {}

    async def get_group(self, group_id: int) -> Dict[str, Any]:
        """Fetch the community's profile (best effort — name lookup only)."""
        response = await self.api_call("groups.getById", {"group_id": group_id})
        if isinstance(response, list) and response:
            return response[0] if isinstance(response[0], dict) else {}
        if isinstance(response, dict):
            return response
        return {}
