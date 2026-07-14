# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-Leader Swarmflow concurrency governor (L1/L2/L3)."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from openjiuwen.agent_teams.workflow.engine.cap import resolve_agents_per_run_cap
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error


@dataclass(frozen=True)
class ConcurrencyLimits:
    """Three-layer Swarmflow concurrency caps."""

    max_workflows: int = 16
    agents_per_run: int | None = None
    max_agents_total: int = 64


@dataclass(frozen=True)
class ConcurrencySnapshot:
    """Read-only governor observability."""

    active_workflows: int
    max_workflows: int
    max_agents_total: int
    agents_per_run: int | None


def validate_swarmflow_concurrency(limits: ConcurrencyLimits) -> int:
    """Validate limits and return the resolved L2 cap.

    Raises:
        ValidationError (StatusCode.AGENT_TEAM_CONFIG_INVALID): When any
            build-time constraint is violated.
    """
    if limits.max_workflows < 1:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="swarmflow_concurrency.max_workflows must be >= 1",
        )
    if limits.max_agents_total < 1:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="swarmflow_concurrency.max_agents_total must be >= 1",
        )
    if limits.agents_per_run is not None and limits.agents_per_run < 1:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason="swarmflow_concurrency.agents_per_run must be >= 1 when set",
        )
    l2 = resolve_agents_per_run_cap(limits.agents_per_run)
    if l2 > limits.max_agents_total:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason=(
                "swarmflow_concurrency: agents_per_run effective cap "
                f"({l2}) must be <= max_agents_total ({limits.max_agents_total})"
            ),
        )
    if limits.max_agents_total < limits.max_workflows:
        raise_error(
            StatusCode.AGENT_TEAM_CONFIG_INVALID,
            reason=(
                "swarmflow_concurrency: max_agents_total "
                f"({limits.max_agents_total}) must be >= max_workflows ({limits.max_workflows})"
            ),
        )
    return l2


class RunAgentAdmission:
    """Swarmflow L2/L3 admission: per-run sem then shared global sem."""

    def __init__(
        self,
        *,
        per_run_sem: asyncio.Semaphore,
        global_sem: asyncio.Semaphore,
    ) -> None:
        self._per_run_sem = per_run_sem
        self._global_sem = global_sem

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        async with self._per_run_sem:
            async with self._global_sem:
                yield


@dataclass(frozen=True)
class WorkflowAdmission:
    """L1 admit result: ticket for release + per-run agent gate."""

    ticket: int
    agent_gate: RunAgentAdmission


class ConcurrencyGovernor:
    """Per-Leader authority for L1 workflow count and L3 global agent sem."""

    def __init__(self, limits: ConcurrencyLimits, *, agents_per_run_cap: int) -> None:
        self._limits = limits
        self._agents_per_run_cap = agents_per_run_cap
        self._lock = asyncio.Lock()
        self._next_ticket = 0
        # In-flight L1 tickets. Replaces a bare counter so release is idempotent
        # by construction: ``discard`` is a no-op on a repeated / unknown ticket,
        # so a double release can never over-admit, and the count is always the
        # set size — it cannot go negative or drift out of sync with the tickets.
        # Bounded by ``max_workflows`` (≤ a few dozen), so the set is tiny.
        self._active_tickets: set[int] = set()
        self._global_sem = asyncio.Semaphore(limits.max_agents_total)

    @property
    def limits(self) -> ConcurrencyLimits:
        return self._limits

    async def admit_workflow(self) -> WorkflowAdmission | None:
        """L1 admit: mint a ticket or return None when at cap."""
        async with self._lock:
            if len(self._active_tickets) >= self._limits.max_workflows:
                return None
            ticket = self._next_ticket
            self._next_ticket += 1
            self._active_tickets.add(ticket)
        per_run_sem = asyncio.Semaphore(self._agents_per_run_cap)
        gate = RunAgentAdmission(
            per_run_sem=per_run_sem,
            global_sem=self._global_sem,
        )
        return WorkflowAdmission(ticket=ticket, agent_gate=gate)

    async def release_workflow(self, ticket: int) -> None:
        """L1 release: drop the ticket (idempotent; unknown ticket is a no-op)."""
        async with self._lock:
            self._active_tickets.discard(ticket)

    def snapshot(self) -> ConcurrencySnapshot:
        """Return a read-only view of governor state."""
        return ConcurrencySnapshot(
            active_workflows=len(self._active_tickets),
            max_workflows=self._limits.max_workflows,
            max_agents_total=self._limits.max_agents_total,
            agents_per_run=self._limits.agents_per_run,
        )


__all__ = [
    "ConcurrencyGovernor",
    "ConcurrencyLimits",
    "ConcurrencySnapshot",
    "RunAgentAdmission",
    "WorkflowAdmission",
    "validate_swarmflow_concurrency",
]
