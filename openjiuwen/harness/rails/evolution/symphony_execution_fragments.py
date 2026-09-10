# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure SDD-0006 execution-fragment projection for canonical trajectories."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    iter_spans,
    read_span_error,
    read_tool_call,
    span_attributes,
    span_identity,
    span_sort_key,
    span_status,
)
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.extensions.observability import semconv

_COMPOSE_TOOL_NAME = "symphony_compose_graph"
_SKILL_TOOL_NAME = "skill_tool"
_SUBAGENT_DISPATCH_TOOLS = frozenset({"task_tool", "subagent_spawn", "sessions_spawn"})
_OBSERVABILITY_TRUNCATED_SUFFIX = re.compile(r"\.\.\.<truncated [1-9]\d* chars>$")
_TRUNCATED_JSON_SUCCESS_PREFIX = re.compile(
    r'^\s*\{\s*"success"\s*:\s*true(?:\s*,|\s*\})',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SymphonyExecutionFragment:
    """One invoke-local, trace-addressable candidate capability window.

    ``span_ids`` only reference the accompanying canonical trajectory.  A
    fragment is not a graph edge, a capability-adoption decision, or a copy of
    tool arguments/results.
    """

    fragment_id: str
    capability_type: Literal["skill", "tool", "subagent"]
    capability_name: str | None
    trace_id: str
    anchor_span_id: str
    branch_span_id: str
    span_ids: tuple[str, ...]
    continuity_index: int


@dataclass(frozen=True)
class _SpanTopology:
    """Branch and descendant indexes shared by Skill window projection."""

    children: Mapping[tuple[str, str], Sequence[tuple[str, str]]]
    branches: Mapping[tuple[str, str], tuple[str, str]]


def project_symphony_execution_fragments(
    continuities: Sequence[tuple[int, Trajectory]],
    *,
    team_members_only: bool = False,
) -> tuple[SymphonyExecutionFragment, ...]:
    """Project immutable execution-fragment references from clean snapshots.

    Every continuity is processed independently so a rejected capture increment
    cannot keep a Skill window alive across an unknown part of the execution.
    """

    fragments: list[SymphonyExecutionFragment] = []
    for continuity_index, trajectory in sorted(continuities, key=lambda item: item[0]):
        fragments.extend(
            _project_continuity(
                int(continuity_index),
                trajectory,
                team_members_only=team_members_only,
            )
        )
    return tuple(fragments)


def _project_continuity(
    continuity_index: int,
    trajectory: Trajectory,
    *,
    team_members_only: bool,
) -> list[SymphonyExecutionFragment]:
    spans = [span for span in iter_spans(trajectory) if span_identity(span) is not None]
    if not spans:
        return []
    spans.sort(key=span_sort_key)
    by_identity = {span_identity(span): span for span in spans}
    parents = {identity: _parent_identity(span, by_identity) for identity, span in by_identity.items()}
    if _has_parent_cycle(parents):
        return []
    children: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for identity, parent in parents.items():
        if parent is not None:
            children[parent].append(identity)
    for descendants in children.values():
        descendants.sort(key=lambda identity: span_sort_key(by_identity[identity]))

    if team_members_only:
        return _team_member_fragments(
            continuity_index,
            by_identity,
            children,
            parents,
        )

    branches = _branch_identities(by_identity, parents)
    fragments = _skill_fragments(
        continuity_index,
        by_identity,
        children,
        branches,
    )
    fragments.extend(
        _tool_and_subagent_fragments(
            continuity_index,
            by_identity,
            children,
            branches,
        )
    )
    fragments.sort(
        key=lambda fragment: (
            span_sort_key(by_identity[(fragment.trace_id, fragment.anchor_span_id)]),
            fragment.capability_type,
        )
    )
    return fragments


def _team_member_fragments(
    continuity_index: int,
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    children: Mapping[tuple[str, str], Sequence[tuple[str, str]]],
    parents: Mapping[tuple[str, str], tuple[str, str] | None],
) -> list[SymphonyExecutionFragment]:
    """Project only Team member agent spans, excluding nested local subagents."""

    roots_by_trace: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for identity in by_identity:
        if parents.get(identity) is None:
            roots_by_trace[identity[0]].append(identity)
    if not roots_by_trace:
        return []
    fragments: list[SymphonyExecutionFragment] = []
    for identity, span in by_identity.items():
        if span_category(span) != "agent":
            continue
        member_id = str(span_attributes(span).get(semconv.AT_MEMBER_ID) or "").strip()
        if not member_id:
            continue
        trace_roots = roots_by_trace.get(identity[0])
        if not trace_roots:
            continue
        trace_branch = min(trace_roots, key=lambda item: span_sort_key(by_identity[item]))
        descendants = _descendants_before_boundary({identity}, None, by_identity, children)
        fragments.append(
            _fragment(
                continuity_index=continuity_index,
                capability_type="subagent",
                capability_name=member_id,
                anchor=identity,
                branch=trace_branch,
                span_ids=_ordered_span_ids(descendants, by_identity),
            )
        )
    fragments.sort(key=lambda fragment: span_sort_key(by_identity[(fragment.trace_id, fragment.anchor_span_id)]))
    return fragments


def _parent_identity(
    span: Mapping[str, Any],
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[str, str] | None:
    identity = span_identity(span)
    parent_span_id = str(span.get("parentSpanId") or "").strip()
    if identity is None or not parent_span_id:
        return None
    parent = (identity[0], parent_span_id)
    return parent if parent in by_identity else None


def _has_parent_cycle(
    parents: Mapping[tuple[str, str], tuple[str, str] | None],
) -> bool:
    """Detect malformed parent loops in linear time before any ancestry walk."""

    finished: set[tuple[str, str]] = set()
    for identity in parents:
        path: set[tuple[str, str]] = set()
        current: tuple[str, str] | None = identity
        while current is not None and current not in finished:
            if current in path:
                return True
            path.add(current)
            current = parents.get(current)
        finished.update(path)
    return False


def _branch_identities(
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    parents: Mapping[tuple[str, str], tuple[str, str] | None],
) -> dict[tuple[str, str], tuple[str, str]]:
    """Return the native agent/task/root branch for each span."""

    orphan_step_branches = _orphan_step_branches(by_identity)
    result: dict[tuple[str, str], tuple[str, str]] = {}
    for identity in by_identity:
        current = identity
        task_candidate: tuple[str, str] | None = None
        root = identity
        while True:
            current_span = by_identity[current]
            category = span_category(current_span)
            if category == "agent" and not _is_step_wrapper(current_span):
                result[identity] = current
                break
            if category == "task" and task_candidate is None:
                task_candidate = current
            parent = parents.get(current)
            if parent is None:
                root = current
                result[identity] = task_candidate or orphan_step_branches.get(root, root)
                break
            current = parent
    return result


def _orphan_step_branches(
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[tuple[str, str], tuple[str, str]]:
    """Group step wrappers while their shared invoke parent is not captured."""

    grouped: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for identity, span in by_identity.items():
        parent_span_id = str(span.get("parentSpanId") or "").strip()
        parent = (identity[0], parent_span_id)
        if _is_step_wrapper(span) and parent_span_id and parent not in by_identity:
            grouped[parent].append(identity)
    result: dict[tuple[str, str], tuple[str, str]] = {}
    for identities in grouped.values():
        branch = min(identities, key=lambda identity: span_sort_key(by_identity[identity]))
        result.update((identity, branch) for identity in identities)
    return result


def _is_step_wrapper(span: Mapping[str, Any]) -> bool:
    """Return whether an agent-named span is an iteration, not a branch."""

    return str(span_attributes(span).get(semconv.OJ_TRAJECTORY_RECORD_KIND) or "").strip() == "step"


def _skill_fragments(
    continuity_index: int,
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    children: Mapping[tuple[str, str], Sequence[tuple[str, str]]],
    branches: Mapping[tuple[str, str], tuple[str, str]],
) -> list[SymphonyExecutionFragment]:
    topology = _SpanTopology(children=children, branches=branches)
    anchors_by_branch: dict[tuple[str, str], list[tuple[tuple[str, str], str]]] = defaultdict(list)
    for identity, span in by_identity.items():
        skill_name = _effective_skill_name(span)
        if skill_name is not None:
            anchors_by_branch[branches[identity]].append((identity, skill_name))

    fragments: list[SymphonyExecutionFragment] = []
    for branch, anchors in anchors_by_branch.items():
        anchors.sort(key=lambda item: span_sort_key(by_identity[item[0]]))
        effective_anchors = [
            (anchor, skill_name)
            for position, (anchor, skill_name) in enumerate(anchors)
            if not position or anchors[position - 1][1] != skill_name
        ]
        owned_span_ids, script_span_owners = _explicit_skill_execution_span_ids(
            effective_anchors,
            branch,
            by_identity,
            branches,
        )
        for position, (anchor, skill_name) in enumerate(effective_anchors):
            explicit_span_ids = owned_span_ids.get(anchor, set())
            if explicit_span_ids:
                span_ids = _skill_execution_span_window(
                    anchor,
                    branch,
                    explicit_span_ids,
                    script_span_owners,
                    effective_anchors,
                    by_identity,
                    topology,
                )
            else:
                next_position = position + 1
                next_boundary = next(
                    (
                        later_anchor
                        for later_anchor, later_skill in effective_anchors[next_position:]
                        if later_skill != skill_name
                    ),
                    None,
                )
                span_ids = _skill_window_span_ids(
                    anchor,
                    branch,
                    next_boundary,
                    by_identity,
                    topology,
                )
                span_ids = _exclude_foreign_script_spans(
                    span_ids,
                    anchor,
                    script_span_owners,
                    by_identity,
                    topology,
                )
            fragments.append(
                _fragment(
                    continuity_index=continuity_index,
                    capability_type="skill",
                    capability_name=skill_name,
                    anchor=anchor,
                    branch=branch,
                    span_ids=span_ids,
                )
            )
    return fragments


def _explicit_skill_execution_span_ids(
    anchors: Sequence[tuple[tuple[str, str], str]],
    branch: tuple[str, str],
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    branches: Mapping[tuple[str, str], tuple[str, str]],
) -> tuple[
    dict[tuple[str, str], set[tuple[str, str]]],
    dict[tuple[str, str], tuple[str, str] | None],
]:
    """Assign explicit ``<skill>/scripts/`` references to their latest read.

    Skill reads are often batched before any command runs.  The historical
    read-to-next-read window then assigns an execution command to whichever
    unrelated Skill happened to be read most recently.  A command that names
    a Skill's script is stronger local evidence, so attach it to the latest
    effective read of that same Skill instead.  This remains trace-local and
    does not treat a Skill read as proof of execution.
    """

    result: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    owners: dict[tuple[str, str], tuple[str, str] | None] = {}
    skill_names = tuple(dict.fromkeys(skill_name for _, skill_name in anchors))
    for identity, span in by_identity.items():
        if branches[identity] != branch or span_category(span) != "tool":
            continue
        referenced = [name for name in skill_names if _tool_references_skill_script(span, name)]
        if not referenced:
            continue
        if len(referenced) != 1:
            owners[identity] = None
            continue
        skill_name = referenced[0]
        eligible_anchors = [
            candidate_anchor
            for candidate_anchor, candidate_name in anchors
            if candidate_name == skill_name and span_sort_key(by_identity[candidate_anchor]) <= span_sort_key(span)
        ]
        if not eligible_anchors:
            owners[identity] = None
            continue
        latest_anchor = max(
            eligible_anchors,
            key=lambda candidate_anchor: span_sort_key(by_identity[candidate_anchor]),
        )
        owners[identity] = latest_anchor
        result[latest_anchor].add(identity)
    return result, owners


def _skill_execution_span_window(
    anchor: tuple[str, str],
    branch: tuple[str, str],
    execution_spans: set[tuple[str, str]],
    script_span_owners: Mapping[tuple[str, str], tuple[str, str] | None],
    anchors: Sequence[tuple[tuple[str, str], str]],
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    topology: _SpanTopology,
) -> tuple[str, ...]:
    """Keep the Skill's local interval while excluding other owned work."""

    first_order = span_sort_key(by_identity[anchor])
    last_order = max(span_sort_key(by_identity[identity]) for identity in execution_spans)
    selected = {
        identity
        for identity, span in by_identity.items()
        if topology.branches.get(identity) == branch and first_order <= span_sort_key(span) <= last_order
    }
    excluded_roots = {
        candidate_anchor
        for candidate_anchor, _ in anchors
        if candidate_anchor != anchor and candidate_anchor in selected
    }
    excluded_roots.update(
        identity for identity, owner in script_span_owners.items() if identity in selected and owner != anchor
    )
    excluded = _descendants_before_boundary(excluded_roots, None, by_identity, topology.children)
    selected.difference_update(excluded)
    selected.add(anchor)
    selected.add(branch)
    selected = {identity for identity in selected if topology.branches.get(identity) == branch}
    return _ordered_span_ids(selected, by_identity)


def _exclude_foreign_script_spans(
    span_ids: Sequence[str],
    anchor: tuple[str, str],
    owners: Mapping[tuple[str, str], tuple[str, str] | None],
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    topology: _SpanTopology,
) -> tuple[str, ...]:
    """Remove explicitly foreign or ambiguous script work from a fallback."""

    trace_id = anchor[0]
    selected = {(trace_id, span_id) for span_id in span_ids if (trace_id, span_id) in by_identity}
    excluded_roots = {identity for identity, owner in owners.items() if identity in selected and owner != anchor}
    excluded = _descendants_before_boundary(excluded_roots, None, by_identity, topology.children)
    selected.difference_update(excluded)
    return _ordered_span_ids(selected, by_identity)


def _tool_references_skill_script(span: Mapping[str, Any], skill_name: str) -> bool:
    tool_call = read_tool_call(span)
    if _canonical_tool_name(tool_call.get("name")) == _SKILL_TOOL_NAME:
        return False
    absolute = re.compile(rf"(?<![A-Za-z0-9_.-]){re.escape(skill_name)}/scripts(?:/|\\b)")
    return any(
        absolute.search(value) is not None or _references_relative_skill_script(value, skill_name)
        for value in _trace_text_values(tool_call.get("input"))
    )


def _references_relative_skill_script(value: str, skill_name: str) -> bool:
    """Recognize relative scripts under the active shell ``cd`` scope."""

    active_directories: set[str] = set()
    relative_script = re.compile(r"(?<![A-Za-z0-9_./-])scripts(?:/|\b)")
    for operator, command in _shell_command_segments(value):
        directory = _shell_cd_directory(command)
        if directory is not None:
            if operator == "||":
                active_directories.add(directory)
            else:
                active_directories = {directory}
            continue
        if relative_script.search(command) is None:
            continue
        if any(path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] == skill_name for path in active_directories):
            return True
    return False


def _shell_command_segments(value: str) -> tuple[tuple[str | None, str], ...]:
    """Split only unquoted ``&&``, ``||``, and ``;`` command boundaries."""

    result: list[tuple[str | None, str]] = []
    start = 0
    operator: str | None = None
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(value):
        char = value[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        boundary = next((item for item in ("&&", "||", ";") if value.startswith(item, index)), None)
        if boundary is None:
            index += 1
            continue
        command = value[start:index].strip()
        if command:
            result.append((operator, command))
        operator = boundary
        index += len(boundary)
        start = index
    command = value[start:].strip()
    if command:
        result.append((operator, command))
    return tuple(result)


def _shell_cd_directory(command: str) -> str | None:
    match = re.fullmatch(r"cd(?:\s+--)?\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))", command.strip())
    if match is None:
        return None
    return next((group for group in match.groups() if group is not None), None)


def _trace_text_values(value: Any) -> Sequence[str]:
    """Return text leaves from a decoded or serialized trace payload."""

    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return (value,)
        return _trace_text_values(decoded)
    if isinstance(value, Mapping):
        return tuple(text for item in value.values() for text in _trace_text_values(item))
    if isinstance(value, (list, tuple)):
        return tuple(text for item in value for text in _trace_text_values(item))
    return ()


def _skill_window_span_ids(
    anchor: tuple[str, str],
    branch: tuple[str, str],
    boundary: tuple[str, str] | None,
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    topology: _SpanTopology,
) -> tuple[str, ...]:
    anchor_order = span_sort_key(by_identity[anchor])
    boundary_order = span_sort_key(by_identity[boundary]) if boundary is not None else None
    seeds: set[tuple[str, str]] = set()
    for identity, span in by_identity.items():
        if topology.branches[identity] != branch:
            continue
        span_order = span_sort_key(span)
        if span_order < anchor_order:
            continue
        if boundary_order is not None and span_order >= boundary_order:
            continue
        seeds.add(identity)
    seeds.add(anchor)
    # The enclosing branch is useful context even when it started before the
    # Skill read.  Descendant traversal remains bounded by the next Skill.
    seeds.add(branch)
    selected = _descendants_before_boundary(seeds, boundary_order, by_identity, topology.children)
    selected_in_branch: set[tuple[str, str]] = set()
    for identity in selected:
        if topology.branches.get(identity) != branch:
            continue
        if identity != branch and span_sort_key(by_identity[identity]) < anchor_order:
            continue
        selected_in_branch.add(identity)
    return _ordered_span_ids(selected_in_branch, by_identity)


def _tool_and_subagent_fragments(
    continuity_index: int,
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    children: Mapping[tuple[str, str], Sequence[tuple[str, str]]],
    branches: Mapping[tuple[str, str], tuple[str, str]],
) -> list[SymphonyExecutionFragment]:
    fragments: list[SymphonyExecutionFragment] = []
    for identity, span in by_identity.items():
        if span_category(span) != "tool":
            continue
        tool_name = _tool_name(span)
        canonical_name = _canonical_tool_name(tool_name)
        if canonical_name in {_COMPOSE_TOOL_NAME, _SKILL_TOOL_NAME}:
            continue
        descendants = _descendants_before_boundary({identity}, None, by_identity, children)
        child_agents = _nearest_child_agents(identity, by_identity, children)
        subagent_name = _subagent_capability_name(span, child_agents, by_identity)
        is_subagent = (canonical_name in _SUBAGENT_DISPATCH_TOOLS and subagent_name is not None) or bool(child_agents)
        if is_subagent:
            branch = child_agents[0] if child_agents else branches[identity]
            fragments.append(
                _fragment(
                    continuity_index=continuity_index,
                    capability_type="subagent",
                    capability_name=subagent_name,
                    anchor=identity,
                    branch=branch,
                    span_ids=_ordered_span_ids(descendants, by_identity),
                )
            )
            continue
        fragments.append(
            _fragment(
                continuity_index=continuity_index,
                capability_type="tool",
                capability_name=tool_name,
                anchor=identity,
                branch=branches[identity],
                span_ids=(identity[1],),
            )
        )
    return fragments


def _nearest_child_agents(
    anchor: tuple[str, str],
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    children: Mapping[tuple[str, str], Sequence[tuple[str, str]]],
) -> tuple[tuple[str, str], ...]:
    """Return the nearest child-agent layer in stable trajectory order."""

    seen = {anchor}
    pending = list(children.get(anchor, ()))
    while pending:
        agents: list[tuple[str, str]] = []
        next_level: list[tuple[str, str]] = []
        for identity in sorted(pending, key=lambda item: span_sort_key(by_identity[item])):
            if identity in seen:
                continue
            seen.add(identity)
            if span_category(by_identity[identity]) == "agent":
                agents.append(identity)
            else:
                next_level.extend(children.get(identity, ()))
        if agents:
            return tuple(agents)
        pending = next_level
    return ()


def _fragment(
    *,
    continuity_index: int,
    capability_type: Literal["skill", "tool", "subagent"],
    capability_name: str | None,
    anchor: tuple[str, str],
    branch: tuple[str, str],
    span_ids: tuple[str, ...],
) -> SymphonyExecutionFragment:
    return SymphonyExecutionFragment(
        fragment_id=f"{anchor[0]}:{continuity_index}:{capability_type}:{anchor[1]}",
        capability_type=capability_type,
        capability_name=capability_name,
        trace_id=anchor[0],
        anchor_span_id=anchor[1],
        branch_span_id=branch[1],
        span_ids=span_ids,
        continuity_index=continuity_index,
    )


def _descendants_before_boundary(
    seeds: set[tuple[str, str]],
    boundary_order: tuple[int, int, str, str] | None,
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    children: Mapping[tuple[str, str], Sequence[tuple[str, str]]],
) -> set[tuple[str, str]]:
    selected: set[tuple[str, str]] = set()
    pending = list(seeds)
    while pending:
        identity = pending.pop()
        if identity in selected:
            continue
        span = by_identity.get(identity)
        if span is None:
            continue
        if boundary_order is not None and identity not in seeds and span_sort_key(span) >= boundary_order:
            continue
        selected.add(identity)
        pending.extend(children.get(identity, ()))
    return selected


def _ordered_span_ids(
    identities: set[tuple[str, str]],
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[str, ...]:
    return tuple(identity[1] for identity in sorted(identities, key=lambda item: span_sort_key(by_identity[item])))


def _effective_skill_name(span: Mapping[str, Any]) -> str | None:
    if span_category(span) != "tool" or read_span_error(span) is not None:
        return None
    tool_call = read_tool_call(span)
    if _canonical_tool_name(tool_call.get("name")) != _SKILL_TOOL_NAME:
        return None
    inputs = _tool_input_mapping(tool_call.get("input"))
    skill_name = str(inputs.get("skill_name") or "").strip() if inputs is not None else ""
    output = tool_call.get("output")
    if not skill_name or not (_explicit_success(output) or _authoritative_truncated_success(span, output)):
        return None
    return skill_name


def _subagent_capability_name(
    span: Mapping[str, Any],
    child_agents: Sequence[tuple[str, str]],
    by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
) -> str | None:
    inputs = _tool_input_mapping(
        read_tool_call(span).get("input"),
        required_key="subagent_type",
    )
    if inputs is not None:
        subagent_type = str(inputs.get("subagent_type") or "").strip()
        if subagent_type:
            return subagent_type
    for identity in child_agents:
        attrs = span_attributes(by_identity[identity])
        for key in (semconv.AT_AGENT_ID, semconv.AT_AGENT_NAME):
            value = str(attrs.get(key) or "").strip()
            if value:
                return value
    return None


def _tool_name(span: Mapping[str, Any]) -> str | None:
    name = read_tool_call(span).get("name")
    if name is not None and str(name).strip():
        return str(name).strip()
    span_name = str(span.get("name") or "").strip()
    if span_name.lower().startswith("tool."):
        suffix = span_name.split(".", 1)[1].strip()
        return suffix or None
    return None


def _canonical_tool_name(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return raw.rsplit(".", 1)[-1].replace("-", "_")


def _mapping_value(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _tool_input_mapping(
    value: Any,
    *,
    required_key: str = "skill_name",
) -> Mapping[str, Any] | None:
    """Read named tool arguments from a direct mapping or callback payload.

    ``OtelCallbackHandler`` serializes a tool invocation as ``[args, kwargs]``.
    ``SkillTool.invoke(inputs, **kwargs)`` normally arrives as
    ``[[{"skill_name": "..."}], {}]``; keyword-only callers arrive as
    ``[[], {"skill_name": "..."}]``. Keep this narrow: only the standard
    two-item envelope and one direct positional mapping are unwrapped, so
    arbitrary JSON arrays cannot masquerade as named inputs.
    """

    direct = _mapping_value(value)
    if direct is not None:
        return direct
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[0], (list, tuple))
        and isinstance(value[1], Mapping)
    ):
        return None
    args, kwargs = value
    if kwargs.get(required_key):
        return kwargs
    if len(args) == 1 and isinstance(args[0], Mapping) and args[0].get(required_key):
        return args[0]
    return None


def _explicit_success(value: Any) -> bool:
    payload = _mapping_value(value)
    if payload is not None:
        return payload.get("success") is True and not payload.get("error")
    if not isinstance(value, str):
        return False
    if re.search(r"(?:^|[\s,{])success\s*[=:]\s*true\b", value, re.IGNORECASE) is None:
        return False
    error = re.search(r"(?:^|\s)error\s*=\s*([^\s]+)", value, re.IGNORECASE)
    return error is None or error.group(1).lower() in {"none", "null"}


def _authoritative_truncated_success(
    span: Mapping[str, Any],
    value: Any,
) -> bool:
    """Trust a standard truncated prefix only on an authoritative OK span."""

    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if (
        _OBSERVABILITY_TRUNCATED_SUFFIX.search(stripped) is None
        or _TRUNCATED_JSON_SUCCESS_PREFIX.match(stripped) is None
    ):
        return False
    if span_attributes(span).get(semconv.OJ_TOOL_AUTHORITATIVE) is not True:
        return False
    code = str(span_status(span).get("code") or "").upper()
    return code in {"1", "OK", "STATUS_CODE_OK"}


__all__ = ["SymphonyExecutionFragment", "project_symphony_execution_fragments"]
