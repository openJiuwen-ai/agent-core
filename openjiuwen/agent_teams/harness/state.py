# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Internal state types for NativeHarness.

All mutable state lives in HarnessInternalState; the supervisor coroutine
is the sole writer. External API methods only push ControlEvent objects.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm import BaseMessage
    from openjiuwen.harness.deep_agent import DeepAgent


class HarnessState(str, Enum):
    """High-level lifecycle phase for NativeHarness.

    Transitions are documented in the NativeHarness state-transition table.
    Only the supervisor coroutine mutates ``HarnessInternalState.phase``.
    """

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    TERMINATED = "terminated"


@dataclass(frozen=True, slots=True)
class SafeStateSnapshot:
    """Snapshot captured at the end of a fully successful ReAct iteration.

    Attributes:
        context_messages: Immutable copy of the ReActAgent context messages.
        deep_agent_state: Result of ``DeepAgentState.to_session_dict()``.
        iteration_index: 1-based index of the iteration just completed.
    """

    context_messages: tuple["BaseMessage", ...]
    deep_agent_state: dict
    iteration_index: int


@dataclass(slots=True)
class InboxMessage:
    """A single inbound message awaiting supervisor handling.

    Attributes:
        seq: Monotonically increasing sequence number; defines global FIFO.
        content: Raw user content.
        immediate: When True, inject into the active round's steering channel;
            when False, buffer until the active round finishes.
    """

    seq: int
    content: str
    immediate: bool


@dataclass(slots=True)
class ActiveRound:
    """An in-flight ReActAgent.stream() invocation.

    Attributes:
        round_id: Monotonically increasing round identifier.
        original_query: The query that started this round (used by pause to
            cache and by send-while-paused to concatenate).
        deep_agent: Reference to the owning DeepAgent. SnapshotRail reads
            this when the AFTER_REACT_ITERATION ctx points at the inner
            ReActAgent (ctx.agent != DeepAgent in that hook).
        task: The asyncio.Task running ``NativeHarness._run_round``.
        steering_queue: Bound into the ReActAgent ctx via
            ``ctx.bind_steering_queue``; NativeHarness pushes here for
            immediate=True sends.
        graceful_abort: When True, SnapshotRail.after_react_iteration will
            ``request_force_finish`` so the next iteration top-of-loop check
            breaks the inner loop cleanly.
        pre_round_snapshot: Snapshot of context+state taken just before the
            round's query was added. pause (which discards the whole round to
            restart with a merged query) and immediate abort with no completed
            iteration roll back to this.
        last_safe_snapshot: Most recent snapshot captured by SnapshotRail at
            the end of a fully successful iteration. None until the first
            iteration completes. immediate abort rolls back to this.
    """

    round_id: int
    original_query: str
    deep_agent: "DeepAgent"
    task: asyncio.Task
    steering_queue: asyncio.Queue
    graceful_abort: bool = False
    pre_round_snapshot: SafeStateSnapshot | None = None
    last_safe_snapshot: SafeStateSnapshot | None = None


@dataclass(slots=True)
class HarnessInternalState:
    """Single source of truth mutated by the supervisor coroutine.

    Attributes:
        phase: Current lifecycle phase.
        pending_queue: FIFO buffer of ``immediate=False`` messages waiting
            for the active round to finish.
        seq_counter: Source of monotonic InboxMessage sequence numbers.
        active: Currently running round, or None when IDLE/PAUSED/TERMINATED.
        paused_query: When PAUSED, the original query of the round that was
            cancelled by pause(); the next send concatenates onto this.
        output_queue: chunk forwarder target consumed by ``outputs()``.
        supervisor_task: The asyncio.Task running ``_supervisor``.
        round_id_counter: Source of monotonic ActiveRound ids.
    """

    phase: HarnessState = HarnessState.IDLE
    pending_queue: deque[InboxMessage] = field(default_factory=deque)
    seq_counter: int = 0
    active: ActiveRound | None = None
    paused_query: str | None = None
    output_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    supervisor_task: asyncio.Task | None = None
    round_id_counter: int = 0

    def next_seq(self) -> int:
        """Return and increment the InboxMessage sequence counter."""
        self.seq_counter += 1
        return self.seq_counter

    def next_round_id(self) -> int:
        """Return and increment the ActiveRound id counter."""
        self.round_id_counter += 1
        return self.round_id_counter
