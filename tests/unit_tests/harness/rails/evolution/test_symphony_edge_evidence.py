from __future__ import annotations

from dataclasses import replace
from typing import Any

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.extensions.observability import semconv
from openjiuwen.harness.rails.evolution import symphony_edge_evidence as evidence_module
from openjiuwen.harness.rails.evolution.symphony_edge_evidence import (
    SymphonyEdgeCandidate,
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
    start_time: Any = None,
    end_time: Any = None,
    with_times: bool = True,
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "traceId": f"{trace_number:032x}",
        "spanId": f"{span_id:016x}",
        "name": name,
        "attributes": attributes_from_map(attributes or {}),
        "status": {"code": "STATUS_CODE_OK"},
    }
    if with_times:
        span["startTimeUnixNano"] = str(span_id) if start_time is None else start_time
        span["endTimeUnixNano"] = str(span_id + 1) if end_time is None else end_time
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
    start_time: Any = None,
    end_time: Any = None,
    with_times: bool = True,
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
        start_time=start_time,
        end_time=end_time,
        with_times=with_times,
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
            "nodes": {name: {"label": name, "metadata": {"type": "skill"}} for name in names},
            "edges": [{"source": source, "target": target} for source, target in edges],
        }
    }


def test_missing_plan_uses_all_forward_skill_occurrence_pairs() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "one"),
        _skill(3, "two"),
        _skill(4, "three"),
    )
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)

    candidates = build_symphony_edge_candidates(fragments, continuity, planned_graph=None)

    assert [_names(candidate) for candidate in candidates] == [
        ("one", "two"),
        ("one", "three"),
        ("two", "three"),
    ]
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
    fragments = project_symphony_execution_fragments(continuity)

    candidates = build_symphony_edge_candidates(
        fragments,
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

    assert candidates[0].candidate_reasons == ("planned",)
    assert decisions[0].status == "insufficient_evidence"
    assert decisions[0].reason == "awaiting_model_evidence"
    assert decisions[0].evidence_refs == ()


def test_unplanned_skill_gets_bounded_proximity_candidates() -> None:
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
        edge_search_max_depth=1,
    )

    reasons = {_names(candidate): candidate.candidate_reasons for candidate in candidates}
    assert reasons[("planned-a", "planned-b")] == ("planned",)
    assert reasons[("planned-a", "deviation")][0].startswith("proximity:")
    assert reasons[("deviation", "planned-b")][0].startswith("proximity:")


def test_exact_structured_reference_adds_real_span_evidence() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _tool(2, "producer", tool_output={"artifact_id": "artifact-17"}),
        _tool(3, "consumer", tool_input=[[], {"artifact_id": "artifact-17"}]),
    )
    continuity = ((0, trajectory),)

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
        planned_graph={},
    )

    assert [_names(candidate) for candidate in candidates] == [("producer", "consumer")]
    assert candidates[0].candidate_reasons == ("structured_reference",)
    assert candidates[0].evidence_refs == (
        f"{1:032x}#span={2:016x}",
        f"{1:032x}#span={3:016x}",
    )


def test_reverse_or_overlapping_reference_spans_fail_closed() -> None:
    reversed_trajectory = _trajectory(
        _span("agent.main", 1),
        _tool(
            2,
            "consumer",
            tool_input={"artifact_id": "shared"},
            start_time="2",
            end_time="3",
        ),
        _tool(
            4,
            "producer",
            tool_output={"artifact_id": "shared"},
            start_time="4",
            end_time="5",
        ),
    )
    overlapping_trajectory = _trajectory(
        _span("agent.main", 10),
        _tool(
            11,
            "producer",
            parent_span_id=10,
            tool_output={"artifact_id": "shared"},
            start_time="11",
            end_time="20",
        ),
        _tool(
            12,
            "consumer",
            parent_span_id=10,
            tool_input={"artifact_id": "shared"},
            start_time="12",
            end_time="13",
        ),
    )

    for trajectory in (reversed_trajectory, overlapping_trajectory):
        continuity = ((0, trajectory),)
        candidates = build_symphony_edge_candidates(
            project_symphony_execution_fragments(continuity),
            continuity,
            planned_graph={},
        )
        assert candidates == ()


def test_missing_or_invalid_reference_span_intervals_fail_closed() -> None:
    invalid_intervals: tuple[tuple[dict[str, Any], dict[str, Any]], ...] = (
        ({"with_times": False}, {}),
        ({"start_time": True, "end_time": "3"}, {}),
        ({"start_time": "-1", "end_time": "3"}, {}),
        ({"start_time": "4", "end_time": "3"}, {}),
        ({}, {"start_time": "not-an-int", "end_time": "5"}),
    )
    for producer_times, consumer_times in invalid_intervals:
        trajectory = _trajectory(
            _span("agent.main", 1),
            _tool(2, "producer", tool_output={"artifact_id": "shared"}, **producer_times),
            _tool(4, "consumer", tool_input={"artifact_id": "shared"}, **consumer_times),
        )
        continuity = ((0, trajectory),)
        candidates = build_symphony_edge_candidates(
            project_symphony_execution_fragments(continuity),
            continuity,
            planned_graph={},
        )
        assert candidates == ()


def test_subagent_result_requires_real_forward_span_order() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _tool(
            4,
            "task_tool",
            tool_input={"subagent_type": "research"},
            tool_output={"artifact_id": "result"},
            start_time="4",
            end_time="5",
        ),
        _tool(
            2,
            "consumer",
            tool_input={"artifact_id": "result"},
            start_time="2",
            end_time="3",
        ),
    )
    continuity = ((0, trajectory),)

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
        planned_graph={},
    )

    assert candidates == ()


def test_plain_text_overlap_is_not_exact_evidence() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _tool(2, "producer", tool_output={"summary": "same text"}),
        _tool(3, "consumer", tool_input={"prompt": "same text"}),
    )
    continuity = ((0, trajectory),)

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
        planned_graph={},
    )

    assert candidates == ()


def test_candidates_never_cross_continuities() -> None:
    first = _trajectory(
        _span("agent.main", 1),
        _tool(2, "producer", tool_output={"artifact_id": "shared"}),
    )
    second = _trajectory(
        _span("agent.main", 10),
        _tool(11, "consumer", tool_input={"artifact_id": "shared"}),
    )
    continuities = ((0, first), (1, second))

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuities),
        continuities,
        planned_graph={},
    )

    assert candidates == ()


def test_candidates_never_cross_traces() -> None:
    trajectory = _trajectory(
        _span("agent.first", 1, trace_number=1),
        _tool(2, "producer", trace_number=1, tool_output={"artifact_id": "shared"}),
        _span("agent.second", 10, trace_number=2),
        _tool(11, "consumer", trace_number=2, parent_span_id=10, tool_input={"artifact_id": "shared"}),
    )
    continuity = ((0, trajectory),)

    candidates = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
        planned_graph={},
    )

    assert candidates == ()


def test_repeated_name_does_not_create_unsubstantiated_self_candidate() -> None:
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
        planned_graph=None,
    )

    assert ("repeat", "repeat") not in {_names(candidate) for candidate in candidates}


def test_forged_fragment_cannot_create_forged_evidence_reference() -> None:
    trajectory = _trajectory(_span("agent.main", 1), _tool(2, "real"))
    continuity = ((0, trajectory),)
    fragment = project_symphony_execution_fragments(continuity)[0]
    forged = replace(fragment, fragment_id="forged", trace_id="not-a-real-trace")

    candidates = build_symphony_edge_candidates(
        (fragment, forged),
        continuity,
        planned_graph=None,
    )

    assert candidates == ()


def test_team_member_candidates_use_only_same_trace_observed_order() -> None:
    trajectory = _trajectory(
        _span("agent.leader", 1, attributes={semconv.AT_MEMBER_ID: "leader"}),
        _span("agent.writer", 2, attributes={semconv.AT_MEMBER_ID: "writer"}),
        _span("agent.other", 3, trace_number=2, attributes={semconv.AT_MEMBER_ID: "other"}),
    )
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity, team_members_only=True)

    candidates = build_symphony_edge_candidates(
        fragments,
        continuity,
        planned_graph=None,
        include_team_member_pairs=True,
    )

    assert [_names(candidate) for candidate in candidates] == [("leader", "writer")]


def test_candidate_order_and_limit_are_stable_for_reversed_input() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "one"),
        _skill(3, "two"),
        _skill(4, "three"),
    )
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)

    forward = build_symphony_edge_candidates(fragments, continuity, max_candidates=2)
    reversed_input = build_symphony_edge_candidates(tuple(reversed(fragments)), continuity, max_candidates=2)

    assert [candidate.candidate_id for candidate in forward] == [candidate.candidate_id for candidate in reversed_input]


def test_candidate_budget_bounds_exact_internal_construction(monkeypatch: Any) -> None:
    spans = [_span("agent.main", 1)]
    spans.extend(_tool(index, f"producer-{index}", tool_output={"artifact_id": "shared"}) for index in range(2, 82))
    spans.extend(_tool(index, f"consumer-{index}", tool_input={"artifact_id": "shared"}) for index in range(82, 162))
    trajectory = _trajectory(*spans)
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)
    calls = 0
    original = evidence_module._candidate_parts

    def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(evidence_module, "_candidate_parts", counted)
    candidates = build_symphony_edge_candidates(
        fragments,
        continuity,
        planned_graph={},
        max_candidates=3,
    )

    assert len(candidates) == 3
    assert calls == 3
    assert all(candidate.candidate_reasons == ("structured_reference",) for candidate in candidates)


def test_candidate_budget_preserves_exact_then_planned_priority() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "planned-a"),
        _skill(3, "planned-b"),
        _tool(4, "producer", tool_output={"artifact_id": "shared"}),
        _tool(5, "consumer", tool_input={"artifact_id": "shared"}),
    )
    continuity = ((0, trajectory),)

    exact_only = build_symphony_edge_candidates(
        project_symphony_execution_fragments(continuity),
        continuity,
        planned_graph=_planned_graph(("planned-a", "planned-b"), names=("planned-a", "planned-b")),
        max_candidates=1,
    )

    assert _names(exact_only[0]) != ("planned-a", "planned-b")
    assert exact_only[0].candidate_reasons == ("structured_reference",)


def test_non_positive_candidate_budget_returns_immediately() -> None:
    assert build_symphony_edge_candidates((), (), max_candidates=0) == ()
    assert build_symphony_edge_candidates((), (), max_candidates=-1) == ()


def test_finite_exact_candidates_equal_canonical_unlimited_prefix() -> None:
    spans = [_span("agent.main", 1)]
    spans.extend(_tool(index, f"producer-{index}", tool_output={"artifact_id": "shared"}) for index in range(2, 8))
    spans.extend(_tool(index, f"consumer-{index}", tool_input={"artifact_id": "shared"}) for index in range(8, 14))
    trajectory = _trajectory(*spans)
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)

    unlimited = build_symphony_edge_candidates(fragments, continuity, planned_graph={}, max_candidates=None)
    limited = build_symphony_edge_candidates(fragments, continuity, planned_graph={}, max_candidates=5)

    assert limited == unlimited[:5]


def test_finite_exact_prefix_does_not_depend_on_reference_sort_order() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _tool(
            2,
            "producer",
            tool_output={"artifact_id": "artifact-later", "path": "/workspace/earlier"},
        ),
        _tool(3, "earlier", tool_input={"path": "/workspace/earlier"}),
        _tool(4, "later", tool_input={"artifact_id": "artifact-later"}),
    )
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)

    unlimited = build_symphony_edge_candidates(fragments, continuity, planned_graph={}, max_candidates=None)
    limited = build_symphony_edge_candidates(fragments, continuity, planned_graph={}, max_candidates=1)

    assert limited == unlimited[:1]
    assert _names(limited[0]) == ("producer", "earlier")


def test_finite_exact_candidate_keeps_all_lower_priority_reasons() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "source"),
        _tool(3, "producer", tool_output={"artifact_id": "shared"}),
        _skill(4, "target"),
        _tool(5, "consumer", tool_input={"artifact_id": "shared"}),
    )
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)
    plan = _planned_graph(("source", "target"), names=("source", "target"))

    unlimited = build_symphony_edge_candidates(fragments, continuity, planned_graph=plan, max_candidates=None)
    limited = build_symphony_edge_candidates(fragments, continuity, planned_graph=plan, max_candidates=1)

    assert limited == unlimited[:1]
    assert limited[0].candidate_reasons == ("structured_reference", "planned")


def test_finite_exact_candidate_ignores_enriched_proximity_depth_for_ordering() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "first"),
        _tool(3, "first-output", parent_span_id=2, tool_output={"artifact_id": "first-result"}),
        _skill(4, "second"),
        _tool(5, "second-output", parent_span_id=4, tool_output={"artifact_id": "second-result"}),
        _skill(6, "consumer"),
        _tool(
            7,
            "consumer-input",
            parent_span_id=6,
            tool_input={"first": {"artifact_id": "first-result"}, "second": {"artifact_id": "second-result"}},
        ),
    )
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)
    empty_plan: dict[str, Any] = {"graph": {"nodes": {}, "edges": []}}

    unlimited = build_symphony_edge_candidates(
        fragments,
        continuity,
        planned_graph=empty_plan,
        edge_search_max_depth=3,
        max_candidates=None,
    )
    limited = build_symphony_edge_candidates(
        fragments,
        continuity,
        planned_graph=empty_plan,
        edge_search_max_depth=3,
        max_candidates=1,
    )

    assert limited == unlimited[:1]
    assert _names(limited[0]) == ("first", "consumer")
    assert limited[0].candidate_reasons[0] == "structured_reference"
    assert any(reason.startswith("proximity:") for reason in limited[0].candidate_reasons)


def test_finite_planned_fill_enriches_selected_exact_candidate() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "deviation"),
        _tool(3, "deviation-output", parent_span_id=2, tool_output={"artifact_id": "shared"}),
        _skill(4, "a"),
        _tool(5, "a-input", parent_span_id=4, tool_input={"artifact_id": "shared"}),
        _skill(6, "b"),
        _skill(7, "c"),
        _skill(8, "d"),
        _skill(9, "e"),
    )
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)
    plan = _planned_graph(
        ("a", "b"),
        ("b", "c"),
        ("c", "d"),
        ("d", "e"),
        names=("a", "b", "c", "d", "e"),
    )

    unlimited = build_symphony_edge_candidates(
        fragments,
        continuity,
        planned_graph=plan,
        edge_search_max_depth=3,
        max_candidates=None,
    )
    limited = build_symphony_edge_candidates(
        fragments,
        continuity,
        planned_graph=plan,
        edge_search_max_depth=3,
        max_candidates=5,
    )

    assert limited == unlimited[:5]
    exact = next(candidate for candidate in limited if _names(candidate) == ("deviation", "a"))
    assert exact.candidate_reasons[0] == "structured_reference"
    assert any(reason.startswith("proximity:") for reason in exact.candidate_reasons)


def test_finite_planned_candidates_ignore_edge_array_order() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        _skill(2, "a"),
        _skill(3, "b"),
        _skill(4, "c"),
        _skill(5, "d"),
    )
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)
    edges = (("c", "d"), ("a", "d"), ("a", "b"))
    graph = _planned_graph(*edges, names=("a", "b", "c", "d"))
    reversed_graph = _planned_graph(*reversed(edges), names=("a", "b", "c", "d"))

    unlimited = build_symphony_edge_candidates(fragments, continuity, planned_graph=graph, max_candidates=None)
    limited = build_symphony_edge_candidates(fragments, continuity, planned_graph=graph, max_candidates=2)
    reordered = build_symphony_edge_candidates(
        fragments,
        continuity,
        planned_graph=reversed_graph,
        max_candidates=2,
    )

    assert limited == unlimited[:2]
    assert reordered == unlimited[:2]


def test_finite_proximity_candidates_equal_canonical_unlimited_prefix() -> None:
    trajectory = _trajectory(
        _span("agent.main", 1),
        *(_skill(index, f"skill-{index}") for index in range(2, 9)),
    )
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)
    empty_plan: dict[str, Any] = {"graph": {"nodes": {}, "edges": []}}

    unlimited = build_symphony_edge_candidates(
        fragments,
        continuity,
        planned_graph=empty_plan,
        edge_search_max_depth=3,
        max_candidates=None,
    )
    limited = build_symphony_edge_candidates(
        tuple(reversed(fragments)),
        continuity,
        planned_graph=empty_plan,
        edge_search_max_depth=3,
        max_candidates=4,
    )

    assert limited == unlimited[:4]


def test_overlapping_shared_reference_bucket_has_bounded_pair_probes(monkeypatch: Any) -> None:
    spans = [_span("agent.main", 1)]
    spans.extend(
        _tool(
            index,
            f"producer-{index}",
            tool_output={"artifact_id": "shared"},
            start_time="1",
            end_time="10000",
        )
        for index in range(2, 82)
    )
    spans.extend(
        _tool(
            index,
            f"consumer-{index}",
            tool_input={"artifact_id": "shared"},
            start_time="2",
            end_time="3",
        )
        for index in range(82, 162)
    )
    trajectory = _trajectory(*spans)
    continuity = ((0, trajectory),)
    fragments = project_symphony_execution_fragments(continuity)
    probes = 0
    original = evidence_module._producer_precedes_consumer

    def counted(*args: Any, **kwargs: Any) -> bool:
        nonlocal probes
        probes += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(evidence_module, "_producer_precedes_consumer", counted)
    candidates = build_symphony_edge_candidates(
        fragments,
        continuity,
        planned_graph={},
        max_candidates=3,
    )

    assert candidates == ()
    assert probes == 0
