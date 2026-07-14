# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for LakeBaseMemoryProvider."""

from __future__ import annotations

import json
import time
from unittest import mock

import httpx
import pytest

from openjiuwen.core.memory.external import LakeBaseMemoryProvider
from openjiuwen.core.memory.external.lakebase_memory_provider import (
    LKB_BRANCH_CREATE_SCHEMA,
    LKB_BRANCH_LIST_SCHEMA,
    DEFAULT_BASE_URL,
    LKB_MEMORY_ADD_SCHEMA,
    LKB_MEMORY_DIGEST_SCHEMA,
    LKB_MEMORY_SEARCH_SCHEMA,
    MEMORY_TYPES,
    LKB_VERSION_CREATE_SCHEMA,
)


# ---------------------------------------------------------------------------
# Fake HTTP helpers
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal httpx.Response mock."""

    def __init__(self, status_code: int = 200, json_data: dict | list | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                message=f"HTTP {self.status_code}",
                request=mock.MagicMock(),
                response=self,
            )


class FakeAsyncClient:
    """Lightweight httpx.AsyncClient mock that records calls and returns preset responses.

    Uses (method, url_pattern) pairs for matching so overlapping URL substrings
    (e.g. "/branches" vs "/branches/.../versions") are distinguished by HTTP method.
    """

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []  # (method, url, kwargs)
        self._responses: dict[tuple[str, str], FakeResponse] = {}  # (method, pattern) -> response
        self._default_response = FakeResponse()
        self._closed = False

    def set_response(self, method: str, url_pattern: str, response: FakeResponse):
        self._responses[(method, url_pattern)] = response

    def _find_response(self, method: str, url: str) -> FakeResponse:
        # Match longest (most specific) pattern first for the given HTTP method
        candidates = [
            (pattern, resp) for (m, pattern), resp in self._responses.items()
            if m == method and pattern in url
        ]
        if candidates:
            # Sort by pattern length descending — longest/most specific wins
            candidates.sort(key=lambda x: len(x[0]), reverse=True)
            return candidates[0][1]
        return self._default_response

    async def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._find_response("GET", url)

    async def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._find_response("POST", url)

    async def delete(self, url: str, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        return self._find_response("DELETE", url)

    async def aclose(self):
        self._closed = True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_provider(**overrides) -> LakeBaseMemoryProvider:
    defaults = {
        "api_key": "lk_test_key",
        "base_url": "http://localhost:8080/api/v1",
        "base_id": "mem_test",
    }
    defaults.update(overrides)
    return LakeBaseMemoryProvider(**defaults)


@pytest.fixture
def provider():
    return _make_provider()


@pytest.fixture
def initialized_provider():
    p = _make_provider()
    fake_http = FakeAsyncClient()
    fake_http.set_response("GET", "/bases/mem_test/stats", FakeResponse(json_data={"count": 5}))
    p._http = fake_http
    p._is_initialized = True
    return p


# ---------------------------------------------------------------------------
# Construction & Properties
# ---------------------------------------------------------------------------


class TestConstructionAndProperties:

    def test_name_returns_lakebase(self, provider):
        assert provider.name == "lakebase"

    def test_default_base_url(self, provider):
        assert provider._base_url == "http://localhost:8080/api/v1"

    def test_custom_base_url_trailing_slash_stripped(self):
        p = _make_provider(base_url="http://host:8080/api/v1/")
        assert p._base_url == "http://host:8080/api/v1"

    def test_is_available_with_all_required_fields(self, provider):
        assert provider.is_available() is True

    def test_is_available_false_when_api_key_missing(self):
        assert _make_provider(api_key="").is_available() is False

    def test_is_available_false_when_base_url_missing(self):
        p = _make_provider(base_url="")
        # base_url="" after rstrip still empty
        assert p._base_url == ""
        assert p.is_available() is False

    def test_is_available_false_when_base_id_missing(self):
        assert _make_provider(base_id="").is_available() is False

    def test_is_initialized_defaults_false(self, provider):
        assert provider.is_initialized is False

    def test_current_base_id_returns_configured_id(self, provider):
        assert provider.current_base_id == "mem_test"

    def test_default_database_id(self, provider):
        assert provider._database_id == "db_agent_memory"

    def test_custom_database_id(self):
        p = _make_provider(database_id="db_custom")
        assert p._database_id == "db_custom"

    def test_default_timeout(self, provider):
        assert provider._timeout == 60.0

    def test_custom_timeout(self):
        p = _make_provider(timeout=30.0)
        assert p._timeout == 30.0


class TestFromConfig:

    def test_from_config_with_full_config(self):
        config = {
            "lakebase": {
                "api_key": "lk_cfg_key",
                "base_url": "http://cfg-host:8080/api/v1",
                "base_id": "mem_cfg",
                "database_id": "db_cfg",
                "timeout": 45.0,
            },
        }
        p = LakeBaseMemoryProvider.from_config(config)
        assert p._api_key == "lk_cfg_key"
        assert p._base_url == "http://cfg-host:8080/api/v1"
        assert p._base_id == "mem_cfg"
        assert p._database_id == "db_cfg"
        assert p._timeout == 45.0

    def test_from_config_with_defaults(self):
        config = {"lakebase": {}}
        p = LakeBaseMemoryProvider.from_config(config)
        assert p._api_key == ""
        assert p._base_url == DEFAULT_BASE_URL
        assert p._base_id == "mem_default"
        assert p._database_id == "db_agent_memory"
        assert p._timeout == 60.0

    def test_from_config_missing_lakebase_key(self):
        p = LakeBaseMemoryProvider.from_config({})
        assert p._api_key == ""
        assert p.is_available() is False


# ---------------------------------------------------------------------------
# Lifecycle (initialize / shutdown)
# ---------------------------------------------------------------------------


class TestLifecycle:

    @pytest.mark.asyncio
    async def test_initialize_creates_http_client(self, provider):
        with mock.patch("httpx.AsyncClient") as mock_client_cls:
            mock_http = mock.MagicMock()
            mock_http.get = mock.AsyncMock(return_value=FakeResponse(status_code=200, json_data={"count": 0}))
            mock_http.aclose = mock.AsyncMock()
            mock_client_cls.return_value = mock_http

            await provider.initialize()
            assert provider.is_initialized is True
            mock_client_cls.assert_called_once()
            call_kwargs = mock_client_cls.call_args[1]
            assert call_kwargs["base_url"] == provider._base_url
            assert "Bearer lk_test_key" in call_kwargs["headers"]["Authorization"]

    @pytest.mark.asyncio
    async def test_initialize_skips_if_already_initialized(self, initialized_provider):
        # Second call should be no-op
        await initialized_provider.initialize()
        # No extra GET calls beyond setup
        assert initialized_provider.is_initialized is True

    @pytest.mark.asyncio
    async def test_initialize_handles_connect_error_gracefully(self):
        p = _make_provider()
        with mock.patch("httpx.AsyncClient") as mock_client_cls:
            mock_http = mock.MagicMock()
            mock_http.get = mock.AsyncMock(side_effect=httpx.ConnectError("no server"))
            mock_http.aclose = mock.AsyncMock()
            mock_client_cls.return_value = mock_http

            await p.initialize()
            assert p.is_initialized is True  # Still marks initialized even if connection fails

    @pytest.mark.asyncio
    async def test_initialize_handles_non_200_status(self):
        p = _make_provider()
        with mock.patch("httpx.AsyncClient") as mock_client_cls:
            mock_http = mock.MagicMock()
            mock_http.get = mock.AsyncMock(return_value=FakeResponse(status_code=404))
            mock_http.aclose = mock.AsyncMock()
            mock_client_cls.return_value = mock_http

            await p.initialize()
            assert p.is_initialized is True

    @pytest.mark.asyncio
    async def test_shutdown_closes_client_and_marks_uninitialized(self, initialized_provider):
        fake_http = initialized_provider._http
        await initialized_provider.shutdown()
        assert initialized_provider.is_initialized is False
        assert initialized_provider._http is None
        assert fake_http._closed is True

    @pytest.mark.asyncio
    async def test_shutdown_no_client_is_safe(self):
        p = _make_provider()
        await p.shutdown()
        assert p.is_initialized is False


# ---------------------------------------------------------------------------
# Tool Schemas & System Prompt
# ---------------------------------------------------------------------------


class TestToolSchemasAndPrompt:

    def test_get_tool_schemas_returns_all_schemas(self, provider):
        schemas = provider.get_tool_schemas()
        schema_names = [s["name"] for s in schemas]
        expected_names = [
            "lkb_memory_search", "lkb_memory_add", "lkb_memory_list", "lkb_memory_get",
            "lkb_memory_delete", "lkb_memory_digest", "lkb_memory_traits", "lkb_memory_stats",
            "lkb_memory_switch_base",
            "lkb_branch_list", "lkb_branch_create", "lkb_branch_delete", "lkb_branch_promote",
            "lkb_branch_restore", "lkb_version_list", "lkb_version_create", "lkb_version_delete",
        ]
        assert schema_names == expected_names

    def test_search_schema_structure(self):
        assert LKB_MEMORY_SEARCH_SCHEMA["name"] == "lkb_memory_search"
        assert "query" in LKB_MEMORY_SEARCH_SCHEMA["parameters"]["properties"]
        assert LKB_MEMORY_SEARCH_SCHEMA["parameters"]["required"] == ["query"]

    def test_add_schema_structure(self):
        assert LKB_MEMORY_ADD_SCHEMA["name"] == "lkb_memory_add"
        assert "content" in LKB_MEMORY_ADD_SCHEMA["parameters"]["properties"]
        assert LKB_MEMORY_ADD_SCHEMA["parameters"]["required"] == ["content"]
        assert LKB_MEMORY_ADD_SCHEMA["parameters"]["properties"]["memory_type"]["enum"] == MEMORY_TYPES

    def test_branch_create_schema_structure(self):
        assert LKB_BRANCH_CREATE_SCHEMA["name"] == "lkb_branch_create"
        assert "name" in LKB_BRANCH_CREATE_SCHEMA["parameters"]["properties"]
        assert LKB_BRANCH_CREATE_SCHEMA["parameters"]["required"] == ["name"]

    def test_version_create_schema_structure(self):
        assert LKB_VERSION_CREATE_SCHEMA["name"] == "lkb_version_create"
        assert "name" in LKB_VERSION_CREATE_SCHEMA["parameters"]["properties"]
        assert LKB_VERSION_CREATE_SCHEMA["parameters"]["required"] == ["name"]

    def test_memory_types_list(self):
        assert MEMORY_TYPES == ["fact", "episode", "procedural", "decision", "rejection", "convention"]

    def test_system_prompt_block_contains_key_operations(self, provider):
        prompt = provider.system_prompt_block()
        assert "LakeBase Memory System" in prompt
        assert "lkb_memory_search" in prompt
        assert "lkb_memory_add" in prompt
        assert "lkb_memory_digest" in prompt
        assert "lkb_branch_create" in prompt
        assert "lkb_version_create" in prompt

    def test_system_prompt_block_contains_memory_types(self, provider):
        prompt = provider.system_prompt_block()
        for mt in MEMORY_TYPES:
            assert mt in prompt


# ---------------------------------------------------------------------------
# handle_tool_call dispatch
# ---------------------------------------------------------------------------


class TestHandleToolCall:

    @pytest.mark.asyncio
    async def test_not_initialized_returns_error(self, provider):
        result = await provider.handle_tool_call("lkb_memory_search", {"query": "x"})
        data = json.loads(result)
        assert "not initialized" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, initialized_provider):
        result = await initialized_provider.handle_tool_call("nonexistent_tool", {})
        data = json.loads(result)
        assert "Unknown tool" in data["error"]

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_returns_error(self, initialized_provider):
        initialized_provider._consecutive_failures = 5
        initialized_provider._breaker_until = time.monotonic() + 120

        result = await initialized_provider.handle_tool_call("lkb_memory_search", {"query": "x"})
        data = json.loads(result)
        assert "Circuit breaker open" in data["error"]

    @pytest.mark.asyncio
    async def test_http_status_error_returns_error_json(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/recall", FakeResponse(status_code=500, text="server error"))

        result = await initialized_provider.handle_tool_call("lkb_memory_search", {"query": "x"})
        data = json.loads(result)
        assert "API error" in data["error"]
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_connect_error_returns_error_json(self, initialized_provider):
        fake_http = initialized_provider._http

        async def _raise_connect(*args, **kwargs):
            raise httpx.ConnectError("no server")

        fake_http.post = _raise_connect

        result = await initialized_provider.handle_tool_call("lkb_memory_search", {"query": "x"})
        data = json.loads(result)
        assert "Connection failed" in data["error"]

    @pytest.mark.asyncio
    async def test_generic_exception_returns_error_json(self, initialized_provider):
        fake_http = initialized_provider._http

        async def _raise_generic(*args, **kwargs):
            raise ValueError("unexpected")

        fake_http.post = _raise_generic

        result = await initialized_provider.handle_tool_call("lkb_memory_search", {"query": "x"})
        data = json.loads(result)
        assert "unexpected" in data["error"]

    @pytest.mark.asyncio
    async def test_success_resets_circuit_breaker(self, initialized_provider):
        initialized_provider._consecutive_failures = 3
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/recall", FakeResponse(json_data={"memories": []}))

        await initialized_provider.handle_tool_call("lkb_memory_search", {"query": "x"})
        assert initialized_provider._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_failure_records_circuit_breaker(self, initialized_provider):
        fake_http = initialized_provider._http

        async def _raise_connect(*args, **kwargs):
            raise httpx.ConnectError("no server")

        fake_http.post = _raise_connect

        await initialized_provider.handle_tool_call("lkb_memory_search", {"query": "x"})
        assert initialized_provider._consecutive_failures == 1


# ---------------------------------------------------------------------------
# Memory Operations (search, add, list, get, delete, digest, traits, stats)
# ---------------------------------------------------------------------------


class TestMemorySearch:

    @pytest.mark.asyncio
    async def test_search_returns_memories_and_count(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/recall", FakeResponse(json_data={
            "memories": [
                {"content": "likes python", "memory_type": "fact", "score": 0.92},
                {"content": "uses vim", "memory_type": "procedural", "score": 0.78},
            ],
        }))

        result = await initialized_provider.handle_tool_call("lkb_memory_search", {"query": "preferences", "top_k": 5})
        data = json.loads(result)
        assert data["count"] == 2
        assert data["memories"][0]["content"] == "likes python"
        assert data["base_id"] == "mem_test"

    @pytest.mark.asyncio
    async def test_search_with_memory_types_filter(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/recall", FakeResponse(json_data={"memories": []}))

        result = await initialized_provider.handle_tool_call(
            "lkb_memory_search", {"query": "x", "memory_types": ["fact", "episode"]}
        )
        data = json.loads(result)
        assert data["count"] == 0

        # Verify filter was passed to the HTTP call
        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "recall" in c[1]]
        assert len(post_calls) == 1
        body = post_calls[0][2]["json"]
        assert body["memory_types"] == ["fact", "episode"]

    @pytest.mark.asyncio
    async def test_search_default_top_k(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/recall", FakeResponse(json_data={"memories": []}))

        await initialized_provider.handle_tool_call("lkb_memory_search", {"query": "x"})
        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "recall" in c[1]]
        body = post_calls[0][2]["json"]
        assert body["top_k"] == 10


class TestMemoryAdd:

    @pytest.mark.asyncio
    async def test_add_returns_success_and_memory_id(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/ingest", FakeResponse(json_data={"memory_id": 42, "memory_type": "fact"}))

        result = await initialized_provider.handle_tool_call(
            "lkb_memory_add", {"content": "prefers dark mode", "memory_type": "fact", "importance": 0.8}
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["memory_id"] == 42
        assert data["memory_type"] == "fact"
        assert data["base_id"] == "mem_test"

    @pytest.mark.asyncio
    async def test_add_defaults_memory_type_to_fact(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/ingest", FakeResponse(json_data={"memory_id": 1, "memory_type": "fact"}))

        await initialized_provider.handle_tool_call("lkb_memory_add", {"content": "something"})
        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "ingest" in c[1]]
        body = post_calls[0][2]["json"]
        assert body["memory_type"] == "fact"
        assert body["importance"] == 0.5

    @pytest.mark.asyncio
    async def test_add_with_metadata(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/ingest", FakeResponse(json_data={"memory_id": 1}))

        await initialized_provider.handle_tool_call(
            "lkb_memory_add", {"content": "test", "metadata": {"source": "unit_test"}}
        )
        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "ingest" in c[1]]
        body = post_calls[0][2]["json"]
        assert body["metadata"] == {"source": "unit_test"}


class TestMemoryList:

    @pytest.mark.asyncio
    async def test_list_returns_memories_and_total(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("GET", "/memories", FakeResponse(json_data={
            "memories": [{"id": 1, "content": "m1"}],
            "total": 1,
        }))

        result = await initialized_provider.handle_tool_call("lkb_memory_list", {"limit": 10, "offset": 0})
        data = json.loads(result)
        assert data["total"] == 1
        assert len(data["memories"]) == 1
        assert data["base_id"] == "mem_test"

    @pytest.mark.asyncio
    async def test_list_with_memory_type_filter(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("GET", "/memories", FakeResponse(json_data={"memories": [], "total": 0}))

        result = await initialized_provider.handle_tool_call(
            "lkb_memory_list", {"memory_type": "fact", "limit": 5}
        )
        data = json.loads(result)
        assert data["total"] == 0

        get_calls = [c for c in fake_http.calls if c[0] == "GET" and "memories" in c[1]]
        assert len(get_calls) == 1
        params = get_calls[0][2].get("params", {})
        assert params.get("memory_type") == "fact"

    @pytest.mark.asyncio
    async def test_list_default_pagination(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("GET", "/memories", FakeResponse(json_data={"memories": [], "total": 0}))

        await initialized_provider.handle_tool_call("lkb_memory_list", {})
        get_calls = [c for c in fake_http.calls if c[0] == "GET" and "memories" in c[1]]
        params = get_calls[0][2].get("params", {})
        assert params.get("offset") == 0
        assert params.get("limit") == 20


class TestMemoryGet:

    @pytest.mark.asyncio
    async def test_get_returns_memory_and_base_id(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("GET", "/memories/10", FakeResponse(json_data={"id": 10, "content": "specific memory"}))

        result = await initialized_provider.handle_tool_call("lkb_memory_get", {"memory_id": 10})
        data = json.loads(result)
        assert data["memory"]["id"] == 10
        assert data["base_id"] == "mem_test"

    @pytest.mark.asyncio
    async def test_get_without_memory_id_returns_error(self, initialized_provider):
        result = await initialized_provider.handle_tool_call("lkb_memory_get", {})
        data = json.loads(result)
        assert "memory_id" in data["error"]


class TestMemoryDelete:

    @pytest.mark.asyncio
    async def test_delete_returns_success(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("DELETE", "/memories/10", FakeResponse(json_data={"deleted": True}))

        result = await initialized_provider.handle_tool_call("lkb_memory_delete", {"memory_id": 10})
        data = json.loads(result)
        assert data["success"] is True
        assert data["deleted_id"] == 10
        assert data["base_id"] == "mem_test"

    @pytest.mark.asyncio
    async def test_delete_without_memory_id_returns_error(self, initialized_provider):
        result = await initialized_provider.handle_tool_call("lkb_memory_delete", {})
        data = json.loads(result)
        assert "memory_id" in data["error"]


class TestMemoryDigest:

    @pytest.mark.asyncio
    async def test_digest_returns_traits(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/digest", FakeResponse(json_data={
            "traits": [{"name": "curious", "strength": 0.7}],
        }))

        result = await initialized_provider.handle_tool_call("lkb_memory_digest", {})
        data = json.loads(result)
        assert data["success"] is True
        assert len(data["traits"]) == 1
        assert data["traits"][0]["name"] == "curious"


class TestMemoryTraits:

    @pytest.mark.asyncio
    async def test_traits_returns_trait_list(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("GET", "/traits", FakeResponse(json_data=[
            {"name": "curious", "strength": 0.7},
        ]))

        result = await initialized_provider.handle_tool_call("lkb_memory_traits", {})
        data = json.loads(result)
        assert len(data["traits"]) == 1
        assert data["base_id"] == "mem_test"


class TestMemoryStats:

    @pytest.mark.asyncio
    async def test_stats_returns_stats_and_base_id(self, initialized_provider):
        fake_http = initialized_provider._http
        # Override the default stats response set during fixture initialization
        fake_http._responses.clear()
        fake_http.set_response("GET", "/bases/mem_test/stats", FakeResponse(json_data={"total_count": 42, "types": {"fact": 20}}))

        result = await initialized_provider.handle_tool_call("lkb_memory_stats", {})
        data = json.loads(result)
        assert data["stats"]["total_count"] == 42
        assert data["base_id"] == "mem_test"


class TestMemorySwitchBase:

    @pytest.mark.asyncio
    async def test_switch_base_updates_base_id(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("GET", "/bases/mem_other/stats", FakeResponse(json_data={"count": 0}))

        result = await initialized_provider.handle_tool_call(
            "lkb_memory_switch_base", {"base_id": "mem_other"}
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["old_base_id"] == "mem_test"
        assert data["new_base_id"] == "mem_other"
        assert initialized_provider.current_base_id == "mem_other"

    @pytest.mark.asyncio
    async def test_switch_base_tracks_available_bases(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("GET", "/bases/mem_other/stats", FakeResponse(json_data={"count": 0}))

        await initialized_provider.handle_tool_call("lkb_memory_switch_base", {"base_id": "mem_other"})
        assert "mem_other" in initialized_provider._available_bases

    @pytest.mark.asyncio
    async def test_switch_base_without_base_id_returns_error(self, initialized_provider):
        result = await initialized_provider.handle_tool_call("lkb_memory_switch_base", {})
        data = json.loads(result)
        assert "base_id" in data["error"]

    @pytest.mark.asyncio
    async def test_switch_base_handles_base_check_failure_gracefully(self, initialized_provider):
        fake_http = initialized_provider._http

        async def _fail_stats(*args, **kwargs):
            raise httpx.ConnectError("no server")

        # Override the stats GET for the new base, but keep post working
        original_get = fake_http.get

        async def _conditional_get(url, **kwargs):
            if "mem_other/stats" in url:
                raise httpx.ConnectError("no server")
            return await original_get(url, **kwargs)

        fake_http.get = _conditional_get

        # Should NOT switch when base check fails — return error instead
        result = await initialized_provider.handle_tool_call(
            "lkb_memory_switch_base", {"base_id": "mem_other"}
        )
        data = json.loads(result)
        assert "error" in data
        assert "Base check failed" in data["error"]
        assert initialized_provider.current_base_id != "mem_other"

    @pytest.mark.asyncio
    async def test_switch_base_rejects_nonexistent_base(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("GET", "/bases/nonexistent/stats", FakeResponse(status_code=404))

        result = await initialized_provider.handle_tool_call(
            "lkb_memory_switch_base", {"base_id": "nonexistent"}
        )
        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()
        assert initialized_provider.current_base_id != "nonexistent"


# ---------------------------------------------------------------------------
# Branch Operations
# ---------------------------------------------------------------------------


class TestBranchList:

    @pytest.mark.asyncio
    async def test_branch_list_returns_branches(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("GET", "/branches", FakeResponse(json_data=[
            {"id": "br_main", "name": "main", "is_default": True},
            {"id": "br_exp", "name": "experiment", "is_default": False},
        ]))

        result = await initialized_provider.handle_tool_call("lkb_branch_list", {})
        data = json.loads(result)
        assert data["count"] == 2
        assert data["branches"][0]["name"] == "main"
        assert data["database_id"] == "db_agent_memory"


class TestBranchCreate:

    @pytest.mark.asyncio
    async def test_branch_create_returns_success(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/branches", FakeResponse(json_data={
            "id": "br_new", "name": "experiment",
        }))

        result = await initialized_provider.handle_tool_call(
            "lkb_branch_create", {"name": "experiment"}
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["branch"]["name"] == "experiment"
        assert data["database_id"] == "db_agent_memory"

    @pytest.mark.asyncio
    async def test_branch_create_with_parent_branch_id(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/branches", FakeResponse(json_data={"id": "br_child", "name": "child"}))

        await initialized_provider.handle_tool_call(
            "lkb_branch_create", {"name": "child", "parent_branch_id": "br_main"}
        )
        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "branches" in c[1]]
        body = post_calls[0][2]["json"]
        assert body["parent_branch_id"] == "br_main"

    @pytest.mark.asyncio
    async def test_branch_create_without_name_returns_error(self, initialized_provider):
        result = await initialized_provider.handle_tool_call("lkb_branch_create", {})
        data = json.loads(result)
        assert "name" in data["error"]


class TestBranchDelete:

    @pytest.mark.asyncio
    async def test_branch_delete_returns_success(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("DELETE", "/branches/br_exp", FakeResponse(json_data={"deleted": True}))

        result = await initialized_provider.handle_tool_call(
            "lkb_branch_delete", {"branch_id": "br_exp"}
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["deleted_branch_id"] == "br_exp"

    @pytest.mark.asyncio
    async def test_branch_delete_without_branch_id_returns_error(self, initialized_provider):
        result = await initialized_provider.handle_tool_call("lkb_branch_delete", {})
        data = json.loads(result)
        assert "branch_id" in data["error"]


class TestBranchPromote:

    @pytest.mark.asyncio
    async def test_branch_promote_returns_success(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/promote", FakeResponse(json_data={"promoted": True}))

        result = await initialized_provider.handle_tool_call(
            "lkb_branch_promote", {"branch_id": "br_exp"}
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["promoted_branch_id"] == "br_exp"

    @pytest.mark.asyncio
    async def test_branch_promote_without_branch_id_returns_error(self, initialized_provider):
        result = await initialized_provider.handle_tool_call("lkb_branch_promote", {})
        data = json.loads(result)
        assert "branch_id" in data["error"]


class TestBranchRestore:

    @pytest.mark.asyncio
    async def test_branch_restore_returns_success(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/restore", FakeResponse(json_data={"restored": True}))

        result = await initialized_provider.handle_tool_call(
            "lkb_branch_restore", {"branch_id": "br_exp", "version_id": "v1", "lsn": "lsn_100"}
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["restored_branch_id"] == "br_exp"

    @pytest.mark.asyncio
    async def test_branch_restore_without_branch_id_returns_error(self, initialized_provider):
        result = await initialized_provider.handle_tool_call("lkb_branch_restore", {})
        data = json.loads(result)
        assert "branch_id" in data["error"]

    @pytest.mark.asyncio
    async def test_branch_restore_passes_version_and_lsn_to_api(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/restore", FakeResponse(json_data={"restored": True}))

        await initialized_provider.handle_tool_call(
            "lkb_branch_restore", {"branch_id": "br_exp", "version_id": "v1", "lsn": "lsn_100"}
        )
        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "restore" in c[1]]
        body = post_calls[0][2]["json"]
        assert body["target_version_id"] == "v1"
        assert body["target_lsn"] == "lsn_100"


# ---------------------------------------------------------------------------
# Version Operations
# ---------------------------------------------------------------------------


class TestVersionList:

    @pytest.mark.asyncio
    async def test_version_list_with_branch_id(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("GET", "/versions", FakeResponse(json_data=[
            {"id": "v1", "name": "baseline"},
        ]))

        result = await initialized_provider.handle_tool_call(
            "lkb_version_list", {"branch_id": "br_main"}
        )
        data = json.loads(result)
        assert data["count"] == 1
        assert data["versions"][0]["name"] == "baseline"

    @pytest.mark.asyncio
    async def test_version_list_defaults_to_current_branch(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("GET", "/branches", FakeResponse(json_data=[
            {"id": "br_main", "name": "main", "is_default": True},
        ]))
        fake_http.set_response("GET", "/versions", FakeResponse(json_data=[
            {"id": "v1", "name": "baseline"},
        ]))

        result = await initialized_provider.handle_tool_call("lkb_version_list", {})
        data = json.loads(result)
        assert data["count"] == 1


class TestVersionCreate:

    @pytest.mark.asyncio
    async def test_version_create_returns_success(self, initialized_provider):
        fake_http = initialized_provider._http
        # _create_version first GETs branches to find default, then POSTs version
        fake_http.set_response("GET", "/branches", FakeResponse(json_data=[
            {"id": "br_main", "name": "main", "is_default": True},
        ]))
        fake_http.set_response("POST", "/versions", FakeResponse(json_data={
            "id": "v_new", "name": "before_refactor",
        }))

        result = await initialized_provider.handle_tool_call(
            "lkb_version_create", {"name": "before_refactor", "description": "stable state"}
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["version"]["name"] == "before_refactor"

    @pytest.mark.asyncio
    async def test_version_create_without_name_returns_error(self, initialized_provider):
        result = await initialized_provider.handle_tool_call("lkb_version_create", {})
        data = json.loads(result)
        assert "name" in data["error"]


class TestVersionDelete:

    @pytest.mark.asyncio
    async def test_version_delete_returns_success(self, initialized_provider):
        fake_http = initialized_provider._http
        # _delete_version first GETs branches to find default, then DELETEs version
        fake_http.set_response("GET", "/branches", FakeResponse(json_data=[
            {"id": "br_main", "name": "main", "is_default": True},
        ]))
        fake_http.set_response("DELETE", "/versions/v1", FakeResponse(json_data={"deleted": True}))

        result = await initialized_provider.handle_tool_call(
            "lkb_version_delete", {"version_id": "v1"}
        )
        data = json.loads(result)
        assert data["success"] is True
        assert data["deleted_version_id"] == "v1"

    @pytest.mark.asyncio
    async def test_version_delete_without_version_id_returns_error(self, initialized_provider):
        result = await initialized_provider.handle_tool_call("lkb_version_delete", {})
        data = json.loads(result)
        assert "version_id" in data["error"]


# ---------------------------------------------------------------------------
# Prefetch
# ---------------------------------------------------------------------------


class TestPrefetch:

    @pytest.mark.asyncio
    async def test_prefetch_returns_formatted_context(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/recall", FakeResponse(json_data={
            "memories": [
                {"content": "likes python", "memory_type": "fact", "score": 0.92},
            ],
        }))

        result = await initialized_provider.prefetch("preferences", top_k=3)
        assert "## Related Memories" in result
        assert "likes python" in result
        assert "[fact]" in result
        assert "0.92" in result

    @pytest.mark.asyncio
    async def test_prefetch_with_memory_type_filter(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/recall", FakeResponse(json_data={
            "memories": [{"content": "c", "memory_type": "episode", "score": 0.5}],
        }))

        result = await initialized_provider.prefetch("query", memory_types=["episode"])
        assert "## Related Memories" in result

        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "recall" in c[1]]
        body = post_calls[0][2]["json"]
        assert body["memory_types"] == ["episode"]

    @pytest.mark.asyncio
    async def test_prefetch_empty_results_returns_empty_string(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/recall", FakeResponse(json_data={"memories": []}))

        result = await initialized_provider.prefetch("nothing")
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_not_initialized_returns_empty(self):
        p = _make_provider()
        result = await p.prefetch("x")
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_empty_query_returns_empty(self, initialized_provider):
        result = await initialized_provider.prefetch("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_failure_returns_empty_string(self, initialized_provider):
        fake_http = initialized_provider._http

        async def _raise(*args, **kwargs):
            raise RuntimeError("recall failed")

        fake_http.post = _raise

        result = await initialized_provider.prefetch("x")
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_default_top_k(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/recall", FakeResponse(json_data={"memories": []}))

        await initialized_provider.prefetch("query")
        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "recall" in c[1]]
        body = post_calls[0][2]["json"]
        assert body["top_k"] == 5  # Default prefetch top_k


# ---------------------------------------------------------------------------
# sync_turn
# ---------------------------------------------------------------------------


class TestSyncTurn:

    @pytest.mark.asyncio
    async def test_sync_turn_stores_episode(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/ingest", FakeResponse(json_data={"memory_id": 1}))

        await initialized_provider.sync_turn("hello", "world")

        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "ingest" in c[1]]
        assert len(post_calls) == 1
        body = post_calls[0][2]["json"]
        assert "hello" in body["content"]
        assert "world" in body["content"]
        assert body["memory_type"] == "episode"
        assert body["importance"] == 0.4  # Default episode importance

    @pytest.mark.asyncio
    async def test_sync_turn_with_custom_importance(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/ingest", FakeResponse(json_data={"memory_id": 1}))

        await initialized_provider.sync_turn("u-msg", "a-msg", importance=0.8)
        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "ingest" in c[1]]
        body = post_calls[0][2]["json"]
        assert body["importance"] == 0.8

    @pytest.mark.asyncio
    async def test_sync_turn_with_metadata(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/ingest", FakeResponse(json_data={"memory_id": 1}))

        await initialized_provider.sync_turn("u-msg", "a-msg", metadata={"source": "test"})
        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "ingest" in c[1]]
        body = post_calls[0][2]["json"]
        assert body["metadata"] == {"source": "test"}

    @pytest.mark.asyncio
    async def test_sync_turn_without_assistant_msg(self, initialized_provider):
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/ingest", FakeResponse(json_data={"memory_id": 1}))

        await initialized_provider.sync_turn("user only", "")
        post_calls = [c for c in fake_http.calls if c[0] == "POST" and "ingest" in c[1]]
        body = post_calls[0][2]["json"]
        # Should not contain "Assistant" when assistant_msg is empty
        assert "Assistant" not in body["content"]

    @pytest.mark.asyncio
    async def test_sync_turn_not_initialized_is_noop(self):
        p = _make_provider()
        await p.sync_turn("u", "a")  # No error

    @pytest.mark.asyncio
    async def test_sync_turn_empty_user_msg_is_noop(self, initialized_provider):
        await initialized_provider.sync_turn("", "a")
        # No ingest calls should be made
        post_calls = [c for c in initialized_provider._http.calls if c[0] == "POST"]
        assert len(post_calls) == 0

    @pytest.mark.asyncio
    async def test_sync_turn_skipped_when_breaker_open(self, initialized_provider):
        initialized_provider._consecutive_failures = 5
        initialized_provider._breaker_until = time.monotonic() + 120

        await initialized_provider.sync_turn("u", "a")
        # No ingest calls should be made
        post_calls = [c for c in initialized_provider._http.calls if c[0] == "POST"]
        assert len(post_calls) == 0

    @pytest.mark.asyncio
    async def test_sync_turn_failure_records_breaker_and_does_not_raise(self, initialized_provider):
        fake_http = initialized_provider._http

        async def _fail_ingest(*args, **kwargs):
            raise httpx.ConnectError("no server")

        fake_http.post = _fail_ingest

        await initialized_provider.sync_turn("u", "a")  # No exception raised
        assert initialized_provider._consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_sync_turn_success_resets_breaker(self, initialized_provider):
        initialized_provider._consecutive_failures = 3
        fake_http = initialized_provider._http
        fake_http.set_response("POST", "/ingest", FakeResponse(json_data={"memory_id": 1}))

        await initialized_provider.sync_turn("u", "a")
        assert initialized_provider._consecutive_failures == 0


# ---------------------------------------------------------------------------
# on_session_end
# ---------------------------------------------------------------------------


class TestOnSessionEnd:

    @pytest.mark.asyncio
    async def test_on_session_end_is_noop(self, initialized_provider):
        # Current implementation is pass
        await initialized_provider.on_session_end([{"role": "user", "content": "msg"}])
        # No HTTP calls should be triggered


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:

    def test_initial_state_is_closed(self, provider):
        assert provider._consecutive_failures == 0
        assert provider._is_breaker_open() is False

    def test_breaker_opens_after_threshold_failures(self, provider):
        for _ in range(5):
            provider._record_failure()
        assert provider._is_breaker_open() is True

    def test_breaker_stays_open_during_cooldown(self, provider):
        provider._consecutive_failures = 5
        provider._breaker_until = time.monotonic() + 120
        assert provider._is_breaker_open() is True

    def test_breaker_closes_after_cooldown(self, provider):
        provider._consecutive_failures = 5
        provider._breaker_until = time.monotonic() - 1  # Already expired
        assert provider._is_breaker_open() is False
        # Should also reset counter
        assert provider._consecutive_failures == 0

    def test_reset_breaker_clears_failures(self, provider):
        provider._consecutive_failures = 4
        provider._reset_breaker()
        assert provider._consecutive_failures == 0

    def test_record_failure_increments_counter(self, provider):
        provider._record_failure()
        assert provider._consecutive_failures == 1
        provider._record_failure()
        assert provider._consecutive_failures == 2

    def test_breaker_threshold_not_reached_stays_closed(self, provider):
        for _ in range(4):
            provider._record_failure()
        assert provider._is_breaker_open() is False


# ---------------------------------------------------------------------------
# Internal API methods (direct calls, not via handle_tool_call)
# ---------------------------------------------------------------------------


class TestInternalAPIMethods:

    @pytest.mark.asyncio
    async def test_ingest_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._ingest("content")

    @pytest.mark.asyncio
    async def test_recall_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._recall("query")

    @pytest.mark.asyncio
    async def test_list_memories_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._list_memories()

    @pytest.mark.asyncio
    async def test_get_memory_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._get_memory(1)

    @pytest.mark.asyncio
    async def test_delete_memory_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._delete_memory(1)

    @pytest.mark.asyncio
    async def test_digest_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._digest()

    @pytest.mark.asyncio
    async def test_list_traits_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._list_traits()

    @pytest.mark.asyncio
    async def test_get_stats_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._get_stats()

    @pytest.mark.asyncio
    async def test_list_branches_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._list_branches()

    @pytest.mark.asyncio
    async def test_create_branch_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._create_branch("test")

    @pytest.mark.asyncio
    async def test_delete_branch_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._delete_branch("br_id")

    @pytest.mark.asyncio
    async def test_promote_branch_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._promote_branch("br_id")

    @pytest.mark.asyncio
    async def test_restore_branch_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._restore_branch("br_id")

    @pytest.mark.asyncio
    async def test_list_versions_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._list_versions()

    @pytest.mark.asyncio
    async def test_create_version_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._create_version("v_name")

    @pytest.mark.asyncio
    async def test_delete_version_without_http_raises_runtime_error(self):
        p = _make_provider()
        with pytest.raises(RuntimeError, match="HTTP client not initialized"):
            await p._delete_version("v_id")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExport:

    def test_lakebase_is_exported_from_external_package(self):
        from openjiuwen.core.memory.external import LakeBaseMemoryProvider
        assert LakeBaseMemoryProvider.__name__ == "LakeBaseMemoryProvider"

    def test_module_all_exports(self):
        from openjiuwen.core.memory.external.lakebase_memory_provider import __all__
        assert "LakeBaseMemoryProvider" in __all__
        assert "MEMORY_TYPES" in __all__
        assert "LKB_BRANCH_LIST_SCHEMA" in __all__
        assert "LKB_VERSION_CREATE_SCHEMA" in __all__