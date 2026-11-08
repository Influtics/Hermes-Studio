"""Tests for the VK platform plugin.

Covers the three things that can break silently in a messaging adapter:

1. **The wire format** — what actually goes out to VK (method names,
   parameter names, peer ids). A wrong parameter name is rejected by VK
   at runtime, not at import time.
2. **The inbound mapping** — a VK message object must become a gateway
   event with the right user/chat identity, and must not be answered when
   it is our own message or a duplicate.
3. **The registration shape** — the hooks the Hermes plugin system reads
   off ``ctx.register_platform()`` (allowlist env vars, cron delivery,
   message limit). Getting one wrong disables a feature without an error.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import MessageType, Platform, PlatformConfig


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _mock_api(
    user: Optional[Dict[str, Any]] = None,
    group: Optional[Dict[str, Any]] = None,
    user_error: Optional[Exception] = None,
    group_error: Optional[Exception] = None,
):
    """A mocked VkApi with correctly-shaped user/group lookups."""
    api = MagicMock()
    if user_error is not None:
        api.get_user = AsyncMock(side_effect=user_error)
    else:
        api.get_user = AsyncMock(return_value=user or {})
    if group_error is not None:
        api.get_group = AsyncMock(side_effect=group_error)
    else:
        api.get_group = AsyncMock(return_value=group or {})
    return api


# ---------------------------------------------------------------------------
# 1. Requirements / validation
# ---------------------------------------------------------------------------


class TestRequirements:

    def test_false_without_httpx(self, vk, monkeypatch):
        monkeypatch.setenv("VK_GROUP_TOKEN", "tok")
        monkeypatch.setenv("VK_GROUP_ID", "243045540")
        monkeypatch.setattr(vk, "HTTPX_AVAILABLE", False)
        assert vk.check_requirements() is False

    def test_false_without_token(self, vk, monkeypatch):
        monkeypatch.setenv("VK_GROUP_TOKEN", "")
        monkeypatch.setenv("VK_GROUP_ID", "243045540")
        monkeypatch.setattr(vk, "HTTPX_AVAILABLE", True)
        assert vk.check_requirements() is False

    def test_false_without_group_id(self, vk, monkeypatch):
        monkeypatch.setenv("VK_GROUP_TOKEN", "tok")
        monkeypatch.setenv("VK_GROUP_ID", "")
        monkeypatch.setattr(vk, "HTTPX_AVAILABLE", True)
        assert vk.check_requirements() is False

    def test_true_with_both(self, vk, monkeypatch):
        monkeypatch.setenv("VK_GROUP_TOKEN", "tok")
        monkeypatch.setenv("VK_GROUP_ID", "243045540")
        monkeypatch.setattr(vk, "HTTPX_AVAILABLE", True)
        assert vk.check_requirements() is True

    def test_validate_config_reads_extra(self, vk, monkeypatch):
        monkeypatch.delenv("VK_GROUP_TOKEN", raising=False)
        monkeypatch.delenv("VK_GROUP_ID", raising=False)
        config = PlatformConfig(extra={"token": "t", "group_id": "1"})
        assert vk.validate_config(config) is True

    def test_validate_config_falls_back_to_env(self, vk, monkeypatch):
        monkeypatch.setenv("VK_GROUP_TOKEN", "tok")
        monkeypatch.setenv("VK_GROUP_ID", "1")
        assert vk.validate_config(PlatformConfig()) is True

    def test_validate_config_false_when_empty(self, vk, monkeypatch):
        monkeypatch.delenv("VK_GROUP_TOKEN", raising=False)
        monkeypatch.delenv("VK_GROUP_ID", raising=False)
        assert vk.validate_config(PlatformConfig()) is False


# ---------------------------------------------------------------------------
# 2. Env enablement
# ---------------------------------------------------------------------------


class TestEnvEnablement:

    def test_returns_none_without_token(self, vk, monkeypatch):
        monkeypatch.delenv("VK_GROUP_TOKEN", raising=False)
        monkeypatch.delenv("VK_GROUP_ID", raising=False)
        assert vk._env_enablement() is None

    def test_seeds_extra(self, vk, monkeypatch):
        monkeypatch.setenv("VK_GROUP_TOKEN", "tok")
        monkeypatch.setenv("VK_GROUP_ID", "243045540")
        monkeypatch.setenv("VK_API_MODE", "direct")
        monkeypatch.setenv("VK_PROXY_URL", "https://proxy.test")
        monkeypatch.setenv("VK_PROXY_API_KEY", "key")
        seed = vk._env_enablement()
        assert seed["token"] == "tok"
        assert seed["group_id"] == "243045540"
        assert seed["api_mode"] == "direct"
        assert seed["proxy_url"] == "https://proxy.test"
        assert seed["proxy_api_key"] == "key"

    def test_seeds_home_channel(self, vk, monkeypatch):
        monkeypatch.setenv("VK_GROUP_TOKEN", "tok")
        monkeypatch.setenv("VK_GROUP_ID", "1")
        monkeypatch.setenv("VK_HOME_CHANNEL", "2191655493")
        monkeypatch.setenv("VK_HOME_CHANNEL_NAME", "Ivan")
        seed = vk._env_enablement()
        assert seed["home_channel"] == {"chat_id": "2191655493", "name": "Ivan"}

    def test_no_home_channel_key_when_unset(self, vk, monkeypatch):
        monkeypatch.setenv("VK_GROUP_TOKEN", "tok")
        monkeypatch.setenv("VK_GROUP_ID", "1")
        monkeypatch.delenv("VK_HOME_CHANNEL", raising=False)
        seed = vk._env_enablement()
        assert "home_channel" not in seed


# ---------------------------------------------------------------------------
# 2b. The seed -> adapter round trip
# ---------------------------------------------------------------------------


class TestSeedRoundTrip:
    """`_env_enablement()` seeds the config the adapter is later built from.

    The seed is not a plain dict of strings: `longpoll_wait` is seeded as
    an int, and that seed comes back as `config.extra` when the gateway
    constructs the adapter. Every field therefore has to survive the trip
    in the type it was seeded in — a type mismatch here does not raise at
    seed time, it raises inside the adapter constructor and takes the whole
    platform down with "Failed to create adapter for platform 'VK'".
    """

    def test_seed_longpoll_wait_is_usable_by_the_adapter(self, vk, monkeypatch):
        monkeypatch.setenv("VK_GROUP_TOKEN", "tok")
        monkeypatch.setenv("VK_GROUP_ID", "243045540")
        monkeypatch.setenv("VK_LONGPOLL_WAIT", "30")
        seed = vk._env_enablement()
        assert isinstance(seed["longpoll_wait"], int)

        config = PlatformConfig(extra=seed)
        adapter = vk.VKAdapter(config)
        assert adapter._poll_wait == 30

    def test_parse_wait_accepts_int_and_str(self, vk):
        assert vk._parse_wait(25) == 25
        assert vk._parse_wait("25") == 25
        assert vk._parse_wait(" 30 ") == 30

    def test_parse_wait_clamps_and_defaults(self, vk):
        assert vk._parse_wait(5) == 10        # below the floor
        assert vk._parse_wait(900) == 90      # above the ceiling
        assert vk._parse_wait(None) == vk.LONGPOLL_DEFAULT_WAIT
        assert vk._parse_wait("") == vk.LONGPOLL_DEFAULT_WAIT
        assert vk._parse_wait("not-a-number") == vk.LONGPOLL_DEFAULT_WAIT

    def test_longpoll_settings_round_trip(self, vk, adapter_factory):
        """Reading the Long Poll event flags uses the right VK method and
        parameter names — and nothing writes the community's settings."""
        api = vk.VkApi(token="tok", group_id="243045540", api_mode="proxy")
        api.api_call = AsyncMock(return_value={"events": {"message_new": {}}})

        _run(api.get_longpoll_settings())
        method, params = api.api_call.await_args.args
        assert method == "groups.getLongPollSettings"
        assert params == {"group_id": "243045540"}


# ---------------------------------------------------------------------------
# 3. VkApi — the wire format
# ---------------------------------------------------------------------------


class TestVkApi:

    def _api(self, vk, **kwargs):
        defaults = dict(token="tok", group_id="243045540", api_mode="proxy")
        defaults.update(kwargs)
        return vk.VkApi(**defaults)

    def test_proxy_call_shape(self, vk):
        api = self._api(vk)
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"success": True, "data": {"peer_id": 1}}
        client.post = AsyncMock(return_value=response)
        api._http_client = client

        result = _run(api.api_call("messages.send", {"peer_id": 1, "message": "hi"}))

        assert result == {"peer_id": 1}
        url = client.post.await_args.args[0]
        assert url == "https://vk-proxy.influtics.com/vk/api-call"
        body = client.post.await_args.kwargs["json"]
        assert body["method"] == "messages.send"
        assert body["params"] == {"peer_id": 1, "message": "hi"}
        assert body["access_token"] == "tok"

    def test_proxy_call_sends_bearer(self, vk):
        api = self._api(vk, proxy_api_key="secret")
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"success": True, "data": {}}
        client.post = AsyncMock(return_value=response)
        api._http_client = client

        _run(api.api_call("users.get", {"user_ids": 5}))

        headers = client.post.await_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer secret"

    def test_proxy_error_raises_with_code(self, vk):
        api = self._api(vk)
        client = MagicMock()
        response = MagicMock()
        response.status_code = 400
        response.json.return_value = {
            "success": False,
            "error": "User authorization failed",
            "error_code": 5,
        }
        client.post = AsyncMock(return_value=response)
        api._http_client = client

        with pytest.raises(vk.VkApiError) as exc:
            _run(api.api_call("messages.send", {"peer_id": 1}))
        assert exc.value.error_code == 5
        assert exc.value.is_fatal is True

    def test_rate_limit_is_not_fatal(self, vk):
        api = self._api(vk)
        client = MagicMock()
        response = MagicMock()
        response.status_code = 400
        response.json.return_value = {
            "success": False, "error": "Too many requests", "error_code": 6,
        }
        client.post = AsyncMock(return_value=response)
        api._http_client = client

        with pytest.raises(vk.VkApiError) as exc:
            _run(api.api_call("messages.send", {"peer_id": 1}))
        assert exc.value.is_fatal is False
        assert exc.value.is_rate_limited is True

    def test_proxy_auth_rejection_is_fatal(self, vk):
        api = self._api(vk)
        client = MagicMock()
        response = MagicMock()
        response.status_code = 401
        response.json.return_value = {"success": False, "error": "Unauthorized"}
        client.post = AsyncMock(return_value=response)
        api._http_client = client

        with pytest.raises(vk.VkApiError) as exc:
            _run(api.api_call("messages.send", {"peer_id": 1}))
        assert exc.value.is_fatal is True

    def test_direct_call_shape(self, vk):
        api = self._api(vk, api_mode="direct")
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"response": {"message_id": 42}}
        client.get = AsyncMock(return_value=response)
        api._http_client = client

        result = _run(api.api_call("messages.send", {"peer_id": 1, "message": "hi"}))

        assert result == {"message_id": 42}
        url = client.get.await_args.args[0]
        assert url == "https://api.vk.com/method/messages.send"
        params = client.get.await_args.kwargs["params"]
        assert params["access_token"] == "tok"
        assert params["v"] == "5.199"
        assert params["peer_id"] == 1

    def test_direct_call_error(self, vk):
        api = self._api(vk, api_mode="direct")
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "error": {"error_code": 1130, "error_msg": "token was given to another ip address"}
        }
        client.get = AsyncMock(return_value=response)
        api._http_client = client

        with pytest.raises(vk.VkApiError) as exc:
            _run(api.api_call("messages.send", {"peer_id": 1}))
        assert exc.value.error_code == 1130
        assert exc.value.is_fatal is True

    def test_none_params_are_dropped(self, vk):
        api = self._api(vk)
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"success": True, "data": {}}
        client.post = AsyncMock(return_value=response)
        api._http_client = client

        _run(api.api_call("groups.getLongPollServer", {"group_id": "1", "x": None}))

        body = client.post.await_args.kwargs["json"]
        assert "x" not in body["params"]

    def test_longpoll_server_shape(self, vk):
        api = self._api(vk)
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "success": True,
            "data": {"server": "https://lp.vk.com/wh123", "key": "abc", "ts": "55"},
        }
        client.post = AsyncMock(return_value=response)
        api._http_client = client

        info = _run(api.get_longpoll_server())

        assert info == {"server": "https://lp.vk.com/wh123", "key": "abc", "ts": "55"}
        body = client.post.await_args.kwargs["json"]
        assert body["method"] == "groups.getLongPollServer"
        assert body["params"]["group_id"] == "243045540"
        assert body["params"]["lp_version"] == 2

    def test_poll_shape(self, vk):
        """The poll request must match VK's documented Long Poll contract.

        VK returns `server` as a bare host+path with NO scheme
        ("lp.vk.com/wh228928227"), and the longpoll key is a QUERY
        parameter — the request is
        `{server}?act=a_check&key={key}&ts={ts}&wait={wait}`.
        Putting the key in the path builds a URL httpx cannot dispatch.
        """
        api = self._api(vk)
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ts": "56", "updates": []}
        client.get = AsyncMock(return_value=response)
        api._http_client = client

        payload = _run(api.poll("lp.vk.com/wh123", "abc", "55", wait=25))

        assert payload["ts"] == "56"
        url = client.get.await_args.args[0]
        assert url == "https://lp.vk.com/wh123"
        params = client.get.await_args.kwargs["params"]
        assert params["act"] == "a_check"
        assert params["key"] == "abc"
        assert params["ts"] == "55"
        assert params["wait"] == 25
        assert params["lp_version"] == 2
        # The key is a query parameter, never a path segment.
        assert not url.endswith("/abc")
        assert "abc" not in url

    def test_poll_does_not_send_the_token(self, vk):
        """The poll URL carries only the longpoll key — never the token."""
        api = self._api(vk)
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ts": "56", "updates": []}
        client.get = AsyncMock(return_value=response)
        api._http_client = client

        _run(api.poll("lp.vk.com/wh123", "abc", "55"))

        call_kwargs = client.get.await_args.kwargs
        assert "tok" not in str(call_kwargs)
        assert "tok" not in client.get.await_args.args[0]

    def test_poll_via_proxy_shape(self, vk):
        """The proxied poll posts the longpoll params to the proxy.

        The Long Poll request carries no access token — only the
        longpoll key — so relaying it does not expose the community
        token.
        """
        api = self._api(vk, proxy_url="https://proxy.test", proxy_api_key="pkey")
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"success": True, "data": {"ts": "56", "updates": []}}
        client.post = AsyncMock(return_value=response)
        api._http_client = client

        payload = _run(api.poll_via_proxy("lp.vk.com/wh123", "abc", "55", wait=25))

        assert payload["ts"] == "56"
        args, kwargs = client.post.await_args
        assert args[0] == "https://proxy.test/vk/longpoll"
        assert kwargs["json"] == {
            "server": "lp.vk.com/wh123",
            "key": "abc",
            "ts": "55",
            "wait": 25,
        }
        assert kwargs["headers"]["Authorization"] == "Bearer pkey"
        # The community token must not travel with the poll.
        assert "tok" not in str(kwargs["json"])
        assert "tok" not in str(kwargs["headers"])

    def test_poll_via_proxy_reports_proxy_errors(self, vk):
        api = self._api(vk, proxy_url="https://proxy.test", proxy_api_key="pkey")
        client = MagicMock()
        response = MagicMock()
        response.status_code = 502
        response.json.return_value = {"success": False, "error": "socket hang up"}
        client.post = AsyncMock(return_value=response)
        api._http_client = client

        with pytest.raises(vk.VkApiError) as excinfo:
            _run(api.poll_via_proxy("lp.vk.com/wh123", "abc", "55"))

        assert "socket hang up" in str(excinfo.value)

    def test_poll_401_raises(self, vk):
        api = self._api(vk)
        client = MagicMock()
        response = MagicMock()
        response.status_code = 401
        client.get = AsyncMock(return_value=response)
        api._http_client = client

        with pytest.raises(vk.VkApiError):
            _run(api.poll("lp.vk.com/wh123", "abc", "55"))

    def test_poll_keeps_an_explicit_scheme(self, vk):
        """A server value that already has a scheme is not double-prefixed."""
        api = self._api(vk)
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ts": "56", "updates": []}
        client.get = AsyncMock(return_value=response)
        api._http_client = client

        _run(api.poll("https://lp.vk.com/wh123", "abc", "55"))

        assert client.get.await_args.args[0] == "https://lp.vk.com/wh123"

    def test_send_message_params(self, vk):
        api = self._api(vk)
        client = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "success": True, "data": {"peer_id": 5, "message_id": 77},
        }
        client.post = AsyncMock(return_value=response)
        api._http_client = client

        result = _run(api.send_message(5, "hello"))

        assert result["message_id"] == 77
        body = client.post.await_args.kwargs["json"]
        assert body["method"] == "messages.send"
        assert body["params"]["peer_id"] == 5
        assert body["params"]["message"] == "hello"
        # random_id is mandatory for messages.send.
        assert isinstance(body["params"]["random_id"], int)
        assert body["params"]["random_id"] > 0

    def test_random_id_differs_between_calls(self, vk):
        from hermes_plugins.vk import vk_api

        assert vk_api._random_id() != vk_api._random_id()


# ---------------------------------------------------------------------------
# 4. Inbound message handling
# ---------------------------------------------------------------------------


class TestInbound:

    def _message(self, **overrides):
        message = {
            "id": 101,
            "conversation_message_id": 7,
            "date": 1700000000,
            "peer_id": 2191655493,
            "from_id": 191655492,
            "text": "hello hermes",
        }
        message.update(overrides)
        return message

    def test_dispatches_event(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "tok", "group_id": "243045540"})
        adapter._api = _mock_api(user={"first_name": "Ivan"}, group={"name": "Hermes"})

        _run(adapter._on_message(self._message()))

        assert len(adapter.handled_events) == 1
        event = adapter.handled_events[0]
        assert event.text == "hello hermes"
        assert event.source.user_id == "191655492"
        assert event.source.chat_id == "2191655493"
        assert event.source.chat_type == "dm"
        assert event.source.user_name == "Ivan"
        assert event.source.chat_name == "Hermes"

    def test_lp_version_2_update_is_unwrapped(self, vk, adapter_factory):
        """A lp_version=2 update nests the message under object.message.

        The plugin asks VK for lp_version=2, so this is the shape that
        actually arrives. Reading the fields off `object` directly (the
        lp_version=1 shape) yields None for from_id and drops every
        message silently — no error, no reply.
        """
        adapter = adapter_factory({"token": "tok", "group_id": "243045540"})
        adapter._api = _mock_api(user={"first_name": "Ivan"}, group={"name": "Hermes"})

        update = {
            "type": 4,
            "object": {
                "message": self._message(),
                "client_info": '{"button_actions": []}',
            },
            "event": {"type": "message_new"},
        }

        _run(adapter._handle_update(update))

        assert len(adapter.handled_events) == 1
        event = adapter.handled_events[0]
        assert event.text == "hello hermes"
        assert event.source.user_id == "191655492"
        assert event.source.chat_id == "2191655493"
        assert event.source.chat_type == "dm"

    def test_lp_version_1_update_still_works(self, vk, adapter_factory):
        """The lp_version=1 shape — fields straight on `object` — still works."""
        adapter = adapter_factory({"token": "tok", "group_id": "243045540"})
        adapter._api = _mock_api()

        update = {"type": 4, "object": self._message()}

        _run(adapter._handle_update(update))

        assert len(adapter.handled_events) == 1
        assert adapter.handled_events[0].text == "hello hermes"

    def test_other_update_types_are_ignored(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "tok", "group_id": "243045540"})
        adapter._api = _mock_api()

        _run(adapter._handle_update({"type": 5, "object": {"message": self._message()}}))
        _run(adapter._handle_update({"type": 4, "object": "not-a-dict"}))
        _run(adapter._handle_update("not-a-dict"))

        assert adapter.handled_events == []

    def test_chat_name_falls_back_to_id(self, vk, adapter_factory):
        """A failed groups.getById must not block the message."""
        adapter = adapter_factory({"token": "tok", "group_id": "243045540"})
        adapter._api = _mock_api(group_error=RuntimeError("boom"))

        _run(adapter._on_message(self._message()))

        assert len(adapter.handled_events) == 1
        assert adapter.handled_events[0].source.chat_name == "VK 243045540"

    def test_group_conversation_type(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = _mock_api()

        _run(adapter._on_message(self._message(peer_id=243045540)))

        assert adapter.handled_events[0].source.chat_type == "group"

    def test_ignores_own_messages(self, vk, adapter_factory):
        """The community's own messages come back with from_id = -group_id."""
        adapter = adapter_factory({"token": "t", "group_id": "243045540"})
        adapter._api = MagicMock()
        adapter._api.get_user = AsyncMock(return_value={})

        _run(adapter._on_message(self._message(from_id=-243045540)))

        assert adapter.handled_events == []

    def test_ignores_system_events(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = _mock_api()

        _run(adapter._on_message(self._message(from_id=0)))

        assert adapter.handled_events == []

    def test_ignores_empty_text(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = _mock_api()

        _run(adapter._on_message(self._message(text="   ")))

        assert adapter.handled_events == []

    def test_deduplicates_repeat(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = _mock_api()

        _run(adapter._on_message(self._message()))
        _run(adapter._on_message(self._message()))

        assert len(adapter.handled_events) == 1

    def test_distinct_messages_both_dispatch(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = _mock_api()

        _run(adapter._on_message(self._message(conversation_message_id=7)))
        _run(adapter._on_message(self._message(conversation_message_id=8)))

        assert len(adapter.handled_events) == 2

    def test_user_lookup_failure_does_not_block(self, vk, adapter_factory):
        """A failed users.get must not stop the message from being handled."""
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = _mock_api(user_error=RuntimeError("boom"))

        _run(adapter._on_message(self._message()))

        assert len(adapter.handled_events) == 1
        assert adapter.handled_events[0].source.user_name == "191655492"

    def test_user_name_is_fetched_once_per_user(self, vk, adapter_factory):
        """The name lookup must not run on every message."""
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = _mock_api(user={"first_name": "Ivan"})

        _run(adapter._on_message(self._message(conversation_message_id=7)))
        _run(adapter._on_message(self._message(conversation_message_id=8)))

        assert adapter._api.get_user.await_count == 1
        assert adapter.handled_events[1].source.user_name == "Ivan"

    def test_only_message_new_updates_dispatch(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = _mock_api()

        _run(adapter._handle_update({"type": 5, "object": self._message()}))
        assert adapter.handled_events == []

        _run(adapter._handle_update({"type": 4, "object": self._message()}))
        assert len(adapter.handled_events) == 1


# ---------------------------------------------------------------------------
# 4b. The Long Poll loop
# ---------------------------------------------------------------------------


class TestPollLoop:

    def test_successful_poll_rearms_without_sleeping(self, vk, adapter_factory):
        """A successful poll must re-arm immediately.

        The Long Poll request already blocks for up to VK_LONGPOLL_WAIT
        seconds, so sleeping again after a successful cycle would add
        latency to every message for no reason.
        """
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.get_longpoll_server = AsyncMock(
            return_value={"server": "https://lp.vk.com/wh1", "key": "k", "ts": "1"}
        )
        adapter._api.get_user = AsyncMock(return_value={})
        adapter._api.get_group = AsyncMock(return_value={})

        calls = {"n": 0}

        async def _poll(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 3:
                adapter._running = False
                return {"ts": "2", "updates": []}
            return {"ts": "2", "updates": []}

        adapter._api.poll = _poll

        sleeps = []

        async def _fake_sleep(delay):
            sleeps.append(delay)
            if len(sleeps) >= 3:
                adapter._running = False

        adapter._running = True
        with patch.object(vk.asyncio, "sleep", _fake_sleep):
            _run(adapter._run_poll())

        assert calls["n"] >= 3
        # No sleep between successful polls.
        assert sleeps == []

    def test_fatal_error_stops_the_loop(self, vk, adapter_factory):
        """A fatal VK error must stop polling, not retry forever."""
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.get_longpoll_server = AsyncMock(
            side_effect=vk.VkApiError("token rejected", error_code=5)
        )
        adapter._running = True

        _run(adapter._run_poll())

        assert adapter._fatal_error_code == "vk_api_fatal"
        assert adapter._running is False

    def test_failed_flag_refetches_the_server(self, vk, adapter_factory):
        """VK's `failed` response means the longpoll key needs replacing."""
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.get_longpoll_server = AsyncMock(
            return_value={"server": "https://lp.vk.com/wh1", "key": "k", "ts": "1"}
        )
        adapter._api.get_user = AsyncMock(return_value={})
        adapter._api.get_group = AsyncMock(return_value={})

        server_calls = {"n": 0}

        async def _poll(*args, **kwargs):
            server_calls["n"] += 1
            if server_calls["n"] >= 2:
                adapter._running = False
            return {"failed": 1, "ts": "2"}

        adapter._api.poll = _poll

        async def _fake_sleep(delay):
            return None

        adapter._running = True
        with patch.object(vk.asyncio, "sleep", _fake_sleep):
            _run(adapter._run_poll())

        assert adapter._api.get_longpoll_server.await_count >= 2

    def test_repeated_failures_escalate_the_backoff(self, vk, adapter_factory):
        """A poll that keeps failing must back off, not spin.

        The backoff index used to be reset every time the longpoll server
        was (re)fetched, so a poll that always failed re-armed at the
        first step forever — a permanent 2-second loop hammering
        groups.getLongPollServer. The reset belongs on a *successful*
        poll only.
        """
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.get_longpoll_server = AsyncMock(
            return_value={"server": "lp.vk.com/wh1", "key": "k", "ts": "1"}
        )
        adapter._api.get_user = AsyncMock(return_value={})
        adapter._api.get_group = AsyncMock(return_value={})

        async def _poll(*args, **kwargs):
            raise vk.VkApiError("Long Poll request failed", method="longpoll")

        adapter._api.poll = _poll

        sleeps = []

        async def _fake_sleep(delay):
            sleeps.append(delay)
            if len(sleeps) >= 4:
                adapter._running = False

        adapter._running = True
        with patch.object(vk.asyncio, "sleep", _fake_sleep):
            _run(adapter._run_poll())

        # The delays must strictly grow (capped), never repeat the first
        # step forever.
        assert len(sleeps) >= 3
        assert sleeps[0] < sleeps[1] < sleeps[2]
        assert max(sleeps) <= vk.RECONNECT_BACKOFF[-1]

    def test_auto_mode_falls_back_to_the_proxy(self, vk, adapter_factory):
        """A direct poll that fails switches the adapter to the proxy.

        The proxy is the egress every other VK call in this stack uses;
        the direct path to VK's Long Poll CDN is not reachable from
        every host the agent runs on.
        """
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.get_longpoll_server = AsyncMock(
            return_value={"server": "lp.vk.com/wh1", "key": "k", "ts": "1"}
        )
        adapter._api.get_longpoll_settings = AsyncMock(
            return_value={"events": {"message_new": {"enabled": 1}}}
        )
        adapter._api.get_user = AsyncMock(return_value={})
        adapter._api.get_group = AsyncMock(return_value={})

        direct_calls = {"n": 0}

        async def _poll(*args, **kwargs):
            direct_calls["n"] += 1
            raise vk.VkApiError("Long Poll timed out", method="longpoll")

        async def _poll_via_proxy(*args, **kwargs):
            adapter._running = False
            return {"ts": "2", "updates": []}

        adapter._api.poll = _poll
        adapter._api.poll_via_proxy = _poll_via_proxy

        adapter._running = True
        _run(adapter._run_poll())

        assert direct_calls["n"] == 1
        # Stuck on the proxy after the first direct failure.
        assert adapter._longpoll_mode == "proxy"

    def test_proxy_mode_never_polls_directly(self, vk, adapter_factory, monkeypatch):
        """VK_LONGPOLL_MODE=proxy skips the direct path entirely."""
        monkeypatch.setenv("VK_LONGPOLL_MODE", "proxy")
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.get_longpoll_server = AsyncMock(
            return_value={"server": "lp.vk.com/wh1", "key": "k", "ts": "1"}
        )
        adapter._api.get_longpoll_settings = AsyncMock(
            return_value={"events": {"message_new": {"enabled": 1}}}
        )
        adapter._api.get_user = AsyncMock(return_value={})
        adapter._api.get_group = AsyncMock(return_value={})

        direct = MagicMock()

        async def _poll_via_proxy(*args, **kwargs):
            adapter._running = False
            return {"ts": "2", "updates": []}

        adapter._api.poll = direct
        adapter._api.poll_via_proxy = _poll_via_proxy

        adapter._running = True
        _run(adapter._run_poll())

        direct.assert_not_called()

    def test_event_flag_reads_both_vk_shapes(self, vk):
        """VK returns the Long Poll flags in two shapes.

        A flat map of event name to 1/0, and a nested
        {"in": .., "out": .., "enabled": ..} object. Reading the wrong
        one raised inside the poll loop, where it surfaced as
        "Unexpected poll error" and looked like a network fault.
        """
        assert vk._event_flag({"message_new": 1}, "message_new") is True
        assert vk._event_flag({"message_new": 0}, "message_new") is False
        assert vk._event_flag(
            {"message_new": {"in": 1, "out": 1, "enabled": 1}}, "message_new"
        ) is True
        assert vk._event_flag(
            {"message_new": {"in": 1, "out": 1, "enabled": 0}}, "message_new"
        ) is False
        # Absent or unintelligible — not a "disabled" verdict.
        assert vk._event_flag({}, "message_new") is None
        assert vk._event_flag({"message_new": "yes-please"}, "message_new") is None
        assert vk._event_flag(None, "message_new") is None
        assert vk._event_flag("not-a-dict", "message_new") is None

    def test_flat_event_shape_does_not_break_the_poll(self, vk, adapter_factory):
        """The flat shape must not raise inside the poll loop."""
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.get_longpoll_settings = AsyncMock(
            return_value={
                "api_version": "2",
                "is_enabled": True,
                "events": {"message_new": 1, "message_reply": 1},
            }
        )
        adapter._api.set_longpoll_settings = AsyncMock(return_value={})

        _run(adapter._ensure_message_events())  # must not raise
        adapter._api.set_longpoll_settings.assert_not_awaited()

    def test_message_events_are_checked_when_the_server_is_acquired(self, vk, adapter_factory):
        """The Long Poll event flags are read on every server acquisition.

        A community whose `message_new` flag is off delivers no messages
        at all, while the poll itself looks perfectly healthy.
        """
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.get_longpoll_server = AsyncMock(
            return_value={"server": "lp.vk.com/wh1", "key": "k", "ts": "1"}
        )
        adapter._api.get_longpoll_settings = AsyncMock(
            return_value={
                "api_version": "2",
                "is_enabled": True,
                "events": {"message_new": {"in": 1, "out": 1, "enabled": 1}},
            }
        )
        adapter._api.set_longpoll_settings = AsyncMock(return_value={})
        adapter._api.get_user = AsyncMock(return_value={})
        adapter._api.get_group = AsyncMock(return_value={})

        async def _poll(*args, **kwargs):
            adapter._running = False
            return {"ts": "2", "updates": []}

        adapter._api.poll = _poll

        adapter._running = True
        _run(adapter._run_poll())

        adapter._api.get_longpoll_settings.assert_awaited_once()
        # Already enabled — nothing to change.
        adapter._api.set_longpoll_settings.assert_not_awaited()

    def test_disabled_message_events_are_reported(self, vk, adapter_factory):
        """With the flag off the adapter reports it — and never writes the
        community's settings itself."""
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.get_longpoll_settings = AsyncMock(
            return_value={
                "api_version": "2",
                "is_enabled": True,
                "events": {"message_new": {"in": 0, "out": 0, "enabled": 0}},
            }
        )
        adapter._api.set_longpoll_settings = AsyncMock(return_value={})

        _run(adapter._ensure_message_events())

        # Reported only — the community's settings are left untouched.
        adapter._api.set_longpoll_settings.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. Outbound messaging
# ---------------------------------------------------------------------------


class TestOutbound:

    def test_send_succeeds(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.send_message = AsyncMock(return_value={"message_id": 55})

        result = _run(adapter.send("2191655493", "hi there"))

        assert result.success is True
        assert result.message_id == "55"
        args = adapter._api.send_message.await_args.args
        assert args[0] == 2191655493
        assert args[1] == "hi there"

    def test_send_reports_failure(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.send_message = AsyncMock(
            side_effect=vk.VkApiError("boom", error_code=5)
        )

        result = _run(adapter.send("2191655493", "hi"))

        assert result.success is False
        assert "VK error 5" in result.error

    def test_rate_limited_send_is_retryable(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.send_message = AsyncMock(
            side_effect=vk.VkApiError("slow down", error_code=6)
        )

        result = _run(adapter.send("2191655493", "hi"))

        assert result.retryable is True

    def test_send_truncates_at_vk_limit(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.send_message = AsyncMock(return_value={})

        _run(adapter.send("1", "x" * 5000))

        body = adapter._api.send_message.await_args.args[1]
        assert len(body) == 4096

    def test_invalid_peer_id(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        adapter._api = MagicMock()
        adapter._api.send_message = AsyncMock(return_value={})

        result = _run(adapter.send("not-a-number", "hi"))

        assert result.success is False

    def test_send_without_client(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        result = _run(adapter.send("1", "hi"))
        assert result.success is False

    def test_typing_is_a_noop(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        assert _run(adapter.send_typing("1")) is None

    def test_chat_info(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        info = _run(adapter.get_chat_info("2191655493"))
        assert info["type"] == "dm"
        assert info["chat_id"] == "2191655493"


# ---------------------------------------------------------------------------
# 6. Markdown stripping
# ---------------------------------------------------------------------------


class TestFormatting:

    def test_strips_bold(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        assert adapter.format_message("**bold**") == "bold"

    def test_strips_italic(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        assert adapter.format_message("*italic*") == "italic"

    def test_strips_inline_code(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        assert adapter.format_message("`code`") == "code"

    def test_keeps_code_block_content(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        assert adapter.format_message("```\n**x**\n```") == "```\n**x**\n```"

    def test_link_keeps_url(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        assert adapter.format_message("[docs](https://x.dev)") == "docs (https://x.dev)"

    def test_strips_heading(self, vk, adapter_factory):
        adapter = adapter_factory({"token": "t", "group_id": "1"})
        assert adapter.format_message("# Title") == "Title"


# ---------------------------------------------------------------------------
# 7. Standalone send (cron delivery)
# ---------------------------------------------------------------------------


class TestStandaloneSend:

    def test_requires_configuration(self, vk, monkeypatch):
        monkeypatch.delenv("VK_GROUP_TOKEN", raising=False)
        monkeypatch.delenv("VK_GROUP_ID", raising=False)
        result = _run(
            vk._standalone_send(PlatformConfig(), "2191655493", "hello")
        )
        assert "error" in result

    def test_sends_message(self, vk, monkeypatch):
        monkeypatch.setenv("VK_GROUP_TOKEN", "tok")
        monkeypatch.setenv("VK_GROUP_ID", "1")

        fake_api = MagicMock()
        fake_api.send_message = AsyncMock(return_value={"message_id": 9})
        fake_api.aclose = AsyncMock()
        monkeypatch.setattr(vk, "VkApi", MagicMock(return_value=fake_api))

        result = _run(
            vk._standalone_send(PlatformConfig(), "2191655493", "hello")
        )

        assert result["success"] is True
        assert result["message_id"] == "9"
        fake_api.send_message.assert_awaited_once()
        fake_api.aclose.assert_awaited_once()

    def test_reports_vk_error(self, vk, monkeypatch):
        monkeypatch.setenv("VK_GROUP_TOKEN", "tok")
        monkeypatch.setenv("VK_GROUP_ID", "1")

        fake_api = MagicMock()
        fake_api.send_message = AsyncMock(
            side_effect=vk.VkApiError("nope", error_code=5)
        )
        fake_api.aclose = AsyncMock()
        monkeypatch.setattr(vk, "VkApi", MagicMock(return_value=fake_api))

        result = _run(
            vk._standalone_send(PlatformConfig(), "2191655493", "hello")
        )

        assert "error" in result


# ---------------------------------------------------------------------------
# 8. Plugin registration
# ---------------------------------------------------------------------------


class TestRegistration:

    def test_register_shape(self, vk):
        captured: Dict[str, Any] = {}

        class FakeCtx:
            def register_platform(self, **kwargs):
                captured.update(kwargs)

        vk.register(FakeCtx())

        assert captured["name"] == "vk"
        assert captured["label"] == "VK"
        assert captured["allowed_users_env"] == "VK_ALLOWED_USERS"
        assert captured["allow_all_env"] == "VK_ALLOW_ALL_USERS"
        assert captured["cron_deliver_env_var"] == "VK_HOME_CHANNEL"
        assert captured["max_message_length"] == 4096
        assert captured["required_env"] == ["VK_GROUP_TOKEN", "VK_GROUP_ID"]
        # Without this hook, `deliver=vk` cron jobs fail with
        # "No live adapter for platform 'vk'".
        assert callable(captured["standalone_sender_fn"])
        assert callable(captured["env_enablement_fn"])
        assert callable(captured["check_fn"])

    def test_adapter_factory_builds_adapter(self, vk):
        captured: Dict[str, Any] = {}

        class FakeCtx:
            def register_platform(self, **kwargs):
                captured.update(kwargs)

        vk.register(FakeCtx())
        adapter = captured["adapter_factory"](PlatformConfig())
        assert isinstance(adapter, vk.VKAdapter)
        assert adapter.platform.value == "vk"


# ---------------------------------------------------------------------------
# 9. Manifest
# ---------------------------------------------------------------------------


class TestManifest:

    def test_plugin_yaml_declares_required_env(self, vk):
        from conftest import PLUGIN_DIR

        manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
        assert "kind: platform" in manifest
        assert "VK_GROUP_TOKEN" in manifest
        assert "VK_GROUP_ID" in manifest
        assert "VK_ALLOWED_USERS" in manifest
