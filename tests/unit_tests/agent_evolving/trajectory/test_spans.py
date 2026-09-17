# coding: utf-8
"""Focused tests for stateless canonical span accessors."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    attributes_to_map,
    crop_trajectory,
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
    span_attributes,
    span_identity,
    trim_spans,
    trim_trajectory,
    write_llm_exchange,
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
                            semconv.GEN_AI_CONVERSATION_ID: "session",
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
            **write_llm_exchange(
                [{"role": "user", "content": "hello"}],
                [{
                    "role": "assistant",
                    "content": "done",
                    "tool_calls": [{"name": "search", "arguments": {"q": "x"}}],
                }],
            ),
            semconv.GEN_AI_USAGE_INPUT_TOKENS: 3,
            semconv.GEN_AI_USAGE_OUTPUT_TOKENS: 2,
        },
    )
    tool = _span(
        "tool-1",
        name="tool.search",
        attrs={
            semconv.GEN_AI_TOOL_NAME: "search",
            semconv.GEN_AI_TOOL_CALL_ID: "call-1",
            semconv.GEN_AI_TOOL_CALL_ARGUMENTS: '{"q": "x"}',
            semconv.GEN_AI_TOOL_CALL_RESULT: '{"ok": true}',
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
            semconv.GEN_AI_TOOL_CALL_ARGUMENTS: "0",
            semconv.GEN_AI_TOOL_CALL_RESULT: "true",
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
            **write_llm_exchange(
                [{"role": "user", "content": "hello"}],
                [{
                    "role": "assistant",
                    "content": "done",
                    "tool_calls": [{"id": "call-1"}],
                }],
            ),
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


def test_llm_exchange_preserves_tool_call_without_completion_attributes() -> None:
    span = _span(
        "llm-tool-call",
        attrs={
            **write_llm_exchange(
                [{"role": "user", "content": "search"}],
                [{
                    "role": "assistant",
                    "tool_calls": [{"id": "call-1", "name": "search"}],
                }],
            ),
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


@pytest.mark.parametrize("transform", [trim_trajectory, crop_trajectory])
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, [["a3", "a1"], ["b2", "b4"]]),
        ({"max_spans": 200}, [["a1", "a3"], ["b2", "b4"]]),
        ({"max_spans": 2}, [["a3"], ["b4"]]),
        ({"start_time": 3, "end_time": 3}, [["a3"], ["b2"]]),
        ({"start_time": 3, "end_time": 3, "max_spans": 1}, [["a3"], []]),
        ({"max_spans": 0}, [[], []]),
        ({"max_spans": -1}, [[], []]),
        ({"start_time": 100}, [[], []]),
    ],
)
def test_trim_preserves_scope_groups_and_global_selection(transform, options, expected) -> None:
    payload = _payload([_span("a3", start=3), _span("a1", start=1)])
    scopes = payload["resourceSpans"][0]["scopeSpans"]
    scopes[0]["scope"] = {"name": "native", "version": "1"}
    scopes[0]["schemaUrl"] = "https://example.test/native"
    scopes.append(
        {
            "scope": {"name": "bridge", "version": "2"},
            "schemaUrl": "https://example.test/bridge",
            "spans": [_span("b2", start=2), _span("b4", start=4)],
        }
    )
    scopes.append({"scope": {"name": "empty"}, "spans": []})
    original = deepcopy(payload)
    trajectory = Trajectory.from_otlp(payload)

    trimmed = transform(trajectory, **options)
    actual = trimmed.to_otlp()
    expected_payload = normalize_otlp(original)
    for group, ids in zip(expected_payload["resourceSpans"][0]["scopeSpans"], expected + [[]]):
        by_id = {span["spanId"]: span for span in group["spans"]}
        group["spans"] = [by_id[span_id] for span_id in ids]
    assert actual == expected_payload
    assert payload == original
    assert trajectory.to_otlp() == original

    if options:
        selected = trim_spans(iter_spans(trajectory), **options)
        assert sorted(span["spanId"] for span in iter_spans(trimmed)) == sorted(span["spanId"] for span in selected)
    actual["resourceSpans"][0]["scopeSpans"][0]["scope"]["name"] = "changed"
    assert trimmed.to_otlp() == expected_payload


def test_trim_preserves_resource_metadata_and_duplicate_span_occurrences() -> None:
    payload = _payload([_span("same", attrs={"origin": "first"})])
    second = _payload([_span("same", attrs={"origin": "second"})], trajectory_id="second")
    payload["resourceSpans"].extend(second["resourceSpans"])
    payload["resourceSpans"][1]["schemaUrl"] = "https://example.test/resource"
    original = deepcopy(payload)

    # Equal sort keys retain traversal order, so a limit of one selects the second occurrence.
    trimmed = trim_trajectory(payload, max_spans=1).to_otlp()
    expected = normalize_otlp(original)
    expected["resourceSpans"][0]["scopeSpans"][0]["spans"] = []
    assert trimmed == expected
    assert trim_trajectory(payload, max_spans=2).to_otlp() == normalize_otlp(original)
    assert payload == original


@pytest.mark.parametrize("scope_groups", [[], [{"scope": {"name": "empty"}, "spans": []}]])
def test_trim_preserves_empty_groups(scope_groups) -> None:
    payload = _payload([])
    payload["resourceSpans"][0]["scopeSpans"] = scope_groups
    assert trim_trajectory(payload, max_spans=200).to_otlp() == normalize_otlp(payload)


def test_trim_spans_returns_detached_occurrences() -> None:
    span = _span("same", attrs={"origin": "original"})
    selected = trim_spans(iter([span, span]), max_spans=2)

    selected[0]["attributes"][0]["value"]["stringValue"] = "changed"
    assert selected[1] == span
    assert span_attributes(selected[1]) == {"origin": "original"}


def test_llm_exchange_reads_the_standard_structured_attributes() -> None:
    """Instrumentation writes one standard shape; the reader flattens it here."""
    span = _span(
        "llm",
        attrs={
            semconv.GEN_AI_SYSTEM_INSTRUCTIONS: json.dumps(
                [{"type": "text", "content": "FIXED"}]
            ),
            semconv.GEN_AI_INPUT_MESSAGES: json.dumps([
                {"role": "user", "parts": [{"type": "text", "content": "hi"}]},
                {
                    "role": "assistant",
                    "parts": [{"type": "text", "content": ""}],
                    "tool_calls": [{"id": "t1"}],
                },
                {"role": "system", "parts": [{"type": "text", "content": "DELTA"}]},
            ]),
            semconv.GEN_AI_OUTPUT_MESSAGES: json.dumps(
                [{"role": "assistant", "parts": [{"type": "text", "content": "done"}]}]
            ),
        },
    )

    prompts, completions = read_llm_exchange(span)

    assert [message["role"] for message in prompts] == [
        "system",
        "user",
        "assistant",
        "system",
    ]
    assert prompts[0]["content"] == "FIXED"
    assert prompts[1]["content"] == "hi"
    assert prompts[2]["tool_calls"] == [{"id": "t1"}]
    assert prompts[3]["content"] == "DELTA"
    assert completions == [{"role": "assistant", "content": "done"}]


def test_an_llm_exchange_round_trips_through_the_standard_attributes() -> None:
    """What write_llm_exchange records, read_llm_exchange gives back unchanged."""
    prompts = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "search"}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "found"},
    ]
    completions = [{"role": "assistant", "content": "done"}]

    span = _span("llm", attrs=write_llm_exchange(prompts, completions))

    assert read_llm_exchange(span) == (prompts, completions)
