# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for OpenJiuwenMemoryProvider."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.memory.external.openjiuwen_memory_provider import (
    LTM_SEARCH_SCHEMA,
    LTM_SEARCH_SUMMARY_SCHEMA,
    OpenJiuwenMemoryProvider,
)
from openjiuwen.core.memory.long_term_memory import MemInfo, MemResult
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType


def _make_mem_result(mem_id="id1", content="test content", mem_type=MemoryType.USER_PROFILE, score=0.85):
    return MemResult(
        mem_info=MemInfo(mem_id=mem_id, content=content, type=mem_type),
        score=score,
    )


@pytest.fixture
def mock_ltm():
    ltm = MagicMock()
    ltm.kv_store = None
    ltm.register_store = AsyncMock()
    ltm.set_scope_config = AsyncMock()
    ltm.search_user_mem = AsyncMock(return_value=[])
    ltm.search_user_history_summary = AsyncMock(return_value=[])
    ltm.add_messages = AsyncMock()
    return ltm


@pytest.fixture
def mock_kv():
    kv = MagicMock()
    kv.__bool__ = lambda self: True
    return kv


@pytest.fixture
def mock_vector_store():
    vs = MagicMock()
    vs.__bool__ = lambda self: True
    return vs


@pytest.fixture
def mock_db_store():
    db = MagicMock()
    db.__bool__ = lambda self: True
    return db


@pytest.fixture
def mock_embedding():
    emb = MagicMock()
    emb.__bool__ = lambda self: True
    return emb


@pytest.fixture
def provider_with_stores(mock_kv, mock_vector_store, mock_db_store, mock_embedding):
    return OpenJiuwenMemoryProvider(
        kv_store=mock_kv,
        vector_store=mock_vector_store,
        db_store=mock_db_store,
        embedding_model=mock_embedding,
    )


class TestNameAndAvailability:
    def test_name_returns_openjiuwen(self, provider_with_stores):
        assert provider_with_stores.name == "openjiuwen"

    def test_is_available_with_all_stores(self, provider_with_stores):
        assert provider_with_stores.is_available() is True

    def test_is_available_with_embedding_config(self):
        provider = OpenJiuwenMemoryProvider(
            config={"embedding": {"model_name": "text-embedding-ada-002"}}
        )
        assert provider.is_available() is True

    def test_is_available_with_no_stores_no_config(self):
        provider = OpenJiuwenMemoryProvider()
        assert provider.is_available() is False

    def test_is_available_with_empty_config(self):
        provider = OpenJiuwenMemoryProvider(config={})
        assert provider.is_available() is False

    def test_is_available_with_partial_stores(self, mock_kv, mock_vector_store):
        provider = OpenJiuwenMemoryProvider(
            kv_store=mock_kv,
            vector_store=mock_vector_store,
        )
        assert provider.is_available() is False


class TestIsInitialized:
    def test_not_initialized_by_default(self, provider_with_stores):
        assert provider_with_stores.is_initialized is False

    @pytest.mark.asyncio
    async def test_initialized_after_initialize(self, provider_with_stores, mock_ltm):
        with patch(
            "openjiuwen.core.memory.external.openjiuwen_memory_provider.LongTermMemory",
            return_value=mock_ltm,
        ):
            await provider_with_stores.initialize(user_id="u1", scope_id="s1")
            assert provider_with_stores.is_initialized is True

    @pytest.mark.asyncio
    async def test_shutdown_resets_initialized(self, provider_with_stores, mock_ltm):
        with patch(
            "openjiuwen.core.memory.external.openjiuwen_memory_provider.LongTermMemory",
            return_value=mock_ltm,
        ):
            await provider_with_stores.initialize()
            await provider_with_stores.shutdown()
            assert provider_with_stores.is_initialized is False


class TestInitialize:
    @pytest.mark.asyncio
    async def test_initialize_with_pre_provided_stores(self, provider_with_stores, mock_ltm):
        with patch(
            "openjiuwen.core.memory.external.openjiuwen_memory_provider.LongTermMemory",
            return_value=mock_ltm,
        ):
            await provider_with_stores.initialize(user_id="u1", scope_id="s1", session_id="sess1")
            assert provider_with_stores._user_id == "u1"
            assert provider_with_stores._scope_id == "s1"
            assert provider_with_stores._session_id == "sess1"
            mock_ltm.register_store.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_initialize_skips_register_if_ltm_already_has_kv(self, mock_ltm, mock_kv, mock_vector_store, mock_db_store, mock_embedding):
        mock_ltm.kv_store = mock_kv
        provider = OpenJiuwenMemoryProvider(
            kv_store=mock_kv,
            vector_store=mock_vector_store,
            db_store=mock_db_store,
            embedding_model=mock_embedding,
        )
        with patch(
            "openjiuwen.core.memory.external.openjiuwen_memory_provider.LongTermMemory",
            return_value=mock_ltm,
        ):
            await provider.initialize()
            mock_ltm.register_store.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_initialize_sets_scope_config_when_non_default(self, provider_with_stores, mock_ltm):
        from openjiuwen.core.memory.config.config import MemoryScopeConfig

        scope_cfg = MemoryScopeConfig()
        provider = OpenJiuwenMemoryProvider(
            kv_store=provider_with_stores._kv_store,
            vector_store=provider_with_stores._vector_store,
            db_store=provider_with_stores._db_store,
            embedding_model=provider_with_stores._embedding_model,
            scope_config=scope_cfg,
        )
        with patch(
            "openjiuwen.core.memory.external.openjiuwen_memory_provider.LongTermMemory",
            return_value=mock_ltm,
        ):
            await provider.initialize(scope_id="my_scope")
            mock_ltm.set_scope_config.assert_awaited_once_with("my_scope", scope_cfg)

    @pytest.mark.asyncio
    async def test_initialize_skips_scope_config_for_default_scope(self, provider_with_stores, mock_ltm):
        from openjiuwen.core.memory.config.config import MemoryScopeConfig

        scope_cfg = MemoryScopeConfig()
        provider = OpenJiuwenMemoryProvider(
            kv_store=provider_with_stores._kv_store,
            vector_store=provider_with_stores._vector_store,
            db_store=provider_with_stores._db_store,
            embedding_model=provider_with_stores._embedding_model,
            scope_config=scope_cfg,
        )
        with patch(
            "openjiuwen.core.memory.external.openjiuwen_memory_provider.LongTermMemory",
            return_value=mock_ltm,
        ):
            await provider.initialize(scope_id="__default__")

    @pytest.mark.asyncio
    async def test_initialize_creates_stores_from_config(self, mock_ltm):
        provider = OpenJiuwenMemoryProvider(
            config={
                "embedding": {"model_name": "text-embedding-ada-002", "base_url": "http://localhost", "api_key": "key"},
            }
        )
        with patch(
            "openjiuwen.core.memory.external.openjiuwen_memory_provider.LongTermMemory",
            return_value=mock_ltm,
        ), patch.object(provider, "_create_kv_store", return_value=MagicMock()) as mock_kv_create, \
             patch.object(provider, "_create_vector_store", return_value=MagicMock()) as mock_vec_create, \
             patch.object(provider, "_create_db_store", return_value=MagicMock()) as mock_db_create, \
             patch.object(provider, "_create_embedding", return_value=MagicMock()) as mock_emb_create:
            await provider.initialize()
            mock_kv_create.assert_called_once()
            mock_vec_create.assert_called_once()
            mock_db_create.assert_called_once()
            mock_emb_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_fails_if_store_creation_returns_none(self, mock_ltm):
        provider = OpenJiuwenMemoryProvider(config={})
        with patch(
            "openjiuwen.core.memory.external.openjiuwen_memory_provider.LongTermMemory",
            return_value=mock_ltm,
        ), patch.object(provider, "_create_kv_store", return_value=None):
            await provider.initialize()
            assert provider.is_initialized is False


class TestSystemPromptBlock:
    def test_returns_non_empty_string(self, provider_with_stores):
        prompt = provider_with_stores.system_prompt_block()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "ltm_search" in prompt


class TestGetToolSchemas:
    def test_returns_two_schemas(self, provider_with_stores):
        schemas = provider_with_stores.get_tool_schemas()
        assert len(schemas) == 2
        names = [s["name"] for s in schemas]
        assert "ltm_search" in names
        assert "ltm_search_summary" in names

    def test_ltm_search_schema_structure(self):
        assert LTM_SEARCH_SCHEMA["name"] == "ltm_search"
        assert "query" in LTM_SEARCH_SCHEMA["parameters"]["properties"]
        assert LTM_SEARCH_SCHEMA["parameters"]["required"] == ["query"]

    def test_ltm_search_summary_schema_structure(self):
        assert LTM_SEARCH_SUMMARY_SCHEMA["name"] == "ltm_search_summary"
        assert "query" in LTM_SEARCH_SUMMARY_SCHEMA["parameters"]["properties"]
        assert LTM_SEARCH_SUMMARY_SCHEMA["parameters"]["required"] == ["query"]


class TestHandleToolCall:
    @pytest.mark.asyncio
    async def test_not_initialized_returns_error(self):
        provider = OpenJiuwenMemoryProvider()
        result = await provider.handle_tool_call("ltm_search", {"query": "test"})
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, provider_with_stores, mock_ltm):
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True
        result = await provider_with_stores.handle_tool_call("unknown_tool", {})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "unknown_tool" in parsed["error"]

    @pytest.mark.asyncio
    async def test_ltm_search_returns_results(self, provider_with_stores, mock_ltm):
        mock_ltm.search_user_mem.return_value = [
            _make_mem_result(mem_id="m1", content="likes Python", mem_type=MemoryType.USER_PROFILE, score=0.92),
        ]
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        result = await provider_with_stores.handle_tool_call("ltm_search", {"query": "likes what"})
        parsed = json.loads(result)
        assert parsed["count"] == 1
        assert parsed["results"][0]["id"] == "m1"
        assert parsed["results"][0]["content"] == "likes Python"
        assert parsed["results"][0]["type"] == "user_profile"
        assert parsed["results"][0]["score"] == 0.92

    @pytest.mark.asyncio
    async def test_ltm_search_summary_returns_results(self, provider_with_stores, mock_ltm):
        mock_ltm.search_user_history_summary.return_value = [
            _make_mem_result(mem_id="s1", content="discussed Rust vs Java", score=0.78),
        ]
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        result = await provider_with_stores.handle_tool_call("ltm_search_summary", {"query": "Rust"})
        parsed = json.loads(result)
        assert parsed["count"] == 1
        assert parsed["results"][0]["content"] == "discussed Rust vs Java"

    @pytest.mark.asyncio
    async def test_ltm_search_empty_results(self, provider_with_stores, mock_ltm):
        mock_ltm.search_user_mem.return_value = []
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        result = await provider_with_stores.handle_tool_call("ltm_search", {"query": "nothing"})
        parsed = json.loads(result)
        assert parsed["count"] == 0
        assert parsed["results"] == []

    @pytest.mark.asyncio
    async def test_handle_tool_call_exception_returns_error(self, provider_with_stores, mock_ltm):
        mock_ltm.search_user_mem.side_effect = RuntimeError("db down")
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        result = await provider_with_stores.handle_tool_call("ltm_search", {"query": "test"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "db down" in parsed["error"]


class TestPrefetch:
    @pytest.mark.asyncio
    async def test_not_initialized_returns_empty(self):
        provider = OpenJiuwenMemoryProvider()
        result = await provider.prefetch("test query")
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_with_mem_results(self, provider_with_stores, mock_ltm):
        mock_ltm.search_user_mem.return_value = [
            _make_mem_result(content="likes Rust", mem_type=MemoryType.EPISODIC_MEMORY, score=0.88),
        ]
        mock_ltm.search_user_history_summary.return_value = []
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        result = await provider_with_stores.prefetch("Rust", user_id="u1", scope_id="s1")
        assert "## Related Memories" in result
        assert "likes Rust" in result
        assert "episodic_memory" in result
        mock_ltm.search_user_mem.assert_awaited_once_with(
            query="Rust", num=5, user_id="u1", scope_id="s1", threshold=0.3,
        )

    @pytest.mark.asyncio
    async def test_prefetch_with_summary_results(self, provider_with_stores, mock_ltm):
        mock_ltm.search_user_mem.return_value = []
        mock_ltm.search_user_history_summary.return_value = [
            _make_mem_result(content="discussed ownership", score=0.75),
        ]
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        result = await provider_with_stores.prefetch("ownership")
        assert "## Related History Summaries" in result
        assert "discussed ownership" in result

    @pytest.mark.asyncio
    async def test_prefetch_with_both_results(self, provider_with_stores, mock_ltm):
        mock_ltm.search_user_mem.return_value = [
            _make_mem_result(content="likes Rust", score=0.9),
        ]
        mock_ltm.search_user_history_summary.return_value = [
            _make_mem_result(content="Rust summary", score=0.7),
        ]
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        result = await provider_with_stores.prefetch("Rust")
        assert "## Related Memories" in result
        assert "## Related History Summaries" in result

    @pytest.mark.asyncio
    async def test_prefetch_no_results_returns_empty(self, provider_with_stores, mock_ltm):
        mock_ltm.search_user_mem.return_value = []
        mock_ltm.search_user_history_summary.return_value = []
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        result = await provider_with_stores.prefetch("nothing")
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_search_exception_returns_partial(self, provider_with_stores, mock_ltm):
        mock_ltm.search_user_mem.side_effect = RuntimeError("search error")
        mock_ltm.search_user_history_summary.return_value = [
            _make_mem_result(content="fallback summary", score=0.6),
        ]
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        result = await provider_with_stores.prefetch("test")
        assert "fallback summary" in result
        assert "## Related Memories" not in result

    @pytest.mark.asyncio
    async def test_prefetch_uses_default_user_scope(self, provider_with_stores, mock_ltm):
        mock_ltm.search_user_mem.return_value = []
        mock_ltm.search_user_history_summary.return_value = []
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        await provider_with_stores.prefetch("test")
        mock_ltm.search_user_mem.assert_awaited_once_with(
            query="test", num=5, user_id="__default__", scope_id="__default__", threshold=0.3,
        )


class TestSyncTurn:
    @pytest.mark.asyncio
    async def test_not_initialized_does_nothing(self, mock_ltm):
        provider = OpenJiuwenMemoryProvider()
        provider._ltm = mock_ltm
        await provider.sync_turn("hello", "hi")
        mock_ltm.add_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_turn_with_both_messages(self, provider_with_stores, mock_ltm):
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        await provider_with_stores.sync_turn("hello", "hi there", user_id="u1", scope_id="s1", session_id="sess1")
        mock_ltm.add_messages.assert_awaited_once()
        call_args = mock_ltm.add_messages.call_args
        messages = call_args[0][0]
        assert len(messages) == 2
        assert call_args.kwargs["user_id"] == "u1"
        assert call_args.kwargs["scope_id"] == "s1"
        assert call_args.kwargs["session_id"] == "sess1"

    @pytest.mark.asyncio
    async def test_sync_turn_with_only_user_msg(self, provider_with_stores, mock_ltm):
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        await provider_with_stores.sync_turn("hello", "")
        messages = mock_ltm.add_messages.call_args[0][0]
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_sync_turn_with_only_assistant_msg(self, provider_with_stores, mock_ltm):
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        await provider_with_stores.sync_turn("", "hi there")
        messages = mock_ltm.add_messages.call_args[0][0]
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_sync_turn_with_empty_messages_does_nothing(self, provider_with_stores, mock_ltm):
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        await provider_with_stores.sync_turn("", "")
        mock_ltm.add_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_turn_exception_is_swallowed(self, provider_with_stores, mock_ltm):
        mock_ltm.add_messages.side_effect = RuntimeError("write failed")
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        await provider_with_stores.sync_turn("hello", "hi")

    @pytest.mark.asyncio
    async def test_sync_turn_uses_default_ids(self, provider_with_stores, mock_ltm):
        provider_with_stores._ltm = mock_ltm
        provider_with_stores._is_initialized = True

        await provider_with_stores.sync_turn("hello", "hi")
        call_kwargs = mock_ltm.add_messages.call_args.kwargs
        assert call_kwargs["user_id"] == "__default__"
        assert call_kwargs["scope_id"] == "__default__"
        assert call_kwargs["session_id"] == "__default__"


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_resets_initialized(self, provider_with_stores, mock_ltm):
        with patch(
            "openjiuwen.core.memory.external.openjiuwen_memory_provider.LongTermMemory",
            return_value=mock_ltm,
        ):
            await provider_with_stores.initialize()
            assert provider_with_stores.is_initialized is True
            await provider_with_stores.shutdown()
            assert provider_with_stores.is_initialized is False
