# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stream + status adaptation for TeamAgent over a MemberRuntime.

The runtime (NativeHarness supervisor, or an external CLI runtime) owns round
driving and input delivery. This controller only:

- forwards ``runtime.outputs()`` chunks — tagged as ``TeamOutputSchema`` — into
  the member's ``stream_queue`` and to fan-out observers;
- maps the runtime's phase/round events onto MemberStatus / ExecutionStatus;
- forwards cancel/abort to the runtime.

It no longer drives rounds, queues pending inputs, or re-starts itself: the
single-supervisor model in the runtime makes those obsolete.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Optional,
)

from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.schema.status import (
    ExecutionStatus,
    MemberStatus,
)
from openjiuwen.agent_teams.schema.stream import TeamOutputSchema
from openjiuwen.core.common.logging import set_member_id, team_logger
from openjiuwen.core.session.stream.base import OutputSchema

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
    from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
    from openjiuwen.agent_teams.agent.state import TeamAgentState

# Async callback fired for each chunk produced by this controller, after the
# chunk has been tagged with the producing member name. Used by SpawnManager to
# forward in-process teammate chunks into the leader's stream_queue.
ChunkObserver = Callable[[OutputSchema], Awaitable[None]]

# Transient DeepAgent round errors surface as a ``task_failed`` chunk carrying a
# ``[code] ...`` prefix; code 181001 is retryable. The forwarder detects it,
# swallows the failed round's remaining chunks, and re-drives the round with a
# retry query. Exhausted / non-retryable failures are forwarded so the consumer
# sees the error (the supervisor model removed the old raise-based exhaustion).
_MAX_RETRY_ATTEMPTS = 10
_RETRYABLE_ERROR_CODES = {181001}
# How long the team must *stay* at rest before the idle marker is emitted.
# Member statuses cross the quiescent set constantly in the seams between
# rounds — a teammate settling to READY a beat before the leader picks its
# next move looks exactly like a finished team for a few milliseconds. The
# marker is only meaningful if the quiet holds, so it is debounced.
_TEAM_IDLE_DEBOUNCE_SECONDS = 2.0
_RETRY_QUERY = "刚才有异常状况，继续执行"
_TASK_FAILED_PAYLOAD_TYPE = "task_failed"
_ERROR_CODE_PATTERN = re.compile(r"^\[(\d+)\]")


def _detect_task_failed(chunk: Any) -> "tuple[int | None, str] | None":
    """Return ``(code, text)`` when ``chunk`` is a task_failed frame, else None."""
    payload = getattr(chunk, "payload", None)
    if payload is None:
        return None
    if getattr(payload, "type", None) != _TASK_FAILED_PAYLOAD_TYPE:
        return None
    text = ""
    data = getattr(payload, "data", None) or []
    if data:
        text = getattr(data[0], "text", "") or ""
    code: Optional[int] = None
    match = _ERROR_CODE_PATTERN.match(text)
    if match:
        try:
            code = int(match.group(1))
        except ValueError:
            code = None
    return code, text


class StreamController:
    """Adapts a MemberRuntime's output stream + lifecycle events to the team.

    Responsibilities:
    - Forward + tag the runtime's output chunks; fan out to observers.
    - Map runtime phase/round events onto MemberStatus / ExecutionStatus.
    - Forward cancel/abort to the runtime.
    """

    def __init__(
        self,
        *,
        blueprint_getter: Callable[[], Optional["TeamAgentBlueprint"]],
        state: "TeamAgentState",
        resources: "PrivateAgentResources",
        status_updater: Callable[[MemberStatus], Any],
        execution_updater: Callable[[ExecutionStatus], Any],
        wake_mailbox_callback: Optional[Callable[[], Any]] = None,
        request_completion_poll_callback: Optional[Callable[[], Awaitable[None]]] = None,
        task_board_probe: Optional[Callable[[], Awaitable[bool]]] = None,
    ):
        self._get_blueprint = blueprint_getter
        self._state = state
        self._resources = resources
        self._update_status = status_updater
        self._update_execution = execution_updater
        self._wake_mailbox_callback = wake_mailbox_callback
        # Fired at round-chain end (runtime back to IDLE) with no pending work.
        # The leader wires it to enqueue a POLL_TASK so team completion is
        # re-evaluated immediately; teammates pass None.
        self._request_completion_poll = request_completion_poll_callback
        # Second condition of the team-idle marker: "is the task board at
        # rest?". Asked once, only after the roster has held still for the
        # whole debounce window — a member view alone cannot tell a finished
        # team from one whose members all happen to be between rounds with
        # work still on the board. None means "unknown", which is what every
        # non-leader passes; it never schedules a marker anyway.
        self._task_board_probe = task_board_probe

        self.stream_queue: Optional[asyncio.Queue] = None
        # Observers fan-out chunks to external consumers (e.g. leader's
        # stream_queue receiving a teammate's chunks). Empty by default;
        # SpawnManager wires entries when a teammate is spawned in-process.
        self._chunk_observers: list[ChunkObserver] = []
        # Background task pumping runtime.outputs() into the stream; per cycle.
        self._forward_task: Optional[asyncio.Task] = None
        # Pending debounced team-idle marker (leader only). At most one is
        # ever alive: arming replaces, any movement cancels, and both stream
        # teardown paths cancel. See _schedule / _cancel below.
        self._idle_marker_task: Optional[asyncio.Task] = None
        # Transient-retry state (per cycle): attempts so far, and whether to
        # swallow the remaining chunks of a round that emitted a retryable
        # task_failed (reset when the next round starts).
        self._retry_attempt: int = 0
        self._swallow_failed_round: bool = False

    def _member_name(self) -> Optional[str]:
        bp = self._get_blueprint()
        return bp.member_name if bp else None

    # ------------------------------------------------------------------
    # Observers + chunk tagging
    # ------------------------------------------------------------------

    def add_chunk_observer(self, cb: ChunkObserver) -> None:
        """Register a chunk observer fired after each chunk is tagged.

        Observers run after the producing member name has been stamped onto the
        chunk and after the chunk has been put into this controller's own
        ``stream_queue``. An observer raising an exception is automatically
        detached so it cannot stall the producer's main stream.
        """
        self._chunk_observers.append(cb)

    def remove_chunk_observer(self, cb: ChunkObserver) -> None:
        """Detach a previously-registered observer; idempotent."""
        with contextlib.suppress(ValueError):
            self._chunk_observers.remove(cb)

    def _tag_chunk(self, chunk: Any) -> Any:
        """Stamp the producing member name and role onto the chunk.

        Plain ``OutputSchema`` instances are upgraded to ``TeamOutputSchema``;
        already-tagged chunks whose ``source_member`` and ``role`` match are
        returned untouched. Non-OutputSchema chunks pass through unchanged.
        """
        bp = self._get_blueprint()
        member_name = bp.member_name if bp else None
        role = bp.role if bp else None
        if not member_name or not isinstance(chunk, OutputSchema):
            return chunk
        if isinstance(chunk, TeamOutputSchema):
            if chunk.source_member == member_name and chunk.role == role:
                return chunk
            return chunk.model_copy(update={"source_member": member_name, "role": role})
        return TeamOutputSchema.from_output(chunk, source_member=member_name, role=role)

    # ------------------------------------------------------------------
    # Lifecycle: attach to / detach from the runtime for one run cycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Attach the output forwarder + status/round mappers to the runtime.

        Called once per run cycle (by coordination, after the runtime started).
        Idempotent on the forwarder task.
        """
        harness = self._resources.harness
        if harness is None:
            return
        self._retry_attempt = 0
        self._swallow_failed_round = False
        await harness.subscribe(on_state=self._map_state, on_round=self._map_round)
        if self._forward_task is None or self._forward_task.done():
            self._forward_task = asyncio.create_task(self._forward_outputs())

    async def stop(self) -> None:
        """Stop the output forwarder. The runtime unregisters its own events."""
        self.cancel_team_idle()
        task = self._forward_task
        self._forward_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _forward_outputs(self) -> None:
        """Pump runtime.outputs() into the stream queue + observers for the cycle."""
        harness = self._resources.harness
        if harness is None:
            return
        member_name = self._member_name()
        if member_name:
            set_member_id(member_name)
        try:
            async for chunk in harness.outputs():
                if await self._handle_retry(chunk):
                    continue
                if self._swallow_failed_round:
                    continue
                tagged = self._tag_chunk(chunk)
                if self.stream_queue is not None:
                    await self.stream_queue.put(tagged)
                # Fan out to observers; an observer raising must NOT block our
                # own stream — auto-detach so a misbehaving consumer cannot stall
                # the producer.
                for ob in list(self._chunk_observers):
                    try:
                        await ob(tagged)
                    except Exception:
                        team_logger.exception(
                            "[{}] chunk observer raised; detaching",
                            member_name or "?",
                        )
                        self.remove_chunk_observer(ob)
        # CancelledError is BaseException, never caught by ``except Exception`` —
        # cancellation propagates without an explicit re-raise clause.
        except Exception:
            team_logger.exception("[{}] output forwarder crashed", member_name or "?")

    async def _emit_retry_output(self, code: int | None, text: str) -> None:
        """Expose a retryable failure as visible streamed model output."""
        retry_chunk = self._tag_chunk(
            OutputSchema(
                type="llm_output",
                index=0,
                payload={
                    "content": (
                        f"\n\n[Retry {self._retry_attempt}/{_MAX_RETRY_ATTEMPTS}] "
                        f"{text}\n\n"
                    ),
                    "retrying": True,
                    "attempt": self._retry_attempt,
                    "max_attempts": _MAX_RETRY_ATTEMPTS,
                    "error_code": code,
                },
            )
        )
        if self.stream_queue is not None:
            await self.stream_queue.put(retry_chunk)
        for observer in list(self._chunk_observers):
            try:
                await observer(retry_chunk)
            except Exception:
                team_logger.exception(
                    "[{}] retry observer raised; detaching",
                    self._member_name() or "?",
                )
                self.remove_chunk_observer(observer)

    async def _handle_retry(self, chunk: Any) -> bool:
        """Detect a task_failed chunk and drive transient retry.

        Returns True when retry handling consumed the chunk (caller skips it). A
        retryable failure within the attempt budget swallows the rest of the
        failed round and re-drives it with a retry query (a follow-up round); an
        exhausted / non-retryable failure falls through (returns False) so the
        task_failed chunk reaches the consumer.
        """
        detected = _detect_task_failed(chunk)
        if detected is None:
            return False
        code, text = detected
        harness = self._resources.harness
        if harness is not None and code in _RETRYABLE_ERROR_CODES and self._retry_attempt < _MAX_RETRY_ATTEMPTS:
            self._retry_attempt += 1
            team_logger.warning(
                "DeepAgent round transient error (code=%s, attempt=%d/%d): %s",
                code,
                self._retry_attempt,
                _MAX_RETRY_ATTEMPTS,
                text,
            )
            self._swallow_failed_round = True
            await self._emit_retry_output(code, text)
            await harness.send(_RETRY_QUERY)
            return True
        team_logger.error(
            "DeepAgent round failed (code=%s, attempts=%d): %s",
            code,
            self._retry_attempt,
            text,
        )
        return False

    # ------------------------------------------------------------------
    # Status / execution mapping (driven by runtime events)
    # ------------------------------------------------------------------

    async def _map_state(self, new: HarnessState) -> None:
        """Map a runtime phase transition onto MemberStatus.

        ``RUNNING`` → BUSY, ``IDLE`` → READY (and round-chain-end settling).
        ``PAUSING`` / ``PAUSED`` / ``TERMINATED`` leave member status to the
        lifecycle layer.

        This edge is also where ``TeamAgentState.idle_since`` — the member's
        process-local idle clock feeding the stale-task sweep — is started
        and cleared. Pause-adjacent phases stay untouched here; the resume
        path re-bases the clock via ``TeamAgent.refresh_idle_baseline()``.
        """
        if new is HarnessState.RUNNING:
            self._state.idle_since = None
            await self._update_status(MemberStatus.BUSY)
        elif new is HarnessState.IDLE:
            self._state.idle_since = time.monotonic()
            await self._update_status(MemberStatus.READY)
            await self._on_idle_settled()

    async def _map_round(self, kind: str, result: Optional[dict] = None) -> None:
        """Map a runtime round event onto ExecutionStatus, walking legal edges."""
        _ = result
        if kind == "started":
            self._swallow_failed_round = False
            await self._update_execution(ExecutionStatus.STARTING)
            await self._update_execution(ExecutionStatus.RUNNING)
        elif kind == "finished":
            await self._update_execution(ExecutionStatus.COMPLETING)
            await self._update_execution(ExecutionStatus.COMPLETED)
            await self._update_execution(ExecutionStatus.IDLE)
        elif kind in ("aborted", "paused"):
            await self._update_execution(ExecutionStatus.CANCEL_REQUESTED)
            await self._update_execution(ExecutionStatus.CANCELLING)
            await self._update_execution(ExecutionStatus.CANCELLED)
            await self._update_execution(ExecutionStatus.IDLE)
        elif kind == "failed":
            await self._update_execution(ExecutionStatus.FAILED)
            await self._update_execution(ExecutionStatus.IDLE)

    async def _on_idle_settled(self) -> None:
        """Round chain ended (runtime IDLE): close on teardown, else poll/wake."""
        if self._state.team_cleaned:
            team_logger.info(
                "[{}] team_cleaned set; closing stream",
                self._member_name() or "?",
            )
            self.close_stream()
            return
        team_member = self._state.team_member
        if team_member is not None and await team_member.status() == MemberStatus.SHUTDOWN_REQUESTED:
            self.close_stream()
            return
        await self._wake_mailbox_if_interrupt_cleared()
        if self._request_completion_poll is not None:
            await self._request_completion_poll()

    async def _wake_mailbox_if_interrupt_cleared(self) -> None:
        """Notify owner so it can re-poll the mailbox after interrupt clears."""
        if self._wake_mailbox_callback is None:
            return
        result = self._wake_mailbox_callback()
        if asyncio.iscoroutine(result):
            await result

    # ------------------------------------------------------------------
    # Stream close / completion marker
    # ------------------------------------------------------------------

    def close_stream(self) -> None:
        """Enqueue the None sentinel that ends the member's stream loop.

        Also drops a pending team-idle marker: the stream is ending, so there
        is nobody left to read it. Covers the teardown paths that close the
        stream without going through :meth:`stop` (external ``stop_team``).
        """
        self.cancel_team_idle()
        if self.stream_queue is not None:
            self.stream_queue.put_nowait(None)

    def emit_completion_and_close(self, member_count: int, task_count: int) -> None:
        """Enqueue a team-completed marker chunk, then close the stream.

        The marker lands on ``stream_queue`` strictly before the ``None``
        sentinel, so a streaming consumer reads the completion signal before the
        stream ends. No-op when the queue is already gone.
        """
        if self.stream_queue is None:
            return
        bp = self._get_blueprint()
        member_name = bp.member_name if bp else None
        role = bp.role if bp else None
        marker = TeamOutputSchema(
            type="message",
            index=0,
            payload={
                "event_type": "team.completed",
                "member_count": member_count,
                "task_count": task_count,
            },
            source_member=member_name,
            role=role,
        )
        self.stream_queue.put_nowait(marker)
        self.close_stream()

    def schedule_team_idle(self) -> None:
        """Arm the debounced team-idle marker, replacing any pending one.

        The team has just gone quiet. Nothing is emitted yet: only if the
        quiet survives ``_TEAM_IDLE_DEBOUNCE_SECONDS`` *and* the task board is
        then found at rest does the marker land, which is what keeps a
        momentary lull between rounds from being reported as an idle team. Any
        movement in that window cancels it via :meth:`cancel_team_idle`.
        """
        self.cancel_team_idle()
        task = asyncio.create_task(self._emit_team_idle_after_debounce())
        self._idle_marker_task = task
        task.add_done_callback(self._on_idle_marker_done)

    def cancel_team_idle(self) -> None:
        """Drop a team-idle marker still waiting to fire; idempotent.

        Called on any observed movement and from both stream teardown paths
        (:meth:`stop` and :meth:`close_stream`), so no timer can outlive the
        stream it would have written to. The field is cleared *before* the
        cancel so the done callback — which runs later, on the event loop —
        cannot null out a marker armed in the meantime.
        """
        task = self._idle_marker_task
        self._idle_marker_task = None
        if task is not None and not task.done():
            task.cancel()

    def _on_idle_marker_done(self, task: asyncio.Task) -> None:
        """Clear the pending reference and surface a failed timer.

        Retrieving the exception is not optional: an unretrieved one prints
        "Task exception was never retrieved" at GC time and hides the real
        failure. Cancellation is the normal outcome here, not an error.
        """
        if self._idle_marker_task is task:
            self._idle_marker_task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            team_logger.error(
                "[{}] team-idle marker task failed: {}",
                self._member_name() or "?",
                error,
            )

    async def _emit_team_idle_after_debounce(self) -> None:
        """Wait out the debounce window, then emit if the team is still at rest.

        Two conditions, checked strictly in this order: every member has held
        still for the whole window, and only then is the task board asked
        whether anything is left open. The order is not cosmetic — the board
        query is a database round-trip, and asking it while members are still
        moving would both waste it and answer a question nobody is asking yet.

        ``CancelledError`` is deliberately not caught: the cancel path wants
        the task to end *as cancelled*, and swallowing it here would both
        report a clean finish and risk holding up loop shutdown. The re-check
        of ``is_idle()`` is belt-and-braces — the cancel on movement already
        covers it, but this way the marker can never contradict the registry.
        """
        await asyncio.sleep(_TEAM_IDLE_DEBOUNCE_SECONDS)
        registry = self._state.member_registry
        if registry is None or not registry.is_idle():
            return
        if not await self._is_task_board_settled():
            return
        snapshot = registry.snapshot()
        team_logger.info(
            "[{}] team went idle: {}",
            self._member_name() or "?",
            snapshot,
        )
        self.emit_team_idle(snapshot)

    async def _is_task_board_settled(self) -> bool:
        """Return whether the task board leaves nothing for the team to do.

        Delegates to the injected probe, which answers True for a board whose
        every task is terminal *and* for an empty board — a team that was
        never given tasks is idle the moment its members stop, and demanding a
        task there would make the marker unreachable. Without a probe (any
        non-leader) the question is unanswerable, so the member view is taken
        as the whole answer.

        Returns:
            True when nothing is left open, False when work remains.
        """
        if self._task_board_probe is None:
            return True
        return await self._task_board_probe()

    def emit_team_idle(self, members: dict[str, str]) -> None:
        """Enqueue a team-idle marker chunk; the stream stays open.

        Unlike :meth:`emit_completion_and_close` this is not a terminal
        signal — the team has merely gone quiet (no member is running) and may
        well be woken again by the next message. The consumer reads it as
        "nothing is moving right now"; the roster snapshot says who is in what
        status so it needs no follow-up query. No-op when the queue is gone
        (paused / torn-down member).

        Args:
            members: Member name to status value for the whole roster.
        """
        if self.stream_queue is None:
            return
        bp = self._get_blueprint()
        marker = TeamOutputSchema(
            type="message",
            index=0,
            payload={
                "event_type": "team.idle",
                "member_count": len(members),
                "members": members,
            },
            source_member=bp.member_name if bp else None,
            role=bp.role if bp else None,
        )
        self.stream_queue.put_nowait(marker)

    # ------------------------------------------------------------------
    # Cancel / abort (forwarded to the runtime; status follows round events)
    # ------------------------------------------------------------------

    async def cancel_agent(self) -> None:
        """Hard-cancel the in-flight round (rollback to last boundary)."""
        harness = self._resources.harness
        if harness is not None:
            await harness.abort(immediate=True)

    async def cooperative_cancel(self) -> None:
        """Ask the in-flight round to finish gracefully (no rollback)."""
        harness = self._resources.harness
        if harness is not None:
            await harness.abort(immediate=False)

    async def pause_agent(self) -> None:
        """Pause the in-flight round at its nearest inner iteration boundary.

        Unlike :meth:`cancel_agent`, the round is preserved: a parked model call
        is interrupted and rewound to the previous boundary, while a running
        iteration's tools finish first. :meth:`resume_agent` continues it.
        """
        harness = self._resources.harness
        if harness is not None:
            await harness.pause()

    async def resume_agent(self, *, query: str | None = None) -> None:
        """Continue a paused round in place, from its preserved context.

        ``query`` drives a cold resume (the harness was rebuilt and its context
        restored from a checkpoint); a warm resume needs none.
        """
        harness = self._resources.harness
        if harness is not None:
            await harness.resume(query=query)

    async def drain_agent_task(self) -> None:
        """Tear down the in-flight round during lifecycle stop / teardown.

        Hard-cancels the round: used by ``stop`` / ``destroy``, where it is being
        discarded outright. A lifecycle *pause* must not come here — it routes
        through :meth:`pause_agent`, which stops at a clean iteration boundary
        and keeps the round resumable.
        """
        await self.cancel_agent()

    # ------------------------------------------------------------------
    # Interrupt-resume queries (forwarded to the runtime)
    # ------------------------------------------------------------------

    def has_pending_interrupt(self) -> bool:
        """Return whether the runtime is waiting on an interrupt resume."""
        harness = self._resources.harness
        if harness is None:
            return False
        return harness.has_pending_interrupt()

    def is_valid_interrupt_resume(self, user_input: Any) -> bool:
        """Return whether ``user_input`` resolves the runtime's pending interrupt."""
        harness = self._resources.harness
        if harness is None:
            return False
        return harness.is_pending_interrupt_resume_valid(user_input)

    def is_agent_running(self) -> bool:
        """Return whether the runtime has an active round (phase RUNNING)."""
        harness = self._resources.harness
        return harness is not None and harness.state is HarnessState.RUNNING

    def has_in_flight_round(self) -> bool:
        """Return whether a round is in flight (phase RUNNING)."""
        return self.is_agent_running()
