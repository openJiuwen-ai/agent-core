# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for claude native llm.call content attachment from raw API bodies."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.observability.claude.bridge import ClaudeSpanBridge
from openjiuwen.extensions.observability.semconv import OJ_SPAN_INPUT, OJ_SPAN_OUTPUT


class _RecordingSpan:
    """Minimal span stand-in capturing attributes and end time."""

    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}
        self.ended = False
        self.end_ns: int | None = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def is_recording(self) -> bool:
        return not self.ended

    def get_span_context(self) -> Any:
        # Trace id 0x11 repeated 16 bytes == "11"*16 hex.
        return SimpleNamespace(
            trace_id=int("11" * 16, 16),
            span_id=0x99,
            trace_flags=1,
            is_valid=True,
        )

    def set_status(self, status: Any) -> None:
        self.attributes["status"] = status

    def end(self, end_time: int | None = None) -> None:
        self.ended = True
        self.end_ns = end_time


_TRACE_ID = "11" * 16
_SOURCE_ID = "source-claude-1"


def _bridge_with_turn(monkeypatch: pytest.MonkeyPatch) -> tuple[ClaudeSpanBridge, _RecordingSpan]:
    """Build a bridge with an active turn span and a stub tracer."""
    b = ClaudeSpanBridge(member_name="claude-1", team_name="t", session_id="s")
    turn = _RecordingSpan()
    b._turn_span = turn
    # Minimal config: redaction off, content passes through as plain text.
    b._config = SimpleNamespace(
        redact_prompts=False,
        redact_completions=False,
        attribute_value_max_length=40960,
    )
    b._native_trace_enabled = True
    b._native_source_id = _SOURCE_ID
    b._turn_started_at_ns = 1_699_999_999_999_999_999

    class _Tracer:
        def start_span(self, **kwargs: Any) -> _RecordingSpan:  # noqa: ARG002
            return _RecordingSpan()

    import openjiuwen.agent_teams.observability.claude.bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "get_tracer", lambda _name: _Tracer(), raising=False)
    # record_native_model_span imports get_tracer lazily from setup; patch it too.
    import openjiuwen.agent_teams.observability.setup as setup_mod

    monkeypatch.setattr(setup_mod, "get_tracer", lambda _name: _Tracer(), raising=False)
    return b, turn


def _llm_request_event(
    *,
    request_id: str = "req-1",
    model: str = "GLM-5.3",
    start_time_ns: int = 1_700_000_000_000_000_000,
    source_id: str = _SOURCE_ID,
) -> dict[str, Any]:
    return {
        "signal": "trace",
        "name": "claude_code.llm_request",
        "start_time_ns": start_time_ns,
        "end_time_ns": 1_700_000_000_250_000_000,
        "attributes": {
            "model": model,
            "request_id": request_id,
            "input_tokens": 100,
            "output_tokens": 20,
        },
        "trace_id": _TRACE_ID,
        "span_id": "aa" * 8,
        "parent_span_id": "",
        "resource_attributes": {"openjiuwen.agent_teams.source.id": source_id},
        "status_code": 1,
        "status_message": "",
    }


def _request_body_event(
    body: str,
    *,
    trace_id: str = _TRACE_ID,
    time_ns: int = 1_700_000_000_100_000_000,
    source_id: str = _SOURCE_ID,
) -> dict[str, Any]:
    return {
        "signal": "log",
        "name": "claude_code.api_request_body",
        "time_ns": time_ns,
        "attributes": {"body": body},
        "trace_id": trace_id,
        "span_id": "",
        "resource_attributes": {"openjiuwen.agent_teams.source.id": source_id},
    }


def _response_body_event(
    body: str,
    *,
    request_id: str = "req-1",
    trace_id: str = _TRACE_ID,
    source_id: str = _SOURCE_ID,
) -> dict[str, Any]:
    return {
        "signal": "log",
        "name": "claude_code.api_response_body",
        "time_ns": 1_700_000_000_200_000_000,
        "attributes": {"body": body, "request_id": request_id},
        "trace_id": trace_id,
        "span_id": "",
        "resource_attributes": {"openjiuwen.agent_teams.source.id": source_id},
    }


def _llm_spans(b: ClaudeSpanBridge) -> list[_RecordingSpan]:
    return [pending["span"] for pending in b._pending_native_calls]


@pytest.mark.level0
def test_response_body_attaches_by_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _turn = _bridge_with_turn(monkeypatch)

    b._on_native_span(_llm_request_event(request_id="req-1"))
    b._on_native_log(_response_body_event('{"content": "hi"}', request_id="req-1"))

    # Span is held open until finish_turn closes pending calls.
    llm = _llm_spans(b)[0]
    assert not llm.ended
    b._close_pending_native_calls()
    assert llm.ended
    assert llm.attributes[OJ_SPAN_OUTPUT] == '{"content": "hi"}'
    assert llm.end_ns == 1_700_000_000_250_000_000


@pytest.mark.level0
def test_request_bodies_attach_in_event_order(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _turn = _bridge_with_turn(monkeypatch)

    b._on_native_span(_llm_request_event(request_id="req-1"))
    b._on_native_span(_llm_request_event(request_id="req-2"))
    first, second = _llm_spans(b)
    b._on_native_log(_request_body_event('{"messages": [1]}'))
    b._on_native_log(_request_body_event('{"messages": [2]}'))
    b._close_pending_native_calls()

    assert first.attributes[OJ_SPAN_INPUT] == '{"messages": [1]}'
    assert second.attributes[OJ_SPAN_INPUT] == '{"messages": [2]}'


@pytest.mark.level0
def test_request_bodies_attach_by_event_time_when_exports_are_reordered(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _turn = _bridge_with_turn(monkeypatch)

    b._on_native_log(_request_body_event('{"messages": [2]}', time_ns=1_700_000_000_200_000_000))
    b._on_native_span(
        _llm_request_event(
            request_id="req-2",
            start_time_ns=1_700_000_000_150_000_000,
        ),
    )
    b._on_native_log(_request_body_event('{"messages": [1]}', time_ns=1_700_000_000_100_000_000))
    b._on_native_span(_llm_request_event(request_id="req-1"))

    calls = sorted(b._pending_native_calls, key=lambda pending: pending["start_ns"])
    first = calls[0]["span"]
    second = calls[1]["span"]
    b._close_pending_native_calls()

    assert first.attributes[OJ_SPAN_INPUT] == '{"messages": [1]}'
    assert second.attributes[OJ_SPAN_INPUT] == '{"messages": [2]}'


@pytest.mark.level0
def test_response_body_arriving_before_span_is_retained(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _turn = _bridge_with_turn(monkeypatch)

    b._on_native_log(_response_body_event('{"content": "early"}', request_id="req-1"))
    b._on_native_span(_llm_request_event(request_id="req-1"))
    llm = _llm_spans(b)[0]
    b._close_pending_native_calls()

    assert llm.attributes[OJ_SPAN_OUTPUT] == '{"content": "early"}'


@pytest.mark.level0
def test_response_with_unknown_request_id_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _turn = _bridge_with_turn(monkeypatch)

    b._on_native_span(_llm_request_event(request_id="req-1"))
    llm = _llm_spans(b)[0]
    b._on_native_log(_response_body_event("{}", request_id="req-other"))
    b._close_pending_native_calls()

    assert "langfuse.observation.output" not in llm.attributes


@pytest.mark.level0
def test_foreign_trace_events_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _turn = _bridge_with_turn(monkeypatch)
    # Trace id mismatch: events from another member's trace must not attach.
    b._on_native_span(_llm_request_event())
    llm = _llm_spans(b)[0]
    b._on_native_log(_response_body_event("{}", trace_id="ff" * 16))
    b._on_native_log(_request_body_event("{}", trace_id="ff" * 16))
    b._close_pending_native_calls()

    assert "langfuse.observation.output" not in llm.attributes
    assert "langfuse.observation.input" not in llm.attributes


@pytest.mark.level0
def test_foreign_source_events_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _turn = _bridge_with_turn(monkeypatch)

    b._on_native_span(_llm_request_event(source_id="source-claude-2"))
    b._on_native_log(_request_body_event("{}", source_id="source-claude-2"))
    b._on_native_log(_response_body_event("{}", source_id="source-claude-2"))

    assert not b._pending_native_calls
    assert not b._pending_native_request_bodies


@pytest.mark.level0
def test_span_started_before_current_turn_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _turn = _bridge_with_turn(monkeypatch)

    b._on_native_span(_llm_request_event(start_time_ns=b._turn_started_at_ns - 1))

    assert not b._pending_native_calls


@pytest.mark.level0
def test_finish_turn_closes_pending_llm_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    b, turn = _bridge_with_turn(monkeypatch)

    b._on_native_span(_llm_request_event())
    b._on_native_log(_response_body_event('{"content": "done"}'))
    llm = _llm_spans(b)[0]
    assert not llm.ended

    b.finish_turn(status="ok")

    assert llm.ended
    assert llm.attributes[OJ_SPAN_OUTPUT] == '{"content": "done"}'
    assert turn.ended


@pytest.mark.level0
def test_native_traceparent_falls_back_to_team_span(monkeypatch: pytest.MonkeyPatch) -> None:
    b, turn = _bridge_with_turn(monkeypatch)

    # With an active turn span, its trace parent is returned.
    parent = b.native_traceparent()
    assert parent is not None
    assert parent.startswith("00-")
    assert f"{int('11' * 16, 16):032x}" in parent

    # Before any turn starts (runtime construction time), the team span from
    # the observability runtime serves as the carrier.
    b._turn_span = None

    class _TeamSpan:
        def get_span_context(self) -> Any:
            return SimpleNamespace(
                trace_id=int("11" * 16, 16),
                span_id=0x77,
                trace_flags=1,
                is_valid=True,
            )

    monkeypatch.setattr(
        b,
        "_observability_runtime",
        staticmethod(lambda: (None, None, _TeamSpan())),
    )
    parent = b.native_traceparent()
    assert parent is not None
    assert f"{int('11' * 16, 16):032x}" in parent
    assert f"{0x77:016x}" in parent
