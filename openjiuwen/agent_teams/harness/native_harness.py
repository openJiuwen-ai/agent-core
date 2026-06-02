# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness: concurrent-safe interaction layer that IS a DeepAgent.

NativeHarness subclasses ``DeepAgent`` and drives the full task-loop kernel
(TaskLoopController / TaskScheduler / TaskLoopEventHandler / LoopCoordinator),
so it keeps every DeepAgent capability — task plan, stop conditions,
SESSION_SPAWN subagents, BEFORE/AFTER_TASK_ITERATION rails, follow-up rounds,
interrupt/resume, plan mode, DeepAgentState management. It only replaces the
*interaction model*: instead of DeepAgent's self-driving ``while`` loop
(``_run_task_loop``), a single supervisor coroutine drives one outer round at a
time via ``submit_round`` + ``wait_round_completion`` and decides whether to
continue using the same multi-round rules.

Why inherit instead of compose: the task-loop executor calls
``agent.react_agent.invoke(...)`` and fires BEFORE/AFTER_TASK_ITERATION on
``agent`` — both must resolve to *this* harness. A composed wrapper that calls
``react_agent.invoke`` directly would bypass the executor (and thus the whole
task loop). By being the DeepAgent, the harness reuses ``_setup_task_loop`` /
``load_state`` / ``save_state`` / ``_has_remaining_tasks`` verbatim.

Round execution model: the harness shares ONE task-loop kernel and ONE stream
lifecycle for its whole life. Each outer round runs in its own asyncio.Task
that submits a round to the controller and awaits completion; the real
``react_agent.invoke`` runs inside the controller's TaskScheduler under a known
``task_id``, so an immediate abort can cancel the *scheduler* task (truly
stopping the LLM/tool work) rather than orphaning it. A single forwarder
coroutine pumps ``session.stream_iterator()`` into the output queue; the stream
is closed only by ``stop()``.

Public API surface (all methods are concurrent-safe — each just enqueues a
control event and awaits an ack resolved solely by the supervisor coroutine):
- ``start(session=None)``: configure self from the provider, build the
  task-loop kernel, start the supervisor + forwarder.
- ``stop()``: cancel any active round, stop the controller, close outputs.
- ``outputs()``: queue-backed AsyncIterator of OutputSchema chunks.
- ``send(content, immediate=False)``: immediate=True steers the active round;
  immediate=False enqueues a follow-up consumed when the round finishes.
- ``abort(immediate=False)``: graceful (round finishes, no continuation) or
  immediate (cancel scheduler task + rollback to the last round boundary).
- ``pause()``: cancel the current round, roll back to its pre-round baseline,
  and cache its query so the next send concatenates and restarts it.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncIterator, Callable

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, raise_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
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


class NativeHarness(DeepAgent):
    """Concurrent-safe multi-round interaction wrapper that is itself a DeepAgent.

    A template DeepAgent is produced lazily by ``deep_agent_provider`` on the
    first ``start()`` call; its config + rails are copied onto this instance,
    which then runs as the real DeepAgent. The harness owns one ``Session``
    (auto-created or injected) reused across all rounds.

    All external API methods push a control event onto an internal channel; the
    supervisor coroutine consumes events serially, mutating
    ``HarnessInternalState`` as the sole writer. Concurrency safety is by
    construction: external callers never observe a half-transitioned state.

    Structurally implements ``HarnessProtocol`` (verified via ``isinstance``).
    """

    def __init__(self, deep_agent_provider: Callable[[], DeepAgent]) -> None:
        """Initialize a NativeHarness over a DeepAgent provider.

        Args:
            deep_agent_provider: Zero-arg callable producing a configured
                DeepAgent template. Invoked exactly once on the first
                ``start()``; its config + rails are copied onto this instance.
        """
        # Placeholder card; replaced by the template's card in start().
        super().__init__(AgentCard(name="native_harness"))
        self._provider = deep_agent_provider
        self._session: Session | None = None
        self._owns_session: bool = False
        self._timeout: float = 600.0
        self._st = HarnessInternalState()
        self._control: asyncio.Queue = asyncio.Queue()
        self._snapshot_rail = SnapshotRail()
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
        """Configure self from the provider, build the task-loop kernel, start supervisor.

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
            template = self._provider()
            if template is None:
                raise_error(
                    StatusCode.DEEPAGENT_RUNTIME_ERROR,
                    error_msg="deep_agent_provider returned None.",
                )

            # Adopt the template's identity + config, then inherit its rails so
            # this instance behaves exactly like the configured template.
            self.card = template.card
            self.configure(template.deep_config)
            for rail in (*template._pending_rails, *template._registered_rails):
                self.add_rail(rail)
            # Round-boundary snapshot rail (AFTER_TASK_ITERATION is a deep event,
            # so add_rail/register_rail routes it onto this outer DeepAgent).
            self.add_rail(self._snapshot_rail)
            await self.ensure_initialized()

            if template.deep_config is not None:
                self._timeout = template.deep_config.completion_timeout

            if session is None:
                self._session = Session(card=self.card)
                self._owns_session = True
                await self._session.pre_run()
            else:
                self._session = session
                self._owns_session = False

            # Build the task-loop kernel once and reuse it across rounds.
            coordinator, controller = await self._setup_task_loop(self._session)
            await controller.bind_session(self._session)
            coordinator.reset()
            self._bound_session_id = self._session.get_session_id()

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
        """Cancel any active round, stop the controller, close outputs.

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

        # Tear down the shared task-loop kernel.
        controller = self.loop_controller
        if controller is not None and self._session is not None:
            try:
                await controller.unbind_session(self._session)
            except Exception:
                logger.exception("[NativeHarness] unbind_session failed during stop")
            try:
                await controller.stop()
            except Exception:
                logger.exception("[NativeHarness] controller.stop failed during stop")

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
        - RUNNING + immediate=True: steers the active round (injected at the
          next ReAct iteration top).
        - RUNNING + immediate=False: enqueued as a follow-up; consumed when the
          active round finishes.
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

        - immediate=False (graceful): the current round runs to completion; the
          supervisor then stops without starting a continuation. No rollback.
        - immediate=True: cancel the scheduler task running the round (truly
          stopping the LLM/tool work), drop pending input, roll context+state
          back to the last completed round boundary (or the pre-round baseline
          if no round completed), and reset the coordinator so the next send can
          start a fresh round. Tool side effects already performed are NOT
          undone.

        Args:
            immediate: Cancel immediately (True) or let the current round finish
                (False).
        """
        self._require_alive()
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._control.put(_Cmd_Abort(immediate=immediate, ack=ack))
        await ack

    async def pause(self) -> None:
        """Stop the current round and cache its query for the next send.

        Cancels the scheduler task running the round, rolls context+state back
        to the round's pre-round baseline (discarding the whole round), resets
        the coordinator, and enters PAUSED. The next send() — regardless of
        ``immediate`` — concatenates onto the cached query and restarts the
        round with the combined content. Tool side effects already performed are
        NOT undone.
        """
        self._require_alive()
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._control.put(_Cmd_Pause(ack=ack))
        await ack

    # ------------------------------------------------------------------
    # Auto-invoke override (session_spawn): route through the supervisor.
    # ------------------------------------------------------------------

    async def schedule_auto_invoke_on_spawn_done(
        self,
        query: str,
        delay: float = 0.5,
    ) -> None:
        """Route a SESSION_SPAWN-completion auto-invoke through the supervisor.

        DeepAgent's default schedules ``self.invoke(...)`` directly, which would
        bypass the single-writer supervisor and race the round driver. Instead,
        after a short merge delay, enqueue the spawn summary as a non-immediate
        send so the supervisor either starts a round (when IDLE) or queues it as
        a follow-up (when RUNNING) — preserving the single-round-source model.

        Args:
            query: The spawn-completion summary to feed back to the agent.
            delay: Delay in seconds to merge multiple concurrent spawn
                completions (mirrors the base contract).
        """
        await asyncio.sleep(delay)
        self._auto_invoke_scheduled = False

        if self._st.phase is HarnessState.TERMINATED:
            return
        if self._st.supervisor_task is None:
            logger.warning("[NativeHarness] auto-invoke before start; skipping")
            return

        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        msg = InboxMessage(seq=0, content=query, immediate=False)
        await self._control.put(_Cmd_Send(msg=msg, ack=ack))
        try:
            await ack
        except Exception:
            logger.debug("[NativeHarness] auto-invoke send rejected", exc_info=True)

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
                # Steer the active round via the shared steering queue that the
                # executor drains before the next inner model call.
                self._push_steer(msg.content)
            else:
                # Single next-round source: enqueue a follow-up the round-done
                # decision drains (FIFO across all RUNNING sends).
                self.loop_controller.enqueue_follow_up(msg.content)
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
            self._transition(HarnessState.IDLE)
            cmd.ack.set_result(None)
            return

        active = self._st.active
        if active is None:
            self._transition(HarnessState.IDLE)
            cmd.ack.set_result(None)
            return

        if cmd.immediate:
            await self._hard_cancel_round(active)
            await self._rollback_to_snapshot(
                active.last_safe_snapshot or active.pre_round_snapshot,
            )
            self._reset_coordinator()
            await self._emit_round_aborted(active.round_id, "abort")
            self._st.active = None
            self._transition(HarnessState.IDLE)
        else:
            # Graceful: let the current round finish; mark it so _on_round_done
            # does not start a continuation, and gate the coordinator so any
            # task-loop continuation check also stops.
            active.graceful_abort = True
            coordinator = self.loop_coordinator
            if coordinator is not None:
                coordinator.request_abort()
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
        await self._hard_cancel_round(active)
        # pause discards the whole round (it will restart with a merged query),
        # so roll back to the pre-round baseline, not the mid-round snapshot —
        # otherwise the restarted round's query duplicates the original.
        await self._rollback_to_snapshot(active.pre_round_snapshot)
        self._reset_coordinator()
        await self._emit_round_aborted(active.round_id, "pause")
        self._st.active = None
        self._st.paused_query = cached_query
        self._transition(HarnessState.PAUSED)
        cmd.ack.set_result(None)

    async def _on_round_done(self, cmd: _Cmd_RoundFinished) -> None:
        """Round finished naturally; reuse DeepAgent's multi-round decision.

        Mirrors the per-round tail of ``DeepAgent._run_task_loop``: advance the
        coordinator, persist its stop-condition state, then decide the next
        action with the same priority — interrupt/abort stop, else follow-up,
        else remaining task-plan tasks, else IDLE.
        """
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

        session = self._session
        coordinator = self.loop_coordinator
        if session is None or coordinator is None:
            self._transition(HarnessState.IDLE)
            return

        # Advance coordinator + persist stop-condition state (as _run_task_loop).
        coordinator.increment_iteration()
        if cmd.result is not None:
            coordinator.set_last_result(cmd.result)
        st = self.load_state(session)
        st.stop_condition_state = coordinator.get_state()
        self.save_state(session, st)

        # Graceful abort: the round finished; the user asked to stop. Drop any
        # queued follow-ups and go IDLE.
        if was_graceful:
            self._drain_follow_ups_discard(session)
            self._transition(HarnessState.IDLE)
            return

        result_type = (cmd.result or {}).get("result_type")
        if result_type == "interrupt" or coordinator.is_aborted:
            self._transition(HarnessState.IDLE)
            return

        # Decision priority (matches _run_task_loop):
        #   follow-up (external immediate=False sends) > remaining task-plan task.
        next_follow_up = self._drain_next_follow_up(session)
        if next_follow_up is not None:
            self._start_round(next_follow_up, is_follow_up=True)
            return

        if self._has_remaining_tasks(session):
            self._start_round(active.original_query)
            return

        self._transition(HarnessState.IDLE)

    async def _on_stop(self, cmd: _Cmd_Stop) -> None:
        """Terminal cleanup: cancel active round, transition TERMINATED.

        The controller teardown + output stream close happen in ``stop()``
        after the supervisor exits; the forwarder then emits the ``_END``
        sentinel.
        """
        active = self._st.active
        if active is not None:
            await self._hard_cancel_round(active)
            self._st.active = None
        self._st.paused_query = None
        self._transition(HarnessState.TERMINATED)
        cmd.ack.set_result(None)

    # ------------------------------------------------------------------
    # Round driver (organised for stage-2 _RoundDriver extraction)
    # ------------------------------------------------------------------
    #
    # The methods below form the round-driving core a future StreamController
    # could reuse: _start_round (begin), _run_round (drive one round through the
    # task loop), _hard_cancel_round (stop scheduler task + wait), and the
    # snapshot/rollback pair. They are kept as cohesive methods rather than a
    # separate class for now to avoid speculative abstraction; the seam is
    # documented so stage 2 can lift them out without reshaping callers.

    def _start_round(self, query: str, is_follow_up: bool = False) -> ActiveRound:
        """Create an ActiveRound (with a pre-round baseline snapshot) and schedule it.

        The round task sets ``_ACTIVE_ROUND`` to this round in its own context
        before submitting, so SnapshotRail locates it during the
        AFTER_TASK_ITERATION hook.

        Args:
            query: Query to drive this round.
            is_follow_up: Whether this round continues a prior one (passed to
                ``submit_round`` so the executor treats it as a follow-up).
        """
        round_id = self._st.next_round_id()
        task_id = uuid.uuid4().hex
        pre_round = capture_snapshot(self, self._session, index=0)

        active = ActiveRound(
            round_id=round_id,
            task_id=task_id,
            original_query=query,
            deep_agent=self,
            task=None,  # type: ignore[arg-type]  # assigned right after create_task
            steering_queue=asyncio.Queue(),
            pre_round_snapshot=pre_round,
        )

        async def _runner() -> None:
            _ACTIVE_ROUND.set(active)
            await self._run_round(active, is_follow_up)

        task = asyncio.create_task(_runner(), name=f"native_harness_round[{round_id}]")
        active.task = task
        self._st.active = active
        logger.info(
            "[NativeHarness] round_id=%s started query=%r follow_up=%s",
            round_id,
            query[:120],
            is_follow_up,
        )
        return active

    async def _run_round(self, active: ActiveRound, is_follow_up: bool) -> None:
        """Drive one outer round through the task-loop kernel.

        Submits the round under ``active.task_id`` so an immediate abort can
        cancel the scheduler task running ``react_agent.invoke``; awaits the
        round result; writes it to the stream. On cancellation (immediate
        abort / pause) the CancelledError propagates so the wait unwinds; a
        ``_Cmd_RoundFinished`` is always posted so the supervisor can transition.

        Args:
            active: The round being driven.
            is_follow_up: Whether this round is a follow-up continuation.
        """
        error: BaseException | None = None
        result: dict | None = None
        try:
            await self.loop_controller.submit_round(
                self._session,
                active.original_query,
                is_follow_up=is_follow_up,
                task_id=active.task_id,
            )
            result = await self.loop_controller.wait_round_completion(self._timeout)
            # wait_completion returns a control-error dict ({"error": "cancelled"
            # / "completion_timeout" / "no active round"}) when the wait itself
            # was cancelled/timed out rather than the round producing a real
            # result — notably, an immediate abort/pause cancels this wait task
            # and the handler swallows the CancelledError into {"error":
            # "cancelled"}. Streaming that as an (empty) answer is noise: an
            # aborted/paused round emits its own round_aborted marker, and a
            # timed-out round has no answer to show. Only stream genuine round
            # results (normal answers / HITL / workflow interrupts) so harness
            # output matches a plain DeepAgent run.
            if not (isinstance(result, dict) and result.get("error")):
                await self._write_round_result_to_stream(result, self._session)
        except asyncio.CancelledError:
            logger.info("[NativeHarness] round_id=%s cancelled", active.round_id)
            raise
        except Exception as exc:  # noqa: BLE001 - reported via control channel
            logger.exception("[NativeHarness] round_id=%s crashed", active.round_id)
            error = exc
        finally:
            await self._control.put(
                _Cmd_RoundFinished(
                    round_id=active.round_id,
                    error=error,
                    result=result,
                ),
            )

    async def _hard_cancel_round(self, active: ActiveRound) -> None:
        """Hard-cancel a round: stop the scheduler task, then the wait task.

        The real LLM/tool work runs inside the TaskScheduler's own task under
        ``active.task_id`` (``submit_round`` returns before it completes). So we
        MUST cancel that scheduler task first (which fires ``executor.cancel`` →
        ``coordinator.request_abort`` + cancels the exec task → ``invoke``'s
        CancelledError handler clears the current round). Only then cancel the
        supervisor's ``_run_round`` task that is blocked on
        ``wait_round_completion``.

        Args:
            active: The round to cancel.
        """
        controller = self.loop_controller
        if controller is not None and controller.task_scheduler is not None:
            try:
                await controller.task_scheduler.cancel_task(active.task_id)
            except Exception:
                logger.exception(
                    "[NativeHarness] cancel_task failed for round_id=%s",
                    active.round_id,
                )
        await self._cancel_round_task(active)

    async def _cancel_round_task(self, active: ActiveRound) -> None:
        """Cancel the ``_run_round`` task (blocked on wait) and await it.

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

    # ------------------------------------------------------------------
    # Task-loop bridge helpers
    # ------------------------------------------------------------------

    def _push_steer(self, content: str) -> None:
        """Push a steering message into the shared steering queue."""
        handler = self.event_handler
        if handler is not None and handler.interaction_queues is not None:
            handler.interaction_queues.push_steer(content)

    def _drain_next_follow_up(self, session: Session) -> str | None:
        """Drain queued follow-ups into state and pop the next one (FIFO).

        Mirrors ``_run_task_loop``: drain ``LoopQueues.follow_up`` into
        ``DeepAgentState.pending_follow_ups``, then pop the first. Returns None
        when no follow-up is pending.
        """
        controller = self.loop_controller
        new_follow_ups = controller.drain_follow_up() if controller is not None else []
        st = self.load_state(session)
        if new_follow_ups:
            st.pending_follow_ups.extend(new_follow_ups)
        if not st.pending_follow_ups:
            return None
        nxt = st.pending_follow_ups.pop(0)
        self.save_state(session, st)
        return nxt

    def _drain_follow_ups_discard(self, session: Session) -> None:
        """Drop all queued follow-ups (LoopQueues + state) on graceful stop."""
        controller = self.loop_controller
        if controller is not None:
            controller.drain_follow_up()
        st = self.load_state(session)
        if st.pending_follow_ups:
            st.pending_follow_ups.clear()
            self.save_state(session, st)

    def _reset_coordinator(self) -> None:
        """Reset the coordinator after a hard cancel.

        Required across rounds: ``executor.cancel`` set ``is_aborted`` via
        ``coordinator.request_abort()``; without resetting it the next round's
        continuation check would see a permanently-aborted coordinator.
        """
        coordinator = self.loop_coordinator
        if coordinator is not None:
            coordinator.reset()

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
        if self._session is None or self.react_agent is None:
            return
        react = self.react_agent
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
                self.save_state(self._session, restored_state)
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
