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

    ``PAUSING`` is a transient phase entered when a pause is requested during
    the tool phase of an iteration: the inner loop finishes the current
    iteration cooperatively, then ``_on_round_done`` settles it to ``PAUSED``.
    """

    IDLE = "idle"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    TERMINATED = "terminated"


class RoundPhase(str, Enum):
    """Sub-iteration phase of the inner ReAct loop within an active round.

    Tracks where the inner loop currently is so pause/abort can pick the right
    stop strategy: interrupt a parked model call (safe to hard-cancel and
    rewind to the previous iteration boundary), versus let a running tool
    finish (side effects are irreversible, so the iteration must complete).
    Maintained by ``PhaseSnapshotRail`` on the inner-loop model/tool callbacks.
    """

    BOUNDARY = "boundary"  # between iterations: no model call, no tool running
    MODEL = "model"  # inside a model call, no tool_calls committed yet
    TOOL = "tool"  # executing tool calls for the current iteration


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
            (index 0). pause/abort roll back to this only when no inner
            iteration has completed yet (``last_iter_snapshot`` is None).
        last_safe_snapshot: Most recent snapshot captured at an outer round
            boundary (AFTER_TASK_ITERATION). None until the first completes.
        iter_phase: Where the inner ReAct loop currently is (BOUNDARY / MODEL /
            TOOL). Maintained by PhaseSnapshotRail; read by pause/abort to pick
            the stop strategy.
        model_call_in_flight: True while the exec task is parked in a model
            call (between before_model_call and the AssistantMessage write).
            This is the only window where a hard-cancel is safe (it lands in
            the LLM await, never in a running tool).
        tool_started: True once the current iteration has begun executing tool
            calls (side effects may have happened). Reset at the iteration
            boundary.
        pause_requested: Set by ``_on_pause`` to arm the cooperative stop; read
            by PhaseSnapshotRail at the model-call boundaries to force_finish
            the inner loop, and by ``_on_round_done`` to settle to PAUSED.
        last_iter_snapshot: Most recent snapshot captured at an inner ReAct
            iteration boundary (AFTER_REACT_ITERATION). Primary rollback target
            for pause/abort — the nearest clean boundary.
        pause_ack: Deferred ack Future for a cooperative (tool-phase) pause,
            resolved by ``_on_round_done`` once the round settles to PAUSED.
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
    iter_phase: RoundPhase = RoundPhase.BOUNDARY
    model_call_in_flight: bool = False
    tool_started: bool = False
    pause_requested: bool = False
    last_iter_snapshot: SafeStateSnapshot | None = None
    pause_ack: asyncio.Future | None = None


@dataclass(slots=True)
class HarnessInternalState:
    """Single source of truth mutated by the supervisor coroutine.

    Attributes:
        phase: Current lifecycle phase.
        pending_queue: FIFO buffer of ``immediate=False`` messages waiting
            for the active round to finish.
        seq_counter: Source of monotonic InboxMessage sequence numbers.
        active: Currently running round, or None when IDLE/PAUSED/TERMINATED.
        paused_query: When PAUSED, the originating query of the paused round.
            ``resume()`` hands it to the continuation round as its
            ``original_query`` so a task-plan continuation can still reuse it;
            the continuation itself appends no user turn (context is preserved).
            None when the paused round was an InteractiveInput resume.
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
