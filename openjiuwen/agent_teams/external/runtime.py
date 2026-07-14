# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CLI-backed member runtimes: the P2 counterparts to ``TeamHarness``.

Instead of driving a local DeepAgent, these drive an external CLI as the
member's brain. Two flavours implement the same :class:`MemberRuntime`
surface, picked by the adapter's ``supports_stdin_injection``:

* :class:`ExternalCliRuntime` — **streaming**. One long-lived subprocess; a
  round delivers the inbound text to its stdin and reads stdout until the
  per-CLI adapter signals the turn is complete. Supports mid-turn steer.
* :class:`ReinvokeCliRuntime` — **one-shot / re-invoke-per-turn**. A fresh
  subprocess per turn (prompt passed as argv, read stdout to EOF); for CLIs
  that take the prompt as a flag and exit (openclaw, hermes). No mid-turn
  steer — messages arriving during a turn are buffered and drained as
  follow-up re-invocations before the round ends.

In both cases the CLI's *actions* (messages, task ops) flow out-of-process
through the team CLI/MCP tools, so the CLI's stdout stays internal and is
not surfaced as team-stream chunks. Rail / memory hooks are
no-ops (the configurator skips those features for external CLI members).

Both flavours implement the :class:`MemberRuntime` interaction surface
(``start`` / ``stop`` / ``outputs`` / ``send`` / ``abort`` / ``pause`` /
``subscribe`` / ``state`` / ``session_id``) through the
shared :class:`_CliRuntimeBase` adapter, which wraps each flavour's single-turn
``_drive`` async generator: ``send`` starts a turn when IDLE (or steers /
buffers a follow-up when RUNNING), ``outputs`` exposes the turn's narration
chunks via a queue-backed iterator, and phase/round events are mapped onto the
same ``HarnessState`` / round-kind vocabulary the team StreamController consumes
from a NativeHarness. ``pause`` and immediate-abort *rollback* degrade to no-ops
(a subprocess turn has no mid-round snapshot to restore); abort still stops the
in-flight turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Callable, Optional

from openjiuwen.agent_teams.external.cli_agent.adapters import CliAgentAdapter
from openjiuwen.agent_teams.external.cli_agent.injector import Injector
from openjiuwen.agent_teams.external.cli_agent.transport.base import ProcessLike, ProcessTransport, StreamReaderLike
from openjiuwen.agent_teams.harness.outputs import _END, _OutputIterator
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework
from openjiuwen.core.session.stream.base import OutputSchema

# Harness-private event topics + namespace, mirroring NativeHarness so the team
# StreamController can subscribe to a CLI runtime with the same
# ``subscribe(on_state=, on_round=)`` contract. ``kind`` is one of started /
# finished / aborted / failed (a CLI turn has no ``paused`` phase).
_EVENT_STATE = "harness.state"
_EVENT_ROUND = "harness.round"
_EVENT_NAMESPACE = "cli_runtime"

# Joins buffered mid-turn messages into a single follow-up prompt.
_FOLLOWUP_SEP = "\n\n---\n\n"

# How much of a failed CLI's stderr to keep for the diagnostic log line.
_STDERR_TAIL_LIMIT = 2000

# Default no-output (inactivity) ceiling for a re-invoked one-shot CLI turn.
# A turn is killed only after the subprocess has been SILENT this long, so a
# CLI doing long but active work is never killed; a hung/startup-stalled one is.
_DEFAULT_INACTIVITY_TIMEOUT_S = 180.0

# Bounded wait for the streaming process's background stderr drain to deliver
# its tail on the premature-EOF path. The drain ends at stderr EOF, which a
# dead process reaches immediately; the cap guards against a half-closed pipe.
_STDERR_PEEK_TIMEOUT_S = 5.0


class _TurnTimeout(Exception):
    """Internal signal that a re-invoked turn hit a timeout.

    ``absolute`` distinguishes the optional wall-clock safety-net from the
    primary inactivity (no-output) ceiling, purely for the diagnostic log.
    """

    def __init__(self, *, absolute: bool):
        """Record which ceiling fired."""
        super().__init__("turn timeout")
        self.absolute = absolute


async def _read_stderr_tail(stream: StreamReaderLike | None) -> str:
    """Drain a subprocess stderr to EOF and return its tail.

    Draining matters for two reasons: an unread stderr pipe fills its OS
    buffer and blocks the CLI's next stderr write — a stuck write is
    indistinguishable from a hang — and the tail carries the human-readable
    failure reason (auth / quota / credit exhaustion / crash) that would
    otherwise be lost when only stdout is consumed.
    """
    if stream is None:
        return ""
    tail = b""
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        tail = (tail + chunk)[-_STDERR_TAIL_LIMIT:]
    return tail.decode("utf-8", errors="replace").strip()


async def _terminate(process: ProcessLike | None) -> None:
    """Terminate a subprocess if still running. Idempotent and quiet."""
    if process is None or process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    with contextlib.suppress(Exception):
        await process.wait()


class _CliRuntimeBase(ABC):
    """Shared :class:`MemberRuntime` surface for CLI-backed members.

    Adapts each flavour's single-turn ``_drive`` async generator into the
    multi-round interaction surface the team StreamController drives: one turn
    runs per IDLE ``send``, its narration chunks flow through a queue-backed
    ``outputs`` iterator, and phase/round transitions fire on the same private
    event bus a NativeHarness uses.
    """

    def __init__(self, *, member_name: str, adapter: CliAgentAdapter):
        self._member_name = member_name
        self._adapter = adapter
        # Lifecycle phase mapped onto the team's HarnessState vocabulary so the
        # StreamController treats a CLI runtime exactly like a NativeHarness.
        self._phase = HarnessState.IDLE
        # Queue-backed output channel for the current run cycle; ``stop`` pushes
        # the ``_END`` sentinel so ``outputs`` terminates. Recreated per start.
        self._output_queue: asyncio.Queue = asyncio.Queue()
        # Harness-private event bus; consumers subscribe via
        # ``subscribe(on_state=, on_round=)``. Metrics/logging off — fired on the
        # turn-driver hot path.
        self._events = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)
        # The asyncio.Task driving the in-flight turn, or None when IDLE.
        self._turn_task: Optional[asyncio.Task] = None
        self._round_seq = 0
        # Set by ``abort`` so the turn driver classifies its end as ``aborted``
        # rather than ``finished``; reset when the next turn starts.
        self._turn_aborted = False

    # ------------------------------------------------------------------
    # MemberRuntime: lifecycle
    # ------------------------------------------------------------------

    async def start(self, *, team_session: Optional[Any] = None) -> None:
        """Prepare the runtime for one run cycle (the subprocess owns its session).

        ``team_session`` is ignored: a CLI subprocess manages its own session,
        so cross-cycle state lives in the subprocess, not a shared team session.
        Resets the output channel + phase so a reused instance starts clean.
        """
        _ = team_session
        self._output_queue = asyncio.Queue()
        self._turn_aborted = False
        self._phase = HarnessState.IDLE

    async def stop(self) -> None:
        """Stop the in-flight turn, release the CLI transport, close outputs.

        Idempotent. Marks the phase TERMINATED first so the turn driver, on its
        abort-induced return, skips the trailing round/state events; then awaits
        the turn task, releases the transport, and pushes ``_END`` so a consumer
        iterating ``outputs`` terminates cleanly.
        """
        if self._phase is HarnessState.TERMINATED:
            return
        self._phase = HarnessState.TERMINATED
        self._turn_aborted = True
        await self._abort_turn()
        task = self._turn_task
        self._turn_task = None
        if task is not None and not task.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.aclose()
        self._output_queue.put_nowait(_END)
        await self._events.unregister_namespace(_EVENT_NAMESPACE)

    @property
    def state(self) -> HarnessState:
        """Return the current lifecycle phase."""
        return self._phase

    @property
    def session_id(self) -> Optional[str]:
        """Return None: a CLI subprocess owns its own session, not a team one."""
        return None

    # ------------------------------------------------------------------
    # MemberRuntime: interaction
    # ------------------------------------------------------------------

    def outputs(self) -> AsyncIterator[Any]:
        """Return a queue-backed iterator over the run cycle's narration chunks."""
        return _OutputIterator(self._output_queue)

    async def send(self, content: Any, *, immediate: bool = False) -> Any:
        """Submit input: start a turn when IDLE, else steer / buffer a follow-up.

        - IDLE: start a new turn driving ``content`` (transition RUNNING, fire
          ``started``); the turn runs in a background task so ``send`` returns
          without blocking, mirroring the NativeHarness supervisor.
        - RUNNING + immediate: steer the in-flight turn (or buffer it for the
          re-invoke flavour, which cannot steer).
        - RUNNING + non-immediate: buffer as a follow-up handled after the turn.

        Returns None (CLI runtimes have no monotonic sequence id to surface).
        """
        if self._phase is HarnessState.TERMINATED:
            raise_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg=f"external CLI member '{self._member_name}' runtime already stopped.",
            )
        text = content if isinstance(content, str) else str(content)
        if self._phase is HarnessState.RUNNING:
            if immediate:
                await self.steer(text)
            else:
                await self.follow_up(text)
            return None
        round_id = self._next_round_id()
        self._turn_aborted = False
        await self._transition(HarnessState.RUNNING)
        await self._emit_round("started", round_id)
        self._turn_task = asyncio.create_task(
            self._drive_turn(text, round_id),
            name=f"cli_runtime_turn[{self._member_name}:{round_id}]",
        )
        return None

    async def abort(self, *, immediate: bool = False) -> None:
        """Stop the in-flight turn. ``immediate`` rollback degrades to a no-op.

        A subprocess turn has no mid-round snapshot to roll back to, so both
        graceful and immediate aborts simply signal the turn driver to stop;
        the turn ends and the driver fires an ``aborted`` round event. The
        ``immediate`` flag only documents intent here.
        """
        _ = immediate
        if self._phase is not HarnessState.RUNNING:
            return
        self._turn_aborted = True
        await self._abort_turn()

    async def pause(self) -> None:
        """No-op: a CLI subprocess turn cannot be paused and cleanly restarted."""
        team_logger.debug("[{}] pause is a no-op for a CLI-backed runtime", self._member_name)
        return None

    async def subscribe(
        self,
        *,
        on_state: Callable[..., Any] | None = None,
        on_round: Callable[..., Any] | None = None,
    ) -> None:
        """Register optional phase/round callbacks (same contract as NativeHarness).

        Both keyword-only and optional; only the non-None callbacks are
        registered. ``on_state`` receives ``old`` / ``new`` / ``session_id``;
        ``on_round`` receives ``kind`` / ``round_id`` / ``result`` (kwargs
        narrowed to each callback's declared parameters).
        """
        if on_state is not None:
            await self._events.register(_EVENT_STATE, on_state, namespace=_EVENT_NAMESPACE)
        if on_round is not None:
            await self._events.register(_EVENT_ROUND, on_round, namespace=_EVENT_NAMESPACE)

    # ------------------------------------------------------------------
    # Turn driving (single-turn ``_drive`` → multi-round surface)
    # ------------------------------------------------------------------

    async def _drive_turn(self, content: str, round_id: int) -> None:
        """Drive one turn through ``_drive``, then fire its terminal round event.

        Pumps each narration chunk into the output queue, then maps the turn's
        outcome onto a round event (``failed`` on a crash, ``aborted`` when
        ``abort`` signalled, else ``finished``) and settles back to IDLE — unless
        ``stop`` already moved the phase to TERMINATED, in which case the trailing
        events are suppressed.
        """
        error: Optional[BaseException] = None
        # CancelledError is BaseException, never caught by ``except Exception`` —
        # cancellation propagates while a crash is captured into ``error``.
        try:
            async for chunk in self._drive({"query": content}):
                await self._output_queue.put(chunk)
        except Exception as exc:  # noqa: BLE001 - reported via round event
            error = exc
            team_logger.exception("[{}] external cli turn crashed", self._member_name)
        finally:
            self._turn_task = None
        if self._phase is HarnessState.TERMINATED:
            return
        if error is not None:
            await self._emit_round("failed", round_id)
        elif self._turn_aborted:
            await self._emit_round("aborted", round_id)
        else:
            await self._emit_round("finished", round_id)
        await self._transition(HarnessState.IDLE)

    def _next_round_id(self) -> int:
        """Return a monotonic round id for this runtime instance."""
        self._round_seq += 1
        return self._round_seq

    async def _transition(self, new_phase: HarnessState) -> None:
        """Update the phase and fire ``harness.state`` (no-op when unchanged)."""
        old_phase = self._phase
        if old_phase is new_phase:
            return
        self._phase = new_phase
        await self._events.trigger(
            _EVENT_STATE,
            old=old_phase,
            new=new_phase,
            session_id=self.session_id,
        )

    async def _emit_round(self, kind: str, round_id: int) -> None:
        """Fire ``harness.round`` for a round lifecycle transition."""
        await self._events.trigger(_EVENT_ROUND, kind=kind, round_id=round_id, result=None)

    # ------------------------------------------------------------------
    # Per-flavour turn mechanics
    # ------------------------------------------------------------------

    @abstractmethod
    async def _drive(self, inputs: dict[str, Any]) -> AsyncIterator[Any]:
        """Drive one round; yield an ``OutputSchema`` per surfaced narration line."""
        ...

    def _make_chunk(self, text: str, index: int) -> OutputSchema:
        """Wrap a narration summary as a stream output chunk (text content)."""
        return OutputSchema(type="llm_output", index=index, payload={"content": text, "result_type": "answer"})

    @abstractmethod
    async def steer(self, content: str) -> None:
        """Deliver content to the in-flight round (or buffer it)."""

    @abstractmethod
    async def follow_up(self, content: str) -> None:
        """Deliver content to be handled after the current turn."""

    @abstractmethod
    async def _abort_turn(self) -> None:
        """Signal the in-flight ``_drive`` to stop (flavour-specific). Idempotent."""
        ...

    @abstractmethod
    async def aclose(self) -> None:
        """Release the CLI transport. Idempotent."""

    # ---- MemberRuntime no-op hooks (external CLI members have none) ----

    def init_cwd_for_round(self) -> None:
        """No-op: the subprocess owns its working directory."""
        return None

    def has_pending_interrupt(self) -> bool:
        """External CLI members have no interrupt-resume concept."""
        return False

    def is_pending_interrupt_resume_valid(self, user_input: Any) -> bool:
        """External CLI members have no interrupt-resume concept."""
        return False

    def find_rails(self, rail_type: type) -> list[Any]:
        """No rails on a CLI-backed runtime."""
        return []

    async def register_rail(self, rail: Any) -> None:
        """No-op: CLI-backed runtime has no rail stack."""
        return None

    async def unregister_rail(self, rail: Any) -> None:
        """No-op: CLI-backed runtime has no rail stack."""
        return None

    def register_member_tools(self, memory_manager: Any) -> None:
        """No-op: external CLI members do not use the team memory toolkit."""
        return None

    async def inject_member_memory(self, memory_manager: Any, query: str) -> None:
        """No-op: external CLI members do not use team memory injection."""
        return None

    def set_background_task_controller(self, controller: Any) -> None:
        """No-op: external CLI members launch no background tools to control."""
        return None

    @property
    def workspace(self) -> Optional[Any]:
        """External CLI runtime exposes no team workspace handle."""
        return None

    @property
    def sys_operation(self) -> Optional[Any]:
        """External CLI runtime exposes no sys_operation handle."""
        return None


class ExternalCliRuntime(_CliRuntimeBase):
    """Streaming runtime: one long-lived CLI subprocess driven via stdin."""

    def __init__(
        self,
        *,
        member_name: str,
        adapter: CliAgentAdapter,
        injector: Injector,
        output_lines: AsyncIterator[str],
        process: ProcessLike | None = None,
        transport: ProcessTransport | None = None,
    ):
        """Bind to a launched CLI subprocess's input/output channels."""
        super().__init__(member_name=member_name, adapter=adapter)
        self._injector = injector
        self._output_lines = output_lines
        self._process = process
        self._transport = transport
        self._abort_requested = False
        self._stderr_task: Optional[asyncio.Task[str]] = None

    def _ensure_stderr_drain(self) -> None:
        """Start draining the long-lived process stderr exactly once.

        An unread stderr pipe fills and blocks the CLI's next stderr write
        (a stuck write looks like a hang), so drain it in the background for
        the lifetime of the process; the tail is surfaced at aclose.
        """
        if self._stderr_task is None and self._process is not None and self._process.stderr is not None:
            self._stderr_task = asyncio.create_task(_read_stderr_tail(self._process.stderr))

    async def _drive(self, inputs: dict[str, Any]) -> AsyncIterator[Any]:
        self._ensure_stderr_drain()
        query = inputs.get("query")
        text = query if isinstance(query, str) else str(query)
        self._abort_requested = False
        await self._injector.write(self._adapter.format_input(text))
        chunk_index = 0
        async for line in self._output_lines:
            if self._abort_requested:
                team_logger.debug("[{}] external cli turn aborted", self._member_name)
                return
            summary = self._adapter.summarize_output_line(line)
            if summary:
                team_logger.debug("[{}] {}", self._member_name, summary)
                yield self._make_chunk(summary, chunk_index)
                chunk_index += 1
            if self._adapter.is_turn_complete(line):
                return
        # The async-for ended without a turn-complete sentinel and without an
        # abort: stdout hit EOF. For a long-lived streaming CLI that means the
        # subprocess died mid-turn (auth / quota / credit exhaustion / crash).
        # Returning here would make a crashed turn look like a successful empty
        # turn, hiding the failure until aclose. Surface it now with the reason.
        await self._raise_on_premature_eof()

    async def _raise_on_premature_eof(self) -> None:
        """Raise a structured error when stdout EOF reflects a dead subprocess.

        Distinguishes a process still running (no handle, or returncode None —
        a degenerate adapter whose output iterator merely finished) from a
        crashed one (non-clean returncode). Only the latter is an error, and it
        carries the drained stderr tail so the failure reason is not lost.
        """
        returncode = self._process.returncode if self._process is not None else None
        if returncode in (0, None):
            # No subprocess to inspect, a clean exit, or still running: treat the
            # exhausted iterator as a benign turn boundary rather than a crash.
            return
        stderr_tail = await self._peek_stderr_tail()
        detail = f"; stderr: {stderr_tail}" if stderr_tail else ""
        raise_error(
            StatusCode.AGENT_TEAM_EXECUTION_ERROR,
            error_msg=(
                f"external CLI member '{self._member_name}' exited with code {returncode} mid-turn "
                f"(likely auth/quota/credit exhaustion or a crash){detail}"
            ),
        )

    async def _peek_stderr_tail(self) -> str:
        """Read the background stderr drain's tail without blocking forever.

        The drain task ends at stderr EOF; a dead process reaches EOF at once,
        so this normally returns immediately. The bounded wait guards the edge
        case where the stderr pipe is left half-open after the crash.
        """
        if self._stderr_task is None:
            return ""
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
            return await asyncio.wait_for(asyncio.shield(self._stderr_task), timeout=_STDERR_PEEK_TIMEOUT_S)
        return ""

    async def steer(self, content: str) -> None:
        """Inject content into the running CLI mid-turn."""
        await self._injector.write(self._adapter.format_input(content))

    async def follow_up(self, content: str) -> None:
        """Inject content for the CLI to handle after the current turn."""
        await self._injector.write(self._adapter.format_input(content))

    async def _abort_turn(self) -> None:
        """Stop the in-flight turn at the next output line (process survives)."""
        self._abort_requested = True

    async def aclose(self) -> None:
        """Close stdin and terminate the long-lived subprocess. Idempotent."""
        try:
            await self._injector.aclose()
            await _terminate(self._process)
            if self._stderr_task is not None:
                if not self._stderr_task.done():
                    self._stderr_task.cancel()
                stderr_tail = ""
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    stderr_tail = await self._stderr_task
                self._stderr_task = None
                returncode = self._process.returncode if self._process is not None else None
                if returncode not in (0, None) and stderr_tail:
                    team_logger.warning(
                        "[external-cli] member {} CLI exited with code {} (likely auth/quota/credit "
                        "exhaustion or a crash). stderr: {}",
                        self._member_name,
                        returncode,
                        stderr_tail,
                    )
        finally:
            if self._transport is not None:
                await self._transport.aclose()


class ReinvokeCliRuntime(_CliRuntimeBase):
    """One-shot runtime: a fresh CLI subprocess per turn (prompt as argv).

    Messages that arrive mid-turn (via steer/follow_up) cannot interrupt a
    one-shot process; they are buffered and drained as follow-up
    re-invocations within the same round before it returns, so nothing is
    lost. A turn completes at the subprocess's stdout EOF / exit.
    """

    def __init__(
        self,
        *,
        member_name: str,
        adapter: CliAgentAdapter,
        env: dict[str, str],
        cwd: Optional[str] = None,
        cli_session_id: Optional[str] = None,
        launch_extra_args: tuple[str, ...] = (),
        inactivity_timeout_s: float = _DEFAULT_INACTIVITY_TIMEOUT_S,
        turn_timeout_s: Optional[float] = None,
    ):
        """Hold the launch config; subprocesses are created per turn.

        Args:
            launch_extra_args: Extra launch args added to every re-invocation
                before the prompt (e.g. MCP-server registration), since a
                one-shot CLI needs them on each fresh process.
            inactivity_timeout_s: No-output ceiling. The deadline resets on
                every stdout OR stderr byte; the subprocess is terminated only
                after it has been SILENT this long. A wall-clock ceiling would
                kill a CLI doing long but active work (the common false
                positive); an inactivity ceiling only catches a truly
                hung / startup-stalled process. On timeout the subprocess is
                terminated and the turn ends, so buffered follow-ups re-invoke
                a fresh process.
            turn_timeout_s: Optional absolute wall-clock safety-net. Defaults to
                ``None`` (no absolute ceiling — the inactivity timeout is the
                primary guard). Set it to bound a pathological CLI that stays
                "active" forever by dribbling output just under the inactivity
                window.
        """
        super().__init__(member_name=member_name, adapter=adapter)
        self._env = env
        self._cwd = cwd
        self._cli_session_id = cli_session_id or uuid.uuid4().hex
        self._inactivity_timeout_s = inactivity_timeout_s
        self._turn_timeout_s = turn_timeout_s
        self._launch_extra_args = launch_extra_args
        self._first_turn = True
        self._pending: list[str] = []
        self._aborted = False
        self._current: Optional[asyncio.subprocess.Process] = None

    async def _drive(self, inputs: dict[str, Any]) -> AsyncIterator[Any]:
        query = inputs.get("query")
        prompt: Optional[str] = query if isinstance(query, str) else str(query)
        self._aborted = False
        chunk_index = 0
        # An abort may have landed before the first re-invocation; honour it
        # without launching a subprocess so abort always preempts the round.
        while prompt is not None and not self._aborted:
            # The per-turn stdout drain runs concurrently with the stderr drain
            # and the inactivity watchdog (see _consume_turn), so it cannot yield
            # directly. It pushes narration summaries onto this queue; we forward
            # them live and stop at the ``None`` sentinel that _run_once puts when
            # the turn ends on ANY path (complete / timeout / abort / spawn
            # error). The watchdog guarantees the sentinel always arrives, so
            # ``queue.get`` never blocks forever.
            queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
            turn_task = asyncio.create_task(self._run_once(prompt, queue))
            forwarded_cleanly = False
            try:
                while True:
                    summary = await queue.get()
                    if summary is None:
                        break
                    yield self._make_chunk(summary, chunk_index)
                    chunk_index += 1
                forwarded_cleanly = True
            finally:
                if not forwarded_cleanly:
                    # Consumer cancelled / generator closing mid-turn: stop the
                    # subprocess so the turn task unwinds promptly instead of
                    # waiting out the watchdog, then await it (suppressing the
                    # induced error) before the close propagates.
                    await self._abort_turn()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await turn_task
            # Normal path: await the turn so a spawn / run error propagates.
            await turn_task
            if self._aborted:
                return
            prompt = self._drain_pending()

    async def _run_once(self, prompt: str, queue: "asyncio.Queue[Optional[str]]") -> None:
        argv = self._adapter.build_turn_command(
            prompt,
            session_id=self._cli_session_id,
            first_turn=self._first_turn,
            extra_args=self._launch_extra_args,
        )
        self._first_turn = False
        team_logger.info("[external-cli] re-invoke {} for member {}", argv, self._member_name)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                cwd=self._cwd,
            )
            self._current = proc
            try:
                await self._consume_turn(proc, queue)
            finally:
                self._current = None
        finally:
            # Always release _drive's queue loop: the sentinel ends it on every
            # exit path, including a spawn failure before the subprocess exists.
            queue.put_nowait(None)

    async def _consume_turn(self, proc: asyncio.subprocess.Process, queue: "asyncio.Queue[Optional[str]]") -> None:
        """Drain stdout+stderr to EOF under inactivity guard, surface failures.

        Output (stdout or stderr) resets a shared inactivity deadline; the turn
        is terminated only after the subprocess has been silent for
        ``inactivity_timeout_s`` (plus an optional absolute ``turn_timeout_s``
        safety-net). This way a CLI doing long but active work is never killed,
        while a hung / startup-stalled process still gets terminated.

        Each parsed stdout narration summary is pushed onto ``queue`` live so
        ``_drive`` can surface it as a stream chunk while the turn runs.

        Stderr is drained concurrently so the CLI cannot block on a full stderr
        pipe; a non-zero exit is logged with the stderr tail so a failed turn
        (auth / quota / credit exhaustion / crash) is visible rather than
        looking like the member simply did nothing.
        """
        if proc.stdin is not None:
            proc.stdin.close()
        # Shared activity marker bumped by both readers; the watchdog reads it.
        last_activity = time.monotonic()
        stderr_tail = ""

        def _bump() -> None:
            nonlocal last_activity
            last_activity = time.monotonic()

        async def _drain_stderr() -> None:
            nonlocal stderr_tail
            if proc.stderr is None:
                return
            tail = b""
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                _bump()
                tail = (tail + chunk)[-_STDERR_TAIL_LIMIT:]
            stderr_tail = tail.decode("utf-8", errors="replace").strip()

        async def _drain_stdout() -> None:
            if proc.stdout is None:
                return
            while True:
                line = await proc.stdout.readline()
                if not line or self._aborted:
                    break
                _bump()
                summary = self._adapter.summarize_output_line(line.decode("utf-8", errors="replace").rstrip("\n"))
                if summary:
                    team_logger.debug("[{}] {}", self._member_name, summary)
                    queue.put_nowait(summary)

        async def _watchdog() -> None:
            start = time.monotonic()
            while True:
                idle_for = time.monotonic() - last_activity
                idle_remaining = self._inactivity_timeout_s - idle_for
                if idle_remaining <= 0:
                    raise _TurnTimeout(absolute=False)
                wait_s = idle_remaining
                if self._turn_timeout_s is not None:
                    abs_remaining = self._turn_timeout_s - (time.monotonic() - start)
                    if abs_remaining <= 0:
                        raise _TurnTimeout(absolute=True)
                    wait_s = min(wait_s, abs_remaining)
                await asyncio.sleep(wait_s)

        drain = asyncio.gather(_drain_stdout(), _drain_stderr())
        watchdog = asyncio.create_task(_watchdog())
        try:
            done, _pending = await asyncio.wait(
                {drain, watchdog},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if watchdog in done and not drain.done():
                # The watchdog fired before the drain finished: terminate the
                # silent / over-budget subprocess so its pipes close and the
                # drain tasks unwind, then re-raise to mark the turn ended.
                exc = watchdog.exception()
                absolute = isinstance(exc, _TurnTimeout) and exc.absolute
                team_logger.warning(
                    "[external-cli] member {} turn {} timeout; terminating subprocess",
                    self._member_name,
                    "absolute" if absolute else "inactivity",
                )
                await _terminate(proc)
                return
            # Drain finished first: the process closed its pipes on exit.
            await proc.wait()
            # A drain that ended because of abort means the user killed the
            # subprocess (negative returncode from the signal); that is not a
            # CLI failure, so do not report it as one.
            if not self._aborted:
                self._report_exit(proc.returncode, stderr_tail)
        finally:
            watchdog.cancel()
            drain.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watchdog
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await drain

    def _report_exit(self, returncode: Optional[int], stderr_tail: str) -> None:
        """Log a warning when the CLI turn exited non-zero."""
        if returncode in (0, None):
            return
        detail = f" stderr: {stderr_tail}" if stderr_tail else ""
        team_logger.warning(
            "[external-cli] member {} CLI turn exited with code {} and did no team work "
            "(likely auth/quota/credit exhaustion or a crash).{}",
            self._member_name,
            returncode,
            detail,
        )

    def _drain_pending(self) -> Optional[str]:
        if not self._pending:
            return None
        combined = self._pending[0] if len(self._pending) == 1 else _FOLLOWUP_SEP.join(self._pending)
        self._pending = []
        return combined

    async def steer(self, content: str) -> None:
        """Buffer content for a follow-up re-invocation (no mid-turn steer)."""
        self._pending.append(content)

    async def follow_up(self, content: str) -> None:
        """Buffer content for a follow-up re-invocation."""
        self._pending.append(content)

    async def _abort_turn(self) -> None:
        """Preempt the in-flight turn: kill the current subprocess and stop.

        Sets the abort flag and immediately terminates the running subprocess
        (if any) so a high-priority steer / abort does not have to wait for the
        turn to finish naturally. With the flag set, the ``_drive`` loop will
        not start another re-invocation. Idempotent and safe when no subprocess
        is running.
        """
        self._aborted = True
        await _terminate(self._current)

    async def aclose(self) -> None:
        """Terminate any in-flight subprocess. Idempotent."""
        await _terminate(self._current)


__all__ = ["ExternalCliRuntime", "ReinvokeCliRuntime"]
