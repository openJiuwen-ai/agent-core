# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Core data types for the skill_train sleep cycle."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class SessionDigest:
    """Normalized summary of one OTLP trajectory conversation."""

    session_id: str
    project: str = ""
    trajectory_id: str = ""
    started_at: str = ""
    ended_at: str = ""
    user_prompts: List[str] = field(default_factory=list)
    assistant_finals: List[str] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    skills_used: List[str] = field(default_factory=list)
    feedback_signals: List[str] = field(default_factory=list)
    n_user_turns: int = 0
    n_assistant_turns: int = 0
    # Ordered conversation turns. Each item is
    # ``{"role": "user"|"assistant"|"tool", "content": str, "skills": [str]}``.
    # ``tool`` turns carry the tool name in ``content`` and any skill names
    # resolved from that call in ``skills``. Optional: older digests may leave
    # this empty, in which case mine falls back to ``user_prompts``.
    turns: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskRecord:
    """Training unit mined from one or more session digests."""

    id: str
    project: str
    intent: str
    context_excerpt: str = ""
    system: str = ""
    attempted_solution: str = ""
    outcome: str = "unknown"
    reference_kind: str = "none"
    reference: str = ""
    judge: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    source_sessions: List[str] = field(default_factory=list)
    split: str = "train"
    origin: str = "real"
    derived_from: str = ""
    skill_hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskRecord":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ReplayResult:
    """Outcome of replaying one TaskRecord under skill+memory."""

    id: str
    hard: float = 0.0
    soft: float = 0.0
    response: str = ""
    fail_reason: str = ""
    task_type: str = "task"
    judge_rationale: str = ""
    tools_called: List[str] = field(default_factory=list)
    tokens: int = 0
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EditRecord:
    """One bounded edit proposed/applied to skill or memory."""

    target: str
    op: str
    content: str = ""
    anchor: str = ""
    rationale: str = ""


@dataclass
class SkillGroupReport:
    """Per-skill gate evidence for a multi-skill night."""

    skill_name: str
    status: str = ""  # consolidated | skipped | failed
    accepted: bool = False
    gate_action: str = ""
    baseline_score: float = 0.0
    candidate_score: float = 0.0
    n_tasks: int = 0
    n_applied_edits: int = 0
    n_rejected_edits: int = 0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SleepReport:
    """Everything one night produced — written to staging for review."""

    night: int
    project: str
    started_at: str = ""
    ended_at: str = ""
    n_sessions: int = 0
    n_tasks: int = 0
    n_replayed: int = 0
    baseline_score: float = 0.0
    candidate_score: float = 0.0
    accepted: bool = False
    gate_action: str = ""
    holdout_leaked: bool = False
    no_edits_reason: str = ""
    edits: List[EditRecord] = field(default_factory=list)
    rejected_edits: List[EditRecord] = field(default_factory=list)
    unmatched_edits: List[EditRecord] = field(default_factory=list)
    skill_groups: List[SkillGroupReport] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    gate_no_regression: bool = False
    gate_trials: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
