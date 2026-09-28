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
    _truncate_utf8,
    _utf8_json_size,
    bound_tracer_agent_io,
)
from openjiuwen.core.session.tracer.span import TraceAgentSpan


def test_bound_tracer_agent_io_keeps_small_payload():
    value = {"outputs": {"success": True, "data": {"ok": 1}}}
    assert bound_tracer_agent_io(value) == value


def test_bound_tracer_agent_io_none_passthrough():
    assert bound_tracer_agent_io(None) is None


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
    assert isinstance(bounded["outputs"]["data"], dict)
    assert _utf8_json_size(bounded) <= _TRACER_AGENT_IO_MAX_BYTES


def test_truncate_utf8_does_not_split_multibyte_char():
    # 3-byte UTF-8 char (中); cutting mid-character must not raise or leave orphan bytes.
    text = "中" * 20
    truncated = _truncate_utf8(text, 20)
    truncated.encode("utf-8")  # round-trip must succeed
    assert len(truncated.encode("utf-8")) <= 20
    # Tiny budget smaller than suffix marker must still stay within max_bytes.
    tiny = _truncate_utf8(text, 5)
    assert len(tiny.encode("utf-8")) <= 5
    tiny.encode("utf-8")


def test_truncate_utf8_appends_marker_when_budget_allows():
    text = "a" * 100
    truncated = _truncate_utf8(text, 40)
    assert truncated.endswith("...[truncated]")
    assert len(truncated.encode("utf-8")) <= 40


def test_bound_tracer_agent_io_stub_respects_budget_with_json_escapes():
    # Quote-heavy leaves expand under json.dumps; result must still fit max_bytes.
    value = {f"k{i}": '"' * 512 for i in range(64)}
    budget = 8 * 1024
    bounded = bound_tracer_agent_io(value, max_bytes=budget, max_string_bytes=1024)
    assert isinstance(bounded, dict)
    assert _utf8_json_size(bounded) <= budget
    # Either progressive leaf shrink kept a dict of keys, or a budget-safe stub.
    if bounded.get("_truncated") is True:
        assert bounded["original_bytes"] > budget
    else:
        assert "k0" in bounded


def test_bound_tracer_agent_io_dict_stub_when_structure_cannot_fit():
    # Many keys with non-shrinkable small values force the dict stub path.
    value = {f"k{i:04d}": i for i in range(4000)}
    budget = 1024
    bounded = bound_tracer_agent_io(value, max_bytes=budget, max_string_bytes=64)
    assert isinstance(bounded, dict)
    assert bounded.get("_truncated") is True
    assert bounded["original_bytes"] > budget
    assert _utf8_json_size(bounded) <= budget


def test_bound_tracer_agent_io_preserves_list_type_for_on_invoke_data():
    # Many medium chunks: must stay a list (onInvokeData shape) and fit budget.
    items = [{"chunk": "y" * 4096, "i": i} for i in range(128)]
    budget = 8 * 1024
    bounded = bound_tracer_agent_io(items, max_bytes=budget, max_string_bytes=2048)
    assert isinstance(bounded, list)
    assert bounded
    assert _utf8_json_size(bounded) <= budget
    # If prefix-truncated, last element is the marker; otherwise all items leaf-shrunk.
    if any(isinstance(item, dict) and item.get("_truncated") for item in bounded):
        assert bounded[-1].get("_truncated") is True
        assert bounded[-1]["dropped"] >= 0


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
    # Grep-style huge leaf: structure preserved for team UI restore.
    assert isinstance(payload["outputs"]["outputs"]["data"], dict)
    assert payload["outputs"]["outputs"]["success"] is True
    # Live span keeps the full tool output for in-process consumers.
    assert span.outputs is original_outputs
    assert span.outputs["outputs"]["data"]["content"] == huge
