# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for JiuwenMemoryProvider (dual-mode: server + sdk).

These tests exercise the provider purely through its public ``MemoryProvider``
surface (``initialize`` / ``prefetch`` / ``sync_turn`` / ``handle_tool_call`` /
``shutdown`` / ``get_tool_schemas`` / ``system_prompt_block`` / ``is_available``
/ ``is_initialized`` / ``mode`` / ``name``). No private attributes or internal
backend classes are accessed.

- SDK-mode backend is driven through the real ``initialize()`` path by patching
  the ``LongTermMemory`` source, then asserting on the injected mock's calls.
- Server-mode backend is driven through ``initialize()`` with a mocked
  ``httpx.AsyncClient``.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.memory.external.jiuwen_memory_provider import (
    JiuwenMemoryProvider,
    LTM_SEARCH_SCHEMA,
    LTM_SEARCH_SUMMARY_SCHEMA,
    _SERVER_READ_TIMEOUT,
    _SERVER_WRITE_TIMEOUT,
)
from jiuwen_memory.memory_core.long_term_memory import MemInfo, MemResult
from jiuwen_memory.memory_core.manage.mem_model.memory_unit import MemoryType


LTM_SOURCE = "jiuwen_memory.memory_core.long_term_memory.LongTermMemory"


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_mem_result(mem_id="id1", content="test content", mem_type=MemoryType.USER_PROFILE, score=0.85):
    return MemResult(
        mem_info=MemInfo(mem_id=mem_id, content=content, type=mem_type),
        score=score,
    )


def _make_http_result_row(mem_id="id1", content="test content", mem_type="user_profile", score=0.85):
    return {"mem_id": mem_id, "content": content, "type": mem_type, "score": score}


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
def sdk_stores(mock_kv, mock_vector_store, mock_db_store, mock_embedding):
    """kwarg bundle for constructing an SDK-mode provider with mock stores."""
    return dict(
        kv_store=mock_kv,
        vector_store=mock_vector_store,
        db_store=mock_db_store,
        embedding_model=mock_embedding,
    )


# ---------------------------------------------------------------------------
# Construction & mode selection (public surface only)
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_default_mode_is_server(self):
        provider = JiuwenMemoryProvider()
        assert provider.mode == "server"

    def test_name_is_jiuwen_for_both_modes(self):
        assert JiuwenMemoryProvider().name == "jiuwen"
        assert JiuwenMemoryProvider(mode="sdk").name == "jiuwen"

    def test_mode_is_case_insensitive(self):
        assert JiuwenMemoryProvider(mode="SDK").mode == "sdk"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Unsupported mode"):
            JiuwenMemoryProvider(mode="bogus")

    def test_server_mode_is_available_with_base_url(self):
        assert JiuwenMemoryProvider(mode="server", base_url="http://localhost:8000").is_available() is True

    def test_server_mode_not_available_with_empty_base_url(self):
        assert JiuwenMemoryProvider(mode="server", base_url="").is_available() is False

    def test_server_mode_not_available_with_none_base_url(self):
        assert JiuwenMemoryProvider(mode="server", base_url=None).is_available() is False

    def test_server_mode_not_initialized_by_default(self):
        assert JiuwenMemoryProvider(mode="server").is_initialized is False


# ---------------------------------------------------------------------------
# Shared surface (tools / prompt) — mode-agnostic
# ---------------------------------------------------------------------------


class TestSharedSurface:
    def test_get_tool_schemas(self):
        names = [s["name"] for s in JiuwenMemoryProvider().get_tool_schemas()]
        assert names == ["ltm_search", "ltm_search_summary"]

    def test_system_prompt_block(self):
        prompt = JiuwenMemoryProvider().system_prompt_block()
        assert isinstance(prompt, str)
        assert "ltm_search" in prompt
        assert len(prompt) > 0

    def test_schema_constants(self):
        assert LTM_SEARCH_SCHEMA["name"] == "ltm_search"
        assert LTM_SEARCH_SUMMARY_SCHEMA["name"] == "ltm_search_summary"
        assert LTM_SEARCH_SCHEMA["parameters"]["required"] == ["query"]


# ---------------------------------------------------------------------------
# SDK-mode availability
# ---------------------------------------------------------------------------


class TestSDKAvailability:
    def test_available_with_all_stores(self, sdk_stores):
        assert JiuwenMemoryProvider(mode="sdk", **sdk_stores).is_available() is True

    def test_available_with_embedding_config(self):
        provider = JiuwenMemoryProvider(
            mode="sdk", config={"embedding": {"model_name": "text-embedding-ada-002"}}
        )
        assert provider.is_available() is True

    def test_not_available_with_no_stores_no_config(self):
        assert JiuwenMemoryProvider(mode="sdk").is_available() is False

    def test_not_available_with_partial_stores(self, mock_kv, mock_vector_store):
        provider = JiuwenMemoryProvider(mode="sdk", kv_store=mock_kv, vector_store=mock_vector_store)
        assert provider.is_available() is False

    def test_not_initialized_by_default(self, sdk_stores):
        assert JiuwenMemoryProvider(mode="sdk", **sdk_stores).is_initialized is False


# ---------------------------------------------------------------------------
# SDK-mode lifecycle (driven through real initialize())
# ---------------------------------------------------------------------------


class TestSDKLifecycle:
    @pytest.mark.asyncio
    async def test_initialize_marks_initialized_and_registers_store(self, sdk_stores, mock_ltm):
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize(user_id="u1", scope_id="s1")
        assert provider.is_initialized is True
        mock_ltm.register_store.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_initialize_skips_register_when_engine_already_bound(
        self, sdk_stores, mock_ltm, mock_kv
    ):
        # Simulate an engine that already has a kv store bound: register_store
        # must not be called.
        mock_ltm.kv_store = mock_kv
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
        mock_ltm.register_store.assert_not_awaited()
        assert provider.is_initialized is True

    @pytest.mark.asyncio
    async def test_shutdown_resets_initialized(self, sdk_stores, mock_ltm):
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            assert provider.is_initialized is True
            await provider.shutdown()
        assert provider.is_initialized is False


# ---------------------------------------------------------------------------
# SDK-mode prefetch (driven through real initialize())
# ---------------------------------------------------------------------------


class TestSDKPrefetch:
    @pytest.mark.asyncio
    async def test_prefetch_before_init_returns_empty(self):
        provider = JiuwenMemoryProvider(mode="sdk", config={})
        assert await provider.prefetch("anything") == ""

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self, sdk_stores, mock_ltm):
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            assert await provider.prefetch("") == ""
        mock_ltm.search_user_mem.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_prefetch_with_mem_results(self, sdk_stores, mock_ltm):
        mock_ltm.search_user_mem.return_value = [
            _make_mem_result(content="likes Rust", mem_type=MemoryType.EPISODIC_MEMORY, score=0.88),
        ]
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            result = await provider.prefetch("Rust", user_id="u1", scope_id="s1")
        assert "## Related Memories" in result
        assert "likes Rust" in result
        assert "episodic_memory" in result
        mock_ltm.search_user_mem.assert_awaited_once_with(
            query="Rust", num=5, user_id="u1", scope_id="s1", threshold=0.3,
        )

    @pytest.mark.asyncio
    async def test_prefetch_with_summary_results(self, sdk_stores, mock_ltm):
        mock_ltm.search_user_history_summary.return_value = [
            _make_mem_result(content="discussed ownership", score=0.75),
        ]
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            result = await provider.prefetch("ownership")
        assert "## Related History Summaries" in result
        assert "discussed ownership" in result

    @pytest.mark.asyncio
    async def test_prefetch_no_results_returns_empty(self, sdk_stores, mock_ltm):
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            assert await provider.prefetch("nothing") == ""

    @pytest.mark.asyncio
    async def test_prefetch_search_exception_returns_partial(self, sdk_stores, mock_ltm):
        mock_ltm.search_user_mem.side_effect = RuntimeError("search error")
        mock_ltm.search_user_history_summary.return_value = [
            _make_mem_result(content="fallback summary", score=0.6),
        ]
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            result = await provider.prefetch("test")
        assert "fallback summary" in result
        assert "## Related Memories" not in result


# ---------------------------------------------------------------------------
# SDK-mode sync_turn
# ---------------------------------------------------------------------------


class TestSDKSyncTurn:
    @pytest.mark.asyncio
    async def test_sync_turn_before_init_does_nothing(self, mock_ltm):
        provider = JiuwenMemoryProvider(mode="sdk", config={})
        await provider.sync_turn("hello", "hi")
        mock_ltm.add_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_turn_with_both_messages(self, sdk_stores, mock_ltm):
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            await provider.sync_turn("hello", "hi there", user_id="u1", scope_id="s1")
        mock_ltm.add_messages.assert_awaited_once()
        call_args = mock_ltm.add_messages.call_args
        messages = call_args[0][0]
        assert len(messages) == 2
        assert call_args.kwargs["user_id"] == "u1"
        assert call_args.kwargs["scope_id"] == "s1"
        assert "session_id" not in call_args.kwargs

    @pytest.mark.asyncio
    async def test_sync_turn_empty_messages_does_nothing(self, sdk_stores, mock_ltm):
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            await provider.sync_turn("", "")
        mock_ltm.add_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_turn_exception_swallowed(self, sdk_stores, mock_ltm):
        mock_ltm.add_messages.side_effect = RuntimeError("write failed")
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            await provider.sync_turn("hello", "hi")  # must not raise


# ---------------------------------------------------------------------------
# SDK-mode handle_tool_call
# ---------------------------------------------------------------------------


class TestSDKHandleToolCall:
    @pytest.mark.asyncio
    async def test_before_init_returns_error(self):
        provider = JiuwenMemoryProvider(mode="sdk", config={})
        parsed = json.loads(await provider.handle_tool_call("ltm_search", {"query": "test"}))
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, sdk_stores, mock_ltm):
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            parsed = json.loads(await provider.handle_tool_call("unknown_tool", {}))
        assert "error" in parsed and "unknown_tool" in parsed["error"]

    @pytest.mark.asyncio
    async def test_ltm_search_returns_results(self, sdk_stores, mock_ltm):
        mock_ltm.search_user_mem.return_value = [
            _make_mem_result(mem_id="m1", content="likes Python", mem_type=MemoryType.USER_PROFILE, score=0.92),
        ]
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            parsed = json.loads(await provider.handle_tool_call("ltm_search", {"query": "likes what"}))
        assert parsed["count"] == 1
        assert parsed["results"][0]["mem_id"] == "m1"
        assert parsed["results"][0]["content"] == "likes Python"
        assert parsed["results"][0]["type"] == "user_profile"

    @pytest.mark.asyncio
    async def test_ltm_search_summary_returns_results(self, sdk_stores, mock_ltm):
        mock_ltm.search_user_history_summary.return_value = [
            _make_mem_result(mem_id="s1", content="discussed Rust", score=0.78),
        ]
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            parsed = json.loads(await provider.handle_tool_call("ltm_search_summary", {"query": "Rust"}))
        assert parsed["count"] == 1
        assert parsed["results"][0]["content"] == "discussed Rust"

    @pytest.mark.asyncio
    async def test_handle_tool_call_exception_returns_error(self, sdk_stores, mock_ltm):
        mock_ltm.search_user_mem.side_effect = RuntimeError("db down")
        provider = JiuwenMemoryProvider(mode="sdk", **sdk_stores)
        with patch(LTM_SOURCE, return_value=mock_ltm):
            await provider.initialize()
            parsed = json.loads(await provider.handle_tool_call("ltm_search", {"query": "test"}))
        assert "error" in parsed and "db down" in parsed["error"]


# ---------------------------------------------------------------------------
# Server-mode — mocked httpx.AsyncClient (driven through real initialize())
# ---------------------------------------------------------------------------


def _mock_response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


def _fake_http(get_side_effect=ConnectionError("refused"), post_value=None, post_side_effect=None):
    """Build a MagicMock standing in for httpx.AsyncClient.

    The health-check GET defaults to failing (non-fatal); POSTs default to a
    no-op success response.
    """
    http = MagicMock()
    http.get = AsyncMock(side_effect=get_side_effect)
    if post_side_effect is not None:
        http.post = AsyncMock(side_effect=post_side_effect)
    else:
        http.post = AsyncMock(return_value=post_value if post_value is not None else _mock_response({"results": []}))
    http.aclose = AsyncMock()
    return http


class TestServerInitialize:
    @pytest.mark.asyncio
    async def test_initialize_creates_http_client_and_checks_health(self):
        provider = JiuwenMemoryProvider(mode="server", base_url="http://localhost:8000")
        fake_http = _fake_http(get_side_effect=_mock_response({"status": "healthy"}))
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
        assert provider.is_initialized is True
        fake_http.get.assert_awaited_once_with("/health")

    @pytest.mark.asyncio
    async def test_initialize_swallows_unreachable_health(self):
        provider = JiuwenMemoryProvider(mode="server", base_url="http://nowhere:9999")
        fake_http = _fake_http()
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
        assert provider.is_initialized is True  # health failure must not be fatal

    @pytest.mark.asyncio
    async def test_initialize_passes_authorization_header(self):
        # Verify api_key flows into the client headers via observable behavior:
        # the client is built with the Authorization header in the headers dict.
        provider = JiuwenMemoryProvider(mode="server", api_key="secret-token")
        with patch("httpx.AsyncClient") as client_cls:
            await provider.initialize()
        _, kwargs = client_cls.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer secret-token"

    @pytest.mark.asyncio
    async def test_shutdown_closes_client(self):
        provider = JiuwenMemoryProvider(mode="server")
        fake_http = _fake_http()
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
            await provider.shutdown()
        fake_http.aclose.assert_awaited_once()
        assert provider.is_initialized is False


class TestServerPrefetch:
    @pytest.mark.asyncio
    async def test_prefetch_before_init_returns_empty(self):
        assert await JiuwenMemoryProvider(mode="server").prefetch("test") == ""

    @pytest.mark.asyncio
    async def test_prefetch_formats_results(self):
        provider = JiuwenMemoryProvider(mode="server")
        fake_http = _fake_http(post_side_effect=[
            _mock_response({"results": [_make_http_result_row(content="likes Python")]}),
            _mock_response({"results": [_make_http_result_row(content="chat summary", mem_type="summary")]}),
        ])
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
            result = await provider.prefetch("Python", user_id="u1", scope_id="s1")
        assert "## Related Memories" in result
        assert "likes Python" in result
        assert "## Related History Summaries" in result
        assert "chat summary" in result
        # Two POSTs: /search_memory/ then /search_user_history_summary/
        assert fake_http.post.await_count == 2
        assert fake_http.post.call_args_list[0][0][0] == "/search_memory/"
        assert fake_http.post.call_args_list[1][0][0] == "/search_user_history_summary/"

    @pytest.mark.asyncio
    async def test_prefetch_no_results_returns_empty(self):
        provider = JiuwenMemoryProvider(mode="server")
        fake_http = _fake_http(post_value=_mock_response({"results": []}))
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
            assert await provider.prefetch("nothing") == ""

    @pytest.mark.asyncio
    async def test_prefetch_search_failure_returns_partial(self):
        provider = JiuwenMemoryProvider(mode="server")
        fake_http = _fake_http(post_side_effect=[
            Exception("boom"),
            _mock_response({"results": [_make_http_result_row(content="fallback summary", mem_type="summary")]}),
        ])
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
            result = await provider.prefetch("test")
        assert "fallback summary" in result
        assert "## Related Memories" not in result


class TestServerSyncTurn:
    @pytest.mark.asyncio
    async def test_sync_turn_before_init_does_nothing(self):
        provider = JiuwenMemoryProvider(mode="server")
        await provider.sync_turn("hello", "hi")  # no http client yet; must not raise

    @pytest.mark.asyncio
    async def test_sync_turn_posts_add_messages(self):
        provider = JiuwenMemoryProvider(mode="server")
        fake_http = _fake_http(post_value=_mock_response({"status": "success"}))
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
            await provider.sync_turn("hello", "hi there", user_id="u1", scope_id="s1")
        fake_http.post.assert_awaited_once()
        path, kwargs = fake_http.post.call_args[0][0], fake_http.post.call_args[1]
        assert path == "/add_messages/"
        payload = kwargs["json"]
        assert payload["messages"] == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        assert payload["user_id"] == "u1"
        assert payload["scope_id"] == "s1"
        # add_messages triggers LLM extraction → write path uses a larger timeout
        assert kwargs["timeout"] == _SERVER_WRITE_TIMEOUT
        assert kwargs["timeout"] > _SERVER_READ_TIMEOUT

    @pytest.mark.asyncio
    async def test_sync_turn_empty_messages_does_nothing(self):
        provider = JiuwenMemoryProvider(mode="server")
        fake_http = _fake_http()
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
            await provider.sync_turn("", "")
        fake_http.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_turn_exception_swallowed(self):
        provider = JiuwenMemoryProvider(mode="server")
        fake_http = _fake_http(post_side_effect=Exception("server down"))
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
            await provider.sync_turn("hello", "hi")  # must not raise


class TestServerHandleToolCall:
    @pytest.mark.asyncio
    async def test_before_init_returns_error(self):
        provider = JiuwenMemoryProvider(mode="server")
        parsed = json.loads(await provider.handle_tool_call("ltm_search", {"query": "test"}))
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        provider = JiuwenMemoryProvider(mode="server")
        fake_http = _fake_http()
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
            parsed = json.loads(await provider.handle_tool_call("unknown_tool", {}))
        assert "error" in parsed and "unknown_tool" in parsed["error"]

    @pytest.mark.asyncio
    async def test_ltm_search_returns_results(self):
        provider = JiuwenMemoryProvider(mode="server")
        fake_http = _fake_http(post_value=_mock_response({
            "results": [_make_http_result_row(mem_id="m1", content="likes Python")],
        }))
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
            parsed = json.loads(await provider.handle_tool_call("ltm_search", {"query": "Python"}))
        assert parsed["count"] == 1
        assert parsed["results"][0]["content"] == "likes Python"
        assert fake_http.post.call_args[0][0] == "/search_memory/"

    @pytest.mark.asyncio
    async def test_ltm_search_summary_returns_results(self):
        provider = JiuwenMemoryProvider(mode="server")
        fake_http = _fake_http(post_value=_mock_response({
            "results": [_make_http_result_row(mem_id="s1", content="discussed Rust", mem_type="summary")],
        }))
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
            parsed = json.loads(await provider.handle_tool_call("ltm_search_summary", {"query": "Rust"}))
        assert parsed["count"] == 1
        assert parsed["results"][0]["content"] == "discussed Rust"
        assert fake_http.post.call_args[0][0] == "/search_user_history_summary/"

    @pytest.mark.asyncio
    async def test_handle_tool_call_exception_returns_error(self):
        provider = JiuwenMemoryProvider(mode="server")
        fake_http = _fake_http(post_side_effect=Exception("network down"))
        with patch("httpx.AsyncClient", return_value=fake_http):
            await provider.initialize()
            parsed = json.loads(await provider.handle_tool_call("ltm_search", {"query": "test"}))
        assert "error" in parsed
