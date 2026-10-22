# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Invocation-local consultation state and immutable diagnostic snapshots."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from itertools import count
from typing import Any

from openjiuwen.core.common.logging import agent_logger
from openjiuwen.core.foundation.llm.schema.message import BaseMessage

TurnKey = tuple[object, int]
_batch_ids = count(1)


@dataclass(frozen=True)
class ProposalSource:
    """One reference's original response, in configured order."""

    index: int
    text: str


@dataclass(frozen=True)
class ProposalAttempt:
    """Request-local diagnostic; never used to decide delivery eligibility."""

    key: TurnKey
    injected: bool
    reason: str
    kept_chars: int = 0


@dataclass
class ProposalBatch:
    sources: tuple[ProposalSource, ...]
    target: TurnKey
    attempts: list[ProposalAttempt] = field(default_factory=list)
    batch_id: int = field(default_factory=lambda: next(_batch_ids), compare=False)


@dataclass
class MoAState:
    """Ephemeral consultation and scoring state for one outer invocation."""

    problem: str = ""
    baseline: list[BaseMessage] = field(default_factory=list)
    pending: ProposalBatch | None = None
    active_key: TurnKey | None = None
    completed_turns: int = 0
    steps: list[str] = field(default_factory=list)
    previous_score: float | None = None
    reference_calls: int = 0
    verifier_calls: int = 0
    scorer: Any = None
    context: Any = None
    consultation_rejection: str | None = None
    closed: bool = False
    jev_calls: int = 0
    jev_initial_checked: bool = False
    last_jev_decision: bool | None = None
    last_jev_reason: str | None = None

    def expire_pending(self, reason: str) -> None:
        if self.pending is not None:
            agent_logger.debug(
                "MoA proposal expired: turn=%s sources=%s reason=%s",
                self.pending.target[1],
                len(self.pending.sources),
                reason,
            )
            self.pending = None


active_moa: ContextVar[MoAState | None] = ContextVar("active_moa", default=None)


@dataclass(frozen=True)
class ProposalAttemptSnapshot:
    """A detached attempt diagnostic with identity stable across snapshots."""

    identity: tuple[int, int]
    key: TurnKey
    injected: bool
    reason: str
    kept_chars: int


@dataclass(frozen=True)
class MoASnapshot:
    """Read-only routing metadata; excludes reference text and mutable state."""

    active_key: TurnKey | None
    completed_turns: int
    reference_calls: int
    verifier_calls: int
    previous_score: float | None
    pending_target: TurnKey | None
    latest_attempt: ProposalAttemptSnapshot | None
    jev_calls: int = 0
    last_jev_decision: bool | None = None
    last_jev_reason: str | None = None


def get_moa_snapshot() -> MoASnapshot | None:
    """Read the active invocation's diagnostics without exposing its state."""
    state = active_moa.get()
    if state is None or state.closed:
        return None
    batch = state.pending
    latest = batch.attempts[-1] if batch is not None and batch.attempts else None
    attempt = (
        ProposalAttemptSnapshot(
            identity=(batch.batch_id, len(batch.attempts)),
            key=latest.key,
            injected=latest.injected,
            reason=latest.reason,
            kept_chars=latest.kept_chars,
        )
        if latest is not None
        else None
    )
    return MoASnapshot(
        active_key=state.active_key,
        completed_turns=state.completed_turns,
        reference_calls=state.reference_calls,
        verifier_calls=state.verifier_calls,
        previous_score=state.previous_score,
        pending_target=batch.target if batch is not None else None,
        latest_attempt=attempt,
        jev_calls=state.jev_calls,
        last_jev_decision=state.last_jev_decision,
        last_jev_reason=state.last_jev_reason,
    )
