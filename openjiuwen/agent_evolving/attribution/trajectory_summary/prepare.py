# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pure turn grouping and length segmentation over a fixed, ordered node pool."""

import json
import re
from dataclasses import asdict
from datetime import datetime

from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode, classify_input
from .models import Segment, SummaryLimits, SummaryTree, TurnInput, Turn, format_time_ns, public_summary
from .tool_view import tool_view

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def node_text(node: TrajectoryNode) -> str:
    """Model-facing view: readable time, ANSI cleanup and bounded large tool bodies."""
    data = asdict(node)
    if isinstance(data.get("content"), str):
        data["content"] = _ANSI.sub("", data["content"])
    return json.dumps(public_summary(tool_view(data)), ensure_ascii=False)


def bounds(nodes: list[TrajectoryNode]) -> tuple[int | None, int | None]:
    starts = [n.start_ts_ns for n in nodes if n.start_ts_ns > 0]
    ends = [n.end_ts_ns or n.start_ts_ns for n in nodes if (n.end_ts_ns or n.start_ts_ns) > 0]
    return min(starts) if starts else None, max(ends) if ends else None


def _calls(node: TrajectoryNode) -> set[str]:
    return {str(c.get("id")) for c in node.tool_calls if c.get("id")}


def _cut(nodes: list[TrajectoryNode], start: int, limits: SummaryLimits) -> int:
    stop = min(len(nodes), start + limits.target_nodes)
    if stop == len(nodes):
        return stop
    # Only repair nearby call/return splits; never construct atomic groups.
    low = max(start, stop - limits.boundary_window)
    high = min(len(nodes), stop + limits.boundary_window)
    for index in range(low, stop):
        calls = _calls(nodes[index])
        if not calls:
            continue
        pending = calls - {n.tool_call_id for n in nodes[index + 1:stop]}
        if not pending:
            continue
        returned = {n.tool_call_id for n in nodes[stop:high]}
        if pending <= returned:
            end = max(i + 1 for i in range(stop, high) if nodes[i].tool_call_id in pending)
            return end
        if index > start:
            return index
    return max(start + 1, stop)


def prepare(nodes_by_agent: dict[str, list[TrajectoryNode]], limits: SummaryLimits) -> tuple[SummaryTree, dict]:
    """Return metadata and private primary-node lists; never infer turns from USER rows."""
    tree = SummaryTree()
    primary: dict[str, list[TrajectoryNode]] = {}
    seen = set()
    for agent_id, nodes in nodes_by_agent.items():
        if not nodes:
            continue
        turns: dict[str, Turn] = {}
        tree.turns[agent_id] = []
        runs: list[list[TrajectoryNode]] = []
        last_id = None
        unknown = 0
        for node in nodes:
            if not node.node_id or node.node_id in seen or node.subagent_key != agent_id:
                raise ValueError("nodes must have unique IDs and match their agent")
            seen.add(node.node_id)
            turn_id = node.turn_id
            if not turn_id:
                if last_id is None or not last_id.startswith(f"unknown:{agent_id}:"):
                    unknown += 1
                turn_id = f"unknown:{agent_id}:{unknown}"
            if turn_id != last_id:
                runs.append([])
            runs[-1].append(node)
            last_id = turn_id
            if turn_id not in turns:
                turn = Turn(turn_id, node.turn_number, agent_id, node.node_id, node.node_id, *bounds([node]))
                turns[turn_id] = turn
                tree.turns[agent_id].append(turn)
                if not node.turn_id or turn_id.startswith("legacy:"):
                    tree.warnings.append({
                        "code": "turn_identity_fallback", "agent_id": agent_id,
                        "turn_id": turn_id, "message": "Runtime turn identity is missing or historical.",
                    })
            turn = turns[turn_id]
            if turn.turn_number is None:
                turn.turn_number = node.turn_number
            elif node.turn_number is not None and node.turn_number != turn.turn_number:
                tree.warnings.append({
                    "code": "turn_number_conflict", "agent_id": agent_id,
                    "turn_id": turn_id, "message": "One runtime turn states different display numbers.",
                })
            turn.end_node_id = node.node_id
            if node.role == "user":
                text = node.content or ""
                source, kind = classify_input(node)
                sender = re.search(r'<team-inbound\b[^>]*\bfrom="([^"]+)"', text) if source == "agent" else None
                sender_name = sender.group(1) if sender else None
                sent_ts_ns = None
                sent = re.search(r'<team-inbound\b[^>]*\btime="([^"]+)"', text) if source == "agent" else None
                if sent:
                    try:
                        sent_ts_ns = int(datetime.strptime(sent.group(1), "%Y-%m-%d %H:%M:%S %z").timestamp()) * 10**9
                    except ValueError:
                        pass
                turn.inputs.append(TurnInput(kind, node.node_id, source,
                    format_time_ns(node.start_ts_ns), text, sender_name, format_time_ns(sent_ts_ns)))
        ordinal = 0
        for run in runs:
            turn_id = run[0].turn_id
            if not turn_id:
                # Runs were built in input order; the first node uniquely locates its group.
                turn_id = next(t.turn_id for t in turns.values() if t.start_node_id == run[0].node_id)
            turn = turns[turn_id]
            start = 0
            while start < len(run):
                stop = _cut(run, start, limits)
                group = run[start:stop]
                ordinal += 1
                segment_id = f"{agent_id}:segment:{ordinal}"
                segment = Segment(segment_id, turn_id, agent_id, group[0].node_id, group[-1].node_id, *bounds(group))
                turn.segments.append(segment)
                primary[segment_id] = group
                start = stop
        for turn in turns.values():
            group = [n for s in turn.segments for n in primary[s.segment_id]]
            turn.start_ts_ns, turn.end_ts_ns = bounds(group)
    return tree, primary
