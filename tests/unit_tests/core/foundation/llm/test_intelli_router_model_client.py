# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Unit tests for IntelliRouterModelClient — wraps intelli_router.ReliableRouter.
"""
import hashlib
import importlib.util
import json
import os
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client import (
    IntelliRouterClientConfig,
    IntelliRouterModelClient,
    _router_cache,
    _web_servers,
)
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType
from openjiuwen.core.foundation.llm.schema.message import UserMessage, AssistantMessage, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def model_request_config():
    """Create model request config for testing."""
    return ModelRequestConfig(
        model="test-model",
        temperature=0.7,
        top_p=0.9,
        max_tokens=1024,
    )


@pytest.fixture
def intelli_router_client_config():
    """Create IntelliRouter client config for testing (DashScope-style)."""
    return ModelClientConfig(
        client_provider=ProviderType.IntelliRouter,
        api_key="placeholder",
        api_base="http://placeholder",
        verify_ssl=False,
        intelli_router_deployments=[
            {
                "model_name": "qwen-turbo",
                "api_key": os.getenv("DASHSCOPE_API_KEY", "mock-dashscope-key"),
                "api_base": "https://dashscope.aliyuncs.com",
                "id": "dashscope-qwen-turbo",
                "provider": "dashscope",
                "tpm": 100000,
                "rpm": 60,
                "tags": ["primary"],
                "timeout": 30.0,
            },
        ],
        intelli_router_strategy="adaptive",
        intelli_router_num_retries=3,
        intelli_router_timeout=30.0,
        intelli_router_strategy_kwargs={
            "token_threshold": 1000,
            "rpm_threshold": 10,
        },
    )


@pytest.fixture(autouse=True)
def clear_router_cache():
    """Clean router cache after each test to avoid cross-test interference."""
    yield
    _router_cache.clear()


@dataclass
class FakeDeployment:
    """Minimal fake Deployment for router construction tests."""
    id: str
    model_name: str
    api_key: str
    api_base: str
    provider: str = "openai"
    tpm: int = None
    rpm: int = None
    tags: list = None
    timeout: float = None
    verify_ssl: bool = True


@dataclass
class FakeToolCall:
    """Fake intelli_router ToolCall for testing type conversion."""
    id: str
    type: str
    name: str
    arguments: str
    index: int = None


class FakeReliableRouter:
    """Minimal fake ReliableRouter for testing cache / construction logic."""
    def __init__(self, **kwargs):
        self.kwargs = kwargs


# ---------------------------------------------------------------------------
# TestIntelliRouterClientConfig
# ---------------------------------------------------------------------------

class TestIntelliRouterClientConfig:
    """Test IntelliRouterClientConfig extraction from ModelClientConfig."""

    def test_from_model_client_config(self, intelli_router_client_config):
        """Verify all intelli_router_* fields are extracted from __pydantic_extra__."""
        config = IntelliRouterClientConfig.from_model_client_config(intelli_router_client_config)

        assert len(config.deployments) == 1
        assert config.deployments[0]["model_name"] == "qwen-turbo"
        assert config.deployments[0]["api_key"] == os.getenv("DASHSCOPE_API_KEY", "mock-dashscope-key")
        assert config.deployments[0]["id"] == "dashscope-qwen-turbo"
        assert config.strategy == "adaptive"
        assert config.num_retries == 3
        assert config.timeout == 30.0
        assert config.strategy_kwargs["token_threshold"] == 1000
        assert config.strategy_kwargs["rpm_threshold"] == 10

    def test_from_model_client_config_defaults(self):
        """Verify defaults when no intelli_router_* fields are provided."""
        config = ModelClientConfig(
            client_provider=ProviderType.IntelliRouter,
            api_key="x",
            api_base="http://x",
        )
        router_config = IntelliRouterClientConfig.from_model_client_config(config)

        assert router_config.deployments == []
        assert router_config.strategy == "simple-shuffle"
        assert router_config.num_retries == 3
        assert router_config.timeout == 30.0
        assert router_config.strategy_kwargs == {}
        assert router_config.enable_health_check is False
        assert router_config.health_check_interval == 300.0
        assert router_config.verify_ssl is True
        assert router_config.enable_observability is False

    def test_from_model_client_config_observability_enabled(self):
        """Verify enable_observability is correctly extracted."""
        config = ModelClientConfig(
            client_provider=ProviderType.IntelliRouter,
            api_key="x",
            api_base="http://x",
            intelli_router_enable_observability=True,
        )
        router_config = IntelliRouterClientConfig.from_model_client_config(config)
        assert router_config.enable_observability is True

    def test_from_model_client_config_web_dashboard_port(self):
        """Verify web_dashboard_port is correctly extracted."""
        config = ModelClientConfig(
            client_provider=ProviderType.IntelliRouter,
            api_key="x",
            api_base="http://x",
            intelli_router_web_dashboard_port=9090,
        )
        router_config = IntelliRouterClientConfig.from_model_client_config(config)
        assert router_config.web_dashboard_port == 9090

    def test_from_model_client_config_web_dashboard_port_default(self):
        """Verify web_dashboard_port defaults to 0."""
        config = ModelClientConfig(
            client_provider=ProviderType.IntelliRouter,
            api_key="x",
            api_base="http://x",
        )
        router_config = IntelliRouterClientConfig.from_model_client_config(config)
        assert router_config.web_dashboard_port == 0


# ---------------------------------------------------------------------------
# TestRouterCache
# ---------------------------------------------------------------------------

class TestRouterCache:
    """Test router cache key generation and instance sharing."""

    def test_make_router_key_deterministic(self):
        """Same config should produce the same cache key."""
        config_a = IntelliRouterClientConfig(
            deployments=[{"model_name": "m", "api_key": "k", "api_base": "b", "id": "d1"}],
            strategy="simple-shuffle",
        )
        config_b = IntelliRouterClientConfig(
            deployments=[{"model_name": "m", "api_key": "k", "api_base": "b", "id": "d1"}],
            strategy="simple-shuffle",
        )
        assert IntelliRouterModelClient._make_router_key(config_a) == \
               IntelliRouterModelClient._make_router_key(config_b)

    def test_make_router_key_different(self):
        """Different configs should produce different cache keys."""
        config_a = IntelliRouterClientConfig(
            deployments=[{"model_name": "m", "api_key": "k", "api_base": "b", "id": "d1"}],
            strategy="simple-shuffle",
        )
        config_b = IntelliRouterClientConfig(
            deployments=[{"model_name": "m2", "api_key": "k", "api_base": "b", "id": "d1"}],
            strategy="simple-shuffle",
        )
        assert IntelliRouterModelClient._make_router_key(config_a) != \
               IntelliRouterModelClient._make_router_key(config_b)

    def test_get_or_create_router_same_instance(self):
        """Same config should return the same router instance (cache hit)."""
        config = IntelliRouterClientConfig(
            deployments=[{"model_name": "m", "api_key": "k", "api_base": "b", "id": "d1"}],
            strategy="simple-shuffle",
        )
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            router1 = IntelliRouterModelClient._get_or_create_router(config)
            router2 = IntelliRouterModelClient._get_or_create_router(config)
            assert router1 is router2

    def test_get_or_create_router_different_instance(self):
        """Different configs should return different router instances."""
        config_a = IntelliRouterClientConfig(
            deployments=[{"model_name": "m", "api_key": "k", "api_base": "b", "id": "d1"}],
            strategy="simple-shuffle",
        )
        config_b = IntelliRouterClientConfig(
            deployments=[{"model_name": "m2", "api_key": "k", "api_base": "b", "id": "d2"}],
            strategy="simple-shuffle",
        )
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            router1 = IntelliRouterModelClient._get_or_create_router(config_a)
            router2 = IntelliRouterModelClient._get_or_create_router(config_b)
            assert router1 is not router2

    def test_create_router_import_error(self):
        """Verify error raised when intelli_router package is not installed."""
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", None),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", None),
        ):
            config = IntelliRouterClientConfig(deployments=[{"id": "d1", "model_name": "m", "api_key": "k", "api_base": "b"}])
            from openjiuwen.core.common.exception.codes import StatusCode
            from openjiuwen.core.common.exception.errors import BaseError
            with pytest.raises(BaseError) as exc_info:
                IntelliRouterModelClient._create_router(config)
            assert exc_info.value.status.code == StatusCode.MODEL_SERVICE_CONFIG_ERROR.code

    def test_create_router_with_observability(self):
        """When enable_observability=True, event_bus is created and passed to router."""
        config = IntelliRouterClientConfig(
            deployments=[{"id": "d1", "model_name": "m", "api_key": "k", "api_base": "b", "provider": "openai"}],
            enable_observability=True,
        )

        mock_event_bus = MagicMock()
        mock_logging_hook = MagicMock()
        mock_metrics_collector = MagicMock()

        fake_intelli_router = MagicMock()
        fake_intelli_router.EventBus = MagicMock(return_value=mock_event_bus)
        fake_intelli_router.LoggingHook = MagicMock(return_value=mock_logging_hook)
        fake_intelli_router.MetricsCollector = MagicMock(return_value=mock_metrics_collector)

        with (
            patch.dict("sys.modules", {"intelli_router": fake_intelli_router}),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            router = IntelliRouterModelClient._create_router(config)

        assert isinstance(router, FakeReliableRouter)
        assert router.kwargs.get("event_bus") is mock_event_bus
        mock_event_bus.register.assert_any_call(mock_logging_hook)
        mock_event_bus.register.assert_any_call(mock_metrics_collector)

    def test_create_router_without_observability(self):
        """When enable_observability=False, event_bus is None."""
        config = IntelliRouterClientConfig(
            deployments=[{"id": "d1", "model_name": "m", "api_key": "k", "api_base": "b", "provider": "openai"}],
            enable_observability=False,
        )
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            router = IntelliRouterModelClient._create_router(config)

        assert isinstance(router, FakeReliableRouter)
        assert router.kwargs.get("event_bus") is None

    def test_make_router_key_differs_with_observability(self):
        """enable_observability changes the cache key."""
        config_off = IntelliRouterClientConfig(
            deployments=[{"model_name": "m", "api_key": "k", "api_base": "b", "id": "d1"}],
            enable_observability=False,
        )
        config_on = IntelliRouterClientConfig(
            deployments=[{"model_name": "m", "api_key": "k", "api_base": "b", "id": "d1"}],
            enable_observability=True,
        )
        assert IntelliRouterModelClient._make_router_key(config_off) != \
               IntelliRouterModelClient._make_router_key(config_on)

    def test_make_router_key_differs_with_web_dashboard_port(self):
        """web_dashboard_port changes the cache key."""
        config_no_port = IntelliRouterClientConfig(
            deployments=[{"model_name": "m", "api_key": "k", "api_base": "b", "id": "d1"}],
            web_dashboard_port=0,
        )
        config_with_port = IntelliRouterClientConfig(
            deployments=[{"model_name": "m", "api_key": "k", "api_base": "b", "id": "d1"}],
            web_dashboard_port=8080,
        )
        assert IntelliRouterModelClient._make_router_key(config_no_port) != \
               IntelliRouterModelClient._make_router_key(config_with_port)

    def test_create_router_with_web_dashboard(self):
        """When enable_observability=True and web_dashboard_port > 0, MetricsWebServer is created and started."""
        config = IntelliRouterClientConfig(
            deployments=[{"id": "d1", "model_name": "m", "api_key": "k", "api_base": "b", "provider": "openai"}],
            enable_observability=True,
            web_dashboard_port=8888,
        )

        mock_event_bus = MagicMock()
        mock_logging_hook = MagicMock()
        mock_metrics_collector = MagicMock()
        mock_web_server = MagicMock()
        mock_web_server.url = "http://localhost:8888"

        fake_intelli_router = MagicMock()
        fake_intelli_router.EventBus = MagicMock(return_value=mock_event_bus)
        fake_intelli_router.LoggingHook = MagicMock(return_value=mock_logging_hook)
        fake_intelli_router.MetricsCollector = MagicMock(return_value=mock_metrics_collector)
        mock_web_cls = MagicMock(return_value=mock_web_server)
        fake_intelli_router.MetricsWebServer = mock_web_cls

        cache_key = "test-key-dashboard"
        with (
            patch.dict("sys.modules", {"intelli_router": fake_intelli_router}),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            router = IntelliRouterModelClient._create_router(config, cache_key=cache_key)

        assert isinstance(router, FakeReliableRouter)
        mock_web_cls.assert_called_once_with(metrics=mock_metrics_collector, port=8888)
        mock_web_server.start.assert_called_once()
        assert _web_servers.get(cache_key) is mock_web_server
        # cleanup
        del _web_servers[cache_key]

    def test_create_router_web_dashboard_not_started_without_observability(self):
        """When enable_observability=False and web_dashboard_port > 0, no web server is created."""
        config = IntelliRouterClientConfig(
            deployments=[{"id": "d1", "model_name": "m", "api_key": "k", "api_base": "b", "provider": "openai"}],
            enable_observability=False,
            web_dashboard_port=8888,
        )

        cache_key = "test-key-no-obs"
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            router = IntelliRouterModelClient._create_router(config, cache_key=cache_key)

        assert isinstance(router, FakeReliableRouter)
        assert cache_key not in _web_servers

    def test_create_router_web_dashboard_not_started_when_port_zero(self):
        """When web_dashboard_port=0, no web server is created even with observability enabled."""
        config = IntelliRouterClientConfig(
            deployments=[{"id": "d1", "model_name": "m", "api_key": "k", "api_base": "b", "provider": "openai"}],
            enable_observability=True,
            web_dashboard_port=0,
        )

        mock_event_bus = MagicMock()
        mock_metrics_collector = MagicMock()

        fake_intelli_router = MagicMock()
        fake_intelli_router.EventBus = MagicMock(return_value=mock_event_bus)
        fake_intelli_router.LoggingHook = MagicMock(return_value=MagicMock())
        fake_intelli_router.MetricsCollector = MagicMock(return_value=mock_metrics_collector)

        cache_key = "test-key-port-zero"
        with (
            patch.dict("sys.modules", {"intelli_router": fake_intelli_router}),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            router = IntelliRouterModelClient._create_router(config, cache_key=cache_key)

        assert isinstance(router, FakeReliableRouter)
        assert cache_key not in _web_servers


# ---------------------------------------------------------------------------
# TestIntelliRouterModelClientInit
# ---------------------------------------------------------------------------

class TestIntelliRouterModelClientInit:
    """Test IntelliRouterModelClient initialization."""

    def test_init_with_router_config(self, model_request_config, intelli_router_client_config):
        """Normal init: router is created from config extra fields."""
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)
            assert client._router is not None
            assert isinstance(client._router, FakeReliableRouter)

    def test_init_with_external_router(self, model_request_config, intelli_router_client_config):
        """External router passed in: should be used directly (skip cache)."""
        external_router = MagicMock()
        client = IntelliRouterModelClient(
            model_request_config, intelli_router_client_config, router=external_router,
        )
        assert client._router is external_router

    def test_init_no_api_key_required(self):
        """_validate_config is a no-op — creating client without api_key should work."""
        config = ModelClientConfig(
            client_provider=ProviderType.IntelliRouter,
            api_key="",
            api_base="",
            verify_ssl=False,
        )
        request_config = ModelRequestConfig(model="test")
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(request_config, config)
            assert client is not None


# ---------------------------------------------------------------------------
# TestIntelliRouterModelClientInvoke
# ---------------------------------------------------------------------------

class TestIntelliRouterModelClientInvoke:
    """Test IntelliRouterModelClient.invoke()."""

    @pytest.fixture
    def client(self, model_request_config, intelli_router_client_config):
        mock_router = MagicMock()
        IntelliRouterModelClient._create_router = MagicMock(return_value=mock_router)
        yield IntelliRouterModelClient(model_request_config, intelli_router_client_config)

    @pytest.mark.asyncio
    async def test_invoke_basic(self, client):
        """Basic invoke returns AssistantMessage with correct content."""
        client._router.invoke = AsyncMock(return_value=MagicMock(
            content="Hello world!", tool_calls=None, usage_metadata=None,
            finish_reason="stop", reasoning_content=None, spec=[],
        ))

        messages = [UserMessage(content="Hi")]
        result = await client.invoke(messages)

        assert isinstance(result, AssistantMessage)
        assert result.content == "Hello world!"

    @pytest.mark.asyncio
    async def test_invoke_with_output_parser(self, client):
        """Output parser is applied to the response content."""
        client._router.invoke = AsyncMock(return_value=MagicMock(
            content='{"output": "parsed"}', tool_calls=None, usage_metadata=None,
            finish_reason="stop", reasoning_content=None, spec=[],
        ))

        async def fake_parse(content):
            return "parsed_result"

        parser = MagicMock(spec=BaseOutputParser)
        parser.parse = fake_parse

        messages = [UserMessage(content="Parse this")]
        result = await client.invoke(messages, output_parser=parser)

        assert result.content == "parsed_result"

    @pytest.mark.asyncio
    async def test_invoke_model_override(self, client):
        """Model name override is passed through to router.invoke."""
        client._router.invoke = AsyncMock(return_value=MagicMock(
            content="ok", tool_calls=None, usage_metadata=None,
            finish_reason="stop", reasoning_content=None, spec=[],
        ))

        messages = [UserMessage(content="Hi")]
        await client.invoke(messages, model="override-model")

        client._router.invoke.assert_called_once()
        call_kwargs = client._router.invoke.call_args.kwargs
        assert call_kwargs["model"] == "override-model"

    @pytest.mark.asyncio
    async def test_invoke_empty_content(self, client):
        """Response with empty content results in empty content."""
        client._router.invoke = AsyncMock(return_value=MagicMock(
            content="", tool_calls=None, usage_metadata=None,
            finish_reason="stop", reasoning_content=None, spec=[],
        ))

        messages = [UserMessage(content="Hi")]
        result = await client.invoke(messages)

        assert result.content == ""

    @pytest.mark.asyncio
    async def test_invoke_output_parser_not_passed_to_router(self, client):
        """output_parser should NOT be forwarded to router.invoke."""
        client._router.invoke = AsyncMock(return_value=MagicMock(
            content="raw", tool_calls=None, usage_metadata=None,
            finish_reason="stop", reasoning_content=None, spec=[],
        ))

        async def fake_parse(content):
            return "parsed"

        parser = MagicMock(spec=BaseOutputParser)
        parser.parse = fake_parse

        messages = [UserMessage(content="Hi")]
        await client.invoke(messages, output_parser=parser)

        call_kwargs = client._router.invoke.call_args.kwargs
        assert "output_parser" not in call_kwargs


# ---------------------------------------------------------------------------
# TestIntelliRouterModelClientStream
# ---------------------------------------------------------------------------

class TestIntelliRouterModelClientStream:
    """Test IntelliRouterModelClient.stream()."""

    @pytest.fixture
    def client(self, model_request_config, intelli_router_client_config):
        mock_router = MagicMock()
        IntelliRouterModelClient._create_router = MagicMock(return_value=mock_router)
        yield IntelliRouterModelClient(model_request_config, intelli_router_client_config)

    @pytest.mark.asyncio
    async def test_stream_basic(self, client):
        """Stream returns a single chunk with correct content."""
        async def fake_stream():
            yield MagicMock(content="Hello", finish_reason="stop", tool_calls=None, reasoning_content=None, spec=[])

        client._router.stream = MagicMock(return_value=fake_stream())

        messages = [UserMessage(content="Hi")]
        chunks = []
        async for chunk in client.stream(messages):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert isinstance(chunks[0], AssistantMessageChunk)
        assert chunks[0].content == "Hello"

    @pytest.mark.asyncio
    async def test_stream_multiple_chunks(self, client):
        """Multiple chunks are yielded in order."""
        async def fake_stream():
            yield MagicMock(content="Hello ", finish_reason="null", tool_calls=None, reasoning_content=None, spec=[])
            yield MagicMock(content="world", finish_reason="null", tool_calls=None, reasoning_content=None, spec=[])
            yield MagicMock(content="!", finish_reason="stop", tool_calls=None, reasoning_content=None, spec=[])

        client._router.stream = MagicMock(return_value=fake_stream())

        messages = [UserMessage(content="Hi")]
        contents = []
        async for chunk in client.stream(messages):
            contents.append(chunk.content)

        assert contents == ["Hello ", "world", "!"]

    @pytest.mark.asyncio
    async def test_stream_empty_content(self, client):
        """Chunk with empty content yields empty content."""
        async def fake_stream():
            yield MagicMock(content="", finish_reason="null", tool_calls=None, reasoning_content=None, spec=[])

        client._router.stream = MagicMock(return_value=fake_stream())

        messages = [UserMessage(content="Hi")]
        chunks = []
        async for chunk in client.stream(messages):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].content == ""


# ---------------------------------------------------------------------------
# TestIntelliRouterModelClientMultimodal
# ---------------------------------------------------------------------------

class TestIntelliRouterModelClientMultimodal:
    """Test that multimodal methods raise errors for unsupported providers."""

    @pytest.fixture
    def client(self, model_request_config, intelli_router_client_config):
        mock_router = MagicMock()
        # Empty deployments -> _resolve_generation_provider returns "unknown"
        mock_router.deployments = []
        IntelliRouterModelClient._create_router = MagicMock(return_value=mock_router)
        yield IntelliRouterModelClient(model_request_config, intelli_router_client_config)

    @pytest.mark.asyncio
    async def test_generate_image_raises_error(self, client):
        with pytest.raises(Exception) as exc_info:
            await client.generate_image([UserMessage(content="draw")])
        assert "does not support image" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_speech_raises_error(self, client):
        with pytest.raises(Exception) as exc_info:
            await client.generate_speech([UserMessage(content="speak")])
        assert "does not support speech" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_generate_video_raises_error(self, client):
        with pytest.raises(Exception) as exc_info:
            await client.generate_video([UserMessage(content="record")])
        assert "does not support video" in str(exc_info.value)


# ---------------------------------------------------------------------------
# TestIntelliRouterDashScopeGeneration — mock DashScope API calls
# ---------------------------------------------------------------------------

class TestIntelliRouterDashScopeGeneration:
    """Test DashScope generation methods with mocked API calls."""

    @pytest.fixture
    def dashscope_client(self, model_request_config, intelli_router_client_config):
        """Client with a DashScope deployment so generation methods are allowed."""
        mock_router = MagicMock()
        mock_dep = MagicMock()
        mock_dep.model_name = "qwen-image-max"
        mock_dep.provider = "dashscope"
        mock_dep.api_key = "test-dashscope-key"
        mock_dep.api_base = "https://dashscope.aliyuncs.com"
        mock_router.deployments = [mock_dep]
        IntelliRouterModelClient._create_router = MagicMock(return_value=mock_router)
        return IntelliRouterModelClient(model_request_config, intelli_router_client_config)

    # ------ generate_image ------

    @pytest.mark.asyncio
    async def test_generate_image_with_text_content(self, dashscope_client):
        """UserMessage with string content is correctly parsed for image generation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.output = {
            "choices": [{"message": {"content": [{"image": "https://img.example.com/cat.png"}]}}]
        }

        with patch("dashscope.MultiModalConversation.call", return_value=mock_response) as mock_call:
            result = await dashscope_client.generate_image([UserMessage(content="draw a cat")])

        assert result.images == ["https://img.example.com/cat.png"]
        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["messages"] == [{"role": "user", "content": [{"text": "draw a cat"}]}]

    @pytest.mark.asyncio
    async def test_generate_image_with_multimodal_content(self, dashscope_client):
        """UserMessage with list content (text + image) is correctly parsed."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.output = {
            "choices": [{"message": {"content": [{"image": "https://img.example.com/result.png"}]}}]
        }

        msg = UserMessage(content=[
            {"text": "generate something similar based on this image"},
            {"image": "https://example.com/ref.jpg"},
        ])

        with patch("dashscope.MultiModalConversation.call", return_value=mock_response) as mock_call:
            result = await dashscope_client.generate_image([msg])

        assert result.images == ["https://img.example.com/result.png"]
        call_kwargs = mock_call.call_args.kwargs
        expected_content = [
            {"text": "generate something similar based on this image"},
            {"image": "https://example.com/ref.jpg"},
        ]
        assert call_kwargs["messages"] == [{"role": "user", "content": expected_content}]

    @pytest.mark.asyncio
    async def test_generate_image_empty_messages_raises(self, dashscope_client):
        """Empty messages list raises ValidationError."""
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            await dashscope_client.generate_image([])

    @pytest.mark.asyncio
    async def test_generate_image_api_failure_raises_model_error(self, dashscope_client):
        """API failure raises ModelError, not RuntimeError."""
        from openjiuwen.core.common.exception.errors import BaseError
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.code = "InvalidParameter"
        mock_response.message = "Bad request"

        with patch("dashscope.MultiModalConversation.call", return_value=mock_response):
            with pytest.raises(BaseError) as exc_info:
                await dashscope_client.generate_image([UserMessage(content="draw")])
            assert "DashScope image generation failed" in str(exc_info.value)

    # ------ generate_speech ------

    @pytest.mark.asyncio
    async def test_generate_speech_with_user_message(self, dashscope_client):
        """UserMessage with string content is correctly parsed for speech generation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.output = {
            "audio": {"url": "https://audio.example.com/speech.mp3", "data": None}
        }

        with patch("dashscope.MultiModalConversation.call", return_value=mock_response) as mock_call:
            result = await dashscope_client.generate_speech([UserMessage(content="hello world")])

        assert result.audio_url == "https://audio.example.com/speech.mp3"
        assert result.format == "mp3"
        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["text"] == "hello world"
        assert call_kwargs["voice"] == "Cherry"

    @pytest.mark.asyncio
    async def test_generate_speech_empty_text_raises(self, dashscope_client):
        """Empty text content raises ValidationError."""
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            await dashscope_client.generate_speech([UserMessage(content="")])

    # ------ generate_video ------

    @pytest.mark.asyncio
    async def test_generate_video_with_user_message(self, dashscope_client):
        """UserMessage with string content is correctly parsed for video generation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.output = MagicMock()
        mock_response.output.video_url = "https://video.example.com/clip.mp4"
        mock_response.usage = {"duration": 5, "size": "1280*720"}

        with patch("dashscope.VideoSynthesis.call", return_value=mock_response) as mock_call:
            result = await dashscope_client.generate_video([UserMessage(content="a cat running")])

        assert result.video_url == "https://video.example.com/clip.mp4"
        assert result.duration == 5
        assert result.format == "mp4"
        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["prompt"] == "a cat running"

    @pytest.mark.asyncio
    async def test_generate_video_with_img_url(self, dashscope_client):
        """Image-to-video generation passes img_url correctly."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.output = MagicMock()
        mock_response.output.video_url = "https://video.example.com/i2v.mp4"
        mock_response.usage = {"duration": 5}

        with patch("dashscope.VideoSynthesis.call", return_value=mock_response) as mock_call:
            result = await dashscope_client.generate_video(
                [UserMessage(content="make the image move")],
                img_url="https://example.com/frame.jpg",
                resolution="720P",
            )

        assert result.video_url == "https://video.example.com/i2v.mp4"
        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["img_url"] == "https://example.com/frame.jpg"
        assert call_kwargs["resolution"] == "720P"

    @pytest.mark.asyncio
    async def test_generate_video_empty_prompt_raises(self, dashscope_client):
        """Empty prompt raises ValidationError."""
        from openjiuwen.core.common.exception.errors import BaseError
        with pytest.raises(BaseError):
            await dashscope_client.generate_video([UserMessage(content="")])


# ---------------------------------------------------------------------------
# TestIntelliRouterConvertResponse
# ---------------------------------------------------------------------------

class TestIntelliRouterConvertResponse:
    """Test _to_ow_assistant_message and _to_ow_chunk utility methods."""

    @pytest.mark.asyncio
    async def test_to_ow_assistant_message_with_content(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        ir_msg = MagicMock(
            content="Hello", tool_calls=None, usage_metadata=None,
            finish_reason="stop", reasoning_content=None, spec=[],
        )
        result = await client._to_ow_assistant_message(ir_msg)
        assert isinstance(result, AssistantMessage)
        assert result.content == "Hello"

    @pytest.mark.asyncio
    async def test_to_ow_assistant_message_empty(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        ir_msg = MagicMock(
            content="", tool_calls=None, usage_metadata=None,
            finish_reason="stop", reasoning_content=None, spec=[],
        )
        result = await client._to_ow_assistant_message(ir_msg)
        assert result.content == ""

    def test_to_ow_chunk_with_content(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        ir_chunk = MagicMock(content="Hello chunk", finish_reason="stop", tool_calls=None, reasoning_content=None, spec=[])
        result = IntelliRouterModelClient._to_ow_chunk(ir_chunk)
        assert isinstance(result, AssistantMessageChunk)
        assert result.content == "Hello chunk"

    def test_to_ow_chunk_empty(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        ir_chunk = MagicMock(content="", finish_reason="null", tool_calls=None, reasoning_content=None, spec=[])
        result = IntelliRouterModelClient._to_ow_chunk(ir_chunk)
        assert result.content == ""

    @pytest.mark.asyncio
    async def test_to_ow_assistant_message_with_tool_calls(self, model_request_config, intelli_router_client_config):
        """tool_calls should be mapped to openjiuwen ToolCall objects."""
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        fake_tc = FakeToolCall(id="call_1", type="function", name="get_weather", arguments='{"city":"Beijing"}', index=0)
        ir_msg = MagicMock(
            content="", tool_calls=[fake_tc], usage_metadata=None,
            finish_reason="tool_calls", reasoning_content=None, spec=[],
        )
        result = await client._to_ow_assistant_message(ir_msg)
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_weather"
        assert result.tool_calls[0].arguments == '{"city":"Beijing"}'
        assert result.finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_to_ow_assistant_message_with_usage(self, model_request_config, intelli_router_client_config):
        """usage_metadata should be mapped."""
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        fake_usage = MagicMock(input_tokens=10, output_tokens=20, total_tokens=30, cache_tokens=5, model_name="gpt-4")
        ir_msg = MagicMock(
            content="hi", tool_calls=None, usage_metadata=fake_usage,
            finish_reason="stop", reasoning_content=None, spec=[],
        )
        result = await client._to_ow_assistant_message(ir_msg)
        assert result.usage_metadata is not None
        assert result.usage_metadata.input_tokens == 10
        assert result.usage_metadata.output_tokens == 20
        assert result.usage_metadata.total_tokens == 30
        assert result.usage_metadata.cache_tokens == 5

    @pytest.mark.asyncio
    async def test_to_ow_assistant_message_with_reasoning(self, model_request_config, intelli_router_client_config):
        """reasoning_content should be passed through."""
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        ir_msg = MagicMock(
            content="answer", tool_calls=None, usage_metadata=None,
            finish_reason="stop", reasoning_content="let me think...", spec=[],
        )
        result = await client._to_ow_assistant_message(ir_msg)
        assert result.reasoning_content == "let me think..."

    def test_to_ow_chunk_with_tool_calls(self, model_request_config, intelli_router_client_config):
        """Chunk tool_calls should be mapped."""
        fake_tc = FakeToolCall(id="call_1", type="function", name="search", arguments='{"q":"hi"}', index=0)
        ir_chunk = MagicMock(
            content="", tool_calls=[fake_tc], finish_reason="null", reasoning_content=None, spec=[],
        )
        result = IntelliRouterModelClient._to_ow_chunk(ir_chunk)
        assert result.tool_calls is not None
        assert result.tool_calls[0].name == "search"

    def test_to_ow_chunk_with_reasoning(self, model_request_config, intelli_router_client_config):
        """Chunk reasoning_content should be passed through."""
        ir_chunk = MagicMock(
            content="", tool_calls=None, finish_reason="null", reasoning_content="thinking...", spec=[],
        )
        result = IntelliRouterModelClient._to_ow_chunk(ir_chunk)
        assert result.reasoning_content == "thinking..."


# ---------------------------------------------------------------------------
# TestIntelliRouterIntegrationDashScope — real API calls (skipped without key)
# ---------------------------------------------------------------------------

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_API_BASE = "https://dashscope.aliyuncs.com"


def _make_dashscope_client_config():
    return ModelClientConfig(
        client_provider=ProviderType.IntelliRouter,
        api_key="placeholder",
        api_base="http://placeholder",
        verify_ssl=False,
        intelli_router_deployments=[
            {
                "model_name": "qwen-turbo",
                "api_key": DASHSCOPE_API_KEY,
                "api_base": DASHSCOPE_API_BASE,
                "id": "dashscope-qwen-turbo",
                "provider": "dashscope",
                "tpm": 100000,
                "rpm": 60,
                "tags": ["primary"],
                "timeout": 30.0,
            },
        ],
        intelli_router_strategy="simple-shuffle",
        intelli_router_num_retries=2,
        intelli_router_timeout=30.0,
    )


@pytest.mark.integration
@pytest.mark.skipif(
    importlib.util.find_spec("intelli_router") is None or not os.getenv("DASHSCOPE_API_KEY"),
    reason="requires the optional intelli_router package and a real DASHSCOPE_API_KEY",
)
class TestIntelliRouterIntegrationDashScope:
    """Integration tests that call real DashScope API via IntelliRouter.

    Run with: pytest -m integration tests/unit_tests/core/foundation/llm/test_intelli_router_model_client.py
    """

    @staticmethod
    def _build_real_router(deployments_cfg):
        """Build a real ReliableRouter directly (bypasses any class-level mocks)."""
        from intelli_router import ReliableRouter, Deployment
        deployments = [
            Deployment(
                id=d.get("id"),
                model_name=d["model_name"],
                api_key=d["api_key"],
                api_base=d["api_base"],
                provider=d.get("provider", "openai"),
                tpm=d.get("tpm"),
                rpm=d.get("rpm"),
                tags=d.get("tags", []),
                timeout=d.get("timeout"),
                verify_ssl=d.get("verify_ssl", True),
            )
            for d in deployments_cfg
        ]
        return ReliableRouter(deployments=deployments, strategy="simple-shuffle", num_retries=2, timeout=30.0)

    @pytest.mark.asyncio
    async def test_invoke_dashscope_qwen_turbo(self):
        """Real invoke call to DashScope qwen-turbo."""
        deployments_cfg = _make_dashscope_client_config().intelli_router_deployments
        router = self._build_real_router(deployments_cfg)

        model_request_config = ModelRequestConfig(model="qwen-turbo", max_tokens=50)
        client_config = _make_dashscope_client_config()
        client = IntelliRouterModelClient(model_request_config, client_config, router=router)

        messages = [UserMessage(content="Say hello in one word.")]
        result = await client.invoke(messages)

        assert isinstance(result, AssistantMessage)
        assert len(result.content) > 0
        print(f"[DashScope invoke] response: {result.content}")

    @pytest.mark.asyncio
    async def test_stream_dashscope_qwen_turbo(self):
        """Real stream call to DashScope qwen-turbo."""
        deployments_cfg = _make_dashscope_client_config().intelli_router_deployments
        router = self._build_real_router(deployments_cfg)

        model_request_config = ModelRequestConfig(model="qwen-turbo", max_tokens=50)
        client_config = _make_dashscope_client_config()
        client = IntelliRouterModelClient(model_request_config, client_config, router=router)

        messages = [UserMessage(content="Count from 1 to 5.")]
        chunks = []
        async for chunk in client.stream(messages):
            chunks.append(chunk)

        assert len(chunks) > 0
        full_content = "".join(c.content for c in chunks if c.content)
        assert len(full_content) > 0
        print(f"[DashScope stream] response: {full_content}")

    @pytest.mark.asyncio
    async def test_failover_dead_to_dashscope(self):
        """Failover: dead endpoint should fall back to real DashScope."""
        deployments_cfg = [
            {
                "model_name": "qwen-turbo",
                "api_key": "fake-dead-key",
                "api_base": "http://localhost:19999",
                "id": "dead-endpoint",
                "provider": "dashscope",
                "tpm": 100000,
                "rpm": 60,
                "tags": ["dead"],
                "timeout": 3.0,
            },
            {
                "model_name": "qwen-turbo",
                "api_key": DASHSCOPE_API_KEY,
                "api_base": DASHSCOPE_API_BASE,
                "id": "dashscope-real",
                "provider": "dashscope",
                "tpm": 100000,
                "rpm": 60,
                "tags": ["primary"],
                "timeout": 30.0,
            },
        ]
        router = self._build_real_router(deployments_cfg)

        client_config = ModelClientConfig(
            client_provider=ProviderType.IntelliRouter,
            api_key="placeholder",
            api_base="http://placeholder",
            verify_ssl=False,
        )
        model_request_config = ModelRequestConfig(model="qwen-turbo", max_tokens=30)
        client = IntelliRouterModelClient(model_request_config, client_config, router=router)

        messages = [UserMessage(content="Say hi")]
        result = await client.invoke(messages)

        assert isinstance(result, AssistantMessage)
        assert len(result.content) > 0
        print(f"[DashScope failover] response: {result.content}")
