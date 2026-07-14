# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for AgentArtsMemoryProvider."""

from __future__ import annotations

import json
import sys
import types
import uuid
from pathlib import Path

import pytest

import openjiuwen.core.memory.external as external_memory
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.memory.external import AgentArtsMemoryProvider


class FakeRecord:
    def __init__(self, content: str):
        self.content = content


class FakeSearchResult:
    def __init__(self, content: str, score: float):
        self.record = FakeRecord(content)
        self.score = score


class FakeSearchResponse:
    def __init__(self, results=None):
        self.results = results if results is not None else [FakeSearchResult("remember this", 0.91)]


class FakeSession:
    def __init__(self, session_id: str):
        self.id = session_id


class FakeMemorySearchFilter:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeTextMessage:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeAgentArtsClient:
    def __init__(self, search_response=None):
        self.search_response = search_response or FakeSearchResponse()
        self.search_calls = []
        self.session_calls = []
        self.message_calls = []
        self.session_call = None
        self.message_call = None
        self._next_session = 0

    def search_memories(self, **kwargs):
        self.search_calls.append(kwargs)
        return self.search_response

    def create_memory_session(self, **kwargs):
        self.session_calls.append(kwargs)
        self.session_call = kwargs
        if "id" in kwargs:
            uuid.UUID(kwargs["id"])
            return FakeSession(kwargs["id"])
        self._next_session += 1
        return FakeSession(f"server-session-{self._next_session}")

    def add_messages(self, **kwargs):
        self.message_calls.append(kwargs)
        self.message_call = kwargs
        return {"ok": True}


class FailingAgentArtsClient:
    def search_memories(self, **kwargs):
        raise RuntimeError("search failed")

    def create_memory_session(self, **kwargs):
        raise RuntimeError("session failed")


@pytest.fixture(autouse=True)
def install_fake_agentarts_config(monkeypatch):
    for module_name in ("agentarts", "agentarts.sdk", "agentarts.sdk.memory", "agentarts.sdk.memory.inner"):
        module = types.ModuleType(module_name)
        setattr(module, "__path__", [])
        monkeypatch.setitem(sys.modules, module_name, module)

    memory_module = types.ModuleType("agentarts.sdk.memory")

    class FakeMemoryClient(FakeAgentArtsClient):
        def __init__(self, **kwargs):
            super().__init__()
            self.init_kwargs = kwargs

    setattr(memory_module, "MemoryClient", FakeMemoryClient)
    monkeypatch.setitem(sys.modules, "agentarts.sdk.memory", memory_module)

    config_module = types.ModuleType("agentarts.sdk.memory.inner.config")
    setattr(config_module, "MemorySearchFilter", FakeMemorySearchFilter)
    setattr(config_module, "TextMessage", FakeTextMessage)
    monkeypatch.setitem(sys.modules, "agentarts.sdk.memory.inner.config", config_module)


def test_exported_provider_imports_without_sdk():
    assert AgentArtsMemoryProvider.__name__ == "AgentArtsMemoryProvider"


def test_external_memory_does_not_export_mapping_abc_or_default_store():
    assert not hasattr(external_memory, "MemorySessionMappingStore")
    assert not hasattr(external_memory, "InMemorySessionMappingStore")


def test_name_returns_agentarts():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    assert provider.name == "agentarts"


def test_default_base_url_and_availability():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    assert provider._base_url == "https://memory.cn-southwest-2.huaweicloud-agentarts.com"
    assert provider.is_available() is True


def test_custom_base_url_is_preserved_for_sdk_endpoint():
    provider = AgentArtsMemoryProvider(base_url="https://custom.example.com/", api_key="k", space_id="space")
    assert provider._base_url == "https://custom.example.com"


def test_is_available_requires_api_key_and_space_id():
    assert AgentArtsMemoryProvider(api_key="", space_id="space").is_available() is False
    assert AgentArtsMemoryProvider(api_key="k", space_id="").is_available() is False


@pytest.mark.asyncio
async def test_initialize_does_not_recheck_required_static_config():
    provider = AgentArtsMemoryProvider(api_key="", space_id="space")
    await provider.initialize(session_id="runtime-session")

    assert provider.is_available() is False
    assert provider.is_initialized is True


@pytest.mark.asyncio
async def test_initialize_creates_uuid_session_and_keeps_static_config():
    provider = AgentArtsMemoryProvider(
        base_url="https://configured.example.com",
        api_key="configured-key",
        space_id="configured-space",
        actor_id="configured-actor",
        assistant_id="configured-assistant",
    )
    fake = FakeAgentArtsClient()
    provider._client = fake

    await provider.initialize(
        user_id="runtime-user",
        scope_id="runtime-scope",
        session_id="runtime-session",
        base_url="https://ignored.example.com",
        api_key="ignored-key",
        space_id="ignored-space",
        actor_id="ignored-actor",
        assistant_id="runtime-assistant",
    )

    assert provider._base_url == "https://configured.example.com"
    assert provider._api_key == "configured-key"
    assert provider._space_id == "configured-space"
    assert provider._actor_id == "runtime-user"
    assert provider._assistant_id == "runtime-assistant"
    assert provider._session_id == "runtime-session"
    assert "id" not in fake.session_call
    assert fake.session_call["actor_id"] == "runtime-user"
    assert fake.session_call["assistant_id"] == "runtime-assistant"
    assert not hasattr(provider, "_agentarts_session_id")


@pytest.mark.asyncio
async def test_static_service_config_is_read_only_after_construction():
    provider = AgentArtsMemoryProvider(
        base_url="https://configured.example.com",
        api_key="configured-key",
        space_id="configured-space",
    )
    fake = FakeAgentArtsClient()
    provider._client = fake

    await provider.initialize(
        session_id="init-session",
        base_url="https://ignored-init.example.com",
        api_key="ignored-init-key",
        space_id="ignored-init-space",
    )
    await provider.sync_turn(
        "u-msg",
        "a-msg",
        session_id="call-session",
        base_url="https://ignored-call.example.com",
        api_key="ignored-call-key",
        space_id="ignored-call-space",
    )

    assert provider._base_url == "https://configured.example.com"
    assert provider._api_key == "configured-key"
    assert provider._space_id == "configured-space"
    assert [call["space_id"] for call in fake.session_calls] == ["configured-space", "configured-space"]
    assert fake.message_call["space_id"] == "configured-space"


def test_agentarts_extra_is_declared_in_pyproject():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'agentarts = ["agentarts-sdk>=0.1.2,<0.2"]' in pyproject


def test_get_client_uses_sdk_api_key_and_configures_client_endpoint(monkeypatch):
    calls = []

    class FakeHttpService:
        def _get_base_url(self, space_id=None):
            return "https://original.example.com"

    class FakeMemoryClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self._data_plane = types.SimpleNamespace(client=FakeHttpService())

    memory_module = types.ModuleType("agentarts.sdk.memory")
    setattr(memory_module, "MemoryClient", FakeMemoryClient)
    monkeypatch.setitem(sys.modules, "agentarts", types.ModuleType("agentarts"))
    monkeypatch.setitem(sys.modules, "agentarts.sdk", types.ModuleType("agentarts.sdk"))
    monkeypatch.setitem(sys.modules, "agentarts.sdk.memory", memory_module)

    provider = AgentArtsMemoryProvider(base_url="https://custom.example.com", api_key="k", space_id="space")
    client = provider._get_client()

    assert client is provider._client
    assert calls == [{"api_key": "k"}]
    assert client._data_plane.client._get_base_url() == "https://custom.example.com"


def test_missing_sdk_error_is_actionable(monkeypatch):
    monkeypatch.setitem(sys.modules, "agentarts.sdk.memory", types.ModuleType("agentarts.sdk.memory"))

    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")

    with pytest.raises(RuntimeError, match="openjiuwen\\[agentarts\\]") as exc_info:
        provider._get_client()
    assert "agentarts-sdk" in str(exc_info.value)
    assert ">=0.1.2" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_prefetch_searches_agentarts_and_formats_backend_neutral_context():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space", actor_id="u1", assistant_id="a1")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="prefetch-session")

    result = await provider.prefetch("who am I", top_k=3)

    assert "## External Memory" in result
    assert "- remember this" in result
    call = fake.search_calls[-1]
    assert call["space_id"] == "space"
    filters = call["filters"]
    assert filters.query == "who am I"
    assert filters.top_k == 3
    assert filters.actor_id == "u1"
    assert not hasattr(filters, "assistant_id")
    assert "AgentArts" not in result
    assert "agentarts" not in result


@pytest.mark.asyncio
async def test_prefetch_uses_call_runtime_scope_over_initialized_scope():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(user_id="init-user", scope_id="init-scope", session_id="init-session")

    await provider.prefetch(
        "who am I",
        user_id="call-user",
        scope_id="call-scope",
        session_id="call-session",
    )

    filters = fake.search_calls[-1]["filters"]
    assert filters.actor_id == "call-user"
    assert not hasattr(filters, "assistant_id")


@pytest.mark.asyncio
async def test_assistant_id_precedence_prefers_call_then_initialize_then_constructor_default():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space", assistant_id="default-assistant")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(assistant_id="init-assistant", session_id="init-session")

    await provider.prefetch("who am I", assistant_id="call-assistant")

    assert fake.session_call["assistant_id"] == "init-assistant"
    assert not hasattr(fake.search_calls[-1]["filters"], "assistant_id")

    provider = AgentArtsMemoryProvider(api_key="k", space_id="space", assistant_id="default-assistant")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="init-session")

    await provider.prefetch("who am I")

    assert fake.session_call["assistant_id"] == "default-assistant"
    assert not hasattr(fake.search_calls[-1]["filters"], "assistant_id")


@pytest.mark.asyncio
async def test_prefetch_without_results_returns_empty_string():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    provider._client = FakeAgentArtsClient(search_response=FakeSearchResponse(results=[]))
    await provider.initialize(session_id="empty-result-session")

    assert await provider.prefetch("missing") == ""


@pytest.mark.asyncio
async def test_prefetch_failure_returns_empty_string():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    await provider.initialize(session_id="prefetch-failure-session")
    provider._client = FailingAgentArtsClient()

    assert await provider.prefetch("x") == ""


@pytest.mark.asyncio
async def test_search_tool_returns_json_results_and_filters():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space", actor_id="u1")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="search-session")

    output = await provider.handle_tool_call(
        "external_memory_search",
        {"query": "x", "top_k": 2, "strategy_type": "semantic", "min_score": 0.7},
    )
    data = json.loads(output)

    assert data["count"] == 1
    assert data["results"][0]["memory"] == "remember this"
    assert data["results"][0]["score"] == 0.91
    filters = fake.search_calls[-1]["filters"]
    assert filters.query == "x"
    assert filters.top_k == 2
    assert filters.strategy_type == "semantic"
    assert filters.min_score == 0.7
    assert filters.actor_id == "u1"


@pytest.mark.asyncio
async def test_search_tool_treats_null_top_k_as_default():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="search-session")

    output = await provider.handle_tool_call("external_memory_search", {"query": "x", "top_k": None})
    data = json.loads(output)

    assert data["count"] == 1
    assert fake.search_calls[-1]["filters"].top_k == 10


@pytest.mark.asyncio
async def test_search_tool_defaults_min_score_to_half_when_omitted_or_null():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="search-session")

    await provider.handle_tool_call("external_memory_search", {"query": "x"})
    assert fake.search_calls[-1]["filters"].min_score == 0.5

    await provider.handle_tool_call("external_memory_search", {"query": "x", "min_score": None})
    assert fake.search_calls[-1]["filters"].min_score == 0.5


@pytest.mark.asyncio
async def test_search_tool_uses_call_runtime_scope_over_initialized_scope():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(user_id="init-user", scope_id="init-scope", session_id="init-session")

    output = await provider.handle_tool_call(
        "external_memory_search",
        {
            "query": "x",
            "user_id": "call-user",
            "scope_id": "call-scope",
            "session_id": "call-session",
        },
    )
    data = json.loads(output)

    assert data["count"] == 1
    filters = fake.search_calls[-1]["filters"]
    assert filters.actor_id == "call-user"
    assert not hasattr(filters, "assistant_id")


@pytest.mark.asyncio
async def test_search_tool_rejects_unknown_tool_and_missing_query():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    await provider.initialize(session_id="tool-validation-session")

    assert "Unknown tool" in json.loads(await provider.handle_tool_call("other", {}))["error"]
    assert "query" in json.loads(await provider.handle_tool_call("external_memory_search", {}))["error"]


@pytest.mark.asyncio
async def test_search_tool_failure_returns_json_error():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    await provider.initialize(session_id="tool-failure-session")
    provider._client = FailingAgentArtsClient()

    data = json.loads(await provider.handle_tool_call("external_memory_search", {"query": "x"}))

    assert "search failed" in data["error"]


def test_tool_schema_and_prompt_are_backend_neutral():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    schemas = provider.get_tool_schemas()
    properties = schemas[0]["parameters"]["properties"]

    assert schemas[0]["name"] == "external_memory_search"
    assert "external memory" in schemas[0]["description"]
    assert "AgentArts" not in schemas[0]["description"]
    assert "agentarts" not in schemas[0]["description"]
    assert schemas[0]["parameters"]["required"] == ["query"]
    assert "session_id" not in properties
    assert "memory_type" not in properties
    assert "semantic" in properties["strategy_type"]["description"]
    assert "custom" in properties["strategy_type"]["description"]
    assert properties["strategy_type"]["enum"] == [
        "semantic",
        "summary",
        "user_preference",
        "episodic",
        "event",
        "custom",
    ]

    prompt = provider.system_prompt_block()
    assert "External Memory" in prompt
    assert "external_memory_search" in prompt
    assert "AgentArts" not in prompt
    assert "agentarts" not in prompt


@pytest.mark.asyncio
async def test_sync_turn_creates_session_and_appends_messages():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space", actor_id="u1", assistant_id="a1")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="sync-session")

    await provider.sync_turn("u-msg", "a-msg")

    assert fake.session_call["space_id"] == "space"
    assert fake.session_call["actor_id"] == "u1"
    assert fake.session_call["assistant_id"] == "a1"
    assert fake.message_call["space_id"] == "space"
    assert fake.message_call["session_id"] == "server-session-1"
    messages = fake.message_call["messages"]
    assert messages[0].role == "user"
    assert messages[0].content == "u-msg"
    assert messages[0].actor_id == "u1"
    assert messages[0].assistant_id == "a1"
    assert messages[1].role == "assistant"
    assert messages[1].content == "a-msg"


@pytest.mark.asyncio
async def test_sync_turn_uses_caller_session_id():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="init-session")

    await provider.sync_turn("u-msg", "a-msg", session_id="session-1")

    assert "id" not in fake.session_call
    assert fake.message_call["session_id"] == "server-session-2"


@pytest.mark.asyncio
async def test_sync_turn_reuses_memory_session_mapping_store_entry():
    mapping_store = InMemoryKVStore()
    await mapping_store.set("agentarts/session_mapping/session-1", "existing-memory-session")
    provider = AgentArtsMemoryProvider(
        api_key="k",
        space_id="space",
        session_mapping_store=mapping_store,
    )
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="init-session")

    await provider.sync_turn("u-msg", "a-msg", session_id="session-1")

    assert len(fake.session_calls) == 1
    assert fake.message_call["session_id"] == "existing-memory-session"


@pytest.mark.asyncio
async def test_sync_turn_records_server_assigned_memory_session_mapping():
    mapping_store = InMemoryKVStore()
    provider = AgentArtsMemoryProvider(
        api_key="k",
        space_id="space",
        session_mapping_store=mapping_store,
    )
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="init-session")

    await provider.sync_turn("u-msg", "a-msg", session_id="session-1")
    await provider.sync_turn("u-msg-2", "a-msg-2", session_id="session-1")
    stored_session_id = await mapping_store.get("agentarts/session_mapping/session-1")

    assert fake.session_calls[-1] == {"space_id": "space"}
    assert len(fake.session_calls) == 2
    assert stored_session_id == "server-session-2"
    assert fake.message_calls[-2]["session_id"] == "server-session-2"
    assert fake.message_calls[-1]["session_id"] == "server-session-2"


@pytest.mark.asyncio
async def test_sync_turn_decodes_bytes_memory_session_mapping():
    mapping_store = InMemoryKVStore()
    await mapping_store.set("agentarts/session_mapping/session-1", b"existing-memory-session")
    provider = AgentArtsMemoryProvider(
        api_key="k",
        space_id="space",
        session_mapping_store=mapping_store,
    )
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="init-session")

    await provider.sync_turn("u-msg", "a-msg", session_id="session-1")

    assert len(fake.session_calls) == 1
    assert fake.message_call["session_id"] == "existing-memory-session"


@pytest.mark.asyncio
async def test_default_mapping_store_reuses_runtime_session():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="init-session")

    await provider.sync_turn("u-msg-1", "a-msg-1", session_id="session-1")
    await provider.sync_turn("u-msg-2", "a-msg-2", session_id="session-1")

    assert len(fake.session_calls) == 2
    assert fake.message_calls[-2]["session_id"] == "server-session-2"
    assert fake.message_calls[-1]["session_id"] == "server-session-2"


@pytest.mark.asyncio
async def test_sync_turn_call_session_does_not_replace_initialized_default_session():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="init-session")
    init_agentarts_session_id = "server-session-1"

    await provider.sync_turn("u-msg-1", "a-msg-1", session_id="call-session")
    call_agentarts_session_id = fake.message_call["session_id"]
    await provider.sync_turn("u-msg-2", "a-msg-2")

    assert call_agentarts_session_id != init_agentarts_session_id
    assert fake.message_calls[-1]["session_id"] == init_agentarts_session_id


@pytest.mark.asyncio
async def test_sync_turn_empty_or_none_session_id_falls_back_to_initialized_session_id():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(session_id="init-session")
    init_agentarts_session_id = "server-session-1"

    await provider.sync_turn("u-msg-1", "a-msg-1", session_id="")
    await provider.sync_turn("u-msg-2", "a-msg-2", session_id=None)

    assert fake.message_calls[-2]["session_id"] == init_agentarts_session_id
    assert fake.message_calls[-1]["session_id"] == init_agentarts_session_id


@pytest.mark.asyncio
async def test_sync_turn_uses_initialized_runtime_ids_by_default():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    fake = FakeAgentArtsClient()
    provider._client = fake
    await provider.initialize(user_id="runtime-user", scope_id="runtime-scope", session_id="session-from-init")
    agentarts_session_id = "server-session-1"

    await provider.sync_turn("u-msg", "a-msg")

    assert agentarts_session_id != "session-from-init"
    assert fake.session_call["actor_id"] == "runtime-user"
    assert fake.session_call["assistant_id"] == "runtime-scope"
    assert fake.message_call["session_id"] == agentarts_session_id


@pytest.mark.asyncio
async def test_sync_failure_does_not_raise():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    await provider.initialize(session_id="sync-failure-session")
    provider._client = FailingAgentArtsClient()

    await provider.sync_turn("u", "a")


@pytest.mark.asyncio
async def test_shutdown_resets_state():
    provider = AgentArtsMemoryProvider(api_key="k", space_id="space")
    await provider.initialize(session_id="shutdown-session")
    provider._client = FakeAgentArtsClient()

    await provider.shutdown()

    assert provider.is_initialized is False
    assert provider._client is None
