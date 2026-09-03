# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for complete system prompts in trajectory telemetry."""

import json

from opentelemetry.sdk.trace import TracerProvider

from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.semconv import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
)


def test_system_prompt_is_complete_in_standard_and_trajectory_views() -> None:
    provider = TracerProvider()
    tracer = provider.get_tracer("complete-system-prompt-test")
    config = ObservabilityConfig(
        enabled=True,
        service_name="complete-system-prompt-test",
        attribute_value_max_length=8,
    )
    handler = OtelCallbackHandler(config, tracer=tracer)
    span = tracer.start_span("llm.call")
    system_prompt = "stable-system-prompt-" * 20
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "long-user-message"},
    ]

    try:
        handler._record_standard_structured_input(span, messages)
        trajectory_messages = handler._trajectory_messages(
            messages,
            occurrence_ids=handler._message_occurrence_ids(messages),
            source_metadata=(None, None),
        )

        instructions = json.loads(span.attributes[GEN_AI_SYSTEM_INSTRUCTIONS])
        input_messages = json.loads(span.attributes[GEN_AI_INPUT_MESSAGES])
        assert instructions[0]["content"] == system_prompt
        assert trajectory_messages[0]["content"] == system_prompt
        assert "OTel attribute truncated" not in trajectory_messages[0]["content"]
        assert "OTel attribute truncated" in input_messages[0]["parts"][0]["content"]
        assert "OTel attribute truncated" in trajectory_messages[1]["content"]
    finally:
        span.end()
        provider.shutdown()
