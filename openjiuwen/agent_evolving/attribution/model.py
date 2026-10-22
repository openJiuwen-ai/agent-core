# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Immutable value objects for the three-stage attribution pipeline.

The pipeline is: task parsing (rule file extraction + one pseudo-agent LLM
call), diagnosis (keyword rules or Jev + optional Reward building), and
attribution (a deep agent with ten tools).  Everything here is pure data;
runtime wiring and persistence belong to the host.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol


class SignalSource(str, Enum):
    """Provenance of a :class:`DiagnosticSignal`."""

    KEYWORD_TOOL = "keyword_tool"
    KEYWORD_USER = "keyword_user"
    REWARD = "reward"
    USER = "user"


SIGNAL_TIERS = frozenset({"perfect", "fair", "weak", "uncertain"})

_RULE_TIER = "uncertain"

# Jev confidence -> tier mapping (proposal §4.5): [90,100] -> weak,
# [70,90) -> uncertain, <70 -> fair.
_JEV_WEAK_MIN = 90.0
_JEV_UNCERTAIN_MIN = 70.0


def jev_tier(confidence: float) -> str:
    """Map a Jev confidence in ``[0, 100]`` to a diagnostic tier."""
    if confidence >= _JEV_WEAK_MIN:
        return "weak"
    if confidence >= _JEV_UNCERTAIN_MIN:
        return "uncertain"
    return "fair"


def reward_tier(score: float) -> str:
    """Map a reward sub-dimension score to a tier (closed-open bands)."""
    if 90.0 <= score <= 100.0:
        return "perfect"
    if 75.0 <= score < 90.0:
        return "fair"
    return "weak"


def make_signal_id(text: str, source: SignalSource | str, ts_ns: int) -> str:
    """Return a content-stable ``diag-<24hex>`` id for a signal."""
    digest = hashlib.sha256(f"{SignalSource(source).value}\0{int(ts_ns or 0)}\0{text}".encode("utf-8")).hexdigest()
    return f"diag-{digest[:24]}"


def new_signal_id() -> str:
    """Return a fresh random id for manually created signals."""
    return f"diag-{uuid.uuid4().hex[:24]}"


def new_attr_id() -> str:
    """Return a fresh ``attr-<8hex>`` id for one attribution entry."""
    return f"attr-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class DiagnosticSignal:
    """One diagnosis output fed to the attribution agent.

    ``node_ids`` are lead anchors (where the problem surfaced), not the
    located cause; the attribution agent locates the causing nodes itself.
    """

    signal_id: str
    source: SignalSource
    text: str
    tier: str
    node_ids: list[str]
    score: float | None = None
    ts_ns: int | None = None
    message_id: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        text: str,
        *,
        source: SignalSource | str,
        tier: str | None = None,
        node_ids: list[str] | None = None,
        score: float | None = None,
        ts_ns: int | None = None,
        message_id: str | None = None,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
        signal_id: str | None = None,
    ) -> "DiagnosticSignal":
        """Create a signal, deriving a stable id when none is supplied."""
        normalized_source = SignalSource(source)
        return cls(
            signal_id=signal_id or make_signal_id(text, normalized_source, int(ts_ns or 0)),
            source=normalized_source,
            text=text,
            tier=tier or _RULE_TIER,
            node_ids=list(node_ids or ()),
            score=score,
            ts_ns=int(ts_ns) if ts_ns is not None else None,
            message_id=message_id,
            confidence=confidence,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly mapping of the signal."""
        return {
            "signal_id": self.signal_id,
            "source": self.source.value,
            "text": self.text,
            "tier": self.tier,
            "node_ids": list(self.node_ids),
            "score": self.score,
            "ts_ns": self.ts_ns,
            "message_id": self.message_id,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiagnosticSignal":
        """Rebuild a signal from :meth:`to_dict` output."""
        return cls(
            signal_id=str(data.get("signal_id") or "") or new_signal_id(),
            source=SignalSource(str(data.get("source") or SignalSource.KEYWORD_TOOL.value)),
            text=str(data.get("text") or ""),
            tier=str(data.get("tier") or _RULE_TIER),
            node_ids=[str(item) for item in data.get("node_ids") or ()],
            score=_optional_float(data.get("score")),
            ts_ns=_optional_int(data.get("ts_ns")),
            message_id=data.get("message_id"),
            confidence=_optional_float(data.get("confidence")),
            metadata=dict(data.get("metadata") or {}),
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class RewardOverall:
    """Reward total score (display only) plus an overall comment."""

    comment: str
    total_score: int

    def to_dict(self) -> dict[str, Any]:
        return {"comment": self.comment, "total_score": self.total_score}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RewardOverall":
        total = _optional_int(data.get("total_score")) or 0
        return cls(comment=str(data.get("comment") or ""), total_score=total)


@dataclass(frozen=True)
class SubjectRef:
    """One participating subagent's identity."""

    id: str
    display_name: str
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "display_name": self.display_name, "kind": self.kind}


@dataclass(frozen=True)
class TaskParseRequest:
    """Stage-one input: sliced nodes plus a language hint."""

    nodes_by_subagent: dict[str, list[Any]]
    language: str
    workspace: str | None = None


@dataclass(frozen=True)
class TaskParseResult:
    """Stage-one output consumed by diagnosis and attribution."""

    task_description: str
    message_artifacts: list[tuple[str, str]]
    file_artifacts: list[str]
    file_candidates: list[tuple[str, str, str]]
    run_meta: dict[str, Any] = field(default_factory=dict)
    user_request: str = ""
    """The user's own messages, verbatim and in order: the task as the user stated it."""
    user_feedback: list[str] | None = None
    """The user's follow-up messages judged to be feedback on the work (a correction, a complaint, asking for
    something left out); ``None`` when task parsing did not judge them."""
    requirements: list[dict[str, Any]] = field(default_factory=list)
    """The current requirement list from all the user's messages: ``[{"text", "corrected"}]``, later changes
    applied; empty when task parsing reported none."""
    delivery_round: str | None = None
    """The user message starting the round in which the result was last delivered (``None``: unknown)."""
    delivery_round_selected: bool | None = None
    """Whether the selection holds the leader (main agent) in that round: only then are deliverables reviewed."""


@dataclass(frozen=True)
class DiagnosisRequest:
    """Stage-two input."""

    run_id: str
    language: str
    nodes_by_subagent: dict[str, list[Any]]
    user_attribution: str
    task_parse: TaskParseResult
    workspace: str | None = None
    keyword_mode: Literal["rule", "jev"] = "rule"


@dataclass(frozen=True)
class DiagnosisResult:
    """Stage-two output: either reward dimensions or keyword/Jev signals.

    ``reward_payload`` carries the full validated reward submission
    (``attribution.reward.v1``: overall + categories + dimensions) so a host
    can display the Reward stage in full; ``reward_overall`` stays as the
    compact display summary.
    """

    reward_overall: RewardOverall | None
    signals: list[DiagnosticSignal]
    signal_mode: Literal["reward", "rule_jev"]
    run_meta: dict[str, Any] = field(default_factory=dict)
    reward_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class AttributionEntry:
    """One located attribution entry (node-level or span-level)."""

    text_md: str
    start_node_id: str
    end_node_id: str | None = None
    attr_id: str = field(default_factory=new_attr_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attr_id": self.attr_id,
            "text_md": self.text_md,
            "start_node_id": self.start_node_id,
            "end_node_id": self.end_node_id,
        }


@dataclass
class AttributionRunOutcome:
    """Attribution run status consumed by the host and the frontend."""

    status: Literal["success", "failed"]
    failure_kind: Literal["timeout", "tokens_exceeded", "error", "incomplete"] | None = None
    error_detail: str | None = None
    completed: bool = False
    pending_signal_ids: list[str] = field(default_factory=list)
    skipped_signal_ids: list[str] = field(default_factory=list)
    wake_attempted: bool = False
    counts: dict[str, Any] = field(default_factory=dict)
    token_usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RewardDimension:
    """One reward sub-dimension before conversion into a diagnostic signal."""

    category: str
    requirement: str
    comment: str
    score: float
    related_signal_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LlmRequestEvent:
    """One LLM request's live status (request-level granularity, §5.6)."""

    request_id: str
    stage: str
    call_kind: str
    status: str
    started_at_ns: int
    ended_at_ns: int | None = None
    agent_id: str | None = None
    iteration: int | None = None
    model: str | None = None
    response_preview: str | None = None
    tool_calls: list[str] = field(default_factory=list)
    token_usage: dict[str, Any] = field(default_factory=dict)
    retry_index: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "stage": self.stage,
            "call_kind": self.call_kind,
            "status": self.status,
            "started_at_ns": self.started_at_ns,
            "ended_at_ns": self.ended_at_ns,
            "agent_id": self.agent_id,
            "iteration": self.iteration,
            "model": self.model,
            "response_preview": self.response_preview,
            "tool_calls": list(self.tool_calls),
            "token_usage": dict(self.token_usage),
            "retry_index": self.retry_index,
            "error": self.error,
        }


class StatusSink(Protocol):
    """Host-injected live status sink; never injected means zero overhead."""

    def report(self, event: LlmRequestEvent) -> None:
        """Receive one LLM request event (never raise into the pipeline)."""


__all__ = [
    "AttributionEntry",
    "AttributionRunOutcome",
    "DiagnosisRequest",
    "DiagnosisResult",
    "DiagnosticSignal",
    "LlmRequestEvent",
    "RewardDimension",
    "RewardOverall",
    "SignalSource",
    "StatusSink",
    "SubjectRef",
    "TaskParseRequest",
    "TaskParseResult",
    "jev_tier",
    "make_signal_id",
    "new_attr_id",
    "new_signal_id",
    "reward_tier",
]
