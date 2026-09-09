from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.extensions.observability import semconv
from openjiuwen.harness.rails.evolution.symphony_edge_evidence import (
    SymphonyEdgeCandidate,
    SymphonyInterruptContinuation,
    build_model_edge_decisions,
    build_symphony_edge_candidates,
)
from openjiuwen.harness.rails.evolution.symphony_execution_fragments import (
    project_symphony_execution_fragments,
)


def _span(
    name: str,
    span_id: int,
    *,
    trace_number: int = 1,
    parent_span_id: int | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "traceId": f"{trace_number:032x}",
        "spanId": f"{span_id:016x}",
        "name": name,
        "startTimeUnixNano": str(span_id),
        "endTimeUnixNano": str(span_id + 1),
        "attributes": attributes_from_map(attributes or {}),
        "status": {"code": "STATUS_CODE_OK"},
    }
    if parent_span_id is not None:
        span["parentSpanId"] = f"{parent_span_id:016x}"
    return span


def _tool(
    span_id: int,
    name: str,
    *,
    trace_number: int = 1,
    parent_span_id: int = 1,
    tool_input: Any = None,
    tool_output: Any = None,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {semconv.GEN_AI_TOOL_NAME: name}
    if tool_input is not None:
        attributes[semconv.GEN_AI_TOOL_INPUT] = tool_input
    if tool_output is not None:
        attributes[semconv.GEN_AI_TOOL_OUTPUT] = tool_output
    return _span(
        f"tool.{name}",
        span_id,
        trace_number=trace_number,
        parent_span_id=parent_span_id,
        attributes=attributes,
    )


def _skill(span_id: int, name: str, *, trace_number: int = 1, parent_span_id: int = 1) -> dict[str, Any]:
    return _tool(
        span_id,
        "skill_tool",
        trace_number=trace_number,
        parent_span_id=parent_span_id,
        tool_input={"skill_name": name, "relative_file_path": "SKILL.md"},
        tool_output={"success": True},
    )


def _trajectory(*spans: dict[str, Any]) -> Trajectory:
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": attributes_from_map({TRAJECTORY_ID: "trajectory-1", SESSION_ID: "session-1"})
                    },
                    "scopeSpans": [{"scope": {"name": "test"}, "spans": list(spans)}],
                }
            ]
        }
    )


def _names(candidate: SymphonyEdgeCandidate) -> tuple[str | None, str | None]:
    return candidate.source_fragment.capability_name, candidate.target_fragment.capability_name


def _planned_graph(*edges: tuple[str, str], names: tuple[str, ...]) -> dict[str, Any]:
    return {
        "graph": {
            "type": "planned_graph",
            "directed": True,
            "metadata": {"status": "ready"},
            "nodes": {name: {"label": name, "metadata": {"type": "skill"}} for name in names},
            "edges": [{"source": source, "target": target} for source, target in edges],
        }
    }


@pytest.mark.parametrize(
    ("depth", "expected"),
    [
        (0, []),
        (1, [("one", "two"), ("two", "three")]),
        (2, [("one", "two"), ("one", "three"), ("two", "three")]),
    ],
)
def test_missing_plan_uses_bounded_forward_skill_pairs(
    depth: int,
    expected: list[tuple[str, str]],
) -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "one"),
        _skill(3, "two"),
        _skill(4, "three"),
    )
    continuity = ((0, trajectory),)

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
        edge_search_max_depth=depth,
    )

    assert [_names(candidate) for candidate in candidates] == expected
    assert all(candidate.candidate_reasons == ("observed_order",) for candidate in candidates)


def test_planned_repeated_names_pair_nearest_forward_occurrences() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "source"),
        _skill(3, "target"),
        _skill(4, "source"),
        _skill(5, "target"),
    )
    continuity = ((0, trajectory),)

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
        planned_graph=_planned_graph(("source", "target"), names=("source", "target")),
    )

    assert [_names(candidate) for candidate in candidates] == [
        ("source", "target"),
        ("source", "target"),
    ]
    assert len({candidate.candidate_id for candidate in candidates}) == 2


def test_planned_candidate_is_only_prior_and_starts_fail_closed() -> None:
    trajectory = _trajectory(_span("agent.main", 1), _skill(2, "source"), _skill(3, "target"))
    continuity = ((0, trajectory),)
    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
        planned_graph=_planned_graph(("source", "target"), names=("source", "target")),
    )

    decisions = build_model_edge_decisions(candidates)

    assert [_names(candidate) for candidate in candidates] == [("source", "target")]
    assert candidates[0].candidate_reasons == ("planned",)
    assert decisions[0].status == "insufficient_evidence"
    assert decisions[0].reason == "awaiting_model_evidence"
    assert decisions[0].evidence_refs == ()


def test_planned_skills_across_main_agent_steps_share_the_agent_branch() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _span(
            "agent.main.react_iteration.1",
            2,
            parent_span_id=1,
            attributes={semconv.OJ_TRAJECTORY_RECORD_KIND: "step"},
        ),
        _skill(3, "weather", parent_span_id=2),
        _span(
            "agent.main.react_iteration.2",
            4,
            parent_span_id=1,
            attributes={semconv.OJ_TRAJECTORY_RECORD_KIND: "step"},
        ),
        _skill(5, "travel-guide-generator", parent_span_id=4),
    )
    continuity = ((0, trajectory),)

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
        planned_graph=_planned_graph(
            ("weather", "travel-guide-generator"),
            names=("weather", "travel-guide-generator"),
        ),
    )

    assert [_names(candidate) for candidate in candidates] == [
        ("weather", "travel-guide-generator"),
    ]


def test_planned_graph_does_not_add_unplanned_or_proximity_pairs() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "planned-a"),
        _skill(3, "deviation"),
        _skill(4, "planned-b"),
    )
    continuity = ((0, trajectory),)

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
        planned_graph=_planned_graph(("planned-a", "planned-b"), names=("planned-a", "planned-b")),
    )

    assert [_names(candidate) for candidate in candidates] == [("planned-a", "planned-b")]
    assert candidates[0].candidate_reasons == ("planned",)


def test_tool_reference_noise_does_not_create_or_displace_candidates() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "weather"),
        _tool(3, "write_file", parent_span_id=2, tool_output={"path": "/workspace/beijing.json"}),
        _skill(4, "travel-guide-generator"),
        *(
            _tool(
                index,
                "edit_file",
                parent_span_id=4,
                tool_input={"path": "/workspace/beijing.json"},
                tool_output={"path": "/workspace/beijing.json"},
            )
            for index in range(5, 34)
        ),
    )
    continuity = ((0, trajectory),)

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
        planned_graph=_planned_graph(
            ("weather", "travel-guide-generator"),
            names=("weather", "travel-guide-generator"),
        ),
        max_candidates=1,
    )

    assert [_names(candidate) for candidate in candidates] == [("weather", "travel-guide-generator")]
    assert candidates[0].candidate_reasons == ("planned",)


def test_tool_and_subagent_fragments_never_become_edge_endpoints() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _tool(2, "producer", tool_output={"artifact_id": "shared"}),
        _tool(3, "consumer", tool_input={"artifact_id": "shared"}),
        _tool(4, "task_tool", tool_input={"subagent_type": "research"}),
    )
    continuity = ((0, trajectory),)

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
    )

    assert candidates == ()


def test_repeated_skill_name_does_not_create_self_candidate() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "repeat"),
        _skill(3, "middle"),
        _skill(4, "repeat"),
    )
    continuity = ((0, trajectory),)

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
    )

    assert ("repeat", "repeat") not in {_names(candidate) for candidate in candidates}


def test_candidates_do_not_cross_continuities_or_traces_without_resume() -> None:
    first = _trajectory(_span("agent.first", 1), _skill(2, "source"))
    second = _trajectory(
        _span("agent.second", 10, trace_number=2),
        _skill(11, "target", trace_number=2, parent_span_id=10),
    )
    continuities = ((0, first), (1, second))

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuities),
        continuities,
    )

    assert candidates == ()


def _cross_trace_fixture():
    first = _trajectory(
        _span("agent.first", 1),
        _skill(2, "producer"),
        _skill(3, "other"),
        _skill(4, "producer"),
    )
    second = _trajectory(
        _span("agent.second", 10, trace_number=2),
        _skill(11, "consumer", trace_number=2, parent_span_id=10),
        _skill(12, "other", trace_number=2, parent_span_id=10),
        _skill(13, "consumer", trace_number=2, parent_span_id=10),
    )
    continuities = ((0, first), (0, second))
    boundary = SymphonyInterruptContinuation(
        f"{1:032x}",
        f"{2:032x}",
        0,
        0,
        1,
        (f"{1:032x}", f"{2:032x}"),
    )
    return (
        project_symphony_execution_fragments(continuities),
        continuities,
        boundary,
        _planned_graph(("producer", "consumer"), names=("producer", "consumer")),
    )


def test_planned_skill_cross_trace_uses_exact_interrupt_continuation() -> None:
    fragments, continuities, boundary, plan = _cross_trace_fixture()

    candidates = build_symphony_edge_candidates(
        fragments,
        continuities,
        planned_graph=plan,
        interrupt_continuations=(boundary,),
    )
    cross = [
        candidate
        for candidate in candidates
        if candidate.source_fragment.trace_id != candidate.target_fragment.trace_id
    ]

    assert len(cross) == 1
    assert cross[0].candidate_reasons == ("interrupt_continuation",)
    assert cross[0].source_fragment.anchor_span_id == f"{4:016x}"
    assert cross[0].target_fragment.anchor_span_id == f"{11:016x}"
    assert cross[0].interrupt_continuation == boundary
    assert set(cross[0].evidence_refs) == {
        f"{1:032x}#span={4:016x}",
        f"{2:032x}#span={11:016x}",
    }


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "no_plan",
        "not_ready",
        "undirected",
        "continuity_gap",
        "nonadjacent",
        "source_branches",
        "target_branches",
        "negative",
        "bool",
        "out_of_range",
        "malformed",
        "bad_trace_ids",
        "wrong_trace",
    ],
)
def test_invalid_interrupt_continuation_does_not_cross_traces(case: str) -> None:
    fragments, continuities, boundary, plan = _cross_trace_fixture()
    descriptors: tuple[Any, ...]
    if case == "missing":
        descriptors = ()
    elif case == "no_plan":
        plan = None
        descriptors = (boundary,)
    elif case == "not_ready":
        plan["graph"]["metadata"]["status"] = "needs_input"
        descriptors = (boundary,)
    elif case == "undirected":
        plan["graph"]["directed"] = False
        descriptors = (boundary,)
    elif case == "continuity_gap":
        fragments = tuple(
            replace(item, continuity_index=1) if item.trace_id == boundary.target_trace_id else item
            for item in fragments
        )
        descriptors = (boundary,)
    elif case == "nonadjacent":
        descriptors = (
            replace(
                boundary,
                target_segment_index=2,
                trace_ids=(boundary.source_trace_id, "middle", boundary.target_trace_id),
            ),
        )
    elif case in {"source_branches", "target_branches"}:
        affected_trace = boundary.source_trace_id if case == "source_branches" else boundary.target_trace_id
        first = next(item for item in fragments if item.trace_id == affected_trace and item.capability_type == "skill")
        fragments = tuple(replace(item, branch_span_id="other-branch") if item is first else item for item in fragments)
        descriptors = (boundary,)
    elif case == "negative":
        descriptors = (replace(boundary, source_segment_index=-1, target_segment_index=0),)
    elif case == "bool":
        descriptors = (replace(boundary, continuity_index=False),)
    elif case == "out_of_range":
        descriptors = (replace(boundary, source_segment_index=8, target_segment_index=9),)
    elif case == "malformed":
        descriptors = ({"source_trace_id": boundary.source_trace_id},)
    elif case == "bad_trace_ids":
        descriptors = (replace(boundary, trace_ids=[boundary.source_trace_id, boundary.target_trace_id]),)
    elif case == "wrong_trace":
        descriptors = (replace(boundary, source_trace_id="wrong"),)

    candidates = build_symphony_edge_candidates(
        fragments,
        continuities,
        planned_graph=plan,
        interrupt_continuations=descriptors,
    )

    assert all(candidate.source_fragment.trace_id == candidate.target_fragment.trace_id for candidate in candidates)


@pytest.mark.parametrize("max_candidates", [1, 64, None])
@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_continuations_are_rejected_before_candidate_limit(
    max_candidates: int | None,
    reverse: bool,
) -> None:
    fragments, continuities, boundary, plan = _cross_trace_fixture()
    conflict = replace(boundary, trace_ids=boundary.trace_ids + (f"{3:032x}",))
    descriptors = (boundary, conflict) if not reverse else (conflict, boundary)

    candidates = build_symphony_edge_candidates(
        fragments,
        continuities,
        planned_graph=plan,
        interrupt_continuations=descriptors,
        max_candidates=max_candidates,
    )

    assert all(candidate.source_fragment.trace_id == candidate.target_fragment.trace_id for candidate in candidates)


def test_planned_tool_nodes_cannot_create_cross_trace_candidate() -> None:
    fragments, continuities, boundary, plan = _cross_trace_fixture()
    for node in plan["graph"]["nodes"].values():
        node["metadata"]["type"] = "tool"

    candidates = build_symphony_edge_candidates(
        fragments,
        continuities,
        planned_graph=plan,
        interrupt_continuations=(boundary,),
    )

    assert candidates == ()


def test_candidate_order_and_limit_are_stable_for_reversed_input() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "one"),
        _skill(3, "two"),
        _skill(4, "three"),
    )
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)

    unlimited = build_symphony_edge_candidates(fragments, continuity, max_candidates=None)
    limited = build_symphony_edge_candidates(tuple(reversed(fragments)), continuity, max_candidates=2)

    assert limited == unlimited[:2]


def test_non_positive_candidate_budget_returns_immediately() -> None:
    assert build_symphony_edge_candidates((), (), max_candidates=0) == ()
    assert build_symphony_edge_candidates((), (), max_candidates=-1) == ()


def test_forged_fragment_cannot_create_forged_evidence_reference() -> None:
    trajectory = _trajectory(_span("agent.main", 1), _skill(2, "real"))
    continuity = ((0, trajectory),)
    fragment = project_symphony_execution_fragments(continuity)[0]
    forged = replace(fragment, fragment_id="forged", trace_id="not-a-real-trace")

    candidates = build_symphony_edge_candidates((fragment, forged), continuity)

    assert candidates == ()
