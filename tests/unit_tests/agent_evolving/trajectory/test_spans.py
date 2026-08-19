# coding: utf-8
"""Focused tests for stateless canonical span accessors."""

from __future__ import annotations

from copy import deepcopy

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    attributes_to_map,
    decode_json_attribute,
    iter_spans,
    merge_trajectories,
    normalize_otlp,
    read_llm_exchange,
    read_llm_messages,
    read_rl_fields,
    read_span_error,
    read_tool_call,
    read_usage,
    span_identity,
    trim_trajectory,
)
from openjiuwen.extensions.observability import semconv


def _value(value):
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    raise TypeError(value)


def _attrs(values):
    return [{"key": key, "value": _value(value)} for key, value in values.items()]


def _span(span_id, *, trace_id="trace", parent=None, name="llm.call", start=1, attrs=None, status=None):
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "startTimeUnixNano": str(start),
        "endTimeUnixNano": str(start + 1),
        "attributes": _attrs(attrs or {}),
    }
    if parent is not None:
        span["parentSpanId"] = parent
    if status is not None:
        span["status"] = status
    return span


def _payload(spans, *, trajectory_id="t1"):
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _attrs(
                        {
                            "openjiuwen.trajectory_id": trajectory_id,
                            semconv.AT_SESSION_ID: "session",
                        }
                    )
                },
                "scopeSpans": [{"scope": {"name": "test"}, "spans": spans}],
            }
        ]
    }


def test_iter_spans_decodes_attributes_and_does_not_leak_payload() -> None:
    payload = _payload([_span("s1", attrs={"nested": "value"})])
    trajectory = Trajectory.from_otlp(payload)

    span = next(iter_spans(trajectory))
    span["attributes"][0]["value"]["stringValue"] = "changed"

    assert attributes_to_map(next(iter_spans(trajectory))["attributes"]) == {"nested": "value"}
    assert payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["spanId"] == "s1"


def test_read_llm_tool_usage_and_error_use_observability_keys() -> None:
    llm = _span(
        "llm-1",
        attrs={
            f"{semconv.GEN_AI_PROMPT}.0.role": "user",
            f"{semconv.GEN_AI_PROMPT}.0.content": "hello",
            f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
            f"{semconv.GEN_AI_COMPLETION}.0.content": "done",
            semconv.GEN_AI_TOOL_CALLS: '[{"name": "search", "arguments": {"q": "x"}}]',
            semconv.GEN_AI_USAGE_PROMPT_TOKENS: 3,
            semconv.GEN_AI_USAGE_COMPLETION_TOKENS: 2,
            semconv.GEN_AI_USAGE_TOTAL_TOKENS: 5,
        },
    )
    tool = _span(
        "tool-1",
        name="tool.search",
        attrs={
            semconv.GEN_AI_TOOL_NAME: "search",
            semconv.GEN_AI_TOOL_ID: "call-1",
            semconv.GEN_AI_TOOL_INPUT: '{"q": "x"}',
            semconv.GEN_AI_TOOL_OUTPUT: '{"ok": true}',
        },
        status={"code": "STATUS_CODE_ERROR", "message": "failed"},
    )

    assert read_llm_messages(llm) == [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "done",
            "tool_calls": [{"name": "search", "arguments": {"q": "x"}}],
        },
    ]
    assert read_usage(llm) == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert read_tool_call(tool) == {
        "name": "search",
        "id": "call-1",
        "input": {"q": "x"},
        "output": {"ok": True},
        "error": {"status": "STATUS_CODE_ERROR", "message": "failed"},
    }
    assert read_span_error(tool) == {"status": "STATUS_CODE_ERROR", "message": "failed"}


def test_tool_accessor_keeps_json_scalar_strings_unchanged() -> None:
    tool = _span(
        "tool-scalar",
        name="tool.scalar",
        attrs={
            semconv.GEN_AI_TOOL_INPUT: "0",
            semconv.GEN_AI_TOOL_OUTPUT: "true",
        },
    )

    assert read_tool_call(tool) == {"input": "0", "output": "true"}


def test_shared_attribute_decoder_and_llm_exchange_are_detached() -> None:
    assert decode_json_attribute('{"temperature": 0.2}') == {"temperature": 0.2}
    assert decode_json_attribute("not-json") == "not-json"
    encoded = {"nested": [1]}
    decoded = decode_json_attribute(encoded)
    decoded["nested"].append(2)
    assert encoded == {"nested": [1]}

    span = _span(
        "llm-exchange",
        attrs={
            f"{semconv.GEN_AI_PROMPT}.0.role": "user",
            f"{semconv.GEN_AI_PROMPT}.0.content": "hello",
            f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
            f"{semconv.GEN_AI_COMPLETION}.0.content": "done",
            semconv.GEN_AI_TOOL_CALLS: '[{"id": "call-1"}]',
        },
    )

    prompts, completions = read_llm_exchange(span)

    assert prompts == [{"role": "user", "content": "hello"}]
    assert completions == [
        {
            "role": "assistant",
            "content": "done",
            "tool_calls": [{"id": "call-1"}],
        }
    ]
    prompts[0]["content"] = "changed"
    assert read_llm_exchange(span)[0][0]["content"] == "hello"


def test_llm_exchange_reads_langfuse_indexed_messages_without_rewriting_span() -> None:
    span = _span(
        "llm-langfuse",
        attrs={
            f"{semconv.LANGFUSE_GEN_AI_PROMPT}.0.role": "user",
            f"{semconv.LANGFUSE_GEN_AI_PROMPT}.0.content": "hello",
            f"{semconv.LANGFUSE_GEN_AI_COMPLETION}.0.role": "assistant",
            f"{semconv.LANGFUSE_GEN_AI_COMPLETION}.0.content": "done",
        },
    )
    original = deepcopy(span)

    assert read_llm_exchange(span) == (
        [{"role": "user", "content": "hello"}],
        [{"role": "assistant", "content": "done"}],
    )
    assert span == original


def test_llm_exchange_prefers_standard_fields_and_falls_back_independently() -> None:
    span = _span(
        "llm-mixed",
        attrs={
            f"{semconv.GEN_AI_PROMPT}.0.role": "user",
            f"{semconv.GEN_AI_PROMPT}.0.content": "standard prompt",
            f"{semconv.LANGFUSE_GEN_AI_PROMPT}.0.role": "user",
            f"{semconv.LANGFUSE_GEN_AI_PROMPT}.0.content": "langfuse prompt",
            f"{semconv.LANGFUSE_GEN_AI_COMPLETION}.0.role": "assistant",
            f"{semconv.LANGFUSE_GEN_AI_COMPLETION}.0.content": "langfuse completion",
        },
    )

    assert read_llm_exchange(span) == (
        [{"role": "user", "content": "standard prompt"}],
        [{"role": "assistant", "content": "langfuse completion"}],
    )


def test_llm_exchange_preserves_tool_call_without_completion_attributes() -> None:
    span = _span(
        "llm-tool-call",
        attrs={
            f"{semconv.GEN_AI_PROMPT}.0.role": "user",
            f"{semconv.GEN_AI_PROMPT}.0.content": "search",
            semconv.GEN_AI_TOOL_CALLS: '[{"id": "call-1", "name": "search"}]',
        },
    )

    assert read_llm_exchange(span) == (
        [{"role": "user", "content": "search"}],
        [
            {
                "role": "assistant",
                "tool_calls": [{"id": "call-1", "name": "search"}],
            }
        ],
    )


def test_read_rl_fields_normalizes_token_ids_and_logprobs() -> None:
    span = _span(
        "llm-rl",
        attrs={
            "evolution.rl.prompt_token_ids": '["101", 102, "bad"]',
            "evolution.rl.completion_token_ids": '[201, "202"]',
            "evolution.rl.logprobs": '{"content": [{"logprob": "-0.1"}, {"logprob": -0.2}, {"logprob": null}]}',
            "evolution.rl.reward": "0.5",
        },
    )

    assert read_rl_fields(span) == {
        "prompt_token_ids": [101, 102],
        "completion_token_ids": [201, 202],
        "logprobs": [-0.1, -0.2],
        "reward": 0.5,
    }


def test_normalize_merge_deduplicates_and_keeps_first_resource_identity() -> None:
    first = _payload([_span("s1", start=20)], trajectory_id="first")
    second = _payload([_span("s1", start=20), _span("s2", start=10)], trajectory_id="second")

    normalized = normalize_otlp(first)
    merged = merge_trajectories(Trajectory.from_otlp(first), Trajectory.from_otlp(second))
    merged_payload = merged.to_otlp()
    spans = list(iter_spans(merged))

    assert normalized is not first
    assert merged.trajectory_id == "first"
    assert [span_identity(span) for span in spans] == [("trace", "s2"), ("trace", "s1")]
    assert len(merged_payload["resourceSpans"]) == 1


def test_trim_trajectory_keeps_newest_spans_and_original_is_unchanged() -> None:
    payload = _payload([_span("s1", start=1), _span("s2", start=2), _span("s3", start=3)])
    original = deepcopy(payload)
    trajectory = Trajectory.from_otlp(payload)

    trimmed = trim_trajectory(trajectory, max_spans=2)

    assert [span["spanId"] for span in iter_spans(trimmed)] == ["s2", "s3"]
    assert payload == original
    assert len(list(iter_spans(trajectory))) == 3
