# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for tracer_agent inputs/outputs wire-size bounding."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from openjiuwen.core.session.tracer.handler import (
    TraceAgentHandler,
    _TRACER_AGENT_IO_MAX_BYTES,
    _TRACER_AGENT_STRING_MAX_BYTES,
    _utf8_json_size,
    bound_tracer_agent_io,
)
from openjiuwen.core.session.tracer.span import TraceAgentSpan


def test_bound_tracer_agent_io_keeps_small_payload():
    value = {"outputs": {"success": True, "data": {"ok": 1}}}
    assert bound_tracer_agent_io(value) == value


def test_bound_tracer_agent_io_truncates_oversized_string_leaf():
    huge = "x" * (_TRACER_AGENT_STRING_MAX_BYTES * 4)
    value = {
        "outputs": {
            "success": True,
            "data": {"content": huge},
        }
    }
    bounded = bound_tracer_agent_io(value)
    content = bounded["outputs"]["data"]["content"]
    assert isinstance(content, str)
    assert content.endswith("...[truncated]")
    assert len(content.encode("utf-8")) <= _TRACER_AGENT_STRING_MAX_BYTES
    assert bounded["outputs"]["success"] is True
    assert _utf8_json_size(bounded) <= _TRACER_AGENT_IO_MAX_BYTES


def test_bound_tracer_agent_io_falls_back_to_stub_when_still_too_large():
    # Many medium strings: leaf truncation alone may still exceed IO budget.
    value = {f"k{i}": "y" * 1024 for i in range(512)}
    bounded = bound_tracer_agent_io(value, max_bytes=8 * 1024, max_string_bytes=2 * 1024)
    assert isinstance(bounded, dict)
    assert bounded.get("_truncated") is True
    assert bounded["original_bytes"] > 8 * 1024
    assert isinstance(bounded.get("preview"), str)
    assert _utf8_json_size(bounded) <= 8 * 1024


def test_format_data_bounds_inputs_and_outputs_without_mutating_span():
    huge = "z" * (2 * 1024 * 1024)
    span = TraceAgentSpan(
        traceId="trace-1",
        invokeId="invoke-1",
        invokeType="plugin",
        name="Grep",
        startTime=datetime(2026, 9, 24, 1, 0, 0),
        endTime=datetime(2026, 9, 24, 1, 0, 5),
        inputs={"inputs": {"path": "board.html", "pattern": "const SEGS", "output_mode": "content"}},
        outputs={"outputs": {"success": True, "data": {"content": huge}}},
        status="finish",
    )
    original_outputs = span.outputs

    handler = TraceAgentHandler(MagicMock(), MagicMock())
    framed = handler._format_data(span)

    assert framed["type"] == "tracer_agent"
    payload = framed["payload"]
    assert payload["name"] == "Grep"
    assert _utf8_json_size(payload["outputs"]) <= _TRACER_AGENT_IO_MAX_BYTES
    assert _utf8_json_size(payload["inputs"]) <= _TRACER_AGENT_IO_MAX_BYTES
    # Live span keeps the full tool output for in-process consumers.
    assert span.outputs is original_outputs
    assert span.outputs["outputs"]["data"]["content"] == huge
