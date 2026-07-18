# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistent Goal domain model and capability error contract."""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class GoalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class GoalAssessmentStatus(str, Enum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class GoalStopStrategy(str, Enum):
    AGENT_REPORT = "agent_report"
    TRANSCRIPT = "transcript"
    HYBRID = "hybrid"


class GoalOperationError(RuntimeError):
    """The one expected failure type exposed by the Goal capability."""

    def __init__(
        self,
        *,
        operation: str,
        code: str,
        message: str,
        goal: Optional["GoalRecord"] = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.code = code
        self.goal = goal.copy_for_response() if goal is not None else None


@dataclass
class GoalAssessment:
    status: GoalAssessmentStatus
    evidence: str
    remaining_work: Optional[str] = None
    next_instruction: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence": self.evidence,
            "remaining_work": self.remaining_work,
            "next_instruction": self.next_instruction,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoalAssessment":
        try:
            status = GoalAssessmentStatus(str(data.get("status", "continue")))
        except ValueError:
            status = GoalAssessmentStatus.CONTINUE
        return cls(
            status=status,
            evidence=str(data.get("evidence") or ""),
            remaining_work=data.get("remaining_work") or None,
            next_instruction=data.get("next_instruction") or None,
        )


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    total_tokens: int = 0

    def accumulate(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_input_tokens: int = 0,
    ) -> None:
        self.input_tokens += int(input_tokens)
        self.output_tokens += int(output_tokens)
        self.cached_input_tokens += int(cached_input_tokens)
        self.total_tokens += int(input_tokens) + int(output_tokens)

    def to_dict(self) -> Dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenUsage":
        return cls(
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            cached_input_tokens=int(data.get("cached_input_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return _utc_now()


@dataclass
class GoalRecord:
    goal_id: str
    session_id: str
    objective: str
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    status: GoalStatus = GoalStatus.ACTIVE
    revision: int = 0
    attempt_count: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    max_attempts: Optional[int] = None
    token_budget: Optional[int] = None
    last_assessment: Optional[GoalAssessment] = None
    last_stop_reason: Optional[str] = None

    def touch(self, *, bump_revision: bool = False) -> None:
        if bump_revision:
            self.revision += 1
        self.updated_at = _utc_now()

    def copy_for_response(self) -> "GoalRecord":
        return copy.deepcopy(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "session_id": self.session_id,
            "objective": self.objective,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "status": self.status.value,
            "revision": self.revision,
            "attempt_count": self.attempt_count,
            "token_usage": self.token_usage.to_dict(),
            "max_attempts": self.max_attempts,
            "token_budget": self.token_budget,
            "last_assessment": self.last_assessment.to_dict() if self.last_assessment else None,
            "last_stop_reason": self.last_stop_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoalRecord":
        objective = data.get("objective")
        session_id = data.get("session_id")
        goal_id = data.get("goal_id")
        if not all(isinstance(value, str) and value for value in (objective, session_id, goal_id)):
            raise ValueError("invalid persisted GoalRecord identity")
        try:
            status = GoalStatus(str(data.get("status", GoalStatus.ACTIVE.value)))
        except ValueError as exc:
            raise ValueError("invalid persisted GoalRecord status") from exc
        usage_data = data.get("token_usage")
        assessment_data = data.get("last_assessment")
        return cls(
            goal_id=goal_id,
            session_id=session_id,
            objective=objective,
            created_at=_to_datetime(data.get("created_at")),
            updated_at=_to_datetime(data.get("updated_at")),
            status=status,
            revision=int(data.get("revision", 0)),
            attempt_count=int(data.get("attempt_count", 0)),
            token_usage=TokenUsage.from_dict(usage_data) if isinstance(usage_data, dict) else TokenUsage(),
            max_attempts=data.get("max_attempts"),
            token_budget=data.get("token_budget"),
            last_assessment=GoalAssessment.from_dict(assessment_data)
            if isinstance(assessment_data, dict)
            else None,
            last_stop_reason=data.get("last_stop_reason") or None,
        )

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        objective: str,
        max_attempts: Optional[int] = None,
        token_budget: Optional[int] = None,
    ) -> "GoalRecord":
        now = _utc_now()
        return cls(
            goal_id=uuid.uuid4().hex[:12],
            session_id=session_id,
            objective=objective,
            created_at=now,
            updated_at=now,
            max_attempts=max_attempts,
            token_budget=token_budget,
        )


@dataclass
class GoalStopConfig:
    strategy: GoalStopStrategy = GoalStopStrategy.HYBRID
    transcript_window_attempts: int = 8
    verification_interval: Optional[int] = None
    max_attempts: Optional[int] = None
    token_budget: Optional[int] = None


__all__ = [
    "GoalAssessment",
    "GoalAssessmentStatus",
    "GoalOperationError",
    "GoalRecord",
    "GoalStatus",
    "GoalStopConfig",
    "GoalStopStrategy",
    "TokenUsage",
]
