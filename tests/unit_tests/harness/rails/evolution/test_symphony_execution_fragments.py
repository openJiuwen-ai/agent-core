from __future__ import annotations

from typing import Any

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.schema import SESSION_ID, TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.extensions.observability import semconv
from openjiuwen.harness.rails.evolution.symphony_execution_fragments import (
    project_symphony_execution_fragments,
)


def _span(
    name: str,
    span_id: int,
    *,
    trace_id: int = 1,
    parent_span_id: int | None = None,
    attributes: dict[str, Any] | None = None,
    status: str = "STATUS_CODE_OK",
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "traceId": f"{trace_id:032x}",
        "spanId": f"{span_id:016x}",
        "name": name,
        "attributes": attributes_from_map(attributes or {}),
        "status": {"code": status},
        "startTimeUnixNano": str(span_id),
        "endTimeUnixNano": str(span_id + 1),
    }
    if parent_span_id is not None:
        span["parentSpanId"] = f"{parent_span_id:016x}"
    return span


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


def _skill_read(span_id: int, skill_name: str, *, parent_span_id: int = 1, success: bool = True) -> dict[str, Any]:
    return _span(
        "tool.skill_tool",
        span_id,
        parent_span_id=parent_span_id,
        attributes={
            semconv.GEN_AI_TOOL_NAME: "skill_tool",
            semconv.GEN_AI_TOOL_INPUT: {"skill_name": skill_name, "relative_file_path": "SKILL.md"},
            semconv.GEN_AI_TOOL_OUTPUT: {"success": success},
        },
    )


def _fragments(*continuities: tuple[int, Trajectory]):
    return project_symphony_execution_fragments(continuities)


def test_skill_windows_merge_repeated_reads_and_split_on_different_skill() -> None:
    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _skill_read(2, "alpha"),
                _span("llm.call", 3, parent_span_id=1),
                _span("tool.lookup", 4, parent_span_id=1, attributes={semconv.GEN_AI_TOOL_NAME: "lookup"}),
                _skill_read(5, "alpha"),
                _span("tool.apply", 6, parent_span_id=1, attributes={semconv.GEN_AI_TOOL_NAME: "apply"}),
                _skill_read(7, "beta"),
                _span("tool.verify", 8, parent_span_id=1, attributes={semconv.GEN_AI_TOOL_NAME: "verify"}),
                _skill_read(9, "alpha"),
            ),
        )
    )

    skills = [fragment for fragment in fragments if fragment.capability_type == "skill"]
    assert [(fragment.capability_name, fragment.anchor_span_id) for fragment in skills] == [
        ("alpha", f"{2:016x}"),
        ("beta", f"{7:016x}"),
        ("alpha", f"{9:016x}"),
    ]
    assert skills[0].span_ids == tuple(f"{value:016x}" for value in (1, 2, 3, 4, 5, 6))
    assert f"{7:016x}" not in skills[0].span_ids
    assert skills[0].branch_span_id == f"{1:016x}"


def test_only_explicit_successful_skill_tool_starts_a_skill_window() -> None:
    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _skill_read(2, "failed", success=False),
                _span(
                    "tool.skill_tool",
                    3,
                    parent_span_id=1,
                    attributes={
                        semconv.GEN_AI_TOOL_NAME: "skill_tool",
                        semconv.GEN_AI_TOOL_INPUT: {"skill_name": "errored"},
                        semconv.GEN_AI_TOOL_OUTPUT: {"success": True},
                    },
                    status="STATUS_CODE_ERROR",
                ),
                _span(
                    "tool.skill_tool",
                    4,
                    parent_span_id=1,
                    attributes={
                        semconv.GEN_AI_TOOL_NAME: "skill_tool",
                        semconv.GEN_AI_TOOL_INPUT: {},
                        semconv.GEN_AI_TOOL_OUTPUT: {"success": True},
                    },
                ),
            ),
        )
    )

    assert not [fragment for fragment in fragments if fragment.capability_type == "skill"]
    assert not [fragment for fragment in fragments if fragment.capability_type == "tool"]


def test_serialized_tool_output_requires_success_without_an_error() -> None:
    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _span(
                    "tool.skill_tool",
                    2,
                    parent_span_id=1,
                    attributes={
                        semconv.GEN_AI_TOOL_NAME: "skill_tool",
                        semconv.GEN_AI_TOOL_INPUT: {"skill_name": "alpha"},
                        semconv.GEN_AI_TOOL_OUTPUT: "success=True data={} error=None",
                    },
                ),
                _span(
                    "tool.skill_tool",
                    3,
                    parent_span_id=1,
                    attributes={
                        semconv.GEN_AI_TOOL_NAME: "skill_tool",
                        semconv.GEN_AI_TOOL_INPUT: {"skill_name": "broken"},
                        semconv.GEN_AI_TOOL_OUTPUT: "success=True data=None error='failed read'",
                    },
                ),
            ),
        )
    )

    assert [(fragment.capability_type, fragment.capability_name) for fragment in fragments] == [
        ("skill", "alpha"),
    ]


def test_skill_read_accepts_observability_args_kwargs_input_envelope() -> None:
    """The observability callback serializes inputs as ``[args, kwargs]``."""

    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _span(
                    "tool.skill_tool",
                    2,
                    parent_span_id=1,
                    attributes={
                        semconv.GEN_AI_TOOL_NAME: "skill_tool",
                        semconv.GEN_AI_TOOL_INPUT: [[], {"skill_name": "alpha"}],
                        semconv.GEN_AI_TOOL_OUTPUT: "success=True data={} error=None",
                    },
                ),
            ),
        )
    )

    assert [(fragment.capability_type, fragment.capability_name) for fragment in fragments] == [
        ("skill", "alpha"),
    ]


def test_skill_read_accepts_observability_positional_input_envelope() -> None:
    """SkillTool.invoke receives its input mapping as a positional argument."""

    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _span(
                    "tool.skill_tool",
                    2,
                    parent_span_id=1,
                    attributes={
                        semconv.GEN_AI_TOOL_NAME: "skill_tool",
                        semconv.GEN_AI_TOOL_INPUT: [[{"skill_name": "alpha"}], {}],
                        semconv.GEN_AI_TOOL_OUTPUT: "success=True data={} error=None",
                    },
                ),
            ),
        )
    )

    assert [(fragment.capability_type, fragment.capability_name) for fragment in fragments] == [
        ("skill", "alpha"),
    ]


def test_skill_window_does_not_absorb_earlier_branch_activity() -> None:
    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _span("tool.before", 2, parent_span_id=1, attributes={semconv.GEN_AI_TOOL_NAME: "before"}),
                _skill_read(3, "alpha"),
                _span("tool.after", 4, parent_span_id=1, attributes={semconv.GEN_AI_TOOL_NAME: "after"}),
            ),
        )
    )

    skill = next(fragment for fragment in fragments if fragment.capability_type == "skill")
    assert skill.span_ids == tuple(f"{value:016x}" for value in (1, 3, 4))


def test_skill_windows_prefer_explicit_script_ownership_over_read_order() -> None:
    """Interleaved Skill reads must not misassign later script invocations."""

    def bash(span_id: int, command: str) -> dict[str, Any]:
        return _span(
            "tool.bash",
            span_id,
            parent_span_id=1,
            attributes={
                semconv.GEN_AI_TOOL_NAME: "bash",
                semconv.GEN_AI_TOOL_INPUT: [[{"command": command}], {}],
                semconv.GEN_AI_TOOL_OUTPUT: {"success": True},
            },
        )

    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _skill_read(2, "energy-calculator"),
                _skill_read(3, "pause-detector"),
                bash(4, "python3 skills/energy-calculator/scripts/calc_energy.py --output energies.json"),
                _skill_read(5, "silence-detector"),
                _skill_read(6, "segment-combiner"),
                bash(
                    7,
                    "python3 skills/silence-detector/scripts/detect_silence.py --energies energies.json "
                    "&& python3 skills/pause-detector/scripts/detect_pauses.py --energies energies.json",
                ),
                _skill_read(8, "video-processor"),
                bash(
                    9, "python3 skills/segment-combiner/scripts/combine_segments.py --segments silence.json pauses.json"
                ),
                bash(10, "python3 skills/video-processor/scripts/process_video.py --remove-segments all.json"),
            ),
        )
    )

    skills = {
        fragment.capability_name: fragment.span_ids for fragment in fragments if fragment.capability_type == "skill"
    }
    assert skills == {
        "energy-calculator": tuple(f"{value:016x}" for value in (1, 2, 4)),
        "pause-detector": tuple(f"{value:016x}" for value in (1, 3, 7)),
        "silence-detector": tuple(f"{value:016x}" for value in (1, 5, 7)),
        "segment-combiner": tuple(f"{value:016x}" for value in (1, 6, 9)),
        "video-processor": tuple(f"{value:016x}" for value in (1, 8, 10)),
    }


def test_tool_and_subagent_fragments_keep_native_invocation_boundaries() -> None:
    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _span("tool.lookup", 2, parent_span_id=1, attributes={semconv.GEN_AI_TOOL_NAME: "lookup"}),
                _span(
                    "tool.symphony_compose_graph",
                    3,
                    parent_span_id=1,
                    attributes={semconv.GEN_AI_TOOL_NAME: "symphony_compose_graph"},
                ),
                _span(
                    "tool.task_tool",
                    4,
                    parent_span_id=1,
                    attributes={
                        semconv.GEN_AI_TOOL_NAME: "task_tool",
                        semconv.GEN_AI_TOOL_INPUT: {"subagent_type": "research"},
                    },
                ),
                _span(
                    "agent.research",
                    5,
                    parent_span_id=4,
                    attributes={semconv.AT_AGENT_ID: "research-worker"},
                ),
                _span("llm.call", 6, parent_span_id=5),
                _span(
                    "tool.subagent_send_input",
                    7,
                    parent_span_id=1,
                    attributes={semconv.GEN_AI_TOOL_NAME: "subagent_send_input"},
                    status="STATUS_CODE_ERROR",
                ),
            ),
        )
    )

    by_anchor = {fragment.anchor_span_id: fragment for fragment in fragments}
    assert by_anchor[f"{2:016x}"].capability_type == "tool"
    assert by_anchor[f"{2:016x}"].capability_name == "lookup"
    assert f"{3:016x}" not in by_anchor
    subagent = by_anchor[f"{4:016x}"]
    assert subagent.capability_type == "subagent"
    assert subagent.capability_name == "research"
    assert subagent.branch_span_id == f"{5:016x}"
    assert subagent.span_ids == tuple(f"{value:016x}" for value in (4, 5, 6))
    assert by_anchor[f"{7:016x}"].capability_type == "tool"


def test_team_member_projection_uses_member_ids_not_nested_subagents() -> None:
    trajectory = _trajectory(
        _span(
            "agent.leader",
            1,
            attributes={semconv.AT_MEMBER_ID: "leader", semconv.AT_AGENT_ID: "leader-agent"},
        ),
        _span(
            "agent.writer",
            2,
            attributes={semconv.AT_MEMBER_ID: "writer", semconv.AT_AGENT_ID: "writer-agent"},
        ),
        _span(
            "agent.inner",
            3,
            attributes={semconv.AT_AGENT_ID: "temporary-inner-agent"},
        ),
    )

    fragments = project_symphony_execution_fragments(
        ((0, trajectory),),
        team_members_only=True,
    )

    assert [(fragment.capability_type, fragment.capability_name) for fragment in fragments] == [
        ("subagent", "leader"),
        ("subagent", "writer"),
    ]
    assert len({fragment.branch_span_id for fragment in fragments}) == 1


def test_team_branches_and_continuities_do_not_merge_skill_windows() -> None:
    first = _trajectory(
        _span("agent.member-a", 1, attributes={semconv.AT_MEMBER_ID: "member-a"}),
        _skill_read(2, "alpha", parent_span_id=1),
        _span("tool.member-a", 3, parent_span_id=1, attributes={semconv.GEN_AI_TOOL_NAME: "member-a"}),
        _span("agent.member-b", 10, attributes={semconv.AT_MEMBER_ID: "member-b"}),
        _skill_read(11, "beta", parent_span_id=10),
        _span("tool.member-b", 12, parent_span_id=10, attributes={semconv.GEN_AI_TOOL_NAME: "member-b"}),
    )
    second = _trajectory(
        _span("agent.member-a", 20, attributes={semconv.AT_MEMBER_ID: "member-a"}),
        _skill_read(21, "alpha", parent_span_id=20),
    )

    fragments = _fragments((0, first), (1, second))
    skills = [fragment for fragment in fragments if fragment.capability_type == "skill"]
    alpha = next(fragment for fragment in skills if fragment.anchor_span_id == f"{2:016x}")
    assert f"{10:016x}" not in alpha.span_ids
    assert f"{11:016x}" not in alpha.span_ids
    assert [(fragment.capability_name, fragment.continuity_index) for fragment in skills] == [
        ("alpha", 0),
        ("beta", 0),
        ("alpha", 1),
    ]


def test_subagent_dispatch_accepts_observability_args_kwargs_input_envelope() -> None:
    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _span(
                    "tool.sessions_spawn",
                    2,
                    parent_span_id=1,
                    attributes={
                        semconv.GEN_AI_TOOL_NAME: "sessions_spawn",
                        semconv.GEN_AI_TOOL_INPUT: [[], {"subagent_type": "research"}],
                    },
                ),
                _span(
                    "agent.worker",
                    3,
                    parent_span_id=2,
                    attributes={semconv.AT_AGENT_ID: "generated-worker-id"},
                ),
            ),
        )
    )

    dispatch = next(fragment for fragment in fragments if fragment.anchor_span_id == f"{2:016x}")
    assert dispatch.capability_type == "subagent"
    assert dispatch.capability_name == "research"


def test_skill_fallback_window_excludes_dispatched_child_branch() -> None:
    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _skill_read(2, "alpha"),
                _span(
                    "tool.sessions_spawn",
                    3,
                    parent_span_id=1,
                    attributes={
                        semconv.GEN_AI_TOOL_NAME: "sessions_spawn",
                        semconv.GEN_AI_TOOL_INPUT: {"subagent_type": "research"},
                    },
                ),
                _span("agent.worker", 4, parent_span_id=3),
                _span(
                    "tool.child_lookup",
                    5,
                    parent_span_id=4,
                    attributes={semconv.GEN_AI_TOOL_NAME: "child_lookup"},
                ),
            ),
        )
    )

    skill = next(fragment for fragment in fragments if fragment.capability_type == "skill")
    assert skill.span_ids == tuple(f"{value:016x}" for value in (1, 2, 3))


def test_explicit_skill_window_excludes_dispatched_child_branch() -> None:
    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _skill_read(2, "alpha"),
                _span(
                    "tool.sessions_spawn",
                    3,
                    parent_span_id=1,
                    attributes={
                        semconv.GEN_AI_TOOL_NAME: "sessions_spawn",
                        semconv.GEN_AI_TOOL_INPUT: {
                            "subagent_type": "research",
                            "task_description": "Run skills/alpha/scripts/run.py",
                        },
                    },
                ),
                _span("agent.worker", 4, parent_span_id=3),
                _span(
                    "tool.child_lookup",
                    5,
                    parent_span_id=4,
                    attributes={semconv.GEN_AI_TOOL_NAME: "child_lookup"},
                ),
            ),
        )
    )

    skill = next(fragment for fragment in fragments if fragment.capability_type == "skill")
    assert skill.span_ids == tuple(f"{value:016x}" for value in (1, 2, 3))


def test_subagent_dispatch_prefers_direct_child_over_nested_agent() -> None:
    fragments = _fragments(
        (
            0,
            _trajectory(
                _span("agent.main", 1),
                _span(
                    "tool.task_tool",
                    2,
                    parent_span_id=1,
                    attributes={semconv.GEN_AI_TOOL_NAME: "task_tool"},
                ),
                _span(
                    "agent.direct",
                    3,
                    parent_span_id=2,
                    attributes={semconv.AT_AGENT_ID: "direct-worker"},
                ),
                _span(
                    "agent.nested",
                    4,
                    parent_span_id=3,
                    attributes={semconv.AT_AGENT_ID: "nested-worker"},
                ),
            ),
        )
    )

    dispatch = next(fragment for fragment in fragments if fragment.anchor_span_id == f"{2:016x}")
    assert dispatch.capability_name == "direct-worker"
    assert dispatch.branch_span_id == f"{3:016x}"


def test_team_member_projection_selects_a_root_within_each_trace() -> None:
    trajectory = _trajectory(
        _span(
            "agent.trace-a-member",
            10,
            trace_id=10,
            attributes={semconv.AT_MEMBER_ID: "trace-a-member"},
        ),
        _span(
            "agent.trace-b-member",
            20,
            trace_id=20,
            attributes={semconv.AT_MEMBER_ID: "trace-b-member"},
        ),
    )

    fragments = project_symphony_execution_fragments(
        ((0, trajectory),),
        team_members_only=True,
    )

    assert {(fragment.trace_id, fragment.branch_span_id) for fragment in fragments} == {
        (f"{10:032x}", f"{10:016x}"),
        (f"{20:032x}", f"{20:016x}"),
    }


def test_parent_cycle_fails_closed_without_blocking_other_continuities() -> None:
    cyclic = _trajectory(
        _span("agent.a", 1, parent_span_id=2),
        _span(
            "tool.b",
            2,
            parent_span_id=1,
            attributes={semconv.GEN_AI_TOOL_NAME: "b"},
        ),
    )
    valid = _trajectory(
        _span("agent.main", 10),
        _span(
            "tool.lookup",
            11,
            parent_span_id=10,
            attributes={semconv.GEN_AI_TOOL_NAME: "lookup"},
        ),
    )

    fragments = project_symphony_execution_fragments(((0, cyclic), (1, valid)))

    assert [(fragment.continuity_index, fragment.capability_name) for fragment in fragments] == [(1, "lookup")]
