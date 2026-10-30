# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rule-only Skill usage detection for the attribution prompt.

A skill counts as used when the trajectory calls ``skill_tool`` or reads a
file whose path ends with ``skill.md`` (case-insensitive).  Listing skills
(``list_skill`` / ``list_skills``) is not usage.  Everything here is pure
data over :class:`~openjiuwen.agent_evolving.attribution.nodes.TrajectoryNode`
so it is cheap and unit-testable; the host never takes part.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_evolving.attribution.model import DiagnosticSignal
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode

_SKILL_TOOL = "skill_tool"
_LISTING_TOOLS = frozenset({"list_skill", "list_skills"})
_SKILL_FILE_SUFFIX = "skill.md"


@dataclass(frozen=True)
class SkillUsage:
    """One skill the selected trajectory used and the node it first appeared in."""

    skill_key: str
    kind: str  # "tool" | "file"
    first_node_id: str


def _split_node_id(node_id: str) -> tuple[str, str] | None:
    text = str(node_id or "").strip()
    if not text or ":" not in text:
        return None
    key, _, seq = text.rpartition(":")
    if not key or not seq.isdigit():
        return None
    return key, seq


def _tool_call_parts(call: Any) -> tuple[str, Any]:
    """Return ``(name, arguments)`` of one tool call in either accepted shape."""
    if not isinstance(call, Mapping):
        return "", None
    name = call.get("name")
    arguments = call.get("arguments")
    function = call.get("function")
    if isinstance(function, Mapping):
        name = name or function.get("name")
        arguments = arguments if arguments is not None else function.get("arguments")
    return str(name or ""), arguments


def _parsed_arguments(arguments: Any) -> Any:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return None
    return arguments


def _iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        found: list[str] = []
        for item in value.values():
            found.extend(_iter_strings(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_iter_strings(item))
        return found
    return []


def _skills_in_node(node: TrajectoryNode) -> list[tuple[str, str]]:
    """Return ``(skill_key, kind)`` for every skill this node used."""
    found: list[tuple[str, str]] = []
    for call in node.tool_calls or ():
        name, raw_arguments = _tool_call_parts(call)
        arguments = _parsed_arguments(raw_arguments)
        if name == _SKILL_TOOL:
            if isinstance(arguments, Mapping):
                skill_name = arguments.get("skill_name") or arguments.get("skill")
                if skill_name is not None and str(skill_name).strip():
                    found.append((str(skill_name).strip(), "tool"))
        if name in _LISTING_TOOLS:
            continue
        for text in _iter_strings(arguments):
            if text.strip().lower().endswith(_SKILL_FILE_SUFFIX):
                found.append((text, "file"))
    return found


def detect_skill_usage(nodes_by_subagent: Mapping[str, list[TrajectoryNode]]) -> list[SkillUsage]:
    """Return the skills used in the selected range, each with its earliest node."""
    earliest: dict[str, tuple[int, str, str]] = {}
    rows: list[tuple[int, str, str, str]] = []
    for nodes in nodes_by_subagent.values():
        for node in nodes:
            for usage in _skills_in_node(node):
                rows.append((node.start_ts_ns, node.node_id, *usage))
    for ts_ns, node_id, skill_key, kind in sorted(rows):
        previous = earliest.get(skill_key)
        if previous is None or (ts_ns, node_id) < (previous[0], previous[1]):
            earliest[skill_key] = (ts_ns, node_id, kind)
    return [
        SkillUsage(skill_key=key, kind=kind, first_node_id=node_id)
        for key, (_ts, node_id, kind) in sorted(earliest.items())
    ]


def rule_premarks(
    signals: list[DiagnosticSignal],
    nodes_by_subagent: Mapping[str, list[TrajectoryNode]],
) -> dict[str, list[str]]:
    """Return, per signal, skills used in the same round (a hint, never an obligation).

    A round starts at the last user row at or before the signal's anchor node
    inside the anchor's subagent.  Signals without an anchor get no hint.
    """
    premarks: dict[str, list[str]] = {}
    indexes = {
        subagent: {node.node_id: index for index, node in enumerate(nodes)}
        for subagent, nodes in nodes_by_subagent.items()
    }
    for signal in signals:
        for anchor in signal.node_ids:
            split = _split_node_id(anchor)
            if split is None:
                continue
            subagent, _seq = split
            nodes = nodes_by_subagent.get(subagent)
            index = indexes.get(subagent, {}).get(anchor)
            if nodes is None or index is None:
                continue
            start = index
            while start >= 0 and nodes[start].role != "user":
                start -= 1
            keys: list[str] = []
            for node in nodes[max(start, 0): index + 1]:
                for skill_key, _kind in _skills_in_node(node):
                    if skill_key not in keys:
                        keys.append(skill_key)
            if keys:
                premarks[signal.signal_id] = keys
            break
    return premarks


__all__ = ["SkillUsage", "detect_skill_usage", "rule_premarks"]
