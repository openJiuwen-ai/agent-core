# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Invocation-local consultation state and immutable diagnostic snapshots."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Literal
from uuid import uuid4

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
    budget_mode: Literal["known", "unspecified", "unresolved"] = "unresolved"
    fallback: bool = False


@dataclass
class ProposalBatch:
    sources: tuple[ProposalSource, ...]
    target: TurnKey
    attempts: list[ProposalAttempt] = field(default_factory=list)
    batch_id: int = field(default_factory=lambda: next(_batch_ids), compare=False)
    disabled_reason: str | None = None
    origin: TurnKey | None = None


@dataclass
class ProposalStore:
    """Private candidate storage owned by one logical user task."""

    active: ProposalBatch | None = None
    ready: ProposalBatch | None = None
    epoch: int = 0
    issued_ticket: int = 0
    accepted_ticket: int = 0
    closed: bool = False

    def activate(self, key: TurnKey) -> None:
        """Promote ready advice only at a different logical decision."""
        if not self.closed and self.ready is not None and self.ready.origin != key:
            self.active = self.ready
            self.ready = None

    def invalidate(self, reason: str, *, clear: bool = True) -> None:
        """Fence asynchronous work, optionally retaining committed advice."""
        self.epoch += 1
        if clear:
            self.active = None
            self.ready = None
        agent_logger.debug("MoA proposal store invalidated: reason=%s clear=%s", reason, clear)

    def close(self, reason: str) -> None:
        """Retire the task without allowing late publication."""
        self.closed = True
        self.invalidate(reason)


@dataclass
class MoAState:
    """Ephemeral consultation and scoring state for one outer invocation."""

    problem: str = ""
    baseline: list[BaseMessage] = field(default_factory=list)
    proposals: ProposalStore = field(default_factory=ProposalStore)
    work_binding: str | None = None
    owns_proposals: bool = True
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
    last_jev_decision: bool | None = None
    last_jev_reason: str | None = None
    invoke_id: str = field(default_factory=lambda: uuid4().hex)

    def accepts_result(self, epoch: int) -> bool:
        """Check the captured task epoch and current work binding after awaits."""
        return (
            not self.closed and not self.proposals.closed and self.proposals.epoch == epoch
            and self.work_binding == active_moa_work_binding.get()
        )


active_moa: ContextVar[MoAState | None] = ContextVar("active_moa", default=None)
active_proposal_store: ContextVar[ProposalStore | None] = ContextVar("active_proposal_store", default=None)
active_moa_work_binding: ContextVar[str | None] = ContextVar("active_moa_work_binding", default=None)
InputOrigin = Literal["user_request", "internal", "interaction_response", "steering"]
active_moa_input_origin: ContextVar[InputOrigin | None] = ContextVar("active_moa_input_origin", default=None)


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
    if state is None or not state.accepts_result(state.proposals.epoch):
        return None
    batch = state.proposals.active
    pending = state.proposals.ready or batch
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
        pending_target=pending.target if pending is not None else None,
        latest_attempt=attempt,
        jev_calls=state.jev_calls,
        last_jev_decision=state.last_jev_decision,
        last_jev_reason=state.last_jev_reason,
    )
