# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Evolution consumers materialize restated attributes at capture time.

A span whose restated GenAI attributes state sequence references cannot hand those references to the evolution clean windows: the windows outlive the registry's TTL and cannot resolve a process-local reference at read time.  The capture in ``_span_to_otlp`` materializes once, so every downstream consumer (RL/SFT converters, ``read_llm_exchange``) sees full content.
"""

from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider

from openjiuwen.agent_evolving.trajectory.processor import (
    _materialize_evolution_attributes,
    _span_to_otlp,
)
from openjiuwen.agent_evolving.trajectory.spans import attributes_to_map, read_llm_exchange
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.content_addressing import parse_sequence_reference
from openjiuwen.extensions.observability.gen_ai_semconv import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
)
from openjiuwen.extensions.observability.sequence_registry import reset_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """Isolate the registry state for every test."""
    reset_registry()
    yield
    reset_registry()


def _span_with_references() -> tuple[Any, TracerProvider]:
    """Build a finished span whose restated attributes are references."""
    provider = TracerProvider()
    tracer = provider.get_tracer("evolution-materialization")
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, service_name="evolution-materialization"),
        tracer=tracer,
    )
    span = tracer.start_span("chat test-model")
    handler._record_standard_structured_input(
        span,
        [
            {"role": "system", "content": "answer briefly"},
            {"role": "user", "content": "hello evolution"},
        ],
    )
    span.end()
    return span, provider


def _encoded_span(payload: dict) -> dict:
    """Extract the captured OTLP span (the shape ``iter_spans`` would yield)."""
    return payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]


def _copied_attributes(payload: dict) -> dict[str, str]:
    """Decode the OTLP attribute pairs of the captured span into a mapping."""
    return attributes_to_map(_encoded_span(payload)["attributes"])


def test_span_to_otlp_materializes_references() -> None:
    """The evolution capture resolves references into full content."""
    span, provider = _span_with_references()
    for attribute_name in (GEN_AI_INPUT_MESSAGES, GEN_AI_SYSTEM_INSTRUCTIONS):
        raw = span.attributes[attribute_name]
        assert isinstance(raw, str) and parse_sequence_reference(raw) is not None, (
            f"precondition: {attribute_name} must be attached as a reference"
        )

    payload, _identity = _span_to_otlp(span)

    copied = _copied_attributes(payload)
    for attribute_name in (GEN_AI_INPUT_MESSAGES, GEN_AI_SYSTEM_INSTRUCTIONS):
        value = copied[attribute_name]
        assert parse_sequence_reference(value) is None, (
            f"evolution capture must materialize {attribute_name}, got reference {value}"
        )
    assert "hello evolution" in copied[GEN_AI_INPUT_MESSAGES]
    provider.shutdown()


def test_read_llm_exchange_sees_full_prompts_after_capture() -> None:
    """RL/SFT converters read non-empty prompts from the captured payload."""
    span, provider = _span_with_references()
    payload, _identity = _span_to_otlp(span)

    prompts, completions = read_llm_exchange(_encoded_span(payload))
    assert prompts, "RL converters need non-empty prompt messages"
    system = next((m for m in prompts if m.get("role") == "system"), None)
    assert system is not None and "answer briefly" in system["content"]
    user = next((m for m in prompts if m.get("role") == "user"), None)
    assert user is not None and "hello evolution" in user["content"]

    provider.shutdown()


def test_non_reference_values_pass_through_unchanged() -> None:
    """Values that are not references are returned as-is by the helper."""
    attrs = {
        GEN_AI_INPUT_MESSAGES: "already plain content",
        GEN_AI_OUTPUT_MESSAGES: "[{}",
        "other.key": "other value",
    }
    result = _materialize_evolution_attributes(attrs)
    assert result == attrs, "non-reference values must pass through unchanged"
