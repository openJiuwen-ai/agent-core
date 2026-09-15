# coding: utf-8
"""Behavior tests for canonical trajectory message projection."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openjiuwen.agent_evolving.trajectory.messages import (
    tool_call_arguments,
    tool_call_id,
    tool_call_name,
    trajectory_to_messages,
)
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    attributes_from_map,
    write_llm_exchange,
)
from openjiuwen.extensions.observability import semconv


def _span(
    span_id: str,
    *,
    start: int,
    name: str = "llm.call",
    attributes: dict | None = None,
    status: dict | None = None,
) -> dict:
    span = {
        "traceId": "trace-1",
        "spanId": span_id,
        "name": name,
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(start + 1),
        "attributes": attributes_from_map(attributes or {}),
    }
    if status is not None:
        span["status"] = status
    return span


def _llm_span(
    span_id: str,
    *,
    start: int,
    prompt: list[dict],
    completion: dict | None = None,
) -> dict:
    attributes: dict = write_llm_exchange(
        prompt,
        [] if completion is None else [completion],
    )
    if completion is not None and completion.get("tool_calls") is not None:
        attributes[semconv.GEN_AI_TOOL_CALLS] = json.dumps(
            completion["tool_calls"],
            ensure_ascii=False,
        )
    return _span(span_id, start=start, attributes=attributes)


def _trajectory(spans: list[dict]) -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map(
                            {
                                "openjiuwen.trajectory_id": "trajectory-1",
                                semconv.AT_SESSION_ID: "session-1",
                            }
                        )
                    },
                    "scopeSpans": [{"scope": {"name": "test"}, "spans": spans}],
                }
            ]
        }
    )


def test_reconstructs_ordered_history_without_global_value_deduplication() -> None:
    first = _llm_span(
        "llm-1",
        start=10,
        prompt=[
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "repeat"},
        ],
        completion={"role": "assistant", "content": "first"},
    )
    second = _llm_span(
        "llm-2",
        start=20,
        prompt=[
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "repeat"},
            {"role": "assistant", "content": "first"},
            {"role": "user", "content": "repeat"},
        ],
        completion={"role": "assistant", "content": "second"},
    )

    messages = trajectory_to_messages(_trajectory([second, first]))

    assert messages == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "repeat"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "repeat"},
        {"role": "assistant", "content": "second"},
    ]


def test_merges_tail_capped_prompt_after_repeated_system_prefix() -> None:
    first = _llm_span(
        "llm-1",
        start=10,
        prompt=[
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "old"},
        ],
        completion={"role": "assistant", "content": "old answer"},
    )
    second = _llm_span(
        "llm-2",
        start=20,
        prompt=[
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "recent"},
        ],
        completion={"role": "assistant", "content": "recent answer"},
    )
    tail_capped = _llm_span(
        "llm-3",
        start=30,
        prompt=[
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "recent"},
            {"role": "assistant", "content": "recent answer"},
            {"role": "user", "content": "latest"},
        ],
        completion={"role": "assistant", "content": "latest answer"},
    )

    messages = trajectory_to_messages(_trajectory([tail_capped, second, first]))

    assert messages == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "recent"},
        {"role": "assistant", "content": "recent answer"},
        {"role": "user", "content": "latest"},
        {"role": "assistant", "content": "latest answer"},
    ]


def test_normalizes_tool_calls_and_links_results_only_by_id() -> None:
    llm = _llm_span(
        "llm-1",
        start=10,
        prompt=[{"role": "custom-role", "content": "run"}],
        completion={
            "role": "assistant",
            "tool_calls": [
                {"id": "call-1", "name": "search", "arguments": {"q": "openjiuwen"}},
                {
                    "type": "function",
                    "function": {
                        "id": "call-2",
                        "name": "read",
                        "arguments": '{"path":"a"}',
                    },
                },
            ],
        },
    )
    linked_tool = _span(
        "tool-1",
        start=20,
        name="tool.search",
        attributes={
            semconv.GEN_AI_TOOL_ID: "call-1",
            semconv.GEN_AI_TOOL_OUTPUT: {"ok": True},
        },
    )
    unlinked_tool = _span(
        "tool-2",
        start=30,
        name="tool.search",
        attributes={semconv.GEN_AI_TOOL_OUTPUT: "same-name output"},
    )

    messages = trajectory_to_messages(_trajectory([unlinked_tool, linked_tool, llm]))

    assert messages == [
        {"role": "custom-role", "content": "run"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"q":"openjiuwen"}'},
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"path":"a"}'},
                },
            ],
        },
        {"role": "tool", "name": "search", "tool_call_id": "call-1", "content": '{"ok":true}'},
        {"role": "tool", "content": "same-name output"},
    ]


@pytest.mark.parametrize(
    ("tool_call", "expected_id", "expected_name", "expected_arguments"),
    [
        (
            {"id": "flat-id", "name": "flat", "arguments": "flat-args"},
            "flat-id",
            "flat",
            "flat-args",
        ),
        (
            {
                "function": {
                    "id": "nested-id",
                    "name": "nested",
                    "arguments": "nested-args",
                }
            },
            "nested-id",
            "nested",
            "nested-args",
        ),
        (
            SimpleNamespace(id="object-id", name="object", arguments="object-args"),
            "object-id",
            "object",
            "object-args",
        ),
        (
            SimpleNamespace(
                function=SimpleNamespace(
                    id="nested-object-id",
                    name="nested-object",
                    arguments="nested-object-args",
                )
            ),
            "nested-object-id",
            "nested-object",
            "nested-object-args",
        ),
        (
            {
                "id": "",
                "name": "",
                "arguments": "",
                "function": {
                    "id": "fallback-id",
                    "name": "fallback",
                    "arguments": "fallback-args",
                },
            },
            "fallback-id",
            "fallback",
            "fallback-args",
        ),
    ],
)
def test_tool_call_accessors_preserve_flat_nested_and_object_formats(
    tool_call: object,
    expected_id: str,
    expected_name: str,
    expected_arguments: str,
) -> None:
    assert tool_call_id(tool_call) == expected_id
    assert tool_call_name(tool_call) == expected_name
    assert tool_call_arguments(tool_call) == expected_arguments


def test_selects_fields_after_reconstruction_and_rejects_unknown_configuration() -> None:
    trajectory = _trajectory(
        [
            _llm_span(
                "llm-1",
                start=10,
                prompt=[{"role": "user", "content": "hello", "name": "caller"}],
                completion={"role": "assistant", "content": "done"},
            )
        ]
    )

    assert trajectory_to_messages(trajectory, fields={"name"}) == [
        {"role": "user", "name": "caller"},
        {"role": "assistant"},
    ]
    with pytest.raises(ValueError, match="unknown trajectory message fields: metadata"):
        trajectory_to_messages(trajectory, fields={"metadata"})  # type: ignore[arg-type]


def test_uses_tool_error_only_when_output_is_absent() -> None:
    tool = _span(
        "tool-1",
        start=10,
        name="tool.failed",
        attributes={semconv.GEN_AI_TOOL_NAME: "failed"},
        status={"code": "STATUS_CODE_ERROR", "message": "boom"},
    )

    assert trajectory_to_messages(_trajectory([tool])) == [{"role": "tool", "name": "failed", "content": "boom"}]
