from __future__ import annotations

import sys
from types import ModuleType
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.model_clients.edge_cloud_router_model_client import (
    EdgeCloudRouterModelClient,
    _EdgeCloudRouterConfig,
    _load_agent_xrouter,
)
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk


class FakeChild:
    def __init__(
        self,
        *,
        response: AssistantMessage | None = None,
        chunks: list[AssistantMessageChunk] | None = None,
        invoke_error: Exception | None = None,
        stream_error: Exception | None = None,
        error_after_chunks: bool = False,
    ) -> None:
        self.response = response or AssistantMessage(content="answer")
        self.chunks = chunks if chunks is not None else [AssistantMessageChunk(content="answer")]
        self.invoke_error = invoke_error
        self.stream_error = stream_error
        self.error_after_chunks = error_after_chunks
        self.invoke_calls: list[tuple[object, dict]] = []
        self.stream_calls: list[tuple[object, dict]] = []

    async def invoke(self, messages, **kwargs) -> AssistantMessage:
        self.invoke_calls.append((messages, kwargs))
        if self.invoke_error:
            raise self.invoke_error
        return self.response

    async def stream(self, messages, **kwargs) -> AsyncIterator[AssistantMessageChunk]:
        self.stream_calls.append((messages, kwargs))
        if self.stream_error and not self.error_after_chunks:
            raise self.stream_error
        for chunk in self.chunks:
            yield chunk
        if self.stream_error:
            raise self.stream_error


def _child_config(model: str, privacy_scope: str | None = None) -> dict:
    config = {
        "model_client_config": {
            "client_provider": "OpenAI",
            "api_base": "http://model.invalid/v1",
            "api_key": "test-key",
        },
        "model_request_config": {"model": model},
    }
    if privacy_scope is not None:
        config["privacy_scope"] = privacy_scope
    return config


def _router_config(*, mode: str = "llm", privacy_enabled: bool = False) -> ModelClientConfig:
    complexity: dict = {"mode": mode, "privacy_scope": "local"}
    if mode == "llm":
        complexity.update(_child_config("classifier-model"))
    return ModelClientConfig(
        client_provider=ProviderType.EdgeCloudRouter,
        edge_cloud_router={
            "privacy": {"enabled": privacy_enabled},
            "complexity": complexity,
            "deployments": {
                "local_fast": _child_config("local-fast", "local"),
                "local_medium": _child_config("local-medium", "local"),
                "cloud_complex": _child_config("cloud-complex", "cloud"),
                "cloud_research": _child_config("cloud-research", "cloud"),
                "cloud_reasoning": _child_config("cloud-reasoning", "cloud"),
            },
        },
    )


def _client(
    local: FakeChild,
    cloud: FakeChild,
    classifier: FakeChild | None = None,
    *,
    mode: str = "llm",
    privacy_enabled: bool = False,
    model_config: ModelRequestConfig | None = None,
    deployments: dict[str, FakeChild] | None = None,
) -> EdgeCloudRouterModelClient:
    try:
        import agent_xrouter  # noqa: F401
    except ImportError:
        pytest.skip("agent-xrouter is not installed")

    deployment_children = {
        "local_fast": local,
        "local_medium": local,
        "cloud_complex": cloud,
        "cloud_research": cloud,
        "cloud_reasoning": cloud,
        **(deployments or {}),
    }
    children = [
        deployment_children[name]
        for name in (
            "local_fast",
            "local_medium",
            "cloud_complex",
            "cloud_research",
            "cloud_reasoning",
        )
    ]
    if mode == "llm":
        children.append(classifier or FakeChild(response=AssistantMessage(content="COMPLEX")))
    with patch.object(EdgeCloudRouterModelClient, "_create_child", side_effect=children):
        return EdgeCloudRouterModelClient(
            model_config=model_config or ModelRequestConfig(model="edge-cloud-router"),
            model_client_config=_router_config(mode=mode, privacy_enabled=privacy_enabled),
        )


@pytest.mark.asyncio
async def test_s2_is_redacted_before_classifier_and_cloud() -> None:
    private_value = "alice@example.com"
    classifier = FakeChild(response=AssistantMessage(content="COMPLEX"))
    cloud = FakeChild()
    client = _client(FakeChild(), cloud, classifier, privacy_enabled=True)

    response = await client.invoke([{"role": "user", "content": f"Analyze all records for {private_value}"}])

    classifier_prompt, _ = classifier.invoke_calls[0]
    cloud_messages, cloud_kwargs = cloud.invoke_calls[0]
    assert isinstance(classifier_prompt, str)
    assert private_value not in classifier_prompt
    assert private_value not in str(cloud_messages)
    assert "[REDACTED:EMAIL_0]" in str(cloud_messages)
    assert cloud_kwargs["model"] == "cloud-complex"
    assert response.metadata["edge_cloud_router"]["privacy_tier"] == "S2"
    assert response.metadata["edge_cloud_router"]["privacy_enabled"] is True
    assert response.metadata["edge_cloud_router"]["target"] == "cloud"
    assert response.metadata["edge_cloud_router"]["selected_deployment"] == "cloud_complex"
    assert response.metadata["edge_cloud_router"]["classifier_model"] == "classifier-model"
    assert response.metadata["edge_cloud_router"]["route_reason"] == "cloud_complexity"
    assert "classifier_usage" not in response.metadata["edge_cloud_router"]


@pytest.mark.asyncio
async def test_s3_skips_classifier_and_uses_original_local_request() -> None:
    classifier = FakeChild(response=AssistantMessage(content="REASONING"))
    local = FakeChild()
    client = _client(local, FakeChild(), classifier, privacy_enabled=True)
    messages = [{"role": "user", "content": "password=keep-this-local"}]

    response = await client.invoke(messages)

    assert not classifier.invoke_calls
    assert local.invoke_calls[0][0] is messages
    assert response.metadata["edge_cloud_router"]["privacy_tier"] == "S3"
    assert response.metadata["edge_cloud_router"]["target"] == "local"
    assert response.metadata["edge_cloud_router"]["selected_deployment"] == "local_medium"


@pytest.mark.asyncio
async def test_classifier_failure_uses_original_local_request() -> None:
    classifier = FakeChild(invoke_error=RuntimeError("classifier failed"))
    local = FakeChild()
    client = _client(local, FakeChild(), classifier)
    messages = [{"role": "user", "content": "Analyze all files"}]

    response = await client.invoke(messages)

    assert local.invoke_calls[0][0] is messages
    assert response.metadata["edge_cloud_router"]["target"] == "local"
    assert response.metadata["edge_cloud_router"]["complexity_level"] is None


@pytest.mark.asyncio
async def test_invalid_external_route_plan_fails_closed_to_local() -> None:
    class InvalidEngine:
        async def route(self, request, classifier=None):
            return object()

    local = FakeChild()
    client = _client(local, FakeChild(), mode="heuristic")
    client._engine = InvalidEngine()
    messages = [{"role": "user", "content": "Analyze all files"}]

    response = await client.invoke(messages)

    assert local.invoke_calls[0][0] is messages
    assert response.metadata["edge_cloud_router"]["target"] == "local"
    assert response.metadata["edge_cloud_router"]["route_reason"] == "router_failed"


@pytest.mark.asyncio
async def test_explicit_heuristic_mode_runs_without_classifier_child() -> None:
    local = FakeChild()
    cloud = FakeChild()
    client = _client(local, cloud, mode="heuristic")

    local_response = await client.invoke("What is DNS?")
    cloud_response = await client.invoke("Prove this theorem by induction")

    assert local_response.metadata["edge_cloud_router"]["complexity_source"] == "heuristic"
    assert cloud_response.metadata["edge_cloud_router"]["selected_model"] == "cloud-reasoning"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "deployment_name", "model"),
    [
        ("What time is it?", "local_fast", "local-fast"),
        ("Implement a parser", "local_medium", "local-medium"),
        ("Analyze this entire codebase end-to-end", "cloud_complex", "cloud-complex"),
        ("Write a systematic review of clinical trial methods", "cloud_research", "cloud-research"),
        ("Prove this theorem by induction", "cloud_reasoning", "cloud-reasoning"),
    ],
)
async def test_each_complexity_level_uses_its_own_deployment(
    prompt: str,
    deployment_name: str,
    model: str,
) -> None:
    deployment_clients = {
        name: FakeChild()
        for name in (
            "local_fast",
            "local_medium",
            "cloud_complex",
            "cloud_research",
            "cloud_reasoning",
        )
    }
    client = _client(
        deployment_clients["local_fast"],
        deployment_clients["cloud_complex"],
        mode="heuristic",
        deployments=deployment_clients,
    )

    response = await client.invoke(prompt)

    assert len(deployment_clients[deployment_name].invoke_calls) == 1
    metadata = response.metadata["edge_cloud_router"]
    assert metadata["selected_deployment"] == deployment_name
    assert metadata["selected_model"] == model


@pytest.mark.asyncio
async def test_cloud_invoke_failure_falls_back_once_to_local() -> None:
    local = FakeChild(
        response=AssistantMessage(
            content="local answer",
            usage_metadata=UsageMetadata(total_tokens=12),
            completion_token_ids=[1, 2],
            logprobs={"content": [{"token": "x"}]},
        )
    )
    cloud = FakeChild(invoke_error=RuntimeError("cloud unavailable"))
    client = _client(local, cloud)

    response = await client.invoke("Analyze all files end-to-end")

    assert len(cloud.invoke_calls) == 1
    assert len(local.invoke_calls) == 1
    assert response.content == "local answer"
    assert response.usage_metadata.total_tokens == 12
    assert response.completion_token_ids == [1, 2]
    assert response.logprobs == {"content": [{"token": "x"}]}
    metadata = response.metadata["edge_cloud_router"]
    assert metadata["target"] == "local"
    assert metadata["fallback_reason"] == "cloud_invoke_failed"


@pytest.mark.asyncio
async def test_local_failure_propagates_without_cloud_fallback() -> None:
    local = FakeChild(invoke_error=RuntimeError("local unavailable"))
    cloud = FakeChild()
    client = _client(local, cloud, mode="heuristic")

    with pytest.raises(RuntimeError, match="local unavailable"):
        await client.invoke("What is DNS?")

    assert not cloud.invoke_calls


@pytest.mark.asyncio
async def test_cloud_empty_stream_falls_back_to_local_and_preserves_metadata() -> None:
    local = FakeChild(chunks=[AssistantMessageChunk(content="local")])
    cloud = FakeChild(chunks=[])
    client = _client(local, cloud)

    chunks = [chunk async for chunk in client.stream("Analyze all files end-to-end")]

    assert [chunk.content for chunk in chunks] == ["local"]
    metadata = chunks[0].metadata["edge_cloud_router"]
    assert metadata["target"] == "local"
    assert metadata["fallback_reason"] == "cloud_stream_failed_before_first_chunk"


@pytest.mark.asyncio
async def test_cloud_stream_failure_after_a_chunk_is_propagated_without_splicing() -> None:
    local = FakeChild(chunks=[AssistantMessageChunk(content="local")])
    cloud = FakeChild(
        chunks=[AssistantMessageChunk(content="cloud")],
        stream_error=RuntimeError("stream failed"),
        error_after_chunks=True,
    )
    client = _client(local, cloud)

    received = []
    with pytest.raises(RuntimeError, match="stream failed"):
        async for chunk in client.stream("Analyze all files end-to-end"):
            received.append(chunk.content)

    assert received == ["cloud"]
    assert not local.stream_calls


@pytest.mark.asyncio
async def test_answer_argument_precedence_and_cloud_header_isolation() -> None:
    cloud = FakeChild()
    model_config = ModelRequestConfig(model="router-name", temperature=0.7)
    client = _client(FakeChild(), cloud, model_config=model_config)

    await client.invoke(
        "Analyze all files end-to-end",
        model="caller-model-is-ignored",
        max_tokens=55,
        custom_headers={"Authorization": "local-token"},
        extra_body={"messages": [{"role": "user", "content": "uninspected"}]},
        session_id="local-session",
        uninspected_payload={"private": "must-not-reach-cloud"},
        reasoning_effort="high",
    )

    _, kwargs = cloud.invoke_calls[0]
    assert kwargs["model"] == "cloud-complex"
    assert kwargs["temperature"] == 0.7
    assert kwargs["top_p"] is None
    assert kwargs["max_tokens"] == 55
    assert kwargs["reasoning_effort"] == "high"
    assert "custom_headers" not in kwargs
    assert "extra_body" not in kwargs
    assert "session_id" not in kwargs
    assert "uninspected_payload" not in kwargs


@pytest.mark.asyncio
async def test_cloud_stream_drops_opaque_extra_body() -> None:
    cloud = FakeChild()
    client = _client(FakeChild(), cloud)

    chunks = [
        chunk
        async for chunk in client.stream(
            "Analyze all files end-to-end",
            extra_body={"messages": [{"role": "user", "content": "uninspected"}]},
        )
    ]

    assert chunks
    _, kwargs = cloud.stream_calls[0]
    assert "extra_body" not in kwargs


@pytest.mark.parametrize("reserved_field", ["messages", "tools", "stream", "extra_body"])
def test_cloud_request_config_cannot_override_router_owned_fields(reserved_field: str) -> None:
    try:
        import agent_xrouter  # noqa: F401
    except ImportError:
        pytest.skip("agent-xrouter is not installed")

    config = _router_config(mode="heuristic")
    value = {"messages": []} if reserved_field == "extra_body" else []
    if reserved_field == "stream":
        value = False
    config.edge_cloud_router["deployments"]["cloud_complex"]["model_request_config"][reserved_field] = value

    with patch.object(EdgeCloudRouterModelClient, "_create_child"):
        with pytest.raises(Exception, match="model service config error"):
            EdgeCloudRouterModelClient(ModelRequestConfig(model="router"), config)


def test_recursive_router_child_is_rejected() -> None:
    try:
        import agent_xrouter  # noqa: F401
    except ImportError:
        pytest.skip("agent-xrouter is not installed")

    config = _router_config(mode="heuristic")
    config.edge_cloud_router["deployments"]["local_fast"]["model_client_config"] = {
        "client_provider": "EdgeCloudRouter",
    }

    with patch.object(EdgeCloudRouterModelClient, "_create_child"):
        with pytest.raises(Exception, match="model service config error"):
            EdgeCloudRouterModelClient(ModelRequestConfig(model="router"), config)


def test_deployment_privacy_scope_must_match_fixed_route() -> None:
    try:
        import agent_xrouter  # noqa: F401
    except ImportError:
        pytest.skip("agent-xrouter is not installed")

    config = _router_config(mode="heuristic")
    config.edge_cloud_router["deployments"]["cloud_reasoning"]["privacy_scope"] = "local"

    with patch.object(EdgeCloudRouterModelClient, "_create_child"):
        with pytest.raises(Exception, match="model service config error"):
            EdgeCloudRouterModelClient(ModelRequestConfig(model="router"), config)


def test_classifier_privacy_scope_must_be_local() -> None:
    try:
        import agent_xrouter  # noqa: F401
    except ImportError:
        pytest.skip("agent-xrouter is not installed")

    config = _router_config()
    config.edge_cloud_router["complexity"]["privacy_scope"] = "cloud"

    with patch.object(EdgeCloudRouterModelClient, "_create_child"):
        with pytest.raises(Exception, match="model service config error"):
            EdgeCloudRouterModelClient(ModelRequestConfig(model="router"), config)


def test_privacy_enforcement_is_disabled_by_default_and_can_be_enabled() -> None:
    default_config = _router_config(mode="heuristic")
    del default_config.edge_cloud_router["privacy"]
    enabled_config = _router_config(mode="heuristic", privacy_enabled=True)

    assert _EdgeCloudRouterConfig.from_model_client_config(default_config).privacy.enabled is False
    assert _EdgeCloudRouterConfig.from_model_client_config(enabled_config).privacy.enabled is True


@pytest.mark.asyncio
async def test_disabled_privacy_routes_sensitive_content_by_complexity() -> None:
    private_value = "alice@example.com"
    classifier = FakeChild(response=AssistantMessage(content="COMPLEX"))
    cloud = FakeChild()
    client = _client(FakeChild(), cloud, classifier)

    response = await client.invoke([{"role": "user", "content": f"Analyze all records for {private_value}"}])

    classifier_prompt, _ = classifier.invoke_calls[0]
    cloud_messages, _ = cloud.invoke_calls[0]
    assert private_value in classifier_prompt
    assert private_value in str(cloud_messages)
    assert response.metadata["edge_cloud_router"]["privacy_enabled"] is False
    assert response.metadata["edge_cloud_router"]["privacy_tier"] == "S1"


def test_missing_optional_package_raises_model_service_config_error() -> None:
    with patch.dict(sys.modules, {"agent_xrouter": None}):
        with pytest.raises(BaseError) as error:
            _load_agent_xrouter()

    assert error.value.code == StatusCode.MODEL_SERVICE_CONFIG_ERROR.code
    assert "agent-xrouter package is missing or incompatible" in str(error.value)


def test_incompatible_optional_package_raises_model_service_config_error() -> None:
    package = ModuleType("agent_xrouter")

    with patch.dict(sys.modules, {"agent_xrouter": package}):
        with pytest.raises(BaseError) as error:
            _load_agent_xrouter()

    assert error.value.code == StatusCode.MODEL_SERVICE_CONFIG_ERROR.code
    assert "agent-xrouter package is missing or incompatible" in str(error.value)
