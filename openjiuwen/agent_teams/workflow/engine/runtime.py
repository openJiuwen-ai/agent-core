# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-run engine state shared by all injected primitives."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from .admission import AgentAdmission
from .backends.base import AgentBackend
from .budget import BudgetLedger
from .cap import resolve_agents_per_run_cap
from .journal import Journal
from .progress import ProgressSink, noop_progress_sink


def _noop_log(message: str) -> None:
    """Default ``log_sink``: drop diagnostics. Embedders inject a real logger.

    Replaces dw's ``print`` default — library code must not ``print`` (the team
    integration injects a ``team_logger``-backed sink).
    """
    return None


@dataclass
class AbortSignal:
    """Cooperative abort flag that carries the control reason (pause vs stop).

    Wraps an ``asyncio.Event`` so the engine's abort checkpoints keep their
    wait/set semantics, while the ``reason`` lets the unwind site distinguish a
    pause (relaunch on resume) from a terminal stop (never relaunch).
    """

    event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = "pause"

    def set(self, reason: str = "pause") -> None:
        self.reason = reason
        self.event.set()

    def is_set(self) -> bool:
        return self.event.is_set()


@dataclass
class Runtime:
    backend: AgentBackend
    journal: Journal
    args: Any = None

    # Behaviour knobs.
    log_sink: Callable[[str], None] = _noop_log
    """Plain-text diagnostics (lint warnings, backend failures, spawn-limit /
    concurrent-scope warnings). NOT the per-phase/agent progress feed — that is
    ``progress_sink``."""
    progress_sink: ProgressSink = noop_progress_sink
    """Structured per-phase / per-agent progress (``WorkflowProgressEvent``).
    Drives the leader spectator broadcast and the 4-layer ``WorkflowRun``."""
    retries: int = 2  # extra attempts after the first on backend/validation error
    strict: bool = False
    spawn_limit: int = 1000
    budget: BudgetLedger = field(default_factory=BudgetLedger)
    """Session-wide token ledger, shared with the backend (which reports real
    usage into it); never resets. The engine only reads it (budget gates,
    ``budget.*``)."""
    workflow_budget: BudgetLedger = field(default_factory=BudgetLedger)
    """Per-run token ledger, reset to ``spent=0`` on each ``swarmflow``
    invocation; ``total`` is the script-declared ``workflow_token_limit``.
    Hitting it is retryable by revising the workflow, unlike the session
    ceiling."""
    cap_override: int | None = None  # force the concurrency cap (tests)
    abort_event: AbortSignal | None = field(default=None, repr=False)
    """External pause/stop signal: when set, the abort checkpoints raise
    ``WorkflowAborted`` carrying its ``reason``; the in-flight call does not
    journal (a resume reruns it). ``None`` disables the checkpoints."""
    run_id: str | None = field(default=None, repr=False)
    """Run identifier threaded into journal records as a cache-isolation key
    (``get_cached`` checks ``sig`` AND ``run_id``): a fresh run never hits the
    prior run's cache; a resume keeps the same id."""
    current_agent: "dict | None" = field(default=None, repr=False)
    """The in-flight agent (``{"agent_id", "label", "started_spent"}``); a
    pause/stop mid-agent reads this to record which agent was interrupted."""

    # Mutable run state (created/advanced inside the running loop).
    agent_gate: AgentAdmission | None = field(default=None, repr=False)
    spawn_count: int = 0
    warned_concurrent_scope: bool = False  # one-shot guard for the raw-gather warning
    warned_concurrent_session: bool = False  # one-shot guard for overlapping session sends

    def make_cap(self) -> int:
        """Concurrent ``agent()`` calls allowed. Clamped to >= 1."""
        return resolve_agents_per_run_cap(None, cap_override=self.cap_override)
