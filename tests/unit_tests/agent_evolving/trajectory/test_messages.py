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
    project_trajectory_messages,
    trajectory_to_messages,
)
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    attributes_from_map,
    write_llm_exchange,
)
from openjiuwen.extensions.observability import semconv
from openjiuwen.extensions.observability.span_context import advance_context_window, reset_state


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
                                semconv.GEN_AI_CONVERSATION_ID: "session-1",
                            }
                        )
                    },
                    "scopeSpans": [{"scope": {"name": "test"}, "spans": spans}],
                }
            ]
        }
    )


def _message(message_id: str, role: str, content: object = None, **fields: object) -> dict:
    message = {"message_id": message_id, "role": role, "origin": "harness_internal", **fields}
    if content is not None:
        message["content"] = content
    return message


def _without_identity(message: dict) -> dict:
    return {key: value for key, value in message.items() if key not in {"message_id", "origin"}}


def _committed_request(
    span_id: str,
    *,
    start: int,
    window: list[dict],
    completion: dict | None = None,
    purpose: str = "assistant",
) -> list[dict]:
    """One model request plus the window commit the observability layer emits for it."""

    epoch, sequence, base_window_id, delta, baseline = advance_context_window(
        session_id="session-1",
        subject_id="main",
        window_id=f"window-{span_id}",
        messages=window,
    )
    payload: dict = {
        "window_id": f"window-{span_id}",
        "base_window_id": base_window_id,
        "complete": True,
        "delta": delta,
        "request_purpose": purpose,
    }
    if baseline:
        payload.update(messages=window, transition_kind="epoch_baseline", baseline_reason="runtime_epoch_start")
    request = _llm_span(
        span_id,
        start=start,
        prompt=[_without_identity(message) for message in window],
        completion=completion,
    )
    commit = _span(
        f"{span_id}-commit",
        start=start,
        name="context.window.commit",
        attributes={
            semconv.OJ_TRAJECTORY_RECORD_KIND: "event",
            semconv.OJ_TRAJECTORY_EVENT_ID: f"event-{span_id}",
            semconv.OJ_TRAJECTORY_EVENT_KIND: "context.window.commit",
            semconv.OJ_TRAJECTORY_SUBJECT_ID: "main",
            semconv.OJ_TRAJECTORY_SEQUENCE_EPOCH: epoch,
            semconv.OJ_TRAJECTORY_SUBJECT_SEQUENCE: sequence,
            semconv.OJ_TRAJECTORY_RECORDED_AT_UNIX_NANO: start,
            semconv.OJ_TRAJECTORY_PAYLOAD: json.dumps(payload),
        },
    )
    commit["parentSpanId"] = span_id
    return [request, commit]


@pytest.fixture(autouse=True)
def _fresh_window_state():
    reset_state()
    yield
    reset_state()


def test_window_chain_rebuilds_messages_without_overlap_heuristics() -> None:
    """chat -> tool -> chat is four messages, however the tool result was recorded.

    The tool span records the ability's whole result envelope, while the model
    was given only its content, and the prompt restates the tool message
    without a name: three ways the same message differs by text. Identity from
    the window chain makes none of them matter.
    """

    call = {"id": "call-1", "name": "search", "arguments": '{"q":"x"}'}
    user = _message("u1", "user", "find x")
    assistant = _message("a1", "assistant", "", tool_calls=[call])
    tool_result = _message("t1", "tool", "x is 42", tool_call_id="call-1")
    spans = [
        *_committed_request(
            "llm-1",
            start=10,
            window=[user],
            completion={"role": "assistant", "content": "", "tool_calls": [call]},
        ),
        _span(
            "tool-1",
            start=20,
            name="execute_tool search",
            attributes={
                semconv.GEN_AI_OPERATION_NAME: "execute_tool",
                semconv.GEN_AI_TOOL_NAME: "search",
                semconv.GEN_AI_TOOL_CALL_ID: "call-1",
                semconv.GEN_AI_TOOL_CALL_RESULT: {"success": True, "data": {"content": "x is 42"}},
            },
        ),
        *_committed_request(
            "llm-2",
            start=30,
            window=[user, assistant, tool_result],
            completion={"role": "assistant", "content": "It is 42."},
        ),
    ]

    messages, issues = project_trajectory_messages(_trajectory(spans))

    assert issues == ()
    assert messages == [
        {"role": "user", "content": "find x"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "search", "arguments": '{"q":"x"}'}}
            ],
        },
        {"role": "tool", "name": "search", "tool_call_id": "call-1", "content": "x is 42"},
        {"role": "assistant", "content": "It is 42."},
    ]


def test_repeated_text_with_distinct_identity_is_kept() -> None:
    first_ask = _message("u1", "user", "repeat")
    first_answer = _message("a1", "assistant", "first")
    second_ask = _message("u2", "user", "repeat")
    spans = [
        *_committed_request("llm-1", start=10, window=[first_ask], completion={"role": "assistant", "content": "first"}),
        *_committed_request(
            "llm-2",
            start=20,
            window=[first_ask, first_answer, second_ask],
            completion={"role": "assistant", "content": "second"},
        ),
    ]

    assert trajectory_to_messages(_trajectory(list(reversed(spans)))) == [
        {"role": "user", "content": "repeat"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "repeat"},
        {"role": "assistant", "content": "second"},
    ]


def test_window_union_keeps_what_a_compaction_removed() -> None:
    old = _message("u1", "user", "old question")
    summary = _message("s1", "user", "summary of old question")
    latest = _message("u2", "user", "latest")
    spans = [
        *_committed_request("llm-1", start=10, window=[old], completion={"role": "assistant", "content": "old answer"}),
        *_committed_request("llm-2", start=20, window=[summary, latest], completion={"role": "assistant", "content": "done"}),
    ]

    assert trajectory_to_messages(_trajectory(spans)) == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "summary of old question"},
        {"role": "user", "content": "latest"},
        {"role": "assistant", "content": "done"},
    ]


def test_missing_window_commit_is_reported_as_issue() -> None:
    first = _llm_span(
        "llm-1",
        start=10,
        prompt=[{"role": "user", "content": "hello"}],
        completion={"role": "assistant", "content": "hi"},
    )
    second = _llm_span(
        "llm-2",
        start=20,
        prompt=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "again"},
        ],
        completion={"role": "assistant", "content": "hi again"},
    )

    messages, issues = project_trajectory_messages(_trajectory([first, second]))

    assert [(issue["code"], issue["span_id"]) for issue in issues] == [
        ("missing_context_window", "llm-1"),
        ("missing_context_window", "llm-2"),
    ]
    # No guess at which messages the second request shared with the first.
    assert [message["content"] for message in messages] == ["hello", "hi", "hello", "hi", "again", "hi again"]


def test_compaction_llm_spans_are_excluded_from_messages() -> None:
    user = _message("u1", "user", "hello")
    compaction = _llm_span(
        "llm-compaction",
        start=20,
        prompt=[{"role": "user", "content": "summarize the conversation"}],
        completion={"role": "assistant", "content": "summary"},
    )
    compaction_attributes = {item["key"]: item["value"] for item in compaction["attributes"]}
    compaction["attributes"] = attributes_from_map(
        {
            **{key: next(iter(value.values())) for key, value in compaction_attributes.items()},
            semconv.OJ_REQUEST_PURPOSE: "compaction",
        }
    )
    spans = [
        *_committed_request("llm-1", start=10, window=[user], completion={"role": "assistant", "content": "hi"}),
        compaction,
    ]

    messages, issues = project_trajectory_messages(_trajectory(spans))

    assert issues == ()
    assert messages == [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]


def test_tool_result_appears_once_with_name_and_id() -> None:
    call = {"id": "call-9", "name": "read", "arguments": "{}"}
    ask = _message("u1", "user", "read it")
    spans = [
        *_committed_request(
            "llm-1",
            start=10,
            window=[ask],
            completion={"role": "assistant", "content": "", "tool_calls": [call]},
        ),
        _span(
            "tool-1",
            start=20,
            name="execute_tool read",
            attributes={
                semconv.GEN_AI_OPERATION_NAME: "execute_tool",
                semconv.GEN_AI_TOOL_NAME: "read",
                semconv.GEN_AI_TOOL_CALL_ID: "call-9",
                semconv.GEN_AI_TOOL_CALL_RESULT: "file body",
            },
        ),
        *_committed_request(
            "llm-2",
            start=30,
            window=[
                ask,
                _message("a1", "assistant", "", tool_calls=[call]),
                _message("t1", "tool", "file body", tool_call_id="call-9"),
            ],
        ),
    ]

    tool_messages = [message for message in trajectory_to_messages(_trajectory(spans)) if message["role"] == "tool"]

    assert tool_messages == [{"role": "tool", "name": "read", "tool_call_id": "call-9", "content": "file body"}]


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
            semconv.GEN_AI_TOOL_CALL_ID: "call-1",
            semconv.GEN_AI_TOOL_CALL_RESULT: {"ok": True},
        },
    )
    unlinked_tool = _span(
        "tool-2",
        start=30,
        name="tool.search",
        attributes={semconv.GEN_AI_TOOL_CALL_RESULT: "same-name output"},
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


def test_reasoning_content_is_selected_only_on_request() -> None:
    trajectory = _trajectory(
        [
            _llm_span(
                "llm-1",
                start=10,
                prompt=[{"role": "user", "content": "why"}],
                completion={"role": "assistant", "content": "because", "reasoning_content": "thinking"},
            )
        ]
    )

    assert trajectory_to_messages(trajectory)[-1] == {"role": "assistant", "content": "because"}
    assert trajectory_to_messages(trajectory, fields={"content", "reasoning_content"})[-1] == {
        "role": "assistant",
        "content": "because",
        "reasoning_content": "thinking",
    }


def test_uses_tool_error_only_when_output_is_absent() -> None:
    tool = _span(
        "tool-1",
        start=10,
        name="tool.failed",
        attributes={semconv.GEN_AI_TOOL_NAME: "failed"},
        status={"code": "STATUS_CODE_ERROR", "message": "boom"},
    )

    assert trajectory_to_messages(_trajectory([tool])) == [{"role": "tool", "name": "failed", "content": "boom"}]


def test_invoke_local_trims_first_llm_prompt_to_last_user() -> None:
    """Prior-round tool failures in the request prompt must not leak in."""
    trajectory = _trajectory(
        [
            _llm_span(
                "llm-1",
                start=10,
                prompt=[
                    {"role": "system", "content": "rules"},
                    {"role": "user", "content": "make xlsx"},
                    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "bash"}]},
                    {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "Error: python3 not found"},
                    {"role": "assistant", "content": "done"},
                    {"role": "user", "content": "你好"},
                ],
                completion={"role": "assistant", "content": "你好！有什么可以帮你？"},
            )
        ]
    )

    full = trajectory_to_messages(trajectory)
    assert any(m.get("role") == "tool" and "python3" in str(m.get("content") or "") for m in full)

    local = trajectory_to_messages(trajectory, invoke_local=True)
    assert local == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么可以帮你？"},
    ]


def test_invoke_local_keeps_same_invoke_react_steps_via_overlap() -> None:
    """After the first trimmed prompt, later spans still merge invoke-local tools."""
    first = _llm_span(
        "llm-1",
        start=10,
        prompt=[
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "old task"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "run bash"},
        ],
        completion={
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "name": "bash", "arguments": {"command": "python3 x"}}],
        },
    )
    tool = _span(
        "tool-1",
        start=20,
        name="tool.bash",
        attributes={
            semconv.GEN_AI_TOOL_NAME: "bash",
            semconv.GEN_AI_TOOL_CALL_ID: "c1",
            semconv.GEN_AI_TOOL_CALL_RESULT: "Error: python3 is not recognized",
        },
    )
    second = _llm_span(
        "llm-2",
        start=30,
        prompt=[
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "old task"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "run bash"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "bash", "arguments": {"command": "python3 x"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "Error: python3 is not recognized"},
        ],
        completion={"role": "assistant", "content": "retry with python"},
    )

    messages = trajectory_to_messages(_trajectory([first, tool, second]), invoke_local=True)
    assert messages[0] == {"role": "user", "content": "run bash"}
    assert not any(m.get("content") == "old task" for m in messages)
    assert any(m.get("role") == "tool" and "python3" in str(m.get("content") or "") for m in messages)
    assert messages[-1] == {"role": "assistant", "content": "retry with python"}


def test_invoke_local_without_user_keeps_only_completions() -> None:
    trajectory = _trajectory(
        [
            _llm_span(
                "llm-1",
                start=10,
                prompt=[
                    {"role": "system", "content": "rules"},
                    {"role": "assistant", "content": "stale"},
                ],
                completion={"role": "assistant", "content": "fresh"},
            )
        ]
    )
    assert trajectory_to_messages(trajectory, invoke_local=True) == [
        {"role": "assistant", "content": "fresh"},
    ]