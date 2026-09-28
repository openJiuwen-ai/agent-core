# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for OfficeAce cloud / PC memory providers."""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from openjiuwen.core.memory.external import (
    OfficeAceMemoryCloudProvider,
    OfficeAceMemoryPcProvider,
)


# ---------------------------------------------------------------------------
# Cloud provider
# ---------------------------------------------------------------------------


class _FakeCloudClient:
    """Minimal stand-in for AgentArts SDK MemoryClient (used by cloud provider)."""

    def __init__(self, **kwargs: Any):
        self.init_kwargs = kwargs
        self.search_calls: list[dict] = []
        self.session_calls: list[dict] = []
        self.message_calls: list[dict] = []

    def search_memories(self, **kwargs):
        self.search_calls.append(kwargs)

        class _Record:
            def __init__(self, content: str):
                self.content = content

        class _Result:
            def __init__(self, content: str, score: float):
                self.record = _Record(content)
                self.score = score

        class _Resp:
            def __init__(self):
                self.results = [_Result("cloud-fact", 0.9)]

        return _Resp()

    def create_memory_session(self, **kwargs):
        self.session_calls.append(kwargs)

        class _Sess:
            id = "cloud-session-1"

        return _Sess()

    def add_messages(self, **kwargs):
        self.message_calls.append(kwargs)
        return {"ok": True}


@pytest.fixture(autouse=True)
def _install_fake_agentarts_sdk(monkeypatch):
    for module_name in (
        "agentarts",
        "agentarts.sdk",
        "agentarts.sdk.memory",
        "agentarts.sdk.memory.inner",
        "agentarts.sdk.memory.inner.config",
    ):
        monkeypatch.setitem(sys.modules, module_name, types.ModuleType(module_name))

    memory_module = sys.modules["agentarts.sdk.memory"]
    memory_module.MemoryClient = _FakeCloudClient  # type: ignore[attr-defined]

    config_module = sys.modules["agentarts.sdk.memory.inner.config"]

    class _Filter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _TextMessage:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    config_module.MemorySearchFilter = _Filter  # type: ignore[attr-defined]
    config_module.TextMessage = _TextMessage  # type: ignore[attr-defined]


def test_cloud_name_is_officeace_cloud():
    assert OfficeAceMemoryCloudProvider(api_key="k", space_id="s").name == "officeace_cloud"


def test_cloud_is_available_requires_api_key_and_space_id():
    assert OfficeAceMemoryCloudProvider(api_key="", space_id="").is_available() is False
    assert OfficeAceMemoryCloudProvider(api_key="k", space_id="s").is_available() is True


@pytest.mark.asyncio
async def test_cloud_sync_turn_is_noop_and_does_not_call_sdk():
    provider = OfficeAceMemoryCloudProvider(api_key="k", space_id="s")
    await provider.initialize(user_id="u", session_id="sess")
    fake = _FakeCloudClient()
    provider._client = fake

    await provider.sync_turn("u-msg", "a-msg", session_id="sess")

    assert fake.message_calls == []  # cloud does not report via provider


@pytest.mark.asyncio
async def test_cloud_search_still_uses_sdk():
    provider = OfficeAceMemoryCloudProvider(api_key="k", space_id="s")
    await provider.initialize(user_id="u", session_id="sess")
    provider._client = _FakeCloudClient()

    out = await provider.prefetch("hello", user_id="u")

    assert "cloud-fact" in out


# ---------------------------------------------------------------------------
# PC provider — httpx stubbed
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else (json.dumps(payload) if payload is not None else "")

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Replaces httpx.AsyncClient. Records the last request."""

    last: dict | None = None
    next_response: _FakeResponse = _FakeResponse(
        200, {"results": [{"content": "pc-fact", "score": 0.8}]}
    )

    def __init__(self, *args: Any, **kwargs: Any):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: Any):
        return False

    async def post(self, url: str, headers: dict | None = None, json: Any = None):
        type(self).last = {"url": url, "headers": headers, "json": json}
        return type(self).next_response


@pytest.fixture(autouse=True)
def _reset_fake_async_client():
    _FakeAsyncClient.last = None
    _FakeAsyncClient.next_response = _FakeResponse(
        200, {"results": [{"content": "pc-fact", "score": 0.8}]}
    )
    yield
    _FakeAsyncClient.last = None


@pytest.fixture
def patch_httpx(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


def test_pc_name_is_officeace_pc():
    assert OfficeAceMemoryPcProvider(api_key="k").name == "officeace_pc"


def test_pc_is_available_requires_api_key():
    assert OfficeAceMemoryPcProvider(api_key="").is_available() is False
    assert OfficeAceMemoryPcProvider(api_key="k").is_available() is True


@pytest.mark.asyncio
async def test_pc_search_posts_to_appapi_search_and_adds_x_chat_user_id(patch_httpx):
    provider = OfficeAceMemoryPcProvider(
        base_url="https://mem.example.com", api_key="k", actor_id="user-42"
    )
    await provider.initialize(user_id="user-42", session_id="thread-1")

    out = await provider.prefetch("hello", user_id="user-42")

    assert _FakeAsyncClient.last is not None
    assert (
        _FakeAsyncClient.last["url"]
        == "https://mem.example.com/v1/appapi/memory/search"
    )
    headers = _FakeAsyncClient.last["headers"]
    assert headers["Authorization"] == "OfficeAceToken k"
    assert headers["X-Chat-User-Id"] == "user-42"
    assert _FakeAsyncClient.last["json"]["query"] == "hello"
    assert "pc-fact" in out


@pytest.mark.asyncio
async def test_pc_search_body_passes_through_optional_fields(patch_httpx):
    provider = OfficeAceMemoryPcProvider(api_key="k", actor_id="u")
    await provider.initialize(user_id="u", session_id="t")

    await provider.handle_tool_call(
        "external_memory_search",
        {"query": "q", "top_k": 5, "strategy_type": "semantic", "min_score": 0.3},
    )

    body = _FakeAsyncClient.last["json"]
    assert body == {
        "query": "q",
        "top_k": 5,
        "strategy_type": "semantic",
        "min_score": 0.3,
    }


@pytest.mark.asyncio
async def test_pc_search_non_200_returns_empty(patch_httpx):
    _FakeAsyncClient.next_response = _FakeResponse(500, text="boom")

    provider = OfficeAceMemoryPcProvider(api_key="k", actor_id="u")
    await provider.initialize(user_id="u", session_id="t")

    out = await provider.prefetch("q", user_id="u")

    assert out == ""


@pytest.mark.asyncio
async def test_pc_search_no_user_id_omits_x_chat_user_id(patch_httpx):
    provider = OfficeAceMemoryPcProvider(api_key="k")
    await provider.initialize(session_id="t")

    await provider.prefetch("q")

    assert "X-Chat-User-Id" not in _FakeAsyncClient.last["headers"]


@pytest.mark.asyncio
async def test_pc_sync_turn_posts_messages_to_pc_threads_endpoint(patch_httpx):
    """PC provider sync_turn POSTs user+assistant messages to pc-threads/{thread_id}.

    thread_id (业务对话 ID) is taken from kwargs — session_id is the sha256 hash
    of thread_id and is NOT used as the upload key.
    """
    provider = OfficeAceMemoryPcProvider(
        base_url="https://mem.example.com", api_key="k", actor_id="user-42"
    )
    await provider.initialize(user_id="user-42", thread_id="thread-9")

    await provider.sync_turn(
        "u-msg", "a-msg", user_id="user-42", scope_id="scope-1", thread_id="thread-9"
    )

    assert _FakeAsyncClient.last is not None
    assert (
        _FakeAsyncClient.last["url"]
        == "https://mem.example.com/v1/appapi/memory/pc-threads/thread-9/messages"
    )
    headers = _FakeAsyncClient.last["headers"]
    assert headers["Authorization"] == "OfficeAceToken k"
    assert headers["X-Chat-User-Id"] == "user-42"
    body = _FakeAsyncClient.last["json"]
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["parts"][0] == {"type": "text", "text": "u-msg"}
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["parts"][0] == {"type": "text", "text": "a-msg"}
    # scope_id → assistant_id stamped on every message
    assert body["messages"][0]["assistant_id"] == "scope-1"
    assert body["messages"][1]["assistant_id"] == "scope-1"


@pytest.mark.asyncio
async def test_pc_sync_turn_skipped_without_thread_id(patch_httpx):
    provider = OfficeAceMemoryPcProvider(api_key="k", actor_id="u")
    await provider.initialize(user_id="u")  # no thread_id

    await provider.sync_turn("u", "a", user_id="u")

    assert _FakeAsyncClient.last is None


@pytest.mark.asyncio
async def test_pc_sync_turn_skipped_on_empty_messages(patch_httpx):
    provider = OfficeAceMemoryPcProvider(api_key="k", actor_id="u")
    await provider.initialize(user_id="u", thread_id="t")

    await provider.sync_turn("", "a-msg", user_id="u", thread_id="t")
    assert _FakeAsyncClient.last is None

    await provider.sync_turn("u-msg", "", user_id="u", thread_id="t")
    assert _FakeAsyncClient.last is None


@pytest.mark.asyncio
async def test_pc_sync_turn_does_not_raise_on_error_status(patch_httpx):
    _FakeAsyncClient.next_response = _FakeResponse(500, text="server down")

    provider = OfficeAceMemoryPcProvider(api_key="k", actor_id="u")
    await provider.initialize(user_id="u", thread_id="t")

    # Should not raise.
    await provider.sync_turn("u", "a", user_id="u", thread_id="t")


@pytest.mark.asyncio
async def test_pc_handle_tool_call_unknown_tool_returns_error(patch_httpx):
    provider = OfficeAceMemoryPcProvider(api_key="k")
    await provider.initialize()

    out = json.loads(await provider.handle_tool_call("nope", {}))
    assert "error" in out


@pytest.mark.asyncio
async def test_pc_handle_tool_call_missing_query_returns_error(patch_httpx):
    provider = OfficeAceMemoryPcProvider(api_key="k")
    await provider.initialize()

    out = json.loads(await provider.handle_tool_call("external_memory_search", {}))
    assert "error" in out


def test_pc_tool_schema_and_prompt_block():
    provider = OfficeAceMemoryPcProvider(api_key="k")
    schemas = provider.get_tool_schemas()
    assert schemas[0]["name"] == "external_memory_search"
    assert "external_memory_search" in provider.system_prompt_block()
