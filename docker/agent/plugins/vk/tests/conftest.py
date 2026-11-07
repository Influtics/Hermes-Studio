"""Test harness for the VK platform plugin.

The plugin imports two modules from the Hermes agent package:

    from gateway.config import Platform, PlatformConfig
    from gateway.platforms.base import (
        BasePlatformAdapter, MessageEvent, MessageType, SendResult,
    )

Hermes is not a dependency of this repo — the plugin is *copied into* the
agent image at ``/app/plugins/platforms/vk`` by ``docker/agent/Dockerfile``.
So the tests stub those two modules with the smallest faithful version of
the real API surface, and exercise the plugin against that.

The stubs mirror the upstream contracts exactly:

* ``Platform`` is an enum-like singleton factory: ``Platform("vk")``
  returns the same object every time and exposes ``.value``.
* ``PlatformConfig`` carries ``enabled`` / ``token`` / ``extra``.
* ``BasePlatformAdapter`` provides ``__init__(config, platform)``,
  ``build_source(...)``, ``handle_message(event)``,
  ``_mark_connected()`` / ``_mark_disconnected()`` / ``_set_fatal_error()``
  and the ``name`` property.

If the upstream contract changes, these stubs must change with it — the
plugin is exercised against the stub, not against a real Hermes install.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "vk"


# ---------------------------------------------------------------------------
# Stub: gateway.config
# ---------------------------------------------------------------------------


class Platform(str, Enum):
    """Minimal stand-in for ``gateway.config.Platform``.

    The real implementation is an ``Enum`` whose ``_missing_`` creates
    pseudo-members for plugin platforms. Only ``Platform("vk")`` and
    ``.value`` are needed here.
    """

    LOCAL = "local"
    VK = "vk"


@dataclass
class PlatformConfig:
    """Minimal stand-in for ``gateway.config.PlatformConfig``."""

    enabled: bool = False
    token: Optional[str] = None
    api_key: Optional[str] = None
    home_channel: Optional[Any] = None
    reply_to_mode: str = "first"
    gateway_restart_notification: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stub: gateway.platforms.base
# ---------------------------------------------------------------------------


class MessageType(Enum):
    TEXT = "text"
    LOCATION = "location"
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    DOCUMENT = "document"
    STICKER = "sticker"
    COMMAND = "command"


@dataclass
class MessageEvent:
    text: str
    message_type: MessageType = MessageType.TEXT
    source: Any = None
    raw_message: Any = None
    message_id: Optional[str] = None
    media_urls: List[str] = field(default_factory=list)
    media_types: List[str] = field(default_factory=list)
    reply_to_message_id: Optional[str] = None
    reply_to_text: Optional[str] = None
    internal: bool = False


@dataclass
class SendResult:
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    raw_response: Any = None
    retryable: bool = False
    continuation_message_ids: tuple = ()


@dataclass
class SessionSource:
    platform: Any = None
    chat_id: str = ""
    chat_name: Optional[str] = None
    chat_type: str = "dm"
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    thread_id: Optional[str] = None
    chat_topic: Optional[str] = None
    user_id_alt: Optional[str] = None
    chat_id_alt: Optional[str] = None
    is_bot: bool = False
    guild_id: Optional[str] = None
    parent_chat_id: Optional[str] = None
    message_id: Optional[str] = None


class BasePlatformAdapter:
    """Minimal stand-in for ``gateway.platforms.base.BasePlatformAdapter``."""

    def __init__(self, config: PlatformConfig, platform: Platform):
        self.config = config
        self.platform = platform
        self._running = False
        self._fatal_error_code: Optional[str] = None
        self._fatal_error_message: Optional[str] = None
        self._fatal_error_retryable = True
        # Test-only record of the events the adapter dispatched.
        self.handled_events: List[MessageEvent] = []

    @property
    def name(self) -> str:
        return self.platform.value.title()

    def build_source(
        self,
        chat_id: str,
        chat_name: Optional[str] = None,
        chat_type: str = "dm",
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        thread_id: Optional[str] = None,
        chat_topic: Optional[str] = None,
        user_id_alt: Optional[str] = None,
        chat_id_alt: Optional[str] = None,
        is_bot: bool = False,
        guild_id: Optional[str] = None,
        parent_chat_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> SessionSource:
        return SessionSource(
            platform=self.platform,
            chat_id=str(chat_id),
            chat_name=chat_name,
            chat_type=chat_type,
            user_id=str(user_id) if user_id else None,
            user_name=user_name,
            thread_id=str(thread_id) if thread_id else None,
            chat_topic=chat_topic.strip() if chat_topic else None,
            user_id_alt=user_id_alt,
            chat_id_alt=chat_id_alt,
            is_bot=is_bot,
            guild_id=str(guild_id) if guild_id else None,
            parent_chat_id=str(parent_chat_id) if parent_chat_id else None,
            message_id=str(message_id) if message_id else None,
        )

    async def handle_message(self, event: MessageEvent) -> None:
        self.handled_events.append(event)

    def _mark_connected(self) -> None:
        self._running = True

    def _mark_disconnected(self) -> None:
        self._running = False

    def _set_fatal_error(self, code: str, message: str, *, retryable: bool) -> None:
        self._running = False
        self._fatal_error_code = code
        self._fatal_error_message = message
        self._fatal_error_retryable = retryable

    def format_message(self, content: str) -> str:
        return content


def _install_stub_modules() -> None:
    """Register the stub ``gateway.*`` modules in ``sys.modules``."""
    if "gateway.config" in sys.modules:
        return

    gateway_pkg = types.ModuleType("gateway")
    gateway_pkg.__path__ = []  # type: ignore[attr-defined]
    sys.modules["gateway"] = gateway_pkg

    config_mod = types.ModuleType("gateway.config")
    config_mod.Platform = Platform  # type: ignore[attr-defined]
    config_mod.PlatformConfig = PlatformConfig  # type: ignore[attr-defined]
    sys.modules["gateway.config"] = config_mod

    platforms_pkg = types.ModuleType("gateway.platforms")
    platforms_pkg.__path__ = []  # type: ignore[attr-defined]
    sys.modules["gateway.platforms"] = platforms_pkg

    base_mod = types.ModuleType("gateway.platforms.base")
    base_mod.BasePlatformAdapter = BasePlatformAdapter  # type: ignore[attr-defined]
    base_mod.MessageEvent = MessageEvent  # type: ignore[attr-defined]
    base_mod.MessageType = MessageType  # type: ignore[attr-defined]
    base_mod.SendResult = SendResult  # type: ignore[attr-defined]
    base_mod.SessionSource = SessionSource  # type: ignore[attr-defined]
    sys.modules["gateway.platforms.base"] = base_mod


_install_stub_modules()


def load_plugin_adapter() -> types.ModuleType:
    """Import the plugin's ``adapter.py`` the way Hermes loads it.

    Hermes loads a platform plugin as a package: ``__init__.py`` is loaded
    with ``submodule_search_locations`` pointing at the plugin directory,
    so relative imports (``from .vk_api import ...``) resolve. This helper
    reproduces that so the plugin is exercised exactly as it runs in the
    agent.

    Returns the ``adapter`` submodule — the module that actually holds the
    adapter class and the registration hooks.
    """
    module_name = f"hermes_plugins.{PLUGIN_NAME}"
    adapter_name = f"{module_name}.adapter"

    cached = sys.modules.get(adapter_name)
    if cached is not None:
        return cached

    namespace = "hermes_plugins"
    if namespace not in sys.modules:
        ns_pkg = types.ModuleType(namespace)
        ns_pkg.__path__ = []  # type: ignore[attr-defined]
        ns_pkg.__package__ = namespace
        sys.modules[namespace] = ns_pkg

    spec = importlib.util.spec_from_file_location(
        module_name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    # ``__init__.py`` does ``from .adapter import register``, so importing
    # the package has already executed adapter.py.
    adapter_module = sys.modules.get(adapter_name)
    if adapter_module is None:
        raise ImportError(f"Plugin adapter module not loaded: {adapter_name}")
    return adapter_module


@pytest.fixture
def vk():
    """The loaded plugin adapter module."""
    return load_plugin_adapter()


@pytest.fixture
def adapter_factory(vk):
    """Build a VKAdapter with the given config extra and env overrides."""
    def _make(extra: Optional[Dict[str, Any]] = None):
        config = PlatformConfig(enabled=True, extra=dict(extra or {}))
        return vk.VKAdapter(config)

    return _make
