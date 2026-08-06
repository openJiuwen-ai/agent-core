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


def _optional_positive_int(value: Any, field_name: str) -> Optional[int]:
    """Parse an optional positive int from persisted GoalRecord fields."""
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid persisted GoalRecord {field_name}") from exc
    if parsed <= 0:
        raise ValueError(f"invalid persisted GoalRecord {field_name}")
    return parsed


def _non_negative_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid persisted GoalRecord {field_name}") from exc
    if parsed < 0:
        raise ValueError(f"invalid persisted GoalRecord {field_name}")
    return parsed


def _parse_timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid persisted GoalRecord {field_name}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid persisted GoalRecord {field_name}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"invalid persisted GoalRecord {field_name}")
    return parsed


def _optional_timestamp(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    _parse_timestamp(value, field_name)
    return str(value)


def _required_timestamp(value: Any, field_name: str) -> str:
    _parse_timestamp(value, field_name)
    return str(value)


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class GoalOperationError(RuntimeError):
    """The one expected failure type exposed by the Goal capability."""

    def __init__(
        self,
        *,
        operation: str,
        code: str,
        message: str,
        goal: Optional[GoalRecord] = None,
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
    def from_dict(cls, data: Dict[str, Any]) -> GoalAssessment:
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
    def from_dict(cls, data: Dict[str, Any]) -> TokenUsage:
        return cls(
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            cached_input_tokens=int(data.get("cached_input_tokens", 0)),
            total_tokens=int(data.get("total_tokens", 0)),
        )


@dataclass
class GoalRecord:
    goal_id: str
    session_id: str
    objective: str
    status: GoalStatus = GoalStatus.ACTIVE
    revision: int = 0
    attempt_count: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    max_attempts: Optional[int] = None
    token_budget: Optional[int] = None
    last_assessment: Optional[GoalAssessment] = None
    last_stop_reason: Optional[str] = None
    time_used_seconds: int = 0
    active_started_at: Optional[str] = None
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def touch(self, *, bump_revision: bool = False) -> None:
        self.updated_at = _utc_now_iso()
        if bump_revision:
            self.revision += 1

    def start_timing(self) -> None:
        now = _utc_now_iso()
        if self.active_started_at is None:
            self.active_started_at = now
        self.updated_at = now

    def settle_active_time(self, *, keep_active: bool) -> None:
        now = datetime.now(tz=timezone.utc)
        now_iso = now.isoformat()
        if self.active_started_at is not None:
            started_at = _parse_timestamp(self.active_started_at, "active_started_at")
            elapsed_seconds = int((now - started_at).total_seconds())
            self.time_used_seconds += max(0, elapsed_seconds)
            self.active_started_at = now_iso if keep_active else None
        elif keep_active and self.status is GoalStatus.ACTIVE:
            self.active_started_at = now_iso
        self.updated_at = now_iso

    def copy_for_response(self) -> GoalRecord:
        return copy.deepcopy(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "session_id": self.session_id,
            "objective": self.objective,
            "status": self.status.value,
            "revision": self.revision,
            "attempt_count": self.attempt_count,
            "token_usage": self.token_usage.to_dict(),
            "max_attempts": self.max_attempts,
            "token_budget": self.token_budget,
            "last_assessment": self.last_assessment.to_dict() if self.last_assessment else None,
            "last_stop_reason": self.last_stop_reason,
            "time_used_seconds": self.time_used_seconds,
            "active_started_at": self.active_started_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GoalRecord:
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
        # Session ``update_dict`` treats nested ``None`` as key deletion. After
        # pause/complete, ``active_started_at`` may be absent rather than null.
        time_used_raw = data.get("time_used_seconds", 0)
        active_started_at = _optional_timestamp(data.get("active_started_at"), "active_started_at")
        now = _utc_now_iso()
        created_raw = data.get("created_at")
        updated_raw = data.get("updated_at")
        created_at = (
            _required_timestamp(created_raw, "created_at") if created_raw is not None else now
        )
        updated_at = (
            _required_timestamp(updated_raw, "updated_at") if updated_raw is not None else created_at
        )
        # ACTIVE records should keep a start mark for further accumulation; heal
        # rather than reject when persistence dropped the optional timestamp.
        if status is GoalStatus.ACTIVE and active_started_at is None:
            active_started_at = updated_at
        return cls(
            goal_id=goal_id,
            session_id=session_id,
            objective=objective,
            status=status,
            revision=int(data.get("revision", 0)),
            attempt_count=int(data.get("attempt_count", 0)),
            token_usage=TokenUsage.from_dict(usage_data) if isinstance(usage_data, dict) else TokenUsage(),
            max_attempts=_optional_positive_int(data.get("max_attempts"), "max_attempts"),
            token_budget=_optional_positive_int(data.get("token_budget"), "token_budget"),
            last_assessment=GoalAssessment.from_dict(assessment_data)
            if isinstance(assessment_data, dict)
            else None,
            last_stop_reason=data.get("last_stop_reason") or None,
            time_used_seconds=_non_negative_int(time_used_raw, "time_used_seconds"),
            active_started_at=active_started_at,
            created_at=created_at,
            updated_at=updated_at,
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
        now = _utc_now_iso()
        return cls(
            goal_id=uuid.uuid4().hex[:12],
            session_id=session_id,
            objective=objective,
            max_attempts=max_attempts,
            token_budget=token_budget,
            active_started_at=now,
            created_at=now,
            updated_at=now,
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
