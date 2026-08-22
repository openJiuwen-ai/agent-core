# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.core.foundation.llm.utils.responses_utils import parse_response


def _payload(usage: dict) -> dict:
    return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": usage}


def test_reasoning_tokens_read_from_output_tokens_details():
    """The Responses API reports the count under ``output_tokens_details``."""
    message = parse_response(_payload({
        "input_tokens": 73,
        "output_tokens": 161,
        "output_tokens_details": {"reasoning_tokens": 65},
        "total_tokens": 234,
    }), model_name="gpt-5.6-luna")

    assert message.usage_metadata is not None
    assert message.usage_metadata.reasoning_tokens == 65


def test_reasoning_tokens_read_from_completion_tokens_details():
    """Some OpenAI-compatible providers use the chat-completions spelling."""
    message = parse_response(_payload({
        "input_tokens": 10,
        "output_tokens": 20,
        "completion_tokens_details": {"reasoning_tokens": 12},
    }), model_name="m")

    assert message.usage_metadata.reasoning_tokens == 12


def test_reasoning_tokens_default_to_zero():
    """Absent, malformed, or non-numeric details must not raise."""
    for usage in (
        {"input_tokens": 1, "output_tokens": 2},
        {"input_tokens": 1, "output_tokens": 2, "output_tokens_details": None},
        {"input_tokens": 1, "output_tokens": 2, "output_tokens_details": {}},
        {"input_tokens": 1, "output_tokens": 2, "output_tokens_details": {"reasoning_tokens": "x"}},
    ):
        message = parse_response(_payload(usage), model_name="m")
        assert message.usage_metadata.reasoning_tokens == 0


def test_other_usage_fields_are_unchanged():
    message = parse_response(_payload({
        "input_tokens": 73,
        "output_tokens": 161,
        "input_tokens_details": {"cached_tokens": 40},
        "output_tokens_details": {"reasoning_tokens": 65},
        "total_tokens": 234,
    }), model_name="gpt-5.6-luna")

    usage = message.usage_metadata
    assert (usage.input_tokens, usage.output_tokens) == (73, 161)
    assert (usage.total_tokens, usage.cache_tokens) == (234, 40)
