# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Unit tests for IntelliRouterModelClient.
"""
import hashlib
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client import (
    IntelliRouterClientConfig,
    IntelliRouterModelClient,
    _router_cache,
)
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType
from openjiuwen.core.foundation.llm.schema.message import UserMessage, AssistantMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
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
    """Create IntelliRouter client config for testing."""
    return ModelClientConfig(
        client_provider=ProviderType.IntelliRouter,
        api_key="placeholder",
        api_base="http://placeholder",
        verify_ssl=False,
        intelli_router_deployments=[
            {
                "model_name": "test-model",
                "api_key": "test-key",
                "api_base": "https://test.api",
                "id": "test-dep-1",
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
    tpm: int = 100000
    rpm: int = 60
    tags: list = None
    timeout: float = 30.0
    verify_ssl: bool = True


class FakeReliableRouter:
    """Minimal fake ReliableRouter for testing cache / construction logic.

    Using a real class instead of MagicMock so that isinstance checks and
    constructor logic in IntelliRouterModelClient work correctly.
    """
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
        assert config.deployments[0]["model_name"] == "test-model"
        assert config.deployments[0]["api_key"] == "test-key"
        assert config.deployments[0]["id"] == "test-dep-1"
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
        external_router = FakeReliableRouter(strategy="external")
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
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter"),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", MagicMock()),
        ):
            mock_router = MagicMock()
            IntelliRouterModelClient._create_router = MagicMock(return_value=mock_router)
            yield IntelliRouterModelClient(model_request_config, intelli_router_client_config)

    @pytest.mark.asyncio
    async def test_invoke_basic(self, client):
        """Basic invoke returns AssistantMessage with correct content."""
        client._router.completion = AsyncMock(return_value={
            "choices": [{"message": {"content": "Hello world!"}, "finish_reason": "stop"}],
        })

        messages = [UserMessage(content="Hi")]
        result = await client.invoke(messages)

        assert isinstance(result, AssistantMessage)
        assert result.content == "Hello world!"

    @pytest.mark.asyncio
    async def test_invoke_with_output_parser(self, client):
        """Output parser is applied to the response content."""
        client._router.completion = AsyncMock(return_value={
            "choices": [{"message": {"content": '{"output": "parsed"}'}, "finish_reason": "stop"}],
        })

        # Use a real async function for parser.parse since BaseOutputParser.parse is async
        async def fake_parse(content):
            return "parsed_result"

        parser = MagicMock(spec=BaseOutputParser)
        parser.parse = fake_parse

        messages = [UserMessage(content="Parse this")]
        result = await client.invoke(messages, output_parser=parser)

        assert result.content == "parsed_result"

    @pytest.mark.asyncio
    async def test_invoke_model_override(self, client):
        """Model name override is passed through to router.completion."""
        client._router.completion = AsyncMock(return_value={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        })

        messages = [UserMessage(content="Hi")]
        await client.invoke(messages, model="override-model")

        client._router.completion.assert_called_once()
        call_kwargs = client._router.completion.call_args.kwargs
        assert call_kwargs["model"] == "override-model"

    @pytest.mark.asyncio
    async def test_invoke_empty_choices(self, client):
        """Empty choices list results in empty content."""
        client._router.completion = AsyncMock(return_value={"choices": []})

        messages = [UserMessage(content="Hi")]
        result = await client.invoke(messages)

        assert result.content == ""


# ---------------------------------------------------------------------------
# TestIntelliRouterModelClientStream
# ---------------------------------------------------------------------------

class TestIntelliRouterModelClientStream:
    """Test IntelliRouterModelClient.stream()."""

    @pytest.fixture
    def client(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter"),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", MagicMock()),
        ):
            mock_router = MagicMock()
            IntelliRouterModelClient._create_router = MagicMock(return_value=mock_router)
            yield IntelliRouterModelClient(model_request_config, intelli_router_client_config)

    @pytest.mark.asyncio
    async def test_stream_basic(self, client):
        """Stream returns a single chunk with correct content."""
        async def fake_stream():
            yield {"choices": [{"delta": {"content": "Hello"}, "finish_reason": "stop"}]}

        client._router.stream_completion = MagicMock(return_value=fake_stream())

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
            yield {"choices": [{"delta": {"content": "Hello "}}]}
            yield {"choices": [{"delta": {"content": "world"}}]}
            yield {"choices": [{"delta": {"content": "!"}, "finish_reason": "stop"}]}

        client._router.stream_completion = MagicMock(return_value=fake_stream())

        messages = [UserMessage(content="Hi")]
        contents = []
        async for chunk in client.stream(messages):
            contents.append(chunk.content)

        assert contents == ["Hello ", "world", "!"]

    @pytest.mark.asyncio
    async def test_stream_empty_choices(self, client):
        """Chunk with empty choices yields empty content."""
        async def fake_stream():
            yield {"choices": []}

        client._router.stream_completion = MagicMock(return_value=fake_stream())

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
    """Test that multimodal methods raise errors."""

    @pytest.fixture
    def client(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter"),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", MagicMock()),
        ):
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
# TestIntelliRouterConvertResponse
# ---------------------------------------------------------------------------

class TestIntelliRouterConvertResponse:
    """Test _convert_response and _convert_chunk utility methods."""

    @pytest.mark.asyncio
    async def test_convert_response_with_content(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        response = {
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
        }
        result = await client._convert_response(response)
        assert isinstance(result, AssistantMessage)
        assert result.content == "Hello"

    @pytest.mark.asyncio
    async def test_convert_response_empty(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        result = await client._convert_response({"choices": []})
        assert result.content == ""

    def test_convert_chunk_with_content(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        chunk = {"choices": [{"delta": {"content": "Hello chunk"}, "finish_reason": "stop"}]}
        result = client._convert_chunk(chunk)
        assert isinstance(result, AssistantMessageChunk)
        assert result.content == "Hello chunk"

    def test_convert_chunk_empty(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        result = client._convert_chunk({"choices": []})
        assert result.content == ""


# ---------------------------------------------------------------------------
# TestIntelliRouterBuildRequestParams
# ---------------------------------------------------------------------------

class TestIntelliRouterBuildRequestParams:
    """Test _build_request_params utility method."""

    def test_build_request_params_basic(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        params = client._build_request_params(
            messages=[{"role": "user", "content": "Hi"}],
            temperature=0.5,
            top_p=0.8,
            max_tokens=512,
            stream=False,
        )

        assert params["temperature"] == 0.5
        assert params["top_p"] == 0.8
        assert params["max_tokens"] == 512

    def test_build_request_params_with_tools(self, model_request_config, intelli_router_client_config):
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(model_request_config, intelli_router_client_config)

        from openjiuwen.core.foundation.tool import ToolInfo
        tools = [ToolInfo(name="test_tool", description="A test tool")]

        params = client._build_request_params(
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
            stream=False,
        )

        assert "tools" in params
        assert len(params["tools"]) == 1
        assert params["tools"][0]["function"]["name"] == "test_tool"

    def test_build_request_params_none_values(self, intelli_router_client_config):
        """None/non-provided values should not be in the output dict.

        Note: temperature and top_p always have defaults (0.95, 0.1) from
        ModelRequestConfig, so they are always included.
        """
        config_with_defaults = ModelRequestConfig(model="test-model")
        with (
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.ReliableRouter", FakeReliableRouter),
            patch("openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client.Deployment", FakeDeployment),
        ):
            client = IntelliRouterModelClient(config_with_defaults, intelli_router_client_config)

        params = client._build_request_params(
            messages=[{"role": "user", "content": "Hi"}],
            temperature=None,
            top_p=None,
            max_tokens=None,
            stop=None,
            stream=False,
        )

        # temperature and top_p have defaults from ModelRequestConfig, so they ARE present
        assert "temperature" in params
        assert "top_p" in params
        # max_tokens and stop should not be in the output when None
        assert "max_tokens" not in params
        assert "stop" not in params
