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
    """The **session-wide** (team/leader) token ledger, shared by reference with
    the backend (which reports real usage into it). Monotonic across runs — never
    resets. Default: an unbounded ledger. The engine only reads it — at the
    ``agent()`` / ``send()`` budget gates and via ``budget.*``."""
    workflow_budget: BudgetLedger = field(default_factory=BudgetLedger)
    """The **per-run** token ledger, shared by reference with the backend like
    ``budget`` but reset to ``spent=0`` on each new ``swarmflow`` invocation.
    Its ``total`` is the script-declared ``workflow_token_limit`` (a per-run
    ceiling independent of the session budget). The engine reads it at the
    per-run ``_check_budget`` gate (``scope="workflow"``). Hitting it is
    retryable by revising the workflow, unlike the session ceiling."""
    cap_override: int | None = None  # force the concurrency cap (tests)
    abort_event: AbortSignal | None = field(default=None, repr=False)
    """External cooperative pause/stop signal. When set, the ``agent()`` /
    ``AgentSession.send()`` abort checkpoints raise ``WorkflowAborted`` carrying
    the signal's ``reason`` (``"pause"`` → relaunch on resume; ``"stop"`` →
    terminal) — the in-flight call does not journal and the run unwinds (a
    resume reruns it). ``None`` disables the checkpoints (default; full
    back-compat)."""
    run_id: str | None = field(default=None, repr=False)
    """This run's identifier, threaded into journal records as an independent
    cache-isolation key (``get_cached`` checks ``sig`` AND ``run_id``). A
    completed/stopped run that relaunches with a fresh ``run_id`` never hits
    the prior run's cache (no cross-run budget bleed); a resume keeps the same
    ``run_id`` so the journal's pause record and cache replay carry forward."""
    current_agent: "dict | None" = field(default=None, repr=False)
    """The in-flight agent (``{"agent_id", "label", "started_spent"}``) between
    ``_emit_agent_started`` and its completed/failed counterpart. A pause/stop
    that lands mid-agent reads this to record *which* agent was interrupted and
    how many tokens it had already billed (``spent - started_spent``)."""

    # Mutable run state (created/advanced inside the running loop).
    agent_gate: AgentAdmission | None = field(default=None, repr=False)
    spawn_count: int = 0
    warned_concurrent_scope: bool = False  # one-shot guard for the raw-gather warning
    warned_concurrent_session: bool = False  # one-shot guard for overlapping session sends

    def make_cap(self) -> int:
        """Concurrent ``agent()`` calls allowed. Clamped to >= 1."""
        return resolve_agents_per_run_cap(None, cap_override=self.cap_override)
