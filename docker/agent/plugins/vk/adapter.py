"""VK (VKontakte) platform adapter (Hermes plugin).

Receives messages sent to a VK community through the **Long Poll API** and
replies via ``messages.send``.

Why Long Poll and not Callback API: the Hermes agent container has no
public URL — it is reached only over the internal Docker network as
``http://hermes-agent:8642``. Callback API would require exposing a path
through Traefik plus a public confirmation-secret handshake. Long Poll
needs nothing public: the adapter asks VK for a longpoll server + key and
then polls it. VK delivers the community's message events over that poll.

The plugin ships under ``plugins/platforms/vk/`` inside the agent image.
The Hermes plugin loader scans that directory at startup, calls
:func:`register`, and the platform becomes available to ``gateway/run.py``
and ``tools/send_message_tool`` through the registry — no edits to core
files required.

Configuration is env-driven (:func:`_env_enablement`), so a Coolify
deployment needs nothing but environment variables:

    VK_GROUP_TOKEN        community access token with the 'messages' scope
    VK_GROUP_ID           numeric community id
    VK_ALLOWED_USERS      comma-separated VK user ids (unset = nobody)
    VK_HOME_CHANNEL       default peer id for cron / notification delivery
    VK_API_MODE           'proxy' (default) or 'direct'
    VK_PROXY_URL          base URL of the VK API proxy
    VK_PROXY_API_KEY      bearer key for the VK API proxy
    VK_LONGPOLL_WAIT      Long Poll wait seconds (default 25)

Identity model: VK's ``from_id`` is the numeric id of the user who wrote
the message and is assigned by VK itself — it is not publisher-controlled
the way ntfy's ``title`` is. ``VK_ALLOWED_USERS`` is therefore a real trust
boundary, and the platform fails closed when it is unset.
"""

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover — httpx ships with Hermes
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

from .vk_api import (
    DEFAULT_PROXY_URL,
    FATAL_ERROR_CODES,
    LONGPOLL_DEFAULT_WAIT,
    VkApi,
    VkApiError,
)

logger = logging.getLogger(__name__)

# VK's messages.send accepts at most 4096 characters of text.
MAX_MESSAGE_LENGTH = 4096

# VK reassigns the conversation message id on every edit, so dedup on the
# message id alone would re-deliver an edited message. The pair
# (conversation_message_id, date) is stable across edits and unique per
# message.
DEDUP_MAX_SIZE = 1000

# Backoff between failed Long Poll attempts (seconds).
RECONNECT_BACKOFF = [2, 5, 10, 30, 60]

# A personal dialog between a user and a community is addressed by
# ``2000000001 + user_id``.
PEER_ID_USER_OFFSET = 2000000001

# Long Poll update type for a new message (lp_version 2).
MESSAGE_NEW_UPDATE = 4


def _parse_wait(value: Any) -> int:
    """Parse ``VK_LONGPOLL_WAIT`` into a sane Long Poll wait in seconds.

    Accepts a string (straight from the environment) or an int (once the
    config seed has already parsed it) — ``_env_enablement()`` seeds
    ``longpoll_wait`` as an int, and that seed is what comes back as
    ``config.extra`` when the adapter is constructed. Both must work.
    """
    try:
        wait = int(str(value).strip())
    except (TypeError, ValueError):
        return LONGPOLL_DEFAULT_WAIT
    if wait < 10:
        return 10
    if wait > 90:
        return 90
    return wait


def check_requirements() -> bool:
    """Check whether the VK adapter is installable and minimally configured.

    Reads the env vars directly to avoid the cost of a full
    ``load_gateway_config()`` on every pre-flight check.
    """
    if not HTTPX_AVAILABLE:
        return False
    token = os.getenv("VK_GROUP_TOKEN", "").strip()
    group_id = os.getenv("VK_GROUP_ID", "").strip()
    return bool(token and group_id)


def validate_config(config) -> bool:
    """Validate that the configured VK platform has a token and a group id."""
    extra = getattr(config, "extra", {}) or {}
    token = extra.get("token") or os.getenv("VK_GROUP_TOKEN", "")
    group_id = extra.get("group_id") or os.getenv("VK_GROUP_ID", "")
    return bool(str(token).strip() and str(group_id).strip())


def is_connected(config) -> bool:
    """Check whether VK is configured (env or config.yaml)."""
    return validate_config(config)


class VKAdapter(BasePlatformAdapter):
    """VK community-messages adapter.

    Polls the community's Long Poll endpoint for message events and
    replies through ``messages.send``.
    """

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        platform = Platform("vk")
        super().__init__(config=config, platform=platform)

        extra = config.extra or {}
        self._token: str = str(extra.get("token") or os.getenv("VK_GROUP_TOKEN", "")).strip()
        self._group_id: str = str(extra.get("group_id") or os.getenv("VK_GROUP_ID", "")).strip()
        self._api_mode: str = str(
            extra.get("api_mode") or os.getenv("VK_API_MODE", "proxy")
        ).strip().lower()
        self._proxy_url: str = str(
            extra.get("proxy_url") or os.getenv("VK_PROXY_URL", DEFAULT_PROXY_URL)
        ).strip().rstrip("/")
        self._proxy_api_key: str = str(
            extra.get("proxy_api_key") or os.getenv("VK_PROXY_API_KEY", "")
        ).strip()
        self._poll_wait: int = _parse_wait(
            extra.get("longpoll_wait") or os.getenv("VK_LONGPOLL_WAIT")
        )
        # How the Long Poll is reached: "direct", "proxy", or "auto".
        # "auto" starts direct and falls back to the proxy the first time
        # a direct poll fails — the proxy is the egress the rest of the
        # VK traffic uses, so it is the reliable path.
        self._longpoll_mode: str = str(
            extra.get("longpoll_mode") or os.getenv("VK_LONGPOLL_MODE", "auto")
        ).strip().lower()
        if self._longpoll_mode not in ("direct", "proxy", "auto"):
            self._longpoll_mode = "auto"

        self._api: Optional[VkApi] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._http_client: Optional["httpx.AsyncClient"] = None

        # Deduplication: message key -> timestamp.
        self._seen_messages: Dict[str, float] = {}

        # user id -> display name. VK's Long Poll events carry no user
        # profile, so the name has to be fetched — but only once per user,
        # not once per message.
        self._user_names: Dict[str, str] = {}

        # Community display name, fetched lazily and cached.
        self._group_name: str = ""

    # -- Connection lifecycle -----------------------------------------------

    async def connect(self) -> bool:
        """Connect to VK by starting the Long Poll task."""
        if not HTTPX_AVAILABLE:
            logger.warning("[%s] httpx not installed — cannot poll VK", self.name)
            return False
        if not self._token or not self._group_id:
            logger.warning(
                "[%s] VK_GROUP_TOKEN / VK_GROUP_ID not configured", self.name
            )
            return False

        try:
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, read=45.0))
            self._api = VkApi(
                self._token,
                group_id=self._group_id,
                api_mode=self._api_mode,
                proxy_url=self._proxy_url,
                proxy_api_key=self._proxy_api_key,
                http_client=self._http_client,
            )
            self._poll_task = asyncio.create_task(self._run_poll())
            self._mark_connected()
            logger.info(
                "[%s] Connected — polling community %s via %s (longpoll: %s)",
                self.name, self._group_id, self._api_mode, self._longpoll_mode,
            )
            return True
        except Exception as e:
            logger.error("[%s] Failed to connect: %s", self.name, e)
            return False

    async def disconnect(self) -> None:
        """Stop polling and release the HTTP client."""
        self._running = False
        self._mark_disconnected()

        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        if self._api:
            await self._api.aclose()
            self._api = None

        self._seen_messages.clear()
        self._user_names.clear()
        self._group_name = ""
        logger.info("[%s] Disconnected", self.name)

    # -- Long Poll loop -----------------------------------------------------

    async def _run_poll(self) -> None:
        """Poll the VK Long Poll endpoint until the adapter is disconnected.

        The poll itself blocks for up to ``VK_LONGPOLL_WAIT`` seconds — VK
        holds the request open until an event arrives — so a successful
        cycle re-arms immediately. Only failures back off.
        """
        backoff_idx = 0
        server_info: Optional[Dict[str, str]] = None

        while self._running:
            try:
                if server_info is None:
                    server_info = await self._api.get_longpoll_server()
                    await self._ensure_message_events()
                    logger.info("[%s] Long Poll server acquired", self.name)
                    # NOTE: no backoff reset here. Resetting on every
                    # server fetch meant a poll that always failed re-armed
                    # at the first backoff step forever — a permanent
                    # 2-second loop hammering groups.getLongPollServer.
                    # The reset belongs on a *successful poll*, below.

                payload = await self._poll_once(
                    server_info["server"],
                    server_info["key"],
                    server_info["ts"],
                )

                ts = payload.get("ts")
                if ts is not None:
                    server_info["ts"] = str(ts)

                if "failed" in payload:
                    # failed=1 → the longpoll key expired; failed=2 → the
                    # event history was lost. Both are fixed by asking VK
                    # for a fresh server + key. Fall through to the shared
                    # backoff below so a server that keeps rejecting the
                    # poll cannot spin this loop.
                    logger.info(
                        "[%s] Long Poll reported failed=%s — refetching server",
                        self.name, payload.get("failed"),
                    )
                    server_info = None
                else:
                    for update in payload.get("updates") or []:
                        if not self._running:
                            return
                        await self._handle_update(update)

                    # Success — re-arm the poll straight away.
                    backoff_idx = 0
                    continue
            except asyncio.CancelledError:
                return
            except VkApiError as e:
                if not self._running:
                    return
                if e.is_fatal:
                    logger.error(
                        "[%s] Fatal VK error: %s — stopping the poll loop. "
                        "Check VK_GROUP_TOKEN (a token IP-locked to another "
                        "host must be used with VK_API_MODE=proxy).",
                        self.name, e,
                    )
                    self._set_fatal_error(
                        "vk_api_fatal", str(e), retryable=False,
                    )
                    self._running = False
                    return
                if e.method == "longpoll":
                    # 401/403 or a transport error on the poll itself: the
                    # key may have rotated. Refetch the server once, then
                    # fall back to backoff.
                    server_info = None
                logger.warning("[%s] Poll error (%s mode): %s", self.name, self._longpoll_mode, e)
            except Exception as e:  # pragma: no cover — defensive
                if not self._running:
                    return
                logger.warning("[%s] Unexpected poll error: %s", self.name, e)

            if not self._running:
                return

            delay = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
            await asyncio.sleep(delay)
            backoff_idx += 1

    async def _poll_once(self, server: str, key: str, ts: str) -> Dict[str, Any]:
        """Run one Long Poll request, over the proxy if that is the mode.

        In "auto" mode the first direct failure switches the adapter to
        the proxy for the rest of the session: the proxy is the egress
        every other VK call in this stack uses, and the direct path to
        VK's Long Poll CDN is not reachable from every host the agent
        runs on.
        """
        if self._longpoll_mode == "proxy":
            return await self._api.poll_via_proxy(server, key, ts, wait=self._poll_wait)

        try:
            return await self._api.poll(server, key, ts, wait=self._poll_wait)
        except VkApiError as e:
            if self._longpoll_mode != "auto" or e.is_fatal or e.method != "longpoll":
                raise
            logger.warning(
                "[%s] Direct Long Poll failed (%s) — switching to the proxy",
                self.name, e,
            )
            self._longpoll_mode = "proxy"
            return await self._api.poll_via_proxy(server, key, ts, wait=self._poll_wait)

    async def _ensure_message_events(self) -> None:
        """Check that VK is actually configured to deliver new messages.

        Enabling Long Poll in the community's admin UI is only half the
        job: the *events* the community wants delivered live in a separate
        set of flags, and they are all off until
        ``groups.setLongPollSettings`` turns them on. A Long Poll with no
        event flags acquires a server, holds the connection open, and
        delivers nothing at all — indistinguishable from a quiet chat.

        This only *reads* the flags. Changing a community's persistent
        settings is not something an adapter should do behind the
        operator's back, so a disabled flag is reported at WARNING level
        (visible in the agent log) with the exact place to fix it.
        """
        try:
            settings = await self._api.get_longpoll_settings()
        except Exception as e:
            # Not fatal — the poll may still work if the flags are already
            # on and it was this call that failed.
            logger.warning("[%s] Could not read Long Poll settings: %s", self.name, e)
            return

        events = settings.get("events") or {}
        message_new = events.get("message_new") or {}
        if int(message_new.get("enabled") or 0) == 1:
            logger.info("[%s] Long Poll message_new already enabled", self.name)
            return

        logger.warning(
            "[%s] Long Poll message_new is DISABLED — VK would deliver no "
            "messages. Enable it in the community's VK admin: "
            "Управление → Сообщения → Long Poll API → tick 'Сообщения' → save.",
            self.name,
        )

    async def _handle_update(self, update: Dict[str, Any]) -> None:
        """Dispatch a single Long Poll update."""
        if not isinstance(update, dict):
            return
        if update.get("type") != MESSAGE_NEW_UPDATE:
            return

        message = update.get("object")
        if not isinstance(message, dict):
            return

        await self._on_message(message)

    async def _on_message(self, message: Dict[str, Any]) -> None:
        """Turn a VK message object into a gateway message event."""
        from_id = message.get("from_id")
        peer_id = message.get("peer_id")
        if from_id is None or peer_id is None:
            return

        # VK system events carry from_id 0; the community's own messages
        # carry the community id (negative). Both must not be answered, or
        # the bot would reply to itself in a loop.
        if int(from_id) == 0:
            return
        if self._group_id and int(from_id) == -abs(int(self._group_id)):
            return

        text = (message.get("text") or "").strip()
        if not text:
            # Attachments-only messages (photos, stickers) have no text to
            # hand to the agent.
            return

        dedup_key = self._dedup_key(message)
        if self._is_duplicate(dedup_key):
            logger.debug("[%s] Duplicate message %s, skipping", self.name, dedup_key)
            return

        user_id = str(from_id)
        chat_id = str(peer_id)
        # A personal dialog with the community is addressed as
        # 2000000001 + user_id; anything else is a group conversation.
        chat_type = "dm" if int(peer_id) == PEER_ID_USER_OFFSET + int(from_id) else "group"

        source = self.build_source(
            chat_id=chat_id,
            chat_name=await self._resolve_chat_name(chat_type),
            chat_type=chat_type,
            user_id=user_id,
            user_name=await self._resolve_user_name(user_id),
        )

        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            message_id=str(message.get("id") or ""),
            raw_message=message,
        )

        logger.debug("[%s] Message from %s: %s", self.name, user_id, text[:80])
        await self.handle_message(event)

    async def _resolve_chat_name(self, chat_type: str) -> str:
        """Display name for the chat.

        Both a personal dialog and a group conversation belong to the
        community, so both are named after it. The community name is
        fetched once and cached — a failure falls back to the id rather
        than blocking the message.
        """
        return await self._resolve_group_name() or f"VK {self._group_id}"

    async def _resolve_group_name(self) -> str:
        """Best-effort community display name, fetched at most once."""
        if self._group_name:
            return self._group_name
        if not self._api or not self._group_id:
            return ""
        try:
            profile = await self._api.get_group(int(self._group_id))
        except Exception:
            return ""
        name = str(profile.get("name") or "").strip()
        if not name:
            return ""
        self._group_name = name
        return name

    async def _resolve_user_name(self, user_id: str) -> str:
        """Best-effort display name for a VK user.

        Cached per user id: VK's Long Poll events carry no profile, so the
        name has to be fetched, but only the first time we see that user.

        A failed lookup must never block the message — the id alone is
        enough for session keying and authorization.
        """
        cached = self._user_names.get(user_id)
        if cached:
            return cached

        if not self._api:
            return user_id
        try:
            profile = await self._api.get_user(int(user_id))
        except Exception:
            return user_id
        if not profile:
            return user_id
        name = " ".join(
            str(profile.get(field) or "").strip()
            for field in ("first_name", "last_name")
        ).strip()
        if not name:
            return user_id
        self._user_names[user_id] = name
        return name

    # -- Deduplication ------------------------------------------------------

    def _dedup_key(self, message: Dict[str, Any]) -> str:
        """Build the dedup key for a VK message.

        ``conversation_message_id`` is stable across edits (a plain
        message id is not), and ``date`` distinguishes two messages that
        reused the same counter.
        """
        conversation_id = message.get("conversation_message_id")
        message_id = message.get("id")
        date = message.get("date")
        if conversation_id is not None:
            return f"c{conversation_id}:{date}"
        return f"m{message_id}:{date}"

    def _is_duplicate(self, dedup_key: str) -> bool:
        """Return True if this message was already handled."""
        now = time.time()
        if len(self._seen_messages) > DEDUP_MAX_SIZE:
            self._seen_messages = {
                key: stamp for key, stamp in self._seen_messages.items()
                if now - stamp < 3600
            }

        if dedup_key in self._seen_messages:
            return True
        self._seen_messages[dedup_key] = now
        return False

    # -- Outbound messaging -------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a text message to a VK peer."""
        if not self._api:
            return SendResult(success=False, error="VK client not initialized")

        body = self.format_message(content or "")
        if len(body) > MAX_MESSAGE_LENGTH:
            logger.warning(
                "[%s] Message truncated from %d to %d chars (VK limit)",
                self.name, len(body), MAX_MESSAGE_LENGTH,
            )
            body = body[:MAX_MESSAGE_LENGTH]

        try:
            peer_id = int(chat_id)
        except (TypeError, ValueError):
            return SendResult(success=False, error=f"Invalid VK peer id: {chat_id!r}")

        try:
            response = await self._api.send_message(peer_id, body)
        except VkApiError as e:
            logger.warning("[%s] Send failed: %s", self.name, e)
            return SendResult(
                success=False,
                error=str(e),
                retryable=e.is_rate_limited,
            )
        except Exception as e:
            logger.error("[%s] Send error: %s", self.name, e)
            return SendResult(success=False, error=str(e))

        message_id = response.get("message_id") if isinstance(response, dict) else None
        return SendResult(
            success=True,
            message_id=str(message_id) if message_id is not None else None,
            raw_response=response,
        )

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """VK has no bot typing indicator for community messages."""
        return None

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return basic info about a VK peer."""
        try:
            peer_id = int(chat_id)
        except (TypeError, ValueError):
            peer_id = 0
        return {
            "name": f"VK {chat_id}",
            "type": "dm" if peer_id > PEER_ID_USER_OFFSET else "group",
            "chat_id": str(chat_id),
        }

    def format_message(self, content: str) -> str:
        """Strip Markdown — VK renders plain text only."""
        return _strip_markdown(content)


def _strip_markdown(text: str) -> str:
    """Remove Markdown emphasis that VK would render literally.

    Only the emphasis constructs are touched: bold, italic, strikethrough,
    inline code and headings. Links keep their URL in parentheses so the
    recipient can still read it, and code fences are left intact.
    """
    if not text:
        return text

    import re

    # Fenced code blocks are left alone.
    parts = text.split("```")
    for index in range(0, len(parts), 2):
        parts[index] = re.sub(r"\*\*(.+?)\*\*", r"\1", parts[index])
        parts[index] = re.sub(r"__(.+?)__", r"\1", parts[index])
        parts[index] = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", parts[index])
        parts[index] = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", parts[index])
        parts[index] = re.sub(r"~~(.+?)~~", r"\1", parts[index])
        parts[index] = re.sub(r"`([^`\n]+)`", r"\1", parts[index])
        parts[index] = re.sub(r"^#{1,6}\s+", "", parts[index], flags=re.MULTILINE)
        parts[index] = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", parts[index])
    return "```".join(parts)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def _env_enablement() -> Optional[dict]:
    """Seed ``PlatformConfig.extra`` from env vars during gateway config load.

    Called by the platform registry's env-enablement hook BEFORE adapter
    construction, so ``hermes gateway status`` and
    ``get_connected_platforms()`` reflect env-only configuration without
    instantiating the adapter. Returns ``None`` when VK is not minimally
    configured; the caller then skips auto-enabling.

    The special ``home_channel`` key in the returned dict is handled by
    the core hook — it becomes a proper ``HomeChannel`` dataclass on the
    ``PlatformConfig`` rather than being merged into ``extra``.
    """
    token = os.getenv("VK_GROUP_TOKEN", "").strip()
    group_id = os.getenv("VK_GROUP_ID", "").strip()
    if not (token and group_id):
        return None

    seed: dict = {
        "token": token,
        "group_id": group_id,
        "api_mode": os.getenv("VK_API_MODE", "proxy").strip().lower() or "proxy",
        "proxy_url": os.getenv("VK_PROXY_URL", DEFAULT_PROXY_URL).strip().rstrip("/"),
    }
    proxy_key = os.getenv("VK_PROXY_API_KEY", "").strip()
    if proxy_key:
        seed["proxy_api_key"] = proxy_key
    wait = _parse_wait(os.getenv("VK_LONGPOLL_WAIT"))
    if wait:
        seed["longpoll_wait"] = wait

    home = os.getenv("VK_HOME_CHANNEL", "").strip()
    if home:
        seed["home_channel"] = {
            "chat_id": home,
            "name": os.getenv("VK_HOME_CHANNEL_NAME", "").strip() or "VK",
        }
    return seed


async def _standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    """Out-of-process send for cron jobs and ``send_message`` fallbacks.

    Used by ``tools/send_message_tool._send_via_adapter`` and the cron
    scheduler when the gateway runner is not in this process (e.g.
    ``hermes cron`` running standalone). Without this hook,
    ``deliver=vk`` cron jobs fail with ``No live adapter for platform``.

    Only a text send is implemented — VK attachments are out of scope for
    the standalone path.
    """
    if not HTTPX_AVAILABLE:
        return {"error": "vk standalone send: httpx not installed"}

    extra = getattr(pconfig, "extra", {}) or {}
    token = str(extra.get("token") or os.getenv("VK_GROUP_TOKEN", "")).strip()
    group_id = str(extra.get("group_id") or os.getenv("VK_GROUP_ID", "")).strip()
    if not (token and group_id):
        return {"error": "vk standalone send: VK_GROUP_TOKEN / VK_GROUP_ID not configured"}

    peer_id = chat_id or str(extra.get("home_channel_id") or "").strip()
    if not peer_id:
        peer_id = os.getenv("VK_HOME_CHANNEL", "").strip()
    if not peer_id:
        return {"error": "vk standalone send: no target peer id"}

    body = _strip_markdown(message or "")
    if len(body) > MAX_MESSAGE_LENGTH:
        body = body[:MAX_MESSAGE_LENGTH]

    api = VkApi(
        token,
        group_id=group_id,
        api_mode=str(extra.get("api_mode") or os.getenv("VK_API_MODE", "proxy")).strip().lower(),
        proxy_url=str(
            extra.get("proxy_url") or os.getenv("VK_PROXY_URL", DEFAULT_PROXY_URL)
        ).strip().rstrip("/"),
        proxy_api_key=str(
            extra.get("proxy_api_key") or os.getenv("VK_PROXY_API_KEY", "")
        ).strip(),
    )
    try:
        response = await api.send_message(int(peer_id), body)
    except VkApiError as e:
        return {"error": f"vk standalone send failed: {e}"}
    except Exception as e:
        return {"error": f"vk standalone send failed: {e}"}
    finally:
        await api.aclose()

    message_id = response.get("message_id") if isinstance(response, dict) else None
    return {
        "success": True,
        "platform": "vk",
        "chat_id": str(peer_id),
        "message_id": str(message_id) if message_id is not None else None,
    }


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system at startup."""
    ctx.register_platform(
        name="vk",
        label="VK",
        adapter_factory=lambda cfg: VKAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["VK_GROUP_TOKEN", "VK_GROUP_ID"],
        install_hint="pip install httpx   # already a Hermes dependency",
        # Env-driven auto-configuration: seeds PlatformConfig.extra so
        # env-only setups show up in `hermes gateway status` without
        # instantiating the HTTP client.
        env_enablement_fn=_env_enablement,
        # Cron home-channel delivery support — `deliver=vk` cron jobs
        # route to VK_HOME_CHANNEL when set.
        cron_deliver_env_var="VK_HOME_CHANNEL",
        # Out-of-process cron delivery. Without this hook, deliver=vk
        # cron jobs fail with "No live adapter" when cron runs separately
        # from the gateway.
        standalone_sender_fn=_standalone_send,
        # Auth env vars for _is_user_authorized() integration. VK's
        # from_id is assigned by VK, so this is a real trust boundary.
        allowed_users_env="VK_ALLOWED_USERS",
        allow_all_env="VK_ALLOW_ALL_USERS",
        max_message_length=MAX_MESSAGE_LENGTH,
        emoji="🔵",
        # VK ids are public identifiers — no phone numbers or emails to
        # redact out of session descriptions.
        pii_safe=True,
        allow_update_command=True,
        platform_hint=(
            "You are communicating via VK (VKontakte) community messages. "
            "VK renders plain text only — do not use Markdown formatting, "
            "tables or code fences; write short, direct replies. "
            "Messages arrive in a community conversation; each user has "
            "their own session."
        ),
    )
