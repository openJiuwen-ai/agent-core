# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness: concurrent-safe external interaction layer over DeepAgent.

Replaces the dual ``steer / follow_up / abort`` + ``runner.run_agent_streaming``
contract with a single-coroutine state machine driven by a control channel.

Round execution model (mirrors DeepAgent's task-loop streaming, not its
one-shot ``stream()``): the whole harness shares ONE stream lifecycle. Each
round drives ``react_agent.invoke(_streaming=True)`` as a task NativeHarness
owns — so cancellation actually stops the LLM/tool work, and because the
session is passed in (``need_cleanup=False``) invoke does not close the shared
stream emitter between rounds. A single forwarder coroutine pumps
``session.stream_iterator()`` into the output queue for the harness's entire
life; the stream is closed only by ``stop()``.

Public API surface (all methods are concurrent-safe — each just enqueues a
ControlEvent and awaits an ack resolved solely by the supervisor coroutine):
- ``start(session=None)``: lazily initialize DeepAgent + supervisor + forwarder.
- ``stop()``: cancel any active round, close the stream, transition TERMINATED.
- ``outputs()``: queue-backed AsyncIterator of OutputSchema chunks (single
  consumer).
- ``send(content, immediate=False)``: queue inbound content.
- ``abort(immediate=False)``: graceful (iteration-granular) or immediate
  (task cancel + rollback to last safe snapshot) abort of the current round.
- ``pause()``: cancel the current round, roll back to its pre-round baseline,
  and cache its query so the next send concatenates and restarts it.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable, TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, raise_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.harness.schema.state import DeepAgentState
from openjiuwen.agent_teams.harness.control import (
    _Cmd_Abort,
    _Cmd_Pause,
    _Cmd_RoundFinished,
    _Cmd_Send,
    _Cmd_Stop,
)
from openjiuwen.agent_teams.harness.outputs import _END, _OutputIterator
from openjiuwen.agent_teams.harness.snapshot_rail import (
    _ACTIVE_ROUND,
    SnapshotRail,
    capture_snapshot,
)
from openjiuwen.agent_teams.harness.state import (
    ActiveRound,
    HarnessInternalState,
    HarnessState,
    InboxMessage,
    SafeStateSnapshot,
)

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


class NativeHarness:
    """Concurrent-safe multi-round interaction wrapper over a DeepAgent.

    The DeepAgent instance is produced lazily by ``deep_agent_provider`` on
    the first ``start()`` call and cached for the harness's lifetime. The
    harness owns one ``Session`` (auto-created or injected) reused across all
    rounds.

    All external API methods push a ``ControlEvent`` onto an internal channel;
    the supervisor coroutine consumes events serially, mutating
    ``HarnessInternalState`` as the sole writer. Concurrency safety is by
    construction: external callers never observe a half-transitioned state.
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
        "_forwarder_task",
        "_started_event",
        "_starting",
    )

    def __init__(self, deep_agent_provider: Callable[[], "DeepAgent"]) -> None:
        """Initialize a NativeHarness over a DeepAgent provider.

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
        self._control: asyncio.Queue = asyncio.Queue()
        self._snapshot_rail = SnapshotRail()
        self._rail_registered: bool = False
        self._forwarder_task: asyncio.Task | None = None
        self._started_event = asyncio.Event()
        self._starting: bool = False

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
        """Lazily initialize the DeepAgent and start the supervisor + forwarder.

        Idempotent and safe against concurrent calls: a ``_starting`` guard
        plus the post-init ``supervisor_task`` check ensure only one caller
        performs initialization.

        Args:
            session: Optional externally-managed session to reuse across
                rounds. When omitted, the harness creates its own session and
                runs ``post_run`` on it at ``stop()``.
        """
        if self._st.supervisor_task is not None or self._starting:
            return
        self._starting = True
        try:
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

            await self._agent.ensure_initialized()
            await self._register_snapshot_rail()

            self._st.supervisor_task = asyncio.create_task(
                self._supervisor(),
                name=f"native_harness_supervisor[{self.session_id}]",
            )
            self._forwarder_task = asyncio.create_task(
                self._forward_outputs(),
                name=f"native_harness_forwarder[{self.session_id}]",
            )
            await self._started_event.wait()
            logger.info("[NativeHarness] started session=%s", self.session_id)
        finally:
            self._starting = False

    async def stop(self) -> None:
        """Cancel any active round, close outputs, transition to TERMINATED.

        Safe to call multiple times. Blocks until the supervisor and forwarder
        have finished and the owned session (if any) is torn down.
        """
        if self._st.supervisor_task is None or self._st.phase is HarnessState.TERMINATED:
            return
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._control.put(_Cmd_Stop(ack=ack))
        try:
            await ack
        except Exception:
            # Supervisor crashed before acking; proceed with teardown anyway.
            logger.debug("[NativeHarness] stop ack rejected", exc_info=True)

        supervisor = self._st.supervisor_task
        if supervisor is not None:
            try:
                await supervisor
            except asyncio.CancelledError:
                pass

        # Closing the stream sends END_FRAME, which lets the forwarder's
        # stream_iterator terminate and push the _END sentinel.
        if self._session is not None:
            try:
                await self._session.close_stream()
            except Exception:
                logger.exception("[NativeHarness] close_stream failed during stop")
        if self._forwarder_task is not None:
            try:
                await self._forwarder_task
            except asyncio.CancelledError:
                pass

        if self._owns_session and self._session is not None:
            try:
                await self._session.post_run()
            except Exception:
                logger.exception("[NativeHarness] session.post_run failed")
        logger.info("[NativeHarness] stopped session=%s", self.session_id)

    def outputs(self) -> AsyncIterator[Any]:
        """Return an AsyncIterator over output chunks.

        Single-consumer contract: the terminating ``_END`` sentinel is emitted
        exactly once, so only one iterator can drain to completion. Wrap
        externally if broadcast or reconnect semantics are needed. The iterator
        ends cleanly after ``stop()`` closes the stream.
        """
        return _OutputIterator(self._st.output_queue)

    # ------------------------------------------------------------------
    # External API: send / abort / pause
    # ------------------------------------------------------------------

    async def send(self, content: str, *, immediate: bool = False) -> str:
        """Push an inbound message to the supervisor.

        Behavior by phase:
        - IDLE: starts a new round with ``content``.
        - RUNNING + immediate=True: injected into the active round's steering
          channel; takes effect at the next ReAct iteration top.
        - RUNNING + immediate=False: buffered; consumed when the round finishes.
        - PAUSED: ``immediate`` is ignored. ``content`` is concatenated onto the
          cached query and the merged query starts a new round.
        - TERMINATED: raises.

        Args:
            content: Raw user content.
            immediate: See above; ignored when PAUSED.

        Returns:
            The monotonic sequence id of this message.
        """
        self._require_alive()
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        msg = InboxMessage(seq=0, content=content, immediate=immediate)
        await self._control.put(_Cmd_Send(msg=msg, ack=ack))
        return await ack

    async def abort(self, *, immediate: bool = False) -> None:
        """Abort the current round.

        - immediate=False (graceful): the current iteration runs to completion
          (LLM + all tools + ToolMessage writes); the next iteration top breaks
          the loop. No rollback.
        - immediate=True: cancel the round task, drop the pending queue, roll
          context+state back to the last safe snapshot (or the pre-round
          baseline if no iteration completed). Tool side effects already
          performed are NOT undone.

        Args:
            immediate: Cancel immediately (True) or let the current iteration
                finish (False).
        """
        self._require_alive()
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._control.put(_Cmd_Abort(immediate=immediate, ack=ack))
        await ack

    async def pause(self) -> None:
        """Stop the current round and cache its query for the next send.

        Cancels the round, rolls context+state back to the round's pre-round
        baseline (discarding the whole round), and enters PAUSED. The next
        send() — regardless of ``immediate`` — concatenates onto the cached
        query and restarts the round with the combined content. Tool side
        effects already performed are NOT undone.
        """
        self._require_alive()
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._control.put(_Cmd_Pause(ack=ack))
        await ack

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_alive(self) -> None:
        """Raise if the harness is stopped or not started."""
        if self._st.phase is HarnessState.TERMINATED:
            raise_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="NativeHarness already stopped.",
            )
        if self._st.supervisor_task is None:
            raise_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="NativeHarness not started. Call start() first.",
            )

    async def _register_snapshot_rail(self) -> None:
        """Register SnapshotRail's callbacks directly onto the inner ReActAgent.

        Bypasses DeepAgent.register_rail because NativeHarness drives
        react_agent.invoke directly; the inner/outer bridging routes do not
        apply here. Idempotent.
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
            await react.register_callback(event, callback, self._snapshot_rail.priority)
        self._rail_registered = True

    # ------------------------------------------------------------------
    # Supervisor main loop
    # ------------------------------------------------------------------

    async def _supervisor(self) -> None:
        """Main supervisor coroutine; sole writer of HarnessInternalState."""
        self._started_event.set()
        crashed_cmd: Any = None
        crash_exc: BaseException | None = None
        try:
            while self._st.phase is not HarnessState.TERMINATED:
                cmd = await self._control.get()
                if isinstance(cmd, _Cmd_Stop):
                    await self._on_stop(cmd)
                    break
                try:
                    await self._dispatch(cmd)
                except Exception as exc:  # noqa: BLE001 - handler crash is terminal
                    crashed_cmd = cmd
                    crash_exc = exc
                    raise
        except Exception:
            logger.exception("[NativeHarness] supervisor crashed; terminating")
            self._st.phase = HarnessState.TERMINATED
        finally:
            # Resolve every ack that will otherwise never be answered, so no
            # external caller hangs forever. Covers the crashed command and
            # anything queued behind it (including commands queued after Stop).
            self._fail_remaining_commands(crashed_cmd, crash_exc)
            if crash_exc is not None:
                # close_stream won't run on the crash path; unblock outputs().
                await self._st.output_queue.put(_END)

    async def _dispatch(self, cmd: Any) -> None:
        """Route a non-Stop control event to its handler."""
        if isinstance(cmd, _Cmd_Send):
            await self._on_send(cmd)
        elif isinstance(cmd, _Cmd_Abort):
            await self._on_abort(cmd)
        elif isinstance(cmd, _Cmd_Pause):
            await self._on_pause(cmd)
        elif isinstance(cmd, _Cmd_RoundFinished):
            await self._on_round_done(cmd)
        else:  # pragma: no cover - defensive
            logger.warning("[NativeHarness] unknown control event: %r", cmd)

    def _fail_remaining_commands(
        self,
        crashed_cmd: Any,
        crash_exc: BaseException | None,
    ) -> None:
        """Reject the acks of the crashed command and all queued commands.

        Without this, a supervisor that crashed (or stopped with commands still
        queued behind ``_Cmd_Stop``) would leave callers blocked on
        ``await ack`` forever.
        """
        err = crash_exc if crash_exc is not None else build_error(
            StatusCode.DEEPAGENT_RUNTIME_ERROR,
            error_msg="NativeHarness stopped before this command was handled.",
        )
        pending = []
        if crashed_cmd is not None:
            pending.append(crashed_cmd)
        while True:
            try:
                pending.append(self._control.get_nowait())
            except asyncio.QueueEmpty:
                break
        for cmd in pending:
            ack = getattr(cmd, "ack", None)
            if ack is not None and not ack.done():
                ack.set_exception(err)

    # ------------------------------------------------------------------
    # Event handlers (single-writer, serialized by supervisor)
    # ------------------------------------------------------------------

    async def _on_send(self, cmd: _Cmd_Send) -> None:
        """Route a send according to current phase."""
        seq = self._st.next_seq()
        msg = InboxMessage(seq=seq, content=cmd.msg.content, immediate=cmd.msg.immediate)

        phase = self._st.phase
        if phase is HarnessState.IDLE:
            self._start_round(msg.content)
            self._transition(HarnessState.RUNNING)
        elif phase is HarnessState.RUNNING:
            active = self._st.active
            if msg.immediate and active is not None:
                active.steering_queue.put_nowait(msg.content)
            else:
                self._st.pending_queue.append(msg)
        elif phase is HarnessState.PAUSED:
            base = self._st.paused_query or ""
            merged = f"{base}\n{msg.content}" if base else msg.content
            self._st.paused_query = None
            self._start_round(merged)
            self._transition(HarnessState.RUNNING)
        cmd.ack.set_result(seq)

    async def _on_abort(self, cmd: _Cmd_Abort) -> None:
        """Handle graceful or immediate abort."""
        phase = self._st.phase
        if phase is HarnessState.IDLE:
            cmd.ack.set_result(None)
            return
        if phase is HarnessState.PAUSED:
            self._st.paused_query = None
            self._st.pending_queue.clear()
            self._transition(HarnessState.IDLE)
            cmd.ack.set_result(None)
            return

        active = self._st.active
        if active is None:
            self._transition(HarnessState.IDLE)
            cmd.ack.set_result(None)
            return

        if cmd.immediate:
            await self._cancel_round(active)
            await self._rollback_to_snapshot(
                active.last_safe_snapshot or active.pre_round_snapshot,
            )
            await self._emit_round_aborted(active.round_id, "abort")
            self._st.active = None
            self._st.pending_queue.clear()
            self._transition(HarnessState.IDLE)
        else:
            # Graceful: let the current iteration finish; the round will end
            # itself and _on_round_done must not auto-start a next round.
            active.graceful_abort = True
            self._st.pending_queue.clear()
        cmd.ack.set_result(None)

    async def _on_pause(self, cmd: _Cmd_Pause) -> None:
        """Cancel current round, roll back to its pre-round baseline, cache query."""
        if self._st.phase is not HarnessState.RUNNING:
            cmd.ack.set_result(None)
            return

        active = self._st.active
        if active is None:
            self._transition(HarnessState.PAUSED)
            cmd.ack.set_result(None)
            return

        cached_query = active.original_query
        await self._cancel_round(active)
        # pause discards the whole round (it will restart with a merged query),
        # so roll back to the pre-round baseline, not the mid-round snapshot —
        # otherwise the restarted round's query duplicates the original.
        await self._rollback_to_snapshot(active.pre_round_snapshot)
        await self._emit_round_aborted(active.round_id, "pause")
        self._st.active = None
        self._st.paused_query = cached_query
        self._transition(HarnessState.PAUSED)
        cmd.ack.set_result(None)

    async def _on_round_done(self, cmd: _Cmd_RoundFinished) -> None:
        """Round finished naturally (success or graceful break)."""
        active = self._st.active
        if active is None or active.round_id != cmd.round_id:
            # Already superseded by an abort/pause that cancelled this round.
            return
        was_graceful = active.graceful_abort
        self._st.active = None

        if cmd.error is not None:
            logger.error(
                "[NativeHarness] round_id=%s ended with error: %r",
                cmd.round_id,
                cmd.error,
            )

        # Graceful abort: the round finished its final iteration; do not pull
        # the next pending message — the user asked to stop.
        if was_graceful:
            self._st.pending_queue.clear()
            self._transition(HarnessState.IDLE)
            return

        if self._st.phase is HarnessState.RUNNING and self._st.pending_queue:
            next_msg = self._st.pending_queue.popleft()
            self._start_round(next_msg.content)
            return

        self._transition(HarnessState.IDLE)

    async def _on_stop(self, cmd: _Cmd_Stop) -> None:
        """Terminal cleanup: cancel active round, transition TERMINATED.

        The output stream is closed by ``stop()`` after the supervisor exits;
        the forwarder then emits the ``_END`` sentinel.
        """
        active = self._st.active
        if active is not None:
            await self._cancel_round(active)
            self._st.active = None
        self._st.pending_queue.clear()
        self._st.paused_query = None
        self._transition(HarnessState.TERMINATED)
        cmd.ack.set_result(None)

    # ------------------------------------------------------------------
    # Round lifecycle
    # ------------------------------------------------------------------

    def _start_round(self, query: str) -> ActiveRound:
        """Create an ActiveRound (with a pre-round baseline snapshot) and schedule it.

        The round task sets ``_ACTIVE_ROUND`` to this round in its own context
        before driving invoke, so SnapshotRail locates it during ReAct hooks.
        """
        round_id = self._st.next_round_id()
        steering_queue: asyncio.Queue = asyncio.Queue()
        pre_round = capture_snapshot(self._agent, self._session, index=0)

        active = ActiveRound(
            round_id=round_id,
            original_query=query,
            deep_agent=self._agent,
            task=None,  # type: ignore[arg-type]  # assigned right after create_task
            steering_queue=steering_queue,
            pre_round_snapshot=pre_round,
        )

        async def _runner() -> None:
            _ACTIVE_ROUND.set(active)
            await self._run_round(active)

        task = asyncio.create_task(_runner(), name=f"native_harness_round[{round_id}]")
        active.task = task
        self._st.active = active
        logger.info(
            "[NativeHarness] round_id=%s started query=%r",
            round_id,
            query[:120],
        )
        return active

    async def _run_round(self, active: ActiveRound) -> None:
        """Drive react_agent.invoke(_streaming=True) for one round.

        invoke streams llm chunks into the session as it runs; the final result
        is written to the session afterward. Both reach the output queue via the
        forwarder. On cancellation (immediate abort / pause) the CancelledError
        propagates so invoke's own cleanup runs; a _Cmd_RoundFinished is always
        posted so the supervisor can transition.
        """
        error: BaseException | None = None
        try:
            inputs = {
                "query": active.original_query,
                "_steering_queue": active.steering_queue,
            }
            final_result = await self._agent.react_agent.invoke(
                inputs,
                self._session,
                _streaming=True,
            )
            # Stream the final result (llm token chunks were already streamed).
            if isinstance(final_result, list):
                for schema in final_result:
                    await self._session.write_stream(schema)
            else:
                await self._agent.react_agent.write_invoke_result_to_stream(
                    final_result,
                    self._session,
                )
        except asyncio.CancelledError:
            logger.info("[NativeHarness] round_id=%s cancelled", active.round_id)
            raise
        except Exception as exc:  # noqa: BLE001 - reported via control channel
            logger.exception("[NativeHarness] round_id=%s crashed", active.round_id)
            error = exc
        finally:
            await self._control.put(
                _Cmd_RoundFinished(round_id=active.round_id, error=error),
            )

    async def _cancel_round(self, active: ActiveRound) -> None:
        """Cancel a round task and await its termination.

        Swallows the round task's own CancelledError, but re-raises if the
        supervisor itself is being cancelled (so shutdown propagates and the
        supervisor never becomes un-cancellable).
        """
        task = active.task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling() > 0:
                raise
        except Exception:
            logger.debug(
                "[NativeHarness] round task raised during cancel",
                exc_info=True,
            )

    async def _forward_outputs(self) -> None:
        """Pump session stream chunks into the output queue for the harness life.

        Runs until ``stop()`` closes the session stream (END_FRAME ends the
        iterator), then emits the ``_END`` sentinel so ``outputs()`` terminates.
        """
        try:
            async for chunk in self._session.stream_iterator():
                await self._st.output_queue.put(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[NativeHarness] output forwarder crashed")
        finally:
            await self._st.output_queue.put(_END)

    async def _emit_round_aborted(self, round_id: int, kind: str) -> None:
        """Emit a marker chunk so consumers know prior chunks of this round are void.

        immediate abort / pause roll back internal state, but chunks already
        forwarded to the consumer cannot be recalled. This marker lets a
        consumer discard the aborted round's output.
        """
        await self._st.output_queue.put(
            OutputSchema(
                type="round_aborted",
                index=0,
                payload={"round_id": round_id, "kind": kind},
            ),
        )

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    async def _rollback_to_snapshot(self, snapshot: SafeStateSnapshot | None) -> None:
        """Restore context messages + DeepAgentState to the given snapshot.

        Both capture and restore use ``with_history=False`` so only the
        current-round message segment is rewound; the persisted history segment
        is preserved. When ``snapshot`` is None, fall back to
        ``clear_context_messages`` (drops the in-progress round, keeps history).
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
                    "[NativeHarness] failed to deserialize DeepAgentState; "
                    "skipping state rollback",
                )
            else:
                self._agent.save_state(self._session, restored_state)
            logger.info(
                "[NativeHarness] rolled back to iteration=%s msgs=%s",
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
                    "[NativeHarness] clear_context_messages failed during rollback",
                )
            logger.info("[NativeHarness] no snapshot; cleared current-round messages")

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _transition(self, new_phase: HarnessState) -> None:
        """Update phase with logging; single-writer invariant assumed."""
        if self._st.phase is new_phase:
            return
        logger.info(
            "[NativeHarness] phase %s -> %s",
            self._st.phase.value,
            new_phase.value,
        )
        self._st.phase = new_phase
