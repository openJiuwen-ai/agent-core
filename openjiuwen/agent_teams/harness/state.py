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
    from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
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
    """Snapshot captured at a task-loop round boundary (AFTER_TASK_ITERATION),
    or the pre-round baseline taken before a round starts.

    Attributes:
        context_messages: Immutable copy of the current-round context messages
            (``with_history=False`` segment).
        deep_agent_state: Result of ``DeepAgentState.to_session_dict()``
            (task_plan / iteration / stop_condition_state / pending_follow_ups
            / plan_mode).
        iteration_index: 0 for the pre-round baseline; 1-based index of the
            round just completed otherwise.
    """

    context_messages: tuple["BaseMessage", ...]
    deep_agent_state: dict
    iteration_index: int


@dataclass(slots=True)
class InboxMessage:
    """A single inbound message awaiting supervisor handling.

    Attributes:
        seq: Monotonically increasing sequence number; defines global FIFO.
        content: Raw user content. An ``InteractiveInput`` carries an interrupt
            resume; the supervisor starts a single-round resume for it.
        immediate: When True, inject into the active round's steering channel;
            when False, buffer until the active round finishes.
    """

    seq: int
    content: "str | InteractiveInput"
    immediate: bool


@dataclass(slots=True)
class ActiveRound:
    """An in-flight task-loop outer round driven by the supervisor.

    The round task runs ``NativeHarness._run_round``, which submits the round
    to the TaskLoopController and awaits its completion; the real
    ``react_agent.invoke`` runs inside the controller's TaskScheduler under
    ``task_id``.

    Attributes:
        round_id: Monotonically increasing round identifier (harness-internal).
        task_id: Scheduler task id passed into ``submit_round`` so immediate
            abort/pause can target it via ``task_scheduler.cancel_task``.
        original_query: The query that started this round (used by pause to
            cache and by send-while-paused to concatenate). An
            ``InteractiveInput`` marks a single-round interrupt resume, which
            ``_on_round_done`` settles to IDLE rather than continuing the task
            plan with the resume payload.
        deep_agent: Reference to the owning DeepAgent (the harness itself; the
            SnapshotRail reads context/state through it).
        task: The asyncio.Task running ``NativeHarness._run_round``.
        steering_queue: Pushed by ``send(immediate=True)``; reaches the inner
            ReAct loop via the round's ``submit_round`` steering wiring.
        graceful_abort: When True, the round is finishing under a graceful
            abort; ``_on_round_done`` must not auto-start a next round.
        failure_retry: When True, this round is the one-shot retry of a round
            that died abnormally (crashed, or was killed by a completion
            timeout). Its inbound query was already marked read at deliver
            time, so without a retry the message would be silently lost. A
            second abnormal death gives up (goes IDLE with a warning) instead
            of retrying again, so a deterministic failure cannot loop forever.
        pre_round_snapshot: Snapshot taken just before the round started
            (index 0). pause (which discards the whole round to restart with a
            merged query) and immediate abort with no completed round roll back
            to this.
        last_safe_snapshot: Most recent snapshot captured by SnapshotRail at a
            completed round iteration (AFTER_TASK_ITERATION). None until the
            first completes. immediate abort rolls back to this.
    """

    round_id: int
    task_id: str
    original_query: "str | InteractiveInput"
    deep_agent: "DeepAgent"
    task: asyncio.Task
    steering_queue: asyncio.Queue
    graceful_abort: bool = False
    failure_retry: bool = False
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
