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
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, raise_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    InvokeInputs,
)
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.state import DeepAgentState
from openjiuwen.agent_teams.harness.control import (
    _CmdAbort,
    _CmdPause,
    _CmdRoundFinished,
    _CmdSend,
    _CmdStop,
)
from openjiuwen.agent_teams.harness.async_tools import AsyncToolRuntime
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
    from openjiuwen.agent_teams.schema.build_context import BuildContext
    from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
    from openjiuwen.core.single_agent.rail.base import AgentRail

# Events fired on the harness-private callback framework so a consumer (e.g. the
# team StreamController) can map harness lifecycle onto its own status machines
# without polling. Both fire inside the supervisor coroutine, so their ordering
# matches the phase/round transitions exactly — no observer sees a torn state.
#   harness.state -> kwargs: old (HarnessState), new (HarnessState), session_id
#   harness.round -> kwargs: kind (str), round_id (int), result (dict | None)
# ``kind`` is one of: started / finished / aborted / paused / failed.
# (Payload keys avoid ``event`` because that is ``trigger``'s own topic arg.)
_EVENT_STATE = "harness.state"
_EVENT_ROUND = "harness.round"
_EVENT_NAMESPACE = "native_harness"


class NativeHarness(DeepAgent):
    """Concurrent-safe multi-round interaction wrapper that is itself a DeepAgent.

    The harness configures itself directly from a ``DeepAgentSpec`` (forward
    construction): the constructor resolves its construction parts and applies
    them onto this instance, which then runs as the real DeepAgent. The harness
    owns one ``Session`` (auto-created or injected) reused across all rounds.

    All external API methods push a control event onto an internal channel; the
    supervisor coroutine consumes events serially, mutating
    ``HarnessInternalState`` as the sole writer. Concurrency safety is by
    construction: external callers never observe a half-transitioned state.

    Structurally implements ``HarnessProtocol`` (verified via ``isinstance``).
    """

    def __init__(
        self,
        agent_spec: "DeepAgentSpec",
        build_context: "BuildContext | None" = None,
        extra_rails: "list[AgentRail] | None" = None,
    ) -> None:
        """Initialize a NativeHarness that configures itself from a spec.

        Forward construction mirrors ``DeepAgentSpec.build``: resolve the spec's
        parts up-front, construct the underlying DeepAgent with the spec's real
        card (no placeholder, no throwaway template), then apply config + tools +
        rails onto this instance. Synchronous, so a host can read ``workspace`` /
        ``sys_operation`` off ``deep_config`` right after construction.

        Args:
            agent_spec: The DeepAgentSpec this harness materializes itself from.
            build_context: Optional runtime carrier forwarded to capability
                providers during ``resolve_parts``.
            extra_rails: Additional rails not declared in ``agent_spec.rails``
                (transitional team-side rails; folded into the spec later).
                Mounted after the spec rails.
        """
        from openjiuwen.harness.factory import apply_deep_agent_parts

        # Resolve parts before super().__init__ so the spec's real card
        # configures this DeepAgent at construction (mirrors
        # ``DeepAgentSpec.build``'s ``DeepAgent(parts.config.card)``).
        parts = agent_spec.resolve_parts(build_context)
        super().__init__(parts.config.card)

        self._agent_spec = agent_spec
        self._build_context = build_context
        self._extra_rails: list[AgentRail] = list(extra_rails) if extra_rails else []
        self._session: Session | None = None
        self._owns_session: bool = False
        self._timeout: float = parts.config.completion_timeout
        self._st = HarnessInternalState()
        self._control: asyncio.Queue = asyncio.Queue()
        # Harness-private event bus; consumers subscribe via
        # ``subscribe(on_state=, on_round=)``. Metrics/logging off — these fire
        # on the supervisor hot path.
        self._events = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)
        self._snapshot_rail = SnapshotRail()
        self._forwarder_task: asyncio.Task | None = None
        self._started_event = asyncio.Event()
        self._starting: bool = False
        # ``_prepared``: ensure_initialized (async rail init) done. The sync
        # config below already ran, so ``start`` only spins up supervisor +
        # session.
        self._prepared: bool = False
        # Async background-tool runtime (lazy): tracks two-phase async tools and
        # injects their completion via this harness's ``send``. None until first
        # async tool launches; ``stop`` cancels any in-flight tasks.
        self._async_tool_runtime: "AsyncToolRuntime | None" = None
        # External pause/resume control surface, attached by TeamHarness when a
        # BackgroundTaskController is threaded through run_agent_team_streaming.
        # None unless an embedder supplied one; SwarmflowTool reads it to register
        # its run handle for pause/resume.
        self.background_task_controller: Any = None

        # Apply config + tools + spec rails directly onto this instance — no
        # throwaway template DeepAgent.
        apply_deep_agent_parts(self, parts)
        # Transitional team-side rails not yet declared in ``agent_spec.rails``.
        # Mounted after the spec rails so init order matches the legacy path.
        for rail in self._extra_rails:
            self.add_rail(rail)
        # Round-boundary snapshot rail (AFTER_TASK_ITERATION is a deep event, so
        # add_rail/register_rail routes it onto this outer DeepAgent).
        self.add_rail(self._snapshot_rail)

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def model(self) -> str | None:
        """The harness's model name (from ``deep_config → react_agent config``)."""
        if self._react_agent is not None:
            return self._react_agent.config.model_name
        return None

    @property
    def build_context(self) -> "BuildContext | None":
        """Runtime carrier forwarded to capability providers at build time."""
        return self._build_context

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

    async def _prepare(self) -> None:
        """Run ``ensure_initialized`` (async rail init).

        Idempotent. ``start`` calls this first; the synchronous config + rail
        mounting already ran in ``__init__``.
        """
        if self._prepared:
            return
        await self.ensure_initialized()
        self._prepared = True

    async def run_once(self, content: "str | InteractiveInput", *, session: Session | None = None) -> dict[str, Any]:
        """Run one non-streaming execution and return the ``Runner.run_agent`` dict.

        Bypasses the supervisor entirely: there is no steering, no external
        multi-round interaction, and no ``outputs()`` stream. The execution is a
        plain ``DeepAgent.invoke`` — the spec's ``enable_task_loop`` decides
        whether it runs a single ReAct round or self-drives the full task loop
        (so DeepAgent's todo planning is preserved). Used by single-shot callers
        (e.g. swarmflow workers) that want a teammate-equivalent agent without the
        streaming interaction model.

        Args:
            content: The query for this execution (an ``InteractiveInput`` resumes
                a pending interrupt, mirroring ``invoke``).
            session: Optional externally-managed session. When omitted the harness
                creates and tears down its own session.

        Returns:
            The invoke result dict, e.g. ``{"output": ..., "result_type": ...}``.
        """
        if self._st.supervisor_task is not None:
            raise_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="run_once cannot run while the supervisor is active (use send()).",
            )
        await self._prepare()
        owns_session = session is None
        if owns_session:
            sess = Session(card=self.card)
            await sess.pre_run()
        else:
            sess = session
        self._session = sess
        self._owns_session = owns_session
        try:
            return await self.invoke({"query": content}, sess)
        finally:
            if owns_session:
                try:
                    await sess.post_run()
                except Exception:
                    logger.exception("[NativeHarness] run_once session post_run failed")
            try:
                self.ability_manager.teardown_tools()
            except Exception:
                logger.exception("[NativeHarness] run_once tool teardown failed")

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
            await self._prepare()

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
        # Cancel in-flight async background tasks before teardown so their
        # completion injection does not race a stopping harness.
        if self._async_tool_runtime is not None:
            self._async_tool_runtime.cancel_all()
        ack: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._control.put(_CmdStop(ack=ack))
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
        # Drop all subscriber callbacks so a stopped harness holds no references
        # to consumer closures (the supervisor no longer fires events).
        await self._events.unregister_namespace(_EVENT_NAMESPACE)
        # Round-end teardown: remove this agent's per-agent (stateful) tools from
        # the process-global resource manager. The native is rebuilt every run
        # cycle (TeamHarness.start), so without this each cycle re-registers every
        # tool over a stale id and add_ability's refresh path logs a warning per
        # tool. Stateless shared tools are left in place for other agents.
        try:
            self.ability_manager.teardown_tools()
        except Exception:
            logger.exception("[NativeHarness] tool teardown failed during stop")
        logger.info("[NativeHarness] stopped session=%s", self.session_id)

    async def dispose(self) -> None:
        """Permanently destroy this native: stop it, then release its resources.

        Distinct from :meth:`stop`, which is round-end teardown — the native is
        rebuilt next cycle on the same session, so its ``sys_operation`` (id
        stable per session) is deliberately kept alive for reuse. ``dispose`` is
        the destruction hook the permanent-teardown path (coordination stop /
        session discard / member shutdown) calls instead: it drops this agent's
        ``sys_operation`` from the process-global resource manager so a shut-down
        member or discarded session does not leak it. Nothing else removes it
        individually — only ``Runner.stop`` would otherwise clear it at process
        exit.

        The supervisor is stopped first because the permanent-teardown path does
        not always run round-end ``finalize_round`` beforehand (e.g. an external
        ``stop_team`` or a session switch): the native may still be alive with
        its ``ContextEngine`` reading/writing through ``sys_operation``, so
        removing it under a live supervisor would yank a resource still in use.
        ``stop`` is idempotent, so this is a no-op when ``finalize_round`` already
        stopped the native. Idempotent overall and safe to call more than once.
        """
        await self.stop()

        from openjiuwen.core.runner import Runner

        config = self.deep_config
        sys_op = config.sys_operation if config is not None else None
        if sys_op is None:
            return
        # Quiet idempotence: only remove when still present, so a second dispose
        # (or a dispose after Runner.stop already cleared everything) is a no-op.
        if Runner.resource_mgr.get_sys_operation(sys_op.id) is not None:
            Runner.resource_mgr.remove_sys_operation(sys_op.id)

    def outputs(self) -> AsyncIterator[Any]:
        """Return an AsyncIterator over output chunks.

        Single-consumer contract: the terminating ``_END`` sentinel is emitted
        exactly once, so only one iterator can drain to completion. Wrap
        externally if broadcast or reconnect semantics are needed. The iterator
        ends cleanly after ``stop()`` closes the stream.
        """
        return _OutputIterator(self._st.output_queue)

    # ------------------------------------------------------------------
    # Event subscription
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        *,
        on_state: Callable[..., Any] | None = None,
        on_round: Callable[..., Any] | None = None,
    ) -> None:
        """Register optional lifecycle callbacks on the harness event bus.

        ``on_state`` fires on every phase transition (kwargs ``old`` / ``new``
        (``HarnessState``) / ``session_id``); ``on_round`` fires on every round
        transition (kwargs ``kind`` (str) / ``round_id`` (int) / ``result``
        (dict | None)). Both are keyword-only and optional; only the non-None
        callbacks are registered. The framework narrows kwargs to each
        callback's declared parameters, so a consumer may accept only the subset
        it needs. Callbacks run inside the supervisor coroutine and must be cheap
        and non-blocking.

        Args:
            on_state: Async callable receiving the ``harness.state`` payload.
            on_round: Async callable receiving the ``harness.round`` payload.
        """
        if on_state is not None:
            await self._events.register(_EVENT_STATE, on_state, namespace=_EVENT_NAMESPACE)
        if on_round is not None:
            await self._events.register(_EVENT_ROUND, on_round, namespace=_EVENT_NAMESPACE)

    # ------------------------------------------------------------------
    # External API: send / abort / pause
    # ------------------------------------------------------------------

    async def send(self, content: "str | InteractiveInput", *, immediate: bool = False) -> str:
        """Push an inbound message to the supervisor.

        ``content`` may be an ``InteractiveInput`` carrying an interrupt resume.
        It still starts a round via ``submit_round``; the task-loop executor
        extracts the InteractiveInput and the inner ReAct agent resumes the
        interrupted turn instead of starting fresh. A resume round settles to
        IDLE on completion rather than continuing the task plan.

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
        await self._control.put(_CmdSend(msg=msg, ack=ack))
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
        await self._control.put(_CmdAbort(immediate=immediate, ack=ack))
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
        await self._control.put(_CmdPause(ack=ack))
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
        await self._control.put(_CmdSend(msg=msg, ack=ack))
        try:
            await ack
        except Exception:
            logger.debug("[NativeHarness] auto-invoke send rejected", exc_info=True)

    # ------------------------------------------------------------------
    # Async background tools
    # ------------------------------------------------------------------

    @property
    def async_tool_runtime(self) -> AsyncToolRuntime:
        """The per-harness async-tool runtime (lazily created).

        Completion is injected through ``send(text, immediate=False)`` so the
        result reaches the model on the next round (IDLE) or as a follow-up
        (RUNNING) — never as a suspended ``tool_result``.
        """
        if self._async_tool_runtime is None:
            self._async_tool_runtime = AsyncToolRuntime(inject=self._inject_async_completion)
        return self._async_tool_runtime

    async def _inject_async_completion(self, text: str) -> None:
        """Feed an async tool's completion text back as a non-immediate send."""
        await self.send(text, immediate=False)

    def launch_async_tool(
        self,
        task_id: str,
        coro_factory: "Callable[[], Any]",
        *,
        tool_name: str,
        description: str,
    ) -> None:
        """Launch a background async-tool task tracked by this harness.

        Args:
            task_id: Caller-generated unique id for this run.
            coro_factory: Zero-arg factory returning the coroutine to run.
            tool_name: The launching tool's name (for the completion message).
            description: Human-readable task description (for the registry).
        """
        self.async_tool_runtime.launch(
            task_id,
            coro_factory,
            tool_name=tool_name,
            description=description,
        )

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
                if isinstance(cmd, _CmdStop):
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
        if isinstance(cmd, _CmdSend):
            await self._on_send(cmd)
        elif isinstance(cmd, _CmdAbort):
            await self._on_abort(cmd)
        elif isinstance(cmd, _CmdPause):
            await self._on_pause(cmd)
        elif isinstance(cmd, _CmdRoundFinished):
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
        queued behind ``_CmdStop``) would leave callers blocked on
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

    @staticmethod
    def _ack(fut: "asyncio.Future | None", value: Any) -> None:
        """Resolve a command's ack future unless the caller already abandoned it.

        During teardown the awaiting coroutine (e.g. an async-tool completion
        injection cancelled by :meth:`stop`) may have cancelled its ack future
        before the supervisor dequeues the command. Setting a result on an
        already-done future raises ``InvalidStateError`` and crashes the
        supervisor, so skip it — nobody is waiting. Mirrors the ``not ack.done()``
        guard already used on the error path.
        """
        if fut is not None and not fut.done():
            fut.set_result(value)

    async def _on_send(self, cmd: _CmdSend) -> None:
        """Route a send according to current phase."""
        seq = self._st.next_seq()
        msg = InboxMessage(seq=seq, content=cmd.msg.content, immediate=cmd.msg.immediate)

        phase = self._st.phase
        if phase is HarnessState.IDLE:
            active = self._start_round(msg.content)
            await self._transition(HarnessState.RUNNING)
            await self._emit_round("started", active.round_id)
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
            if isinstance(msg.content, str):
                merged: "str | InteractiveInput" = f"{base}\n{msg.content}" if base else msg.content
            else:
                # An InteractiveInput resume cannot be concatenated onto cached
                # text; start the resume round directly and drop the cache.
                merged = msg.content
            self._st.paused_query = None
            active = self._start_round(merged)
            await self._transition(HarnessState.RUNNING)
            await self._emit_round("started", active.round_id)
        self._ack(cmd.ack, seq)

    async def _on_abort(self, cmd: _CmdAbort) -> None:
        """Handle graceful or immediate abort."""
        phase = self._st.phase
        if phase is HarnessState.IDLE:
            self._ack(cmd.ack, None)
            return
        if phase is HarnessState.PAUSED:
            self._st.paused_query = None
            await self._transition(HarnessState.IDLE)
            self._ack(cmd.ack, None)
            return

        active = self._st.active
        if active is None:
            await self._transition(HarnessState.IDLE)
            self._ack(cmd.ack, None)
            return

        if cmd.immediate:
            await self._hard_cancel_round(active)
            await self._rollback_to_snapshot(
                active.last_safe_snapshot or active.pre_round_snapshot,
            )
            self._reset_coordinator()
            await self._emit_round_aborted(active.round_id, "abort")
            await self._emit_round("aborted", active.round_id)
            self._st.active = None
            await self._transition(HarnessState.IDLE)
        else:
            # Graceful: let the current round finish; mark it so _on_round_done
            # does not start a continuation, and gate the coordinator so any
            # task-loop continuation check also stops.
            active.graceful_abort = True
            coordinator = self.loop_coordinator
            if coordinator is not None:
                coordinator.request_abort()
        self._ack(cmd.ack, None)

    async def _on_pause(self, cmd: _CmdPause) -> None:
        """Cancel current round, roll back to its pre-round baseline, cache query."""
        if self._st.phase is not HarnessState.RUNNING:
            self._ack(cmd.ack, None)
            return

        active = self._st.active
        if active is None:
            await self._transition(HarnessState.PAUSED)
            self._ack(cmd.ack, None)
            return

        # A resume round (InteractiveInput query) cannot be re-merged onto cached
        # text, so do not cache it; the next send simply starts fresh.
        cached_query = active.original_query if isinstance(active.original_query, str) else None
        await self._hard_cancel_round(active)
        # pause discards the whole round (it will restart with a merged query),
        # so roll back to the pre-round baseline, not the mid-round snapshot —
        # otherwise the restarted round's query duplicates the original.
        await self._rollback_to_snapshot(active.pre_round_snapshot)
        self._reset_coordinator()
        await self._emit_round_aborted(active.round_id, "pause")
        await self._emit_round("paused", active.round_id)
        self._st.active = None
        self._st.paused_query = cached_query
        await self._transition(HarnessState.PAUSED)
        self._ack(cmd.ack, None)

    async def _on_round_done(self, cmd: _CmdRoundFinished) -> None:
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
        is_resume = isinstance(active.original_query, InteractiveInput)
        self._st.active = None

        if cmd.error is not None:
            logger.error(
                "[NativeHarness] round_id=%s ended with error: %r",
                cmd.round_id,
                cmd.error,
            )
            await self._emit_round("failed", cmd.round_id, cmd.result)
        else:
            await self._emit_round("finished", cmd.round_id, cmd.result)

        session = self._session
        coordinator = self.loop_coordinator
        if session is None or coordinator is None:
            await self._transition(HarnessState.IDLE)
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
            await self._transition(HarnessState.IDLE)
            return

        result_type = (cmd.result or {}).get("result_type")
        if result_type == "interrupt" or coordinator.is_aborted:
            await self._transition(HarnessState.IDLE)
            return

        # Decision priority (matches _run_task_loop):
        #   follow-up (external immediate=False sends) > remaining task-plan task.
        next_follow_up = self._drain_next_follow_up(session)
        if next_follow_up is not None:
            nxt = self._start_round(next_follow_up, is_follow_up=True)
            await self._emit_round("started", nxt.round_id)
            return

        # A resume round has single-round semantics: it must not continue the
        # task plan using its InteractiveInput query (that would re-resume an
        # already-cleared interrupt). Settle to IDLE; any follow-up queued above
        # still ran first.
        if is_resume:
            await self._transition(HarnessState.IDLE)
            return

        if self._has_remaining_tasks(session):
            nxt = self._start_round(active.original_query)
            await self._emit_round("started", nxt.round_id)
            return

        await self._transition(HarnessState.IDLE)

    async def _on_stop(self, cmd: _CmdStop) -> None:
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
        await self._transition(HarnessState.TERMINATED)
        self._ack(cmd.ack, None)

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

    def _start_round(self, query: "str | InteractiveInput", is_follow_up: bool = False) -> ActiveRound:
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
            str(query)[:120],
            is_follow_up,
        )
        return active

    async def _run_round(self, active: ActiveRound, is_follow_up: bool) -> None:
        """Drive one outer round through the task-loop kernel.

        Submits the round under ``active.task_id`` so an immediate abort can
        cancel the scheduler task running ``react_agent.invoke``; awaits the
        round result; writes it to the stream. On cancellation (immediate
        abort / pause) the CancelledError propagates so the wait unwinds; a
        ``_CmdRoundFinished`` is always posted so the supervisor can transition.

        Args:
            active: The round being driven.
            is_follow_up: Whether this round is a follow-up continuation.
        """
        error: BaseException | None = None
        result: dict | None = None
        # Per-round BEFORE/AFTER_INVOKE lifecycle. The supervisor drives outer
        # rounds directly (bypassing DeepAgent.invoke), so the invoke-level
        # callbacks have no other firing site here — each outer round is one
        # logical invoke. The task-iteration callbacks still fire independently
        # inside the shared TaskLoopEventExecutor; this wraps a separate ctx.
        inv_inputs = InvokeInputs(
            query=active.original_query,
            conversation_id=self._session.get_session_id(),
        )
        ctx = AgentCallbackContext(agent=self, inputs=inv_inputs, session=self._session)
        try:
            async with ctx.lifecycle(
                AgentCallbackEvent.BEFORE_INVOKE,
                AgentCallbackEvent.AFTER_INVOKE,
            ):
                await self.loop_controller.submit_round(
                    self._session,
                    active.original_query,
                    is_follow_up=is_follow_up,
                    task_id=active.task_id,
                )
                result = await self.loop_controller.wait_round_completion(self._timeout)
                # wait_completion returns a control-error dict ({"error":
                # "cancelled" / "completion_timeout" / "no active round"}) when
                # the wait itself was cancelled/timed out rather than the round
                # producing a real result — notably, an immediate abort/pause
                # cancels this wait task and the handler swallows the
                # CancelledError into {"error": "cancelled"}. Streaming that as
                # an (empty) answer is noise: an aborted/paused round emits its
                # own round_aborted marker, and a timed-out round has no answer
                # to show. Only stream genuine round results (normal answers /
                # HITL / workflow interrupts) so harness output matches a plain
                # DeepAgent run.
                if isinstance(result, dict) and result.get("error") == "completion_timeout":
                    # The harness gave up waiting, but the TaskScheduler task
                    # may still be running (e.g. a slow API call).  Cancel it
                    # now so it doesn't linger as a zombie that concurrently
                    # writes to the context buffer when the next round starts,
                    # causing interleaved messages and 400 errors.
                    controller = self.loop_controller
                    if controller is not None and controller.task_scheduler is not None:
                        try:
                            await controller.task_scheduler.cancel_task(active.task_id)
                        except Exception:
                            logger.exception(
                                "[NativeHarness] cancel_task failed after "
                                "completion_timeout, round_id=%s",
                                active.round_id,
                            )
                if not (isinstance(result, dict) and result.get("error")):
                    await self._write_round_result_to_stream(result, self._session)
                # Expose the round result to AFTER_INVOKE rails (fired in the
                # lifecycle's finally). Control-error dicts carry no real answer,
                # so leave result as None for them.
                inv_inputs.result = result if isinstance(result, dict) and not result.get("error") else None
        except asyncio.CancelledError:
            logger.info("[NativeHarness] round_id=%s cancelled", active.round_id)
            raise
        except Exception as exc:  # noqa: BLE001 - reported via control channel
            logger.exception("[NativeHarness] round_id=%s crashed", active.round_id)
            error = exc
        finally:
            await self._control.put(
                _CmdRoundFinished(
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
        # CancelledError is BaseException (not Exception) on py3.8+, so the broad
        # ``except Exception`` below never catches it — cancellation propagates
        # naturally while ``finally`` still emits the _END sentinel.
        try:
            async for chunk in self._session.stream_iterator():
                await self._st.output_queue.put(chunk)
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

    async def _transition(self, new_phase: HarnessState) -> None:
        """Update phase, log, and fire ``harness.state``; single-writer invariant.

        Fired inside the supervisor coroutine so observers see transitions in the
        exact order they happen. ``trigger`` swallows callback exceptions, so a
        misbehaving subscriber cannot crash the supervisor.

        Args:
            new_phase: The phase to transition into; a no-op when unchanged.
        """
        old_phase = self._st.phase
        if old_phase is new_phase:
            return
        logger.info(
            "[NativeHarness] phase %s -> %s",
            old_phase.value,
            new_phase.value,
        )
        self._st.phase = new_phase
        await self._events.trigger(
            _EVENT_STATE,
            old=old_phase,
            new=new_phase,
            session_id=self.session_id,
        )

    async def _emit_round(
        self,
        event: str,
        round_id: int,
        result: dict | None = None,
    ) -> None:
        """Fire ``harness.round`` for a round lifecycle transition.

        Args:
            event: One of ``started`` / ``finished`` / ``aborted`` / ``paused`` /
                ``failed``.
            round_id: The round this event concerns.
            result: The round result dict for ``finished``; None otherwise.
        """
        await self._events.trigger(
            _EVENT_ROUND,
            kind=event,
            round_id=round_id,
            result=result,
        )
