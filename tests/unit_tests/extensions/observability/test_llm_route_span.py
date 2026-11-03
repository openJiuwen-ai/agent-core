# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A model-group request span names the deployment its router picked."""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import set_span_in_context

from openjiuwen.extensions.observability import callback_handler as callback_handler_module
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client import ROUTED_MODEL_PLACEHOLDER
from openjiuwen.extensions.observability.gen_ai_semconv import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_SYSTEM_INSTRUCTIONS,
)
from openjiuwen.extensions.observability.semconv import OJ_MODEL_GROUP_ID, OJ_MODEL_GROUP_ROUTE_ID


def _open_span(monkeypatch: pytest.MonkeyPatch, model: str) -> tuple[Any, Any, Any]:
    """Open an LLM span for ``model`` and make it the current one."""
    provider = TracerProvider()
    tracer = provider.get_tracer("llm-route-span-test")
    root = tracer.start_span("agent.root")
    handler = OtelCallbackHandler(ObservabilityConfig(enabled=True, service_name="llm-route-span-test"), tracer=tracer)
    monkeypatch.setattr(handler, "_get_parent_context_for_llm_tool", lambda: set_span_in_context(root))
    span = handler._open_llm_span({"messages": [], "model": model}, is_streaming=False)
    assert span is not None
    monkeypatch.setattr(callback_handler_module, "get_current_llm_span", lambda: span)
    return handler, span, provider


@pytest.mark.asyncio
async def test_selected_route_renames_the_logical_model_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """The '*' span becomes 'chat <routed model>' and records group and route."""
    handler, span, provider = _open_span(monkeypatch, "*")

    await handler.on_llm_route(
        route_metadata={"status": "selected", "model_group_id": "mgp_1", "route_id": "rte_a", "model_name": "GLM-5.3"},
    )

    assert span.name == "chat GLM-5.3"
    assert span.attributes[GEN_AI_REQUEST_MODEL] == "GLM-5.3"
    assert span.attributes[OJ_MODEL_GROUP_ID] == "mgp_1"
    assert span.attributes[OJ_MODEL_GROUP_ROUTE_ID] == "rte_a"
    span.end()
    provider.shutdown()


@pytest.mark.asyncio
async def test_failover_names_the_route_that_finally_served(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the router fails over, the last selected route wins."""
    handler, span, provider = _open_span(monkeypatch, "*")

    for model, route in (("GLM-5.3", "rte_a"), ("deepseek-flash", "rte_b")):
        await handler.on_llm_route(
            route_metadata={"status": "selected", "model_group_id": "mgp_1", "route_id": route, "model_name": model},
        )

    assert span.name == "chat deepseek-flash"
    assert span.attributes[OJ_MODEL_GROUP_ROUTE_ID] == "rte_b"
    span.end()
    provider.shutdown()


@pytest.mark.asyncio
async def test_route_without_a_model_keeps_the_span_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A route event that names no model only records the group."""
    handler, span, provider = _open_span(monkeypatch, "*")

    await handler.on_llm_route(route_metadata={"status": "failed", "model_group_id": "mgp_1"})

    assert span.name == "chat *"
    assert span.attributes[OJ_MODEL_GROUP_ID] == "mgp_1"
    span.end()
    provider.shutdown()


@pytest.mark.asyncio
async def test_plain_model_span_is_untouched_without_route_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-model-group call never sees a route event and keeps its name."""
    handler, span, provider = _open_span(monkeypatch, "qwen-plus")

    await handler.on_llm_route(route_metadata=None)

    assert span.name == "chat qwen-plus"
    assert span.attributes[GEN_AI_REQUEST_MODEL] == "qwen-plus"
    assert OJ_MODEL_GROUP_ID not in span.attributes
    span.end()
    provider.shutdown()


def _open_span_with_prompt(monkeypatch: pytest.MonkeyPatch, messages: list[dict[str, Any]]) -> tuple[Any, Any, Any]:
    """Open a '*' span whose recorded input carries ``messages``."""
    provider = TracerProvider()
    tracer = provider.get_tracer("llm-route-span-test")
    root = tracer.start_span("agent.root")
    handler = OtelCallbackHandler(ObservabilityConfig(enabled=True, service_name="llm-route-span-test"), tracer=tracer)
    monkeypatch.setattr(handler, "_get_parent_context_for_llm_tool", lambda: set_span_in_context(root))
    span = handler._open_llm_span({"messages": messages, "model": "*"}, is_streaming=False)
    assert span is not None
    monkeypatch.setattr(callback_handler_module, "get_current_llm_span", lambda: span)
    return handler, span, provider


@pytest.mark.asyncio
async def test_recorded_input_names_the_model_the_prompt_was_sent_to(monkeypatch: pytest.MonkeyPatch) -> None:
    """The placeholder in the recorded prompt becomes the routed model's name."""
    messages = [
        {"role": "system", "content": f"system says {ROUTED_MODEL_PLACEHOLDER}"},
        {"role": "user", "content": f"- Current model: {ROUTED_MODEL_PLACEHOLDER}"},
    ]
    handler, span, provider = _open_span_with_prompt(monkeypatch, messages)

    await handler.on_llm_route(
        route_metadata={"status": "selected", "model_name": "glm-b", "routed_model": "GLM-B", "route_id": "rte_b"},
    )

    recorded = span.attributes[GEN_AI_INPUT_MESSAGES] + span.attributes.get(GEN_AI_SYSTEM_INSTRUCTIONS, "")
    assert ROUTED_MODEL_PLACEHOLDER not in recorded
    assert "Current model: GLM-B" in recorded
    assert span.name == "chat glm-b"
    span.end()
    provider.shutdown()


@pytest.mark.asyncio
async def test_request_that_fails_before_answering_still_names_its_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """The send-time announcement names a request that never answers."""
    messages = [{"role": "user", "content": "- Current model: <routed-model/>"}]
    handler, span, provider = _open_span_with_prompt(monkeypatch, messages)

    await handler.on_llm_route(
        route_metadata={"status": "sending", "model_name": "glm-a", "routed_model": "GLM-A", "route_id": "rte_a"},
    )

    assert span.name == "chat glm-a"
    assert "Current model: GLM-A" in span.attributes[GEN_AI_INPUT_MESSAGES]
    span.end()
    provider.shutdown()


@pytest.mark.asyncio
async def test_failover_attempt_refills_the_recorded_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each attempt rewrites the input as its own request said it."""
    messages = [{"role": "user", "content": "- Current model: <routed-model/>\n- Other"}]
    handler, span, provider = _open_span_with_prompt(monkeypatch, messages)

    await handler.on_llm_route(
        route_metadata={"status": "sending", "model_name": "glm-a", "routed_model": "GLM-A", "route_id": "rte_a"},
    )
    first = span.attributes[GEN_AI_INPUT_MESSAGES]
    await handler.on_llm_route(
        route_metadata={"status": "sending", "model_name": "glm-b", "routed_model": "GLM-B", "route_id": "rte_b"},
    )
    await handler.on_llm_route(
        route_metadata={"status": "selected", "model_name": "glm-b", "routed_model": "GLM-B", "route_id": "rte_b"},
    )

    recorded = span.attributes[GEN_AI_INPUT_MESSAGES]
    assert "Current model: GLM-A\\n- Other" in first
    assert "Current model: GLM-B\\n- Other" in recorded
    assert "<routed-model" not in recorded
    assert span.name == "chat glm-b"
    span.end()
    provider.shutdown()
