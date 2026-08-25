# coding: utf-8
"""Focused tests for stateless Team forest/member selectors."""

from __future__ import annotations

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.team import (
    build_team_forest,
    flatten_forest,
    group_spans_by_member,
    member_ids,
    select_member_spans,
    select_task_spans,
    select_team_spans,
    span_category,
)
from openjiuwen.extensions.observability import semconv


def _value(value):
    if isinstance(value, bool):
        return {"boolValue": value}
    return {"stringValue": str(value)}


def _attrs(values):
    return [{"key": key, "value": _value(value)} for key, value in values.items()]


def _span(span_id, name, *, parent=None, member=None, task=None, start=1, has_team=True):
    attrs = {semconv.AT_TEAM_ID: "team-a"} if has_team else {}
    if member is not None:
        attrs[semconv.AT_MEMBER_ID] = member
    if task is not None:
        attrs[semconv.AT_TASK_ID] = task
    span = {
        "traceId": "trace-a",
        "spanId": span_id,
        "name": name,
        "startTimeUnixNano": str(start),
        "attributes": _attrs(attrs),
    }
    if parent is not None:
        span["parentSpanId"] = parent
    return span


def _trajectory():
    spans = [
        _span("team", "team.demo", start=1),
        _span("agent-a", "agent.worker.task_iteration.1", parent="team", member="a", task="task-1", start=2),
        _span("llm-a", "llm.call", parent="agent-a", start=3, has_team=False),
        _span("tool-a", "tool.bash", parent="agent-a", start=4, has_team=False),
        _span("task", "task.task-1.completed", parent="team", task="task-1", start=5),
        _span("agent-b", "agent.worker.task_iteration.1", parent="team", member="b", task="task-2", start=6),
        _span("llm-b", "llm.call", parent="agent-b", start=7, has_team=False),
        _span("reason", "llm.reasoning", parent="llm-b", start=8, has_team=False),
        _span("orphan", "member.orphan.spawned", parent="missing", member="c", start=9),
    ]
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": _attrs(
                            {
                                TRAJECTORY_ID: "team-trajectory",
                                semconv.AT_SESSION_ID: "session-a",
                            }
                        )
                    },
                    "scopeSpans": [{"spans": spans}],
                }
            ]
        }
    )


def test_team_forest_preserves_native_parent_relationships_and_missing_roots() -> None:
    forest = build_team_forest(_trajectory())
    roots = [node["span"]["spanId"] for node in forest]

    assert roots == ["team", "orphan"]
    team = forest[0]
    assert [node["span"]["spanId"] for node in team["children"]] == [
        "agent-a",
        "task",
        "agent-b",
    ]
    assert [node["span"]["spanId"] for node in team["children"][0]["children"]] == [
        "llm-a",
        "tool-a",
    ]
    assert [span["spanId"] for span in flatten_forest(forest)] == [
        "team",
        "agent-a",
        "llm-a",
        "tool-a",
        "task",
        "agent-b",
        "llm-b",
        "orphan",
    ]


def test_member_selector_includes_child_llm_and_tool_without_member_copy() -> None:
    selected = select_member_spans(_trajectory(), "a")

    assert [span["spanId"] for span in selected] == ["agent-a", "llm-a", "tool-a"]
    assert [span["spanId"] for span in select_member_spans(_trajectory(), "b")] == [
        "agent-b",
        "llm-b",
    ]


def test_task_selector_and_grouping_are_stateless() -> None:
    trajectory = _trajectory()

    task_spans = select_task_spans(trajectory, "task-1")
    groups = group_spans_by_member(trajectory)

    assert [span["spanId"] for span in task_spans] == ["agent-a", "llm-a", "tool-a", "task"]
    assert member_ids(trajectory) == ("a", "b", "c")
    assert [span["spanId"] for span in groups["a"]] == ["agent-a"]
    assert [span["spanId"] for span in select_team_spans(trajectory, categories={"llm"})] == [
        "llm-a",
        "llm-b",
    ]


def test_reasoning_span_is_not_team_trajectory_input() -> None:
    assert span_category({"name": "llm.reasoning"}) is None


def test_span_category_prefers_tool_semantics_over_namespaced_tool_name() -> None:
    span = {
        "name": "team.custom_tool",
        "attributes": [
            {"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
        ],
    }

    assert span_category(span) == "tool"


def test_team_id_routes_children_without_repeated_team_attributes() -> None:
    selected = select_team_spans(_trajectory(), team_id="team-a", categories={"llm", "tool"})

    assert [span["spanId"] for span in selected] == ["llm-a", "tool-a", "llm-b"]
