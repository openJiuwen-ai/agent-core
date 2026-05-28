# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SuperHarness: concurrent-safe external interaction layer over DeepAgent.

Replaces the dual ``steer / follow_up / abort`` + ``runner.run_agent_streaming``
contract with a single-coroutine state machine driven by a control channel.

Public API surface (all methods are concurrent-safe):
- ``start(session=None)``: lazily initialize DeepAgent + supervisor.
- ``stop()``: cancel any active round, close output, transition to TERMINATED.
- ``outputs()``: queue-backed AsyncIterator of OutputSchema chunks.
- ``send(content, immediate=False)``: queue inbound content.
- ``abort(immediate=False)``: graceful (iteration-granular) or immediate
  (task cancel + rollback) abort of the current round.
- ``pause()``: cancel current round, cache its query for the next send to
  concatenate onto.

State transitions and intermediate-state behavior are documented in the
plan at ``/Users/alan/.claude/plans/openjiuwen-harness-deep-agent-py-deepag-eager-whale.md``.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable, TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.harness.schema.state import DeepAgentState
from openjiuwen.harness.super_harness.control import (
    ControlEvent,
    _Cmd_Abort,
    _Cmd_Pause,
    _Cmd_RoundFinished,
    _Cmd_Send,
    _Cmd_Stop,
)
from openjiuwen.harness.super_harness.outputs import _END, _OutputIterator
from openjiuwen.harness.super_harness.snapshot_rail import (
    _ACTIVE_ROUND,
    SnapshotRail,
)
from openjiuwen.harness.super_harness.state import (
    ActiveRound,
    HarnessInternalState,
    HarnessState,
    InboxMessage,
    SafeStateSnapshot,
)

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


class SuperHarness:
    """Concurrent-safe multi-round interaction wrapper over a DeepAgent.

    The DeepAgent instance is produced lazily by ``deep_agent_provider`` on
    the first ``start()`` call and cached for the harness's lifetime. The
    harness owns one ``Session`` (auto-created or injected) which it reuses
    across all rounds.

    All external API methods push a ``ControlEvent`` onto an internal channel;
    the supervisor coroutine consumes events serially, mutating
    ``HarnessInternalState`` as the sole writer. This is how concurrency
    safety is achieved without locks: external callers cannot observe a
    half-transitioned state.
    """

    __slots__ = (
        "_provider",
        "_agent",
        "_session",
        "_owns_session",
        "_st",
        "_control",
        "_snapshot_rail",
        "_rail_registered",
        "_started_event",
        "_terminate_event",
    )

    def __init__(self, deep_agent_provider: Callable[[], "DeepAgent"]) -> None:
        """Initialize a SuperHarness over a DeepAgent provider.

        Args:
            deep_agent_provider: Zero-arg callable producing a configured
                DeepAgent. Invoked exactly once on the first ``start()``;
                the result is cached for the harness's lifetime.
        """
        self._provider = deep_agent_provider
        self._agent: "DeepAgent | None" = None
        self._session: Session | None = None
        self._owns_session: bool = False
        self._st = HarnessInternalState()
        self._control: asyncio.Queue[ControlEvent] = asyncio.Queue()
        self._snapshot_rail = SnapshotRail()
        self._rail_registered: bool = False
        self._started_event = asyncio.Event()
        self._terminate_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def state(self) -> HarnessState:
        """Current lifecycle phase."""
        return self._st.phase

    @property
    def session_id(self) -> str | None:
        """Owned (or injected) session id, or None before ``start()``."""
        return self._session.get_session_id() if self._session is not None else None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, *, session: Session | None = None) -> None:
        """Lazily initialize the DeepAgent and start the supervisor coroutine.

        Idempotent: calling ``start()`` a second time is a no-op.

        Args:
            session: Optional externally-managed session to reuse across
                rounds. When omitted, the harness creates its own session
                and is responsible for ``post_run`` at ``stop()``.
        """
        if self._st.supervisor_task is not None:
            return

        self._agent = self._provider()
        if self._agent is None:
            raise_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="deep_agent_provider returned None.",
            )

        if session is None:
            self._session = Session(card=self._agent.card)
            self._owns_session = True
            await self._session.pre_run()
        else:
            self._session = session
            self._owns_session = False

        # Ensure DeepAgent's lazy init has run (MCPs, workspace, rails, ...).
        await self._agent.ensure_initialized()

        # Register SnapshotRail's callbacks directly onto the inner ReActAgent.
        # We bypass DeepAgent.register_rail to avoid touching _BRIDGE_EVENTS:
        # SuperHarness drives react_agent.stream() directly, so rail bridging
        # routes (outer DeepAgent vs inner ReActAgent) don't apply here.
        await self._register_snapshot_rail()

        self._st.supervisor_task = asyncio.create_task(
            self._supervisor(),
            name=f"super_harness_supervisor[{self.session_id}]",
        )
        await self._started_event.wait()
        logger.info("[SuperHarness] started session=%s", self.session_id)

    async def stop(self) -> None:
        """Cancel any active round, close outputs, transition to TERMINATED.

        Safe to call multiple times. Blocks until the supervisor has finished
        cleanup.
        """
        if self._st.supervisor_task is None or self._st.phase is HarnessState.TERMINATED:
            return
        ack: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._control.put(_Cmd_Stop(ack=ack))
        await ack

        # Wait for supervisor to finish, then release the owned session.
        if self._st.supervisor_task is not None:
            try:
                await self._st.supervisor_task
            except asyncio.CancelledError:
                pass

        if self._owns_session and self._session is not None:
            try:
                await self._session.post_run()
            except Exception:
                logger.exception("[SuperHarness] session.post_run failed")
        logger.info("[SuperHarness] stopped session=%s", self.session_id)

    def outputs(self) -> AsyncIterator[Any]:
        """Return an AsyncIterator over output chunks.

        Single-consumer contract: multiple concurrent iterators steal items
        from each other. Wrap externally if broadcast semantics are needed.
        Iterator terminates cleanly when ``stop()`` is called.
        """
        return _OutputIterator(self._st.output_queue)

    # ------------------------------------------------------------------
    # External API: send / abort / pause
    # ------------------------------------------------------------------

    async def send(self, content: str, *, immediate: bool = False) -> str:
        """Push an inbound message to the supervisor.

        Behavior depends on current phase:
        - IDLE: starts a new round with ``content``.
        - RUNNING + immediate=True: injected into the active round's
          steering channel; takes effect at the next ReAct iteration top.
        - RUNNING + immediate=False: buffered in pending_queue; consumed
          after the active round finishes.
        - PAUSED: ``immediate`` is ignored. ``content`` is concatenated onto
          ``paused_query`` and the resulting query starts a new round.
        - TERMINATED: raises.

        Args:
            content: Raw user content.
            immediate: See above; ignored when PAUSED.

        Returns:
            The monotonic sequence id of this message.
        """
        self._require_alive()
        ack: asyncio.Future = asyncio.get_event_loop().create_future()
        # seq is assigned by the supervisor under the single-writer invariant.
        msg = InboxMessage(seq=0, content=content, immediate=immediate)
        await self._control.put(_Cmd_Send(msg=msg, ack=ack))
        return await ack

    async def abort(self, *, immediate: bool = False) -> None:
        """Abort the current round.

        - immediate=False (graceful): set the active round's graceful_abort
          flag. The current iteration runs to completion (LLM + all tools +
          ToolMessage writes); the next iteration top breaks the loop.
        - immediate=True: cancel the active round task, drop the pending
          queue, roll context state back to ``last_safe_snapshot``.

        Args:
            immediate: Whether to cancel immediately (True) or let the
                current iteration finish (False).
        """
        self._require_alive()
        ack: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._control.put(_Cmd_Abort(immediate=immediate, ack=ack))
        await ack

    async def pause(self) -> None:
        """Stop the current round and cache its query for the next send.

        After pause(), the harness enters PAUSED. The next send() — regardless
        of ``immediate`` — concatenates onto the cached query and restarts
        the same round with the combined content. Tool side effects from the
        cancelled iteration are not rolled back.
        """
        self._require_alive()
        ack: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._control.put(_Cmd_Pause(ack=ack))
        await ack

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_alive(self) -> None:
        """Raise if the harness has been stopped."""
        if self._st.phase is HarnessState.TERMINATED:
            raise_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="SuperHarness already stopped.",
            )
        if self._st.supervisor_task is None:
            raise_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="SuperHarness not started. Call start() first.",
            )

    async def _register_snapshot_rail(self) -> None:
        """Register SnapshotRail callbacks onto the inner ReActAgent.

        Called once on the first ``start()``. Idempotent for safety.
        """
        if self._rail_registered:
            return
        react = self._agent.react_agent
        if react is None:
            raise_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent has no inner ReActAgent.",
            )
        callbacks = self._snapshot_rail.get_callbacks()
        for event, callback in callbacks.items():
            await react.register_callback(
                event,
                callback,
                self._snapshot_rail.priority,
            )
        self._rail_registered = True

    # ------------------------------------------------------------------
    # Supervisor main loop
    # ------------------------------------------------------------------

    async def _supervisor(self) -> None:
        """Main supervisor coroutine; single writer to HarnessInternalState."""
        self._started_event.set()
        try:
            while self._st.phase is not HarnessState.TERMINATED:
                cmd = await self._control.get()
                if isinstance(cmd, _Cmd_Send):
                    await self._on_send(cmd)
                elif isinstance(cmd, _Cmd_Abort):
                    await self._on_abort(cmd)
                elif isinstance(cmd, _Cmd_Pause):
                    await self._on_pause(cmd)
                elif isinstance(cmd, _Cmd_RoundFinished):
                    await self._on_round_done(cmd)
                elif isinstance(cmd, _Cmd_Stop):
                    await self._on_stop(cmd)
                    break
                else:  # pragma: no cover - defensive
                    logger.warning("[SuperHarness] unknown control event: %r", cmd)
        except Exception:
            logger.exception("[SuperHarness] supervisor crashed")
            self._st.phase = HarnessState.TERMINATED
            await self._st.output_queue.put(_END)

    # ------------------------------------------------------------------
    # Event handlers (single-writer, serialized by supervisor)
    # ------------------------------------------------------------------

    async def _on_send(self, cmd: _Cmd_Send) -> None:
        """Route a send according to current phase."""
        seq = self._st.next_seq()
        # Replace the placeholder with the assigned seq.
        msg = InboxMessage(seq=seq, content=cmd.msg.content, immediate=cmd.msg.immediate)

        phase = self._st.phase
        if phase is HarnessState.IDLE:
            self._start_round(msg.content)
            self._transition(HarnessState.RUNNING)
        elif phase is HarnessState.RUNNING:
            if msg.immediate:
                active = self._st.active
                if active is not None:
                    active.steering_queue.put_nowait(msg.content)
                else:
                    # Race: round just finished. Fall back to next_round.
                    self._st.pending_queue.append(msg)
            else:
                self._st.pending_queue.append(msg)
        elif phase is HarnessState.PAUSED:
            # PAUSED: ignore immediate flag; concatenate onto paused_query
            # and resume by starting a new round.
            base = self._st.paused_query or ""
            merged = f"{base}\n{msg.content}" if base else msg.content
            self._st.paused_query = None
            self._start_round(merged)
            self._transition(HarnessState.RUNNING)
        elif phase is HarnessState.TERMINATED:
            cmd.ack.set_exception(
                RuntimeError("SuperHarness already stopped."),
            )
            return
        cmd.ack.set_result(seq)

    async def _on_abort(self, cmd: _Cmd_Abort) -> None:
        """Handle graceful or immediate abort."""
        phase = self._st.phase
        if phase is HarnessState.IDLE:
            # Nothing to abort.
            cmd.ack.set_result(None)
            return
        if phase is HarnessState.PAUSED:
            # Drop cached query + pending queue.
            self._st.paused_query = None
            self._st.pending_queue.clear()
            self._transition(HarnessState.IDLE)
            cmd.ack.set_result(None)
            return

        # phase is RUNNING
        active = self._st.active
        if active is None:
            self._transition(HarnessState.IDLE)
            cmd.ack.set_result(None)
            return

        if cmd.immediate:
            await self._cancel_round(active)
            await self._rollback_to_snapshot(active.last_safe_snapshot)
            self._st.active = None
            self._st.pending_queue.clear()
            self._transition(HarnessState.IDLE)
        else:
            active.graceful_abort = True
            # Drop pending so we don't auto-start a next round after this one.
            self._st.pending_queue.clear()
        cmd.ack.set_result(None)

    async def _on_pause(self, cmd: _Cmd_Pause) -> None:
        """Cancel current round and cache its query for the next send."""
        phase = self._st.phase
        if phase is not HarnessState.RUNNING:
            cmd.ack.set_result(None)
            return

        active = self._st.active
        if active is None:
            self._transition(HarnessState.PAUSED)
            cmd.ack.set_result(None)
            return

        cached_query = active.original_query
        await self._cancel_round(active)
        await self._rollback_to_snapshot(active.last_safe_snapshot)
        self._st.active = None
        self._st.paused_query = cached_query
        # Keep pending_queue intact across pause: items will be consumed
        # whenever the next send re-enters RUNNING.
        self._transition(HarnessState.PAUSED)
        cmd.ack.set_result(None)

    async def _on_round_done(self, cmd: _Cmd_RoundFinished) -> None:
        """Round finished naturally (success or graceful break)."""
        active = self._st.active
        if active is None or active.round_id != cmd.round_id:
            # Already handled by abort/pause path.
            return
        self._st.active = None

        if cmd.error is not None and not isinstance(
            cmd.error, asyncio.CancelledError,
        ):
            logger.error(
                "[SuperHarness] round_id=%s ended with error: %r",
                cmd.round_id,
                cmd.error,
            )

        # Drain pending queue: pop FIFO, start next round.
        if self._st.phase is HarnessState.RUNNING and self._st.pending_queue:
            next_msg = self._st.pending_queue.popleft()
            self._start_round(next_msg.content)
            return

        # Nothing pending: back to IDLE.
        self._transition(HarnessState.IDLE)

    async def _on_stop(self, cmd: _Cmd_Stop) -> None:
        """Terminal cleanup: cancel active round, close outputs."""
        active = self._st.active
        if active is not None:
            await self._cancel_round(active)
            self._st.active = None
        self._st.pending_queue.clear()
        self._st.paused_query = None
        self._transition(HarnessState.TERMINATED)
        await self._st.output_queue.put(_END)
        cmd.ack.set_result(None)

    # ------------------------------------------------------------------
    # Round lifecycle
    # ------------------------------------------------------------------

    def _start_round(self, query: str) -> ActiveRound:
        """Create an ActiveRound and schedule its asyncio.Task.

        The task runs in a copied context where ``_ACTIVE_ROUND`` is set to
        the new round, so SnapshotRail can locate the round during ReAct
        hook callbacks without explicit threading.
        """
        round_id = self._st.next_round_id()
        steering_queue: asyncio.Queue[str] = asyncio.Queue()

        # Build the ActiveRound shell before the task starts so we can refer
        # to it from inside the task. The task field is set right after.
        # Use a sentinel future-like placeholder for task until create_task.
        # We construct ActiveRound first, then assign .task once the Task is
        # created.
        active = ActiveRound(
            round_id=round_id,
            original_query=query,
            deep_agent=self._agent,
            task=None,  # type: ignore[arg-type]  # filled in below
            steering_queue=steering_queue,
        )

        async def _runner() -> None:
            _ACTIVE_ROUND.set(active)
            await self._run_round(active)

        task = asyncio.create_task(
            _runner(),
            name=f"super_harness_round[{round_id}]",
        )
        active.task = task
        self._st.active = active
        logger.info(
            "[SuperHarness] round_id=%s started query=%r",
            round_id,
            query[:120],
        )
        return active

    async def _run_round(self, active: ActiveRound) -> None:
        """Drive the inner ReActAgent.stream() and forward chunks.

        Pushes a _Cmd_RoundFinished to the control channel on completion or
        on any exception (including CancelledError for immediate abort/pause).
        """
        error: BaseException | None = None
        try:
            inputs = {"query": active.original_query}
            async for chunk in self._agent.react_agent.stream(
                inputs,
                self._session,
            ):
                await self._st.output_queue.put(chunk)
        except asyncio.CancelledError:
            # Immediate abort or pause path. Do not re-raise into supervisor:
            # we still want to deliver the RoundFinished signal so the
            # supervisor can transition cleanly.
            logger.info(
                "[SuperHarness] round_id=%s cancelled",
                active.round_id,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - reported via control channel
            logger.exception(
                "[SuperHarness] round_id=%s crashed",
                active.round_id,
            )
            error = exc
        finally:
            # Always notify the supervisor. For CancelledError this runs in
            # the finally block before the exception re-raises.
            await self._control.put(
                _Cmd_RoundFinished(round_id=active.round_id, error=error),
            )

    async def _cancel_round(self, active: ActiveRound) -> None:
        """Cancel the given round's task and await its termination."""
        if active.task is None or active.task.done():
            return
        active.task.cancel()
        try:
            await active.task
        except (asyncio.CancelledError, Exception):
            # Already logged in _run_round; swallow here so the supervisor
            # continues without escalating cancellation upstream.
            pass

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    async def _rollback_to_snapshot(
        self,
        snapshot: SafeStateSnapshot | None,
    ) -> None:
        """Restore context messages + DeepAgentState to the given snapshot.

        When ``snapshot`` is None (first iteration aborted before any
        snapshot was captured), fall back to ``clear_context_messages``
        which preserves chat history but drops the in-progress round.
        """
        if self._session is None or self._agent is None:
            return
        react = self._agent.react_agent
        context = react.context_engine.get_context(
            session_id=self._session.get_session_id(),
        )

        if snapshot is not None and context is not None:
            context.set_messages(list(snapshot.context_messages), with_history=False)
            try:
                restored_state = DeepAgentState.from_session_dict(snapshot.deep_agent_state)
            except Exception:
                logger.exception(
                    "[SuperHarness] failed to deserialize DeepAgentState; "
                    "skipping state rollback",
                )
            else:
                self._agent.save_state(self._session, restored_state)
            logger.info(
                "[SuperHarness] rolled back to iteration=%s msgs=%s",
                snapshot.iteration_index,
                len(snapshot.context_messages),
            )
        else:
            try:
                await react.clear_context_messages(
                    session_id=self._session.get_session_id(),
                )
            except Exception:
                logger.exception(
                    "[SuperHarness] clear_context_messages failed during rollback",
                )
            logger.info(
                "[SuperHarness] no safe snapshot; cleared current-round messages",
            )

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _transition(self, new_phase: HarnessState) -> None:
        """Update phase with logging; single-writer invariant assumed."""
        if self._st.phase is new_phase:
            return
        logger.info(
            "[SuperHarness] phase %s -> %s",
            self._st.phase.value,
            new_phase.value,
        )
        self._st.phase = new_phase
