# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team observability integration with the shared file exporter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.agent_teams.observability import init_observability, shutdown_observability
from openjiuwen.agent_teams.observability.setup import get_tracer
from openjiuwen.agent_teams.observability.span_context import (
    get_or_create_team_span,
    remove_team_span,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import LLMCallEvents
from openjiuwen.extensions.observability.config import ObservabilityConfig


class _FakeUsage:
    input_tokens = 12
    output_tokens = 7
    total_tokens = 19
    model_name = "fake-llm-1"


class _FakeAssistantMessage:
    content = "hello"
    reasoning_content = ""
    finish_reason = "stop"
    tool_calls = None
    usage_metadata = _FakeUsage()


@pytest.mark.asyncio
async def test_team_observability_writes_otlp_jsonl(tmp_path: Path) -> None:
    config = ObservabilityConfig(
        exporter="file",
        traces_dir=str(tmp_path / "traces"),
        file_retention_days=7,
        sample_rate=1.0,
    )
    init_observability(config)
    try:
        get_or_create_team_span("e2e_team", get_tracer("openjiuwen.agent_teams.observability"))
        messages = [{"role": "user", "content": "hi"}]
        await Runner.callback_framework.trigger(
            LLMCallEvents.LLM_INVOKE_INPUT,
            messages=messages,
            model="fake-llm-1",
        )
        await Runner.callback_framework.trigger(
            LLMCallEvents.LLM_INVOKE_OUTPUT,
            messages=messages,
            result=_FakeAssistantMessage(),
        )
        remove_team_span("e2e_team")
    finally:
        shutdown_observability()

    trace_files = list((tmp_path / "traces").glob("*.jsonl"))
    assert trace_files
    for line in trace_files[0].read_text("utf-8").splitlines():
        payload = json.loads(line)
        for resource_spans in payload["resourceSpans"]:
            for scope_spans in resource_spans["scopeSpans"]:
                for span in scope_spans["spans"]:
                    trace_id = span["traceId"]
                    assert len(trace_id) == 32
                    assert all(character in "0123456789abcdef" for character in trace_id)
