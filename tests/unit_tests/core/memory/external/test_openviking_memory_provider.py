# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for OpenVikingMemoryProvider."""

import json
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from openjiuwen.core.memory.external.openviking_memory_provider import (
    VIKING_ADD_RESOURCE_SCHEMA,
    VIKING_BROWSE_SCHEMA,
    VIKING_READ_SCHEMA,
    VIKING_REMEMBER_SCHEMA,
    VIKING_SEARCH_SCHEMA,
    OpenVikingMemoryProvider,
    _VikingClient,
)


@pytest.fixture
def mock_client():
    client = MagicMock(spec=_VikingClient)
    client.health.return_value = True
    client.post.return_value = {}
    client.get.return_value = {}
    client.close.return_value = None
    return client


@pytest.fixture
def provider():
    return OpenVikingMemoryProvider(endpoint="http://localhost:8080", api_key="test-key")


@pytest.fixture
def initialized_provider(provider, mock_client):
    provider._client = mock_client
    provider._session_id = "sess-123"
    return provider


class TestNameAndAvailability:
    def test_name_returns_openviking(self, provider):
        assert provider.name == "openviking"

    def test_is_available_with_endpoint(self, provider):
        assert provider.is_available() is True

    def test_is_available_without_endpoint(self):
        provider = OpenVikingMemoryProvider()
        assert provider.is_available() is False

    def test_is_available_with_empty_endpoint(self):
        provider = OpenVikingMemoryProvider(endpoint="")
        assert provider.is_available() is False

    def test_is_available_reads_env_var(self):
        with patch.dict("os.environ", {"OPENVIKING_ENDPOINT": "http://env-host:9090"}):
            provider = OpenVikingMemoryProvider()
            assert provider.is_available() is True
            assert provider._endpoint == "http://env-host:9090"


class TestIsInitialized:
    def test_not_initialized_by_default(self, provider):
        assert provider.is_initialized is False

    @pytest.mark.asyncio
    async def test_initialized_after_initialize(self, provider, mock_client):
        with patch(
            "openjiuwen.core.memory.external.openviking_memory_provider._VikingClient",
            return_value=mock_client,
        ):
            await provider.initialize(session_id="sess-1")
            assert provider.is_initialized is True

    @pytest.mark.asyncio
    async def test_not_initialized_if_health_check_fails(self, provider):
        unhealthy_client = MagicMock(spec=_VikingClient)
        unhealthy_client.health.return_value = False
        with patch(
            "openjiuwen.core.memory.external.openviking_memory_provider._VikingClient",
            return_value=unhealthy_client,
        ):
            await provider.initialize()
            assert provider.is_initialized is False

    @pytest.mark.asyncio
    async def test_shutdown_resets_initialized(self, initialized_provider):
        assert initialized_provider.is_initialized is True
        await initialized_provider.shutdown()
        assert initialized_provider.is_initialized is False


class TestInitialize:
    @pytest.mark.asyncio
    async def test_initialize_creates_client_and_checks_health(self, provider, mock_client):
        with patch(
            "openjiuwen.core.memory.external.openviking_memory_provider._VikingClient",
            return_value=mock_client,
        ) as MockClient:
            await provider.initialize(session_id="sess-1")
            MockClient.assert_called_once_with(
                "http://localhost:8080", "test-key",
                account="default", user="default", agent="hermes",
            )
            mock_client.health.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_sets_session_id(self, provider, mock_client):
        with patch(
            "openjiuwen.core.memory.external.openviking_memory_provider._VikingClient",
            return_value=mock_client,
        ):
            await provider.initialize(session_id="my-session")
            assert provider._session_id == "my-session"

    @pytest.mark.asyncio
    async def test_initialize_sets_empty_session_id_by_default(self, provider, mock_client):
        with patch(
            "openjiuwen.core.memory.external.openviking_memory_provider._VikingClient",
            return_value=mock_client,
        ):
            await provider.initialize()
            assert provider._session_id == ""

    @pytest.mark.asyncio
    async def test_initialize_handles_import_error(self, provider):
        with patch(
            "openjiuwen.core.memory.external.openviking_memory_provider._VikingClient",
            side_effect=ImportError("no httpx"),
        ):
            await provider.initialize()
            assert provider.is_initialized is False

    @pytest.mark.asyncio
    async def test_initialize_handles_general_exception(self, provider):
        with patch(
            "openjiuwen.core.memory.external.openviking_memory_provider._VikingClient",
            side_effect=RuntimeError("connection refused"),
        ):
            await provider.initialize()
            assert provider.is_initialized is False

    @pytest.mark.asyncio
    async def test_initialize_uses_env_vars_for_account_and_user(self):
        with patch.dict("os.environ", {
            "OPENVIKING_ENDPOINT": "http://env:8080",
            "OPENVIKING_ACCOUNT": "myacct",
            "OPENVIKING_USER": "myuser",
        }):
            provider = OpenVikingMemoryProvider()
            mock_client = MagicMock(spec=_VikingClient)
            mock_client.health.return_value = True
            with patch(
                "openjiuwen.core.memory.external.openviking_memory_provider._VikingClient",
                return_value=mock_client,
            ) as MockClient:
                await provider.initialize()
                MockClient.assert_called_once_with(
                    "http://env:8080", "",
                    account="myacct", user="myuser", agent="hermes",
                )


class TestSystemPromptBlock:
    def test_returns_non_empty_string(self, provider):
        prompt = provider.system_prompt_block()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "viking_search" in prompt
        assert "viking_read" in prompt
        assert "viking_browse" in prompt
        assert "viking_remember" in prompt
        assert "viking_add_resource" in prompt


class TestGetToolSchemas:
    def test_returns_five_schemas(self, provider):
        schemas = provider.get_tool_schemas()
        assert len(schemas) == 5
        names = [s["name"] for s in schemas]
        assert names == [
            "viking_search",
            "viking_read",
            "viking_browse",
            "viking_remember",
            "viking_add_resource",
        ]

    def test_viking_search_schema_structure(self):
        assert VIKING_SEARCH_SCHEMA["name"] == "viking_search"
        assert "query" in VIKING_SEARCH_SCHEMA["parameters"]["properties"]
        assert "mode" in VIKING_SEARCH_SCHEMA["parameters"]["properties"]
        assert "top_k" in VIKING_SEARCH_SCHEMA["parameters"]["properties"]
        assert VIKING_SEARCH_SCHEMA["parameters"]["required"] == ["query"]

    def test_viking_read_schema_structure(self):
        assert VIKING_READ_SCHEMA["name"] == "viking_read"
        assert "uri" in VIKING_READ_SCHEMA["parameters"]["properties"]
        assert "detail" in VIKING_READ_SCHEMA["parameters"]["properties"]
        assert VIKING_READ_SCHEMA["parameters"]["required"] == ["uri"]

    def test_viking_browse_schema_structure(self):
        assert VIKING_BROWSE_SCHEMA["name"] == "viking_browse"
        assert "action" in VIKING_BROWSE_SCHEMA["parameters"]["properties"]
        assert "path" in VIKING_BROWSE_SCHEMA["parameters"]["properties"]
        assert VIKING_BROWSE_SCHEMA["parameters"]["required"] == ["action"]

    def test_viking_remember_schema_structure(self):
        assert VIKING_REMEMBER_SCHEMA["name"] == "viking_remember"
        assert "content" in VIKING_REMEMBER_SCHEMA["parameters"]["properties"]
        assert "category" in VIKING_REMEMBER_SCHEMA["parameters"]["properties"]
        assert VIKING_REMEMBER_SCHEMA["parameters"]["required"] == ["content"]

    def test_viking_add_resource_schema_structure(self):
        assert VIKING_ADD_RESOURCE_SCHEMA["name"] == "viking_add_resource"
        assert "url" in VIKING_ADD_RESOURCE_SCHEMA["parameters"]["properties"]
        assert "title" in VIKING_ADD_RESOURCE_SCHEMA["parameters"]["properties"]
        assert VIKING_ADD_RESOURCE_SCHEMA["parameters"]["required"] == ["url"]


class TestHandleToolCall:
    @pytest.mark.asyncio
    async def test_not_initialized_returns_error(self):
        provider = OpenVikingMemoryProvider(endpoint="http://localhost:8080")
        result = await provider.handle_tool_call("viking_search", {"query": "test"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "not connected" in parsed["error"]

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, initialized_provider):
        result = await initialized_provider.handle_tool_call("unknown_tool", {})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "unknown_tool" in parsed["error"]

    @pytest.mark.asyncio
    async def test_viking_search(self, initialized_provider, mock_client):
        mock_client.post.return_value = {
            "result": {
                "memories": [
                    {"uri": "viking://mem/1", "score": 0.9, "abstract": "Python Guide"},
                ],
                "resources": [
                    {"uri": "viking://res/1", "score": 0.8, "abstract": "Rust Book"},
                ],
            }
        }
        result = await initialized_provider.handle_tool_call(
            "viking_search", {"query": "programming", "mode": "deep", "limit": 5},
        )
        parsed = json.loads(result)
        assert len(parsed["results"]) == 2
        mock_client.post.assert_called_once_with(
            "/api/v1/search/find",
            {"query": "programming", "mode": "deep", "top_k": 5},
        )

    @pytest.mark.asyncio
    async def test_viking_search_with_defaults(self, initialized_provider, mock_client):
        mock_client.post.return_value = {"result": {}}
        await initialized_provider.handle_tool_call(
            "viking_search", {"query": "test"},
        )
        mock_client.post.assert_called_once_with(
            "/api/v1/search/find",
            {"query": "test"},
        )

    @pytest.mark.asyncio
    async def test_viking_read_overview(self, initialized_provider, mock_client):
        mock_client.get.return_value = {"result": "Overview text"}
        result = await initialized_provider.handle_tool_call(
            "viking_read", {"uri": "viking://doc/1"},
        )
        parsed = json.loads(result)
        assert parsed["content"] == "Overview text"
        mock_client.get.assert_called_once_with(
            "/api/v1/content/overview",
            {"uri": "viking://doc/1"},
        )

    @pytest.mark.asyncio
    async def test_viking_read_abstract(self, initialized_provider, mock_client):
        mock_client.get.return_value = {"result": "Abstract text"}
        await initialized_provider.handle_tool_call(
            "viking_read", {"uri": "viking://doc/1", "detail": "abstract"},
        )
        mock_client.get.assert_called_once_with(
            "/api/v1/content/abstract",
            {"uri": "viking://doc/1"},
        )

    @pytest.mark.asyncio
    async def test_viking_read_full(self, initialized_provider, mock_client):
        mock_client.get.return_value = {"result": "Full text"}
        await initialized_provider.handle_tool_call(
            "viking_read", {"uri": "viking://doc/1", "detail": "full"},
        )
        mock_client.get.assert_called_once_with(
            "/api/v1/content/read",
            {"uri": "viking://doc/1"},
        )

    @pytest.mark.asyncio
    async def test_viking_browse_list(self, initialized_provider, mock_client):
        mock_client.get.return_value = {
            "result": [{"name": "file1.md", "uri": "viking://file1.md", "isDir": False}]
        }
        result = await initialized_provider.handle_tool_call(
            "viking_browse", {"action": "list", "path": "viking://docs"},
        )
        parsed = json.loads(result)
        assert "entries" in parsed
        mock_client.get.assert_called_once_with(
            "/api/v1/fs/ls",
            {"uri": "viking://docs"},
        )

    @pytest.mark.asyncio
    async def test_viking_browse_tree(self, initialized_provider, mock_client):
        mock_client.get.return_value = {"result": []}
        await initialized_provider.handle_tool_call(
            "viking_browse", {"action": "tree"},
        )
        mock_client.get.assert_called_once_with(
            "/api/v1/fs/tree",
            {"uri": "viking://"},
        )

    @pytest.mark.asyncio
    async def test_viking_browse_stat(self, initialized_provider, mock_client):
        mock_client.get.return_value = {"result": {"size": 1024}}
        await initialized_provider.handle_tool_call(
            "viking_browse", {"action": "stat", "path": "viking://docs/file.md"},
        )
        mock_client.get.assert_called_once_with(
            "/api/v1/fs/stat",
            {"uri": "viking://docs/file.md"},
        )

    @pytest.mark.asyncio
    async def test_viking_remember(self, initialized_provider, mock_client):
        result = await initialized_provider.handle_tool_call(
            "viking_remember", {"content": "User prefers dark mode", "category": "preference"},
        )
        parsed = json.loads(result)
        assert parsed["status"] == "stored"
        mock_client.post.assert_called_once_with(
            "/api/v1/sessions/sess-123/messages",
            {
                "role": "user",
                "parts": [{"type": "text", "text": "[Remember — preference] User prefers dark mode"}],
            },
        )

    @pytest.mark.asyncio
    async def test_viking_remember_default_category(self, initialized_provider, mock_client):
        await initialized_provider.handle_tool_call(
            "viking_remember", {"content": "some fact"},
        )
        mock_client.post.assert_called_once_with(
            "/api/v1/sessions/sess-123/messages",
            {
                "role": "user",
                "parts": [{"type": "text", "text": "[Remember] some fact"}],
            },
        )

    @pytest.mark.asyncio
    async def test_viking_add_resource(self, initialized_provider, mock_client):
        mock_client.post.return_value = {"result": {"root_uri": "viking://res/docs"}}
        result = await initialized_provider.handle_tool_call(
            "viking_add_resource", {"url": "https://example.com/docs", "reason": "Useful docs"},
        )
        parsed = json.loads(result)
        assert parsed["status"] == "added"
        mock_client.post.assert_called_once_with(
            "/api/v1/resources",
            {"path": "https://example.com/docs", "reason": "Useful docs"},
        )

    @pytest.mark.asyncio
    async def test_viking_add_resource_default_title(self, initialized_provider, mock_client):
        mock_client.post.return_value = {"result": {}}
        await initialized_provider.handle_tool_call(
            "viking_add_resource", {"url": "https://example.com"},
        )
        mock_client.post.assert_called_once_with(
            "/api/v1/resources",
            {"path": "https://example.com"},
        )

    @pytest.mark.asyncio
    async def test_handle_tool_call_exception_returns_error(self, initialized_provider, mock_client):
        mock_client.post.side_effect = RuntimeError("network error")
        result = await initialized_provider.handle_tool_call(
            "viking_search", {"query": "test"},
        )
        parsed = json.loads(result)
        assert "error" in parsed
        assert "network error" in parsed["error"]


class TestPrefetch:
    @pytest.mark.asyncio
    async def test_not_initialized_returns_empty(self):
        provider = OpenVikingMemoryProvider(endpoint="http://localhost:8080")
        result = await provider.prefetch("test query")
        assert result == ""

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self, initialized_provider):
        result = await initialized_provider.prefetch("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_with_results(self, initialized_provider, mock_client):
        mock_client.post.return_value = {
            "result": {
                "memories": [
                    {"uri": "viking://mem/1", "abstract": "Memory A", "score": 0.9},
                ],
                "resources": [
                    {"uri": "viking://res/1", "abstract": "Resource B", "score": 0.8},
                ],
            }
        }
        result = await initialized_provider.prefetch("python")
        assert "## OpenViking Context" in result
        assert "Memory A" in result
        assert "Resource B" in result
        mock_client.post.assert_called_once_with(
            "/api/v1/search/find",
            {"query": "python", "top_k": 5},
        )

    @pytest.mark.asyncio
    async def test_prefetch_no_results_returns_empty(self, initialized_provider, mock_client):
        mock_client.post.return_value = {"result": {}}
        result = await initialized_provider.prefetch("nothing")
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_exception_returns_empty(self, initialized_provider, mock_client):
        mock_client.post.side_effect = RuntimeError("timeout")
        result = await initialized_provider.prefetch("test")
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_missing_abstract_skipped(self, initialized_provider, mock_client):
        mock_client.post.return_value = {
            "result": {
                "memories": [
                    {"uri": "viking://mem/1", "score": 0.5},
                ],
            }
        }
        result = await initialized_provider.prefetch("test")
        assert result == ""


class TestSyncTurn:
    @pytest.mark.asyncio
    async def test_not_initialized_does_nothing(self):
        provider = OpenVikingMemoryProvider(endpoint="http://localhost:8080")
        await provider.sync_turn("hello", "hi")

    @pytest.mark.asyncio
    async def test_sync_turn_posts_messages(self, initialized_provider, mock_client):
        await initialized_provider.sync_turn("hello", "hi there")
        calls = mock_client.post.call_args_list
        assert len(calls) == 2
        assert calls[0] == call(
            "/api/v1/sessions/sess-123/messages",
            {"role": "user", "content": "hello"},
        )
        assert calls[1] == call(
            "/api/v1/sessions/sess-123/messages",
            {"role": "assistant", "content": "hi there"},
        )

    @pytest.mark.asyncio
    async def test_sync_turn_uses_kwargs_session_id(self, initialized_provider, mock_client):
        await initialized_provider.sync_turn(
            "hello", "hi", session_id="custom-session",
        )
        calls = mock_client.post.call_args_list
        assert len(calls) == 2
        assert calls[0] == call(
            "/api/v1/sessions/custom-session/messages",
            {"role": "user", "content": "hello"},
        )
        assert calls[1] == call(
            "/api/v1/sessions/custom-session/messages",
            {"role": "assistant", "content": "hi"},
        )

    @pytest.mark.asyncio
    async def test_sync_turn_exception_is_swallowed(self, initialized_provider, mock_client):
        mock_client.post.side_effect = RuntimeError("network error")
        await initialized_provider.sync_turn("hello", "hi")


class TestOnSessionEnd:
    @pytest.mark.asyncio
    async def test_not_initialized_does_nothing(self):
        provider = OpenVikingMemoryProvider(endpoint="http://localhost:8080")
        await provider.on_session_end([])

    @pytest.mark.asyncio
    async def test_no_session_id_does_nothing(self, mock_client):
        provider = OpenVikingMemoryProvider(endpoint="http://localhost:8080")
        provider._client = mock_client
        provider._session_id = ""
        await provider.on_session_end([{"role": "user", "content": "bye"}])
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_end_commits_session(self, initialized_provider, mock_client):
        await initialized_provider.on_session_end(
            [{"role": "user", "content": "bye"}],
        )
        mock_client.post.assert_called_once_with(
            "/api/v1/sessions/sess-123/commit",
            {},
        )

    @pytest.mark.asyncio
    async def test_session_end_exception_is_swallowed(self, initialized_provider, mock_client):
        mock_client.post.side_effect = RuntimeError("commit failed")
        await initialized_provider.on_session_end([])


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_closes_client(self, initialized_provider, mock_client):
        await initialized_provider.shutdown()
        mock_client.close.assert_called_once()
        assert initialized_provider.is_initialized is False

    @pytest.mark.asyncio
    async def test_shutdown_without_client(self):
        provider = OpenVikingMemoryProvider(endpoint="http://localhost:8080")
        await provider.shutdown()
        assert provider.is_initialized is False

    @pytest.mark.asyncio
    async def test_shutdown_handles_close_exception(self, mock_client):
        mock_client.close.side_effect = RuntimeError("close error")
        provider = OpenVikingMemoryProvider(endpoint="http://localhost:8080")
        provider._client = mock_client
        await provider.shutdown()
        assert provider.is_initialized is False
