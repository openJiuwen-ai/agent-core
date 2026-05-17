# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stream and round management for TeamAgent."""

from __future__ import annotations

import asyncio
import contextlib
import re
import traceback
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Optional,
    Tuple,
)

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.schema.status import (
    ExecutionStatus,
    MemberStatus,
)
from openjiuwen.agent_teams.schema.stream import TeamOutputSchema
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import set_member_id, team_logger
from openjiuwen.core.session.stream.base import OutputSchema

if TYPE_CHECKING:
    from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
    from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
    from openjiuwen.agent_teams.agent.state import TeamAgentState
    from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

# Async callback fired for each chunk produced by this StreamController,
# after the chunk has been tagged with the producing member name. Used by
# SpawnManager to forward in-process teammate chunks into the leader's
# stream_queue.
ChunkObserver = Callable[[OutputSchema], Awaitable[None]]

_MAX_RETRY_ATTEMPTS = 10
_RETRYABLE_ERROR_CODES = {181001}
_RETRY_QUERY = "刚才有异常状况，继续执行"
_TASK_FAILED_PAYLOAD_TYPE = "task_failed"
_ERROR_CODE_PATTERN = re.compile(r"^\[(\d+)\]")
# Soft deadline for cooperative cancel before falling back to Task.cancel.
# Picked low enough that a stuck task loop doesn't block teardown for long,
# but high enough that a normally-responsive abort handler can drain its
# work (model output flush, state persistence) without being interrupted.
_COOPERATIVE_ABORT_TIMEOUT_SECONDS = 2.0


def _detect_task_failed(chunk: Any) -> Optional[Tuple[Optional[int], str]]:
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
    """Manages agent execution rounds, streaming, and input delivery.

    Responsibilities:
    - Round lifecycle management
    - Streaming control and chunk handling
    - Input queuing and delivery
    - Interrupt handling
    - Retry logic
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
    ):
        self._get_blueprint = blueprint_getter
        self._state = state
        self._resources = resources
        self._update_status = status_updater
        self._update_execution = execution_updater
        self._wake_mailbox_callback = wake_mailbox_callback

        self.stream_queue: Optional[asyncio.Queue] = None
        self.agent_task: Optional[asyncio.Task] = None
        self.streaming_active: bool = False
        self.pending_interrupt_resumes: list[InteractiveInput] = []
        self.pending_inputs: list[Any] = []
        # Observers fan-out chunks to external consumers (e.g. leader's
        # stream_queue receiving a teammate's chunks). Empty by default;
        # SpawnManager wires entries when a teammate is spawned in-process.
        self._chunk_observers: list[ChunkObserver] = []
        # Tracks whether the in-flight round was cancelled by the team
        # (cancel_agent / drain / shutdown) rather than completing on its
        # own. Cooperative aborts let the round exit without raising
        # CancelledError, so this flag is needed to keep the
        # ExecutionStatus state machine honest and to gate the post-round
        # restart paths in ``_run_one_round``.
        self._cancel_requested: bool = False

    def _member_name(self) -> Optional[str]:
        bp = self._get_blueprint()
        return bp.member_name if bp else None

    def add_chunk_observer(self, cb: ChunkObserver) -> None:
        """Register a chunk observer fired after each chunk is tagged.

        Observers run after the producing member name has been stamped
        onto the chunk and after the chunk has been put into this
        controller's own ``stream_queue``. An observer raising an
        exception is automatically detached so it cannot stall the
        producer's main stream.
        """
        self._chunk_observers.append(cb)

    def remove_chunk_observer(self, cb: ChunkObserver) -> None:
        """Detach a previously-registered observer; idempotent."""
        with contextlib.suppress(ValueError):
            self._chunk_observers.remove(cb)

    def _tag_chunk(self, chunk: Any) -> Any:
        """Stamp the producing member name and role onto the chunk.

        Plain ``OutputSchema`` instances are upgraded to
        ``TeamOutputSchema`` via :meth:`TeamOutputSchema.from_output`;
        already-tagged chunks whose ``source_member`` and ``role``
        match are returned untouched. Non-OutputSchema chunks pass
        through unchanged so custom stream payloads are preserved.
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

    def is_agent_running(self) -> bool:
        return self.streaming_active

    def has_in_flight_round(self) -> bool:
        return self.agent_task is not None and not self.agent_task.done()

    def has_pending_interrupt(self) -> bool:
        harness = self._resources.harness
        if harness is None:
            return False
        return harness.has_pending_interrupt()

    async def start_round(self, content: Any) -> None:
        harness = self._resources.harness
        if harness is None or self.stream_queue is None:
            return
        preview = content if isinstance(content, str) else type(content).__name__
        team_logger.info("[{}] start_agent: {:.120}", self._member_name() or "?", str(preview))
        self.agent_task = asyncio.create_task(
            self._run_one_round(content),
        )
        self.agent_task.add_done_callback(self._log_agent_task_exception)

    async def steer(self, content: str) -> None:
        harness = self._resources.harness
        if harness is not None:
            await harness.steer(content)

    async def follow_up(self, content: str) -> None:
        harness = self._resources.harness
        if harness is not None:
            await harness.follow_up(content)

    async def cancel_agent(self) -> None:
        """Cancel the in-flight round, advancing the execution state machine.

        Idempotent: when no round is running (``agent_task`` is ``None``
        or already ``done``) this is a no-op. The ``RUNNING ->
        CANCEL_REQUESTED -> CANCELLING`` walk is required by
        ``EXECUTION_TRANSITIONS`` so the downstream ``CANCELLED`` and
        ``IDLE`` writes in ``_execute_round`` land on legal edges.
        """
        if self.agent_task is None or self.agent_task.done():
            return
        await self._update_execution(ExecutionStatus.CANCEL_REQUESTED)
        await self._update_execution(ExecutionStatus.CANCELLING)
        await self.cooperative_cancel()

    def close_stream(self) -> None:
        if self.stream_queue is not None:
            self.stream_queue.put_nowait(None)

    async def drain_agent_task(self) -> None:
        """Tear down the in-flight round during lifecycle pause/stop.

        Equivalent to ``cancel_agent`` plus pending-queue cleanup:
        ``pending_inputs`` / ``pending_interrupt_resumes`` are wiped so
        any teardown-time follow-up cannot survive the kernel
        pause/stop. State-machine advancement lives entirely in
        ``cancel_agent`` — the two entry points must never diverge on
        what they tell the execution status machine.
        """
        self.pending_inputs.clear()
        self.pending_interrupt_resumes.clear()
        await self.cancel_agent()

    async def cooperative_cancel(self) -> None:
        """Ask the underlying task loop to abort, then hard-cancel if needed.

        Two-phase shutdown:

        1. Set ``_cancel_requested`` and call ``harness.abort()`` so the
           DeepAgent task loop exits at its next safe checkpoint. This
           lets in-flight model output flush and state persistence run.
        2. Wait up to ``_COOPERATIVE_ABORT_TIMEOUT_SECONDS`` for the
           round task to complete naturally. If the deadline passes,
           fall back to ``task.cancel`` so an unresponsive loop cannot
           block teardown indefinitely.

        Suppresses every exception the task surfaces — the caller has
        already declared intent to cancel.
        """
        task = self.agent_task
        if task is None or task.done():
            return
        self._cancel_requested = True
        harness = self._resources.harness
        if harness is not None:
            try:
                await harness.abort()
            except Exception as exc:
                team_logger.debug(
                    "[{}] harness.abort failed: {}",
                    self._member_name() or "?",
                    exc,
                )
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_COOPERATIVE_ABORT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        except (asyncio.CancelledError, Exception):
            pass

    def _log_agent_task_exception(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        team_logger.exception(
            "[{}] _run_one_round task crashed silently",
            self._member_name() or "?",
            stacktrace="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )

    async def _run_one_round(self, message: Any) -> None:
        # Re-assert the member identity on this round task. A round may
        # be driven from a task context that never ran the coordination
        # kernel's ``set_member_id`` -- a human agent's round, for
        # instance, is started by ``HumanAgentInbox`` from the leader's
        # interact path rather than from the agent's own coordination
        # loop. Without this, status updates, event publishing and logs
        # inside the round carry an empty ``member_id`` contextvar.
        member_name = self._member_name()
        if member_name:
            set_member_id(member_name)
        # Reset cancel state for the new round. cooperative_cancel sets
        # this flag for the *current* round only; otherwise a stale True
        # from a prior aborted round would suppress restarts here.
        self._cancel_requested = False
        harness = self._resources.harness
        if harness is not None:
            harness.init_cwd_for_round()

        await self._update_status(MemberStatus.READY)
        await self._update_status(MemberStatus.BUSY)
        cancelled = False
        try:
            await self._execute_round(message)
            team_member = self._state.team_member
            if team_member is None or await team_member.status() != MemberStatus.SHUTDOWN_REQUESTED:
                await self._update_status(MemberStatus.READY)
        except asyncio.CancelledError:
            # Task was cancelled (e.g. session switch tearing down the
            # agent). Skip the post-round restart paths so we don't
            # immediately resurrect the round we just cancelled.
            cancelled = True
            raise
        except BaseException as e:
            team_logger.error("Failed to execute deep agent, {}", e, exc_info=True)
            await self._update_status(MemberStatus.ERROR)
        finally:
            self.agent_task = None
            # Highest-priority terminal condition: this round just cleaned
            # the team (the clean_team success callback latched
            # state.team_cleaned). Close the stream so the leader's
            # invoke/stream loop breaks on the None sentinel — the leader
            # deliberately ignores its own TeamCleanedEvent, so this is the
            # only path that ends a TEMPORARY-team leader's stream. Do NOT
            # restart for pending interrupt resumes / pending inputs: the
            # team is gone. A double None enqueue (e.g. the cancel path
            # also enqueued one) is harmless — the outer loop breaks on the
            # first None and finalize_round nulls the queue.
            if self._state.team_cleaned:
                team_logger.info(
                    "[{}] team_cleaned set; closing stream after round",
                    self._member_name() or "?",
                )
                self.close_stream()
            # Cooperative abort exits without CancelledError, so check
            # _cancel_requested as well to suppress the restart paths.
            elif not cancelled and not self._cancel_requested:
                next_resume = self._dequeue_valid_interrupt_resume()
                if next_resume is not None and self.stream_queue is not None:
                    await self.start_round(next_resume)
                elif self.pending_inputs and self.stream_queue is not None:
                    drained = self.pending_inputs
                    self.pending_inputs = []
                    if len(drained) == 1:
                        combined = drained[0]
                    else:
                        combined = "\n\n---\n\n".join(item if isinstance(item, str) else str(item) for item in drained)
                    await self.start_round(combined)
                else:
                    await self._wake_mailbox_if_interrupt_cleared()
                    team_member = self._state.team_member
                    if team_member and await team_member.status() == MemberStatus.SHUTDOWN_REQUESTED:
                        self.close_stream()

    async def _stream_one_round(self, query: Any) -> Optional[Tuple[Optional[int], str]]:
        harness = self._resources.harness
        inputs = {"query": query}
        error_seen = False
        error_code: Optional[int] = None
        error_text: str = ""
        self.streaming_active = True
        try:
            async for chunk in harness.run_streaming(
                inputs,
                session_id=get_session_id() or None,
            ):
                if error_seen:
                    continue
                detected = _detect_task_failed(chunk)
                if detected is not None:
                    error_seen = True
                    error_code, error_text = detected
                    continue
                tagged = self._tag_chunk(chunk)
                if self.stream_queue is not None:
                    await self.stream_queue.put(tagged)
                # Fan out to observers; an observer raising must NOT
                # block our own stream — auto-detach so a misbehaving
                # consumer cannot stall the producer.
                for ob in list(self._chunk_observers):
                    try:
                        await ob(tagged)
                    except Exception:
                        team_logger.exception(
                            "[{}] chunk observer raised; detaching",
                            self._member_name() or "?",
                        )
                        self.remove_chunk_observer(ob)
        finally:
            self.streaming_active = False

        if not error_seen:
            return None
        return error_code, error_text

    async def _run_retrying_stream(self, initial_query: Any) -> None:
        current_query: Any = initial_query
        attempt = 0
        while True:
            outcome = await self._stream_one_round(current_query)
            if outcome is None:
                return

            error_code, error_text = outcome
            if error_code in _RETRYABLE_ERROR_CODES and attempt < _MAX_RETRY_ATTEMPTS:
                attempt += 1
                team_logger.warning(
                    "DeepAgent round transient error (code=%s, attempt=%d/%d): %s",
                    error_code,
                    attempt,
                    _MAX_RETRY_ATTEMPTS,
                    error_text,
                )
                current_query = _RETRY_QUERY
                continue

            team_logger.error(
                "DeepAgent round failed (code=%s, attempts=%d): %s",
                error_code,
                attempt,
                error_text,
            )
            raise build_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg=(
                    f"streaming task failed after {attempt} retries, last error code={error_code}: {error_text}"
                ),
            )

    async def _execute_round(self, message: Any) -> None:
        await self._update_execution(ExecutionStatus.STARTING)
        await self._update_execution(ExecutionStatus.RUNNING)
        try:
            await self._run_retrying_stream(message)
            # Cooperative abort path: the stream finished without
            # raising, but cancel was requested. Surface CANCELLED
            # rather than COMPLETED so the state machine matches user
            # intent.
            if self._cancel_requested:
                await self._update_execution(ExecutionStatus.CANCELLED)
            else:
                await self._update_execution(ExecutionStatus.COMPLETING)
                await self._update_execution(ExecutionStatus.COMPLETED)
        except asyncio.CancelledError:
            await self._update_execution(ExecutionStatus.CANCELLED)
            raise
        except asyncio.TimeoutError:
            await self._update_execution(ExecutionStatus.TIMED_OUT)
            raise
        except Exception as e:
            team_logger.error("DeepAgent round error: %s", e)
            await self._update_execution(ExecutionStatus.FAILED)
            raise
        finally:
            await self._update_execution(ExecutionStatus.IDLE)

    def is_valid_interrupt_resume(self, user_input: Any) -> bool:
        harness = self._resources.harness
        if harness is None:
            return False
        return harness.is_pending_interrupt_resume_valid(user_input)

    def _dequeue_valid_interrupt_resume(self) -> Optional[InteractiveInput]:
        while self.pending_interrupt_resumes:
            candidate = self.pending_interrupt_resumes.pop(0)
            if self.is_valid_interrupt_resume(candidate):
                return candidate
        return None

    async def _wake_mailbox_if_interrupt_cleared(self) -> None:
        """Notify owner so it can re-poll the mailbox after interrupt clears."""
        if self._wake_mailbox_callback is None:
            return
        result = self._wake_mailbox_callback()
        if asyncio.iscoroutine(result):
            await result
