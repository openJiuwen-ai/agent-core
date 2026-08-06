# coding: utf-8
"""Canonical offline builder/extractor tests."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.offline import (
    TrajectoryBuilder,
    TrajectoryExtractor,
)
from openjiuwen.agent_evolving.trajectory.schema import (
    CASE_ID,
    MEMBER_ID,
    SESSION_ID,
    TRAJECTORY_SOURCE,
)
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    read_llm_messages,
    read_tool_call,
)
from openjiuwen.extensions.observability import semconv


def _span(span_id: str, name: str = "tool.lookup") -> dict:
    return {
        "traceId": "offline-trace",
        "spanId": span_id,
        "name": name,
        "attributes": [],
    }


def _session_with_spans(spans):
    tracer = SimpleNamespace(
        tracer_agent_span_manager=SimpleNamespace(get_all_spans=lambda: spans),
    )
    return SimpleNamespace(tracer=lambda: tracer)


def _legacy_span(*, invoke_type="llm", name="llm", outputs=None, **kwargs):
    return SimpleNamespace(
        invoke_type=invoke_type,
        invoke_id=kwargs.pop("invoke_id", "invoke-1"),
        parent_invoke_id=kwargs.pop("parent_invoke_id", None),
        inputs=kwargs.pop("inputs", {"inputs": {"query": "hello"}}),
        outputs=outputs or {"outputs": {"content": "answer"}},
        error=kwargs.pop("error", None),
        start_time=kwargs.pop("start_time", datetime(2024, 1, 1, tzinfo=timezone.utc)),
        end_time=kwargs.pop("end_time", datetime(2024, 1, 1, 0, 0, 1, tzinfo=timezone.utc)),
        meta_data=kwargs.pop("meta_data", {}),
        operator_id=kwargs.pop("operator_id", "operator"),
        llm_call_id=kwargs.pop("llm_call_id", None),
        agent_id=kwargs.pop("agent_id", None),
        name=name,
        **kwargs,
    )


def test_builder_records_canonical_spans_and_resource_metadata() -> None:
    builder = TrajectoryBuilder(
        session_id="session-1",
        case_id="case-1",
        member_id="member-1",
        source="offline",
    )
    builder.record_span(_span("span-1"))

    trajectory = builder.build()
    assert isinstance(trajectory, Trajectory)
    assert trajectory.session_id == "session-1"
    assert trajectory.member_id == "member-1"
    assert trajectory.resource_attributes[CASE_ID] == "case-1"
    assert trajectory.resource_attributes[TRAJECTORY_SOURCE] == "offline"
    assert [span["spanId"] for span in iter_spans(trajectory)] == ["span-1"]


def test_builder_is_detached_and_deduplicates_span_identity() -> None:
    raw = _span("same")
    builder = TrajectoryBuilder(session_id="session-1")
    builder.record_span(raw)
    builder.record_span(raw)
    raw["name"] = "changed"

    trajectory = builder.build()
    assert len(list(iter_spans(trajectory))) == 1
    assert next(iter(iter_spans(trajectory)))["name"] == "tool.lookup"


def test_builder_releases_evicted_span_identities() -> None:
    builder = TrajectoryBuilder(session_id="session-1", max_spans=1)
    first = {**_span("span-1"), "startTimeUnixNano": "1"}
    second = {**_span("span-2"), "startTimeUnixNano": "2"}
    repeated = {**_span("span-1"), "startTimeUnixNano": "3"}

    builder.record_span(first)
    builder.record_span(second)
    builder.record_span(repeated)

    assert [span["spanId"] for span in iter_spans(builder.build())] == ["span-1"]


def test_extractor_generates_genai_and_tool_attributes_without_mutating_response() -> None:
    response = {
        "role": "assistant",
        "content": "answer",
        "prompt_token_ids": [1, 2],
        "completion_token_ids": [3],
        "logprobs": [-0.1],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }
    llm = _legacy_span(
        invoke_type="llm",
        name="llm-operator",
        on_invoke_data=[
            {
                "llm_params": {
                    "model": "model-a",
                    "messages": [{"role": "user", "content": "question"}],
                    "tools": [{"name": "lookup"}],
                }
            }
        ],
        outputs=response,
    )
    tool = _legacy_span(
        invoke_type="plugin",
        name="lookup",
        inputs={"inputs": {"q": "x"}},
        outputs={"outputs": {"ok": True}},
    )
    original_response = dict(response)

    trajectory = TrajectoryExtractor().extract(_session_with_spans([llm, tool]), case_id="case-1")
    spans = list(iter_spans(trajectory))
    llm_span, tool_span = spans

    assert read_llm_messages(llm_span) == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]
    assert read_tool_call(tool_span)["name"] == "lookup"
    assert read_tool_call(tool_span)["input"] == {"q": "x"}
    assert response == original_response
    attrs = trajectory.resource_attributes
    assert attrs[CASE_ID] == "case-1"
    assert attrs[SESSION_ID] == "case-1"


def test_extractor_handles_missing_tracer_with_canonical_empty_payload() -> None:
    session = SimpleNamespace(tracer=lambda: None)

    trajectory = TrajectoryExtractor().extract(session, case_id="case-empty")

    assert isinstance(trajectory, Trajectory)
    assert trajectory.session_id == "case-empty"
    assert list(iter_spans(trajectory)) == []


def test_offline_modules_do_not_import_legacy_step_model() -> None:
    source = inspect.getsource(TrajectoryBuilder) + inspect.getsource(TrajectoryExtractor)
    assert "trajectory.types" not in source
    assert semconv.GEN_AI_PROMPT not in {MEMBER_ID, SESSION_ID}
