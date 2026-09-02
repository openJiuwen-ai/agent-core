# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""init_model must forward reasoning / extra_body into ModelRequestConfig."""

from __future__ import annotations

from openjiuwen.core.foundation.llm import init_model


def test_init_model_forwards_reasoning_effort_and_extra_body() -> None:
    model = init_model(
        provider="OpenAI",
        model_name="test-model",
        api_key="sk-test",
        api_base="https://example.invalid/v1",
        reasoning_effort="max",
        extra_body={"thinking": {"type": "enabled"}},
        enable_thinking=True,
    )
    extras = model.model_config.model_extra or {}
    assert extras.get("reasoning_effort") == "max"
    assert extras.get("extra_body") == {"thinking": {"type": "enabled"}}
    assert extras.get("enable_thinking") is True
    assert model.model_config.top_p == 0.95
