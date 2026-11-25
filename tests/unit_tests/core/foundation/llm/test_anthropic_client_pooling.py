# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for AnthropicModelClient shared/cached AsyncAnthropic client lifecycle."""

import asyncio
import http.server
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
    UserMessage,
)
from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import AnthropicModelClient
from openjiuwen.core.foundation.llm.schema.config import LLMAuthMode


def _build_mock_anthropic_response(text: str = "ok") -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.usage = None
    resp.stop_reason = "end_turn"
    return resp


def _make_model(use_shared: bool = True) -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.Anthropic,
            api_key="sk-ant",
            api_base="https://api.anthropic.com",
            verify_ssl=False,
            use_shared_llm_http_client=use_shared,
        ),
        model_config=ModelRequestConfig(model="claude-opus-4"),
    )


def _mock_async_client() -> AsyncMock:
    mc = AsyncMock()
    mc.messages.create = AsyncMock(return_value=_build_mock_anthropic_response())
    return mc


@pytest.fixture(autouse=True)
def _clear_client_cache():
    AnthropicModelClient._client_cache.clear()
    AnthropicModelClient._client_cache_loops.clear()
    yield
    AnthropicModelClient._client_cache.clear()
    AnthropicModelClient._client_cache_loops.clear()


class TestSharedClientPooling:
    @pytest.mark.asyncio
    async def test_shared_client_is_built_once_and_reused_without_hot_path_close(self):
        model = _make_model(use_shared=True)
        client = model._client

        mock_async_client = _mock_async_client()
        build_mock = MagicMock(return_value=mock_async_client)
        with patch.object(client, "_build_async_anthropic_client", build_mock):
            await model.invoke(messages=[UserMessage(content="hi")])
            await model.invoke(messages=[UserMessage(content="hi again")])

        assert build_mock.call_count == 1
        assert mock_async_client.messages.create.call_count == 2
        assert mock_async_client.close.call_count == 0

    @pytest.mark.asyncio
    async def test_per_request_timeout_forwarded_without_rebuilding_client(self):
        model = _make_model(use_shared=True)
        client = model._client

        mock_async_client = _mock_async_client()
        build_mock = MagicMock(return_value=mock_async_client)
        with patch.object(client, "_build_async_anthropic_client", build_mock):
            await model.invoke(messages=[UserMessage(content="hi")], timeout=15.0)

        assert build_mock.call_count == 1
        assert mock_async_client.messages.create.call_args.kwargs["timeout"] == 15.0

    @pytest.mark.asyncio
    async def test_aclose_closes_cached_clients_and_clears_cache(self):
        model = _make_model(use_shared=True)
        client = model._client

        mock_async_client = _mock_async_client()
        build_mock = MagicMock(return_value=mock_async_client)
        with patch.object(client, "_build_async_anthropic_client", build_mock):
            await model.invoke(messages=[UserMessage(content="hi")])

        assert AnthropicModelClient._client_cache

        await AnthropicModelClient.aclose()

        assert mock_async_client.close.call_count == 1
        assert AnthropicModelClient._client_cache == {}


class TestAcloseConnections:
    def _cfg(self, api_base: str) -> ModelClientConfig:
        return ModelClientConfig(
            client_provider=ProviderType.Anthropic,
            api_key="sk-ant",
            api_base=api_base,
            verify_ssl=False,
        )

    @pytest.mark.asyncio
    async def test_aclose_connections_closes_only_given(self):
        cfg_keep = self._cfg("https://api.anthropic.com")
        cfg_drop = self._cfg("https://old.example.com")
        keep_client, drop_client = AsyncMock(), AsyncMock()
        AnthropicModelClient._client_cache[AnthropicModelClient.connection_key(cfg_keep)] = keep_client
        AnthropicModelClient._client_cache[AnthropicModelClient.connection_key(cfg_drop)] = drop_client

        await AnthropicModelClient.aclose_connections([cfg_drop])

        # Only the targeted connection is closed; the untouched one stays.
        assert drop_client.close.call_count == 1
        assert keep_client.close.call_count == 0
        assert AnthropicModelClient.connection_key(cfg_keep) in AnthropicModelClient._client_cache
        assert AnthropicModelClient.connection_key(cfg_drop) not in AnthropicModelClient._client_cache

    @pytest.mark.asyncio
    async def test_aclose_connections_unknown_config_is_noop(self):
        cfg = self._cfg("https://api.anthropic.com")
        client = AsyncMock()
        AnthropicModelClient._client_cache[AnthropicModelClient.connection_key(cfg)] = client

        await AnthropicModelClient.aclose_connections([self._cfg("https://other.example.com")])

        assert client.close.call_count == 0
        assert AnthropicModelClient.connection_key(cfg) in AnthropicModelClient._client_cache

    def test_connection_key_includes_auth_mode(self):
        api_key_cfg = self._cfg("https://api.anthropic.com")
        custom_headers_cfg = ModelClientConfig(
            client_provider=ProviderType.Anthropic,
            api_key="sk-ant",
            api_base="https://api.anthropic.com",
            auth_mode=LLMAuthMode.CustomHeaders,
            verify_ssl=False,
        )

        assert (
            AnthropicModelClient.connection_key(api_key_cfg)
            != AnthropicModelClient.connection_key(custom_headers_cfg)
        )


class TestFallbackClient:
    @pytest.mark.asyncio
    async def test_fallback_builds_and_closes_client_per_request(self):
        model = _make_model(use_shared=False)
        client = model._client

        built = []

        def fake_build(timeout=None):
            mc = _mock_async_client()
            built.append(mc)
            return mc

        with patch.object(client, "_build_async_anthropic_client", side_effect=fake_build):
            await model.invoke(messages=[UserMessage(content="hi")])
            await model.invoke(messages=[UserMessage(content="hi again")])

        assert len(built) == 2
        assert all(mc.close.call_count == 1 for mc in built)
        assert AnthropicModelClient._client_cache == {}


class TestSdkHttpClientBinding:
    @pytest.mark.asyncio
    async def test_build_uses_sdk_default_async_httpx_client(self):
        from anthropic import DefaultAsyncHttpxClient

        from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import (
            _anthropic_sdk_httpx_module,
        )

        model = _make_model()
        sdk_client = model._client._build_async_anthropic_client()
        try:
            inner = sdk_client._client
            assert isinstance(inner, DefaultAsyncHttpxClient)
            expected_async_client = _anthropic_sdk_httpx_module().AsyncClient
            assert isinstance(inner, expected_async_client)
            limits = getattr(inner, "_limits", None)
            if limits is not None:
                assert limits.keepalive_expiry == 60.0
        finally:
            await sdk_client.close()


class TestCrossEventLoopPooling:
    def test_sequential_asyncio_run_reuses_within_loop_and_rebuilds_across_loops(self, monkeypatch):
        monkeypatch.setenv("NO_PROXY", "*")
        body = json.dumps(
            {
                "id": "msg_pool",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4",
                "content": [{"type": "text", "text": "pong"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 1},
            }
        ).encode("utf-8")
        requests_seen = []

        class _Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                requests_seen.append(1)
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            model = Model(
                model_client_config=ModelClientConfig(
                    client_provider=ProviderType.Anthropic,
                    api_key="sk-ant",
                    api_base=f"http://127.0.0.1:{srv.server_address[1]}",
                    verify_ssl=False,
                    use_shared_llm_http_client=True,
                ),
                model_config=ModelRequestConfig(model="claude-opus-4"),
            )
            orig_build = model._client._build_async_anthropic_client
            builds = []

            def counting_build(timeout=None):
                c = orig_build(timeout=timeout)
                builds.append(c)
                return c

            with patch.object(model._client, "_build_async_anthropic_client", side_effect=counting_build):
                for _ in range(3):
                    async def _two_turns():
                        r1 = await model.invoke(messages=[UserMessage(content="hi")])
                        r2 = await model.invoke(messages=[UserMessage(content="hi2")])
                        return r1.content, r2.content

                    assert asyncio.run(_two_turns()) == ("pong", "pong")
                    assert AnthropicModelClient._client_cache == {}

            assert len(builds) == 3
            assert len(requests_seen) == 6
        finally:
            srv.shutdown()
            srv.server_close()

    def test_manual_loop_closed_without_shutdown_asyncgens_still_evicts_stale_client(self):
        model = _make_model(use_shared=True)
        client = model._client
        built = []

        def fake_build(timeout=None):
            mc = _mock_async_client()
            built.append(mc)
            return mc

        with patch.object(client, "_build_async_anthropic_client", side_effect=fake_build):
            loop1 = asyncio.new_event_loop()
            try:
                loop1.run_until_complete(model.invoke(messages=[UserMessage(content="hi")]))
            finally:
                loop1.close()

            loop2 = asyncio.new_event_loop()
            try:
                loop2.run_until_complete(model.invoke(messages=[UserMessage(content="hi2")]))
                loop2.run_until_complete(AnthropicModelClient.aclose())
            finally:
                loop2.close()

        assert len(built) == 2
        assert built[0] is not built[1]
