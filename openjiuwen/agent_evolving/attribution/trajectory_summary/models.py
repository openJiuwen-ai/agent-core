# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Serializable hierarchical summaries; node evidence stays in the source snapshot."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal

ProblemKind = Literal["tool_error", "user_correction", "retry", "conflict", "other"]
SummaryStatus = Literal["completed", "degraded", "failed"]
ProblemState = Literal["unresolved", "resolved", "unknown"]


def format_time_ns(value: int | None) -> str | None:
    """Match team time rendering, adding milliseconds without float rounding."""
    if value is None:
        return None
    value = int(value)
    if value <= 0:
        return None
    seconds, nanos = divmod(int(value), 10**9)
    dt = datetime.fromtimestamp(seconds, timezone.utc).astimezone()
    offset = dt.strftime("%z")
    return f"{dt:%Y-%m-%d %H:%M:%S}.{nanos // 10**6:03d} {offset[:3]}:{offset[3:]}"


def public_summary(value: object) -> object:
    """Keep nanoseconds internally; expose readable, explicitly named time fields."""
    if isinstance(value, dict):
        return {({"start_ts_ns": "start_time", "end_ts_ns": "end_time"}.get(key, key)):
                format_time_ns(item) if key in {"start_ts_ns", "end_ts_ns"} else public_summary(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [public_summary(item) for item in value]
    return value


@dataclass(frozen=True)
class ProblemRecord:
    problem_id: str
    turn_id: str
    agent_id: str
    segment_id: str
    kind: ProblemKind
    summary: str
    node_ids: list[str]
    source: Literal["model", "rule"]
    state: ProblemState = "unknown"
    resolution_node_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TurnInput:
    kind: str
    node_id: str
    source: str
    observed_at: str | None
    text: str
    sender_name: str | None = None
    sent_at: str | None = None


@dataclass
class Segment:
    segment_id: str
    turn_id: str
    agent_id: str
    start_node_id: str
    end_node_id: str
    start_ts_ns: int | None
    end_ts_ns: int | None
    narrative: str = ""
    problems: list[ProblemRecord] = field(default_factory=list)
    status: SummaryStatus = "failed"


@dataclass
class Turn:
    turn_id: str
    turn_number: int | None
    agent_id: str
    start_node_id: str
    end_node_id: str
    start_ts_ns: int | None
    end_ts_ns: int | None
    narrative: str = ""
    segments: list[Segment] = field(default_factory=list)
    inputs: list[TurnInput] = field(default_factory=list)
    status: SummaryStatus = "failed"
    problems: list["ProblemSummary"] = field(default_factory=list)


@dataclass
class AgentSummary:
    agent_id: str
    display_name: str
    kind: str
    summary: str
    start_node_id: str
    end_node_id: str
    start_ts_ns: int | None
    end_ts_ns: int | None
    status: SummaryStatus = "failed"
    problems: list["ProblemSummary"] = field(default_factory=list)


@dataclass(frozen=True)
class ProblemLocation:
    agent_id: str
    turn_id: str
    segment_id: str
    node_ids: list[str]
    node_refs: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ProblemSummary:
    problem_id: str
    kind: ProblemKind
    summary: str
    locations: list[ProblemLocation]
    source_problem_ids: list[str] = field(default_factory=list)
    source: Literal["model", "rule"] = "model"
    state: ProblemState = "unknown"
    resolution_node_ids: list[str] = field(default_factory=list)


@dataclass
class SummaryTree:
    trajectory_summary: str = ""
    agent_summaries: dict[str, AgentSummary] = field(default_factory=dict)
    turns: dict[str, list[Turn]] = field(default_factory=dict)
    problems: list[ProblemSummary] = field(default_factory=list)
    status: SummaryStatus = "failed"
    warnings: list[dict] = field(default_factory=list)
    display_summary: str = ""

    def to_dict(self) -> dict:
        """Return JSON-compatible data for the host's record persistence."""
        return public_summary(asdict(self))

    def view(self, agent_id: str = "", turn_id: str = "", segment_id: str = "") -> dict:
        """Read one scope instead of sending the entire tree to an agent."""
        if segment_id and not (agent_id and turn_id):
            return {"error": "segment_id requires agent_id and turn_id"}
        if turn_id and not agent_id:
            return {"error": "turn_id requires agent_id"}
        if not agent_id:
            return {
                "trajectory_summary": self.trajectory_summary,
                "status": self.status,
                "agents": list(self.turns),
                "warnings": self.warnings,
            }
        turns = self.turns.get(agent_id)
        if turns is None:
            return {"error": f"unknown agent_id: {agent_id}"}
        if not turn_id:
            agent = self.agent_summaries.get(agent_id)
            agent_view = public_summary(asdict(agent)) if agent is not None else None
            if isinstance(agent_view, dict):
                agent_view.pop("problems", None)
            return {
                "agent": agent_view,
                "turns": [
                    {
                        "turn_id": t.turn_id,
                        "turn_number": t.turn_number,
                        "start_node_id": t.start_node_id,
                        "end_node_id": t.end_node_id,
                        "status": t.status,
                    }
                    for t in turns
                ],
            }
        turn = next((t for t in turns if t.turn_id == turn_id), None)
        if turn is None:
            return {"error": f"unknown turn_id: {turn_id}"}
        if segment_id:
            segment = next((s for s in turn.segments if s.segment_id == segment_id), None)
            if segment is None:
                return {"error": f"unknown segment_id: {segment_id}"}
            segment_view = public_summary(asdict(segment))
            if isinstance(segment_view, dict):
                segment_view.pop("problems", None)
            return segment_view
        return {
            "turn_id": turn.turn_id,
            "turn_number": turn.turn_number,
            "agent_id": turn.agent_id,
            "start_node_id": turn.start_node_id,
            "end_node_id": turn.end_node_id,
            "narrative": turn.narrative,
            "inputs": [asdict(t) for t in turn.inputs],
            "status": turn.status,
            "segments": [
                {
                    "segment_id": s.segment_id,
                    "start_node_id": s.start_node_id,
                    "end_node_id": s.end_node_id,
                    "status": s.status,
                }
                for s in turn.segments
            ],
        }


@dataclass(frozen=True)
class SummaryLimits:
    """Node grouping and execution controls; context/output limits belong to the model."""

    target_nodes: int = 128
    boundary_window: int = 4
    concurrency: int = 8
    timeout_s: float = 180.0
    retries: int = 4

    def __post_init__(self) -> None:
        if self.target_nodes < 1 or self.boundary_window < 0:
            raise ValueError("invalid summary limits")
        if self.concurrency < 1 or self.timeout_s <= 0 or self.retries < 0:
            raise ValueError("invalid summary limits")
