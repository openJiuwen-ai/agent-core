# coding: utf-8
"""Stream and round management for TeamAgent."""

from __future__ import annotations

import asyncio
import contextlib
import re
import traceback
from typing import (
    Any,
    Callable,
    Optional,
    Tuple,
)

from openjiuwen.agent_teams.schema.status import (
    ExecutionStatus,
    MemberStatus,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.harness.deep_agent import DeepAgent

_MAX_RETRY_ATTEMPTS = 10
_RETRYABLE_ERROR_CODES = {181001}
_RETRY_QUERY = "刚才有异常状况，继续执行"
_TASK_FAILED_PAYLOAD_TYPE = "task_failed"
_ERROR_CODE_PATTERN = re.compile(r"^\[(\d+)\]")


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
        deep_agent_getter: Callable[[], Optional[DeepAgent]],
        member_name_getter: Callable[[], Optional[str]],
        status_updater: Callable[[MemberStatus], Any],
        execution_updater: Callable[[ExecutionStatus], Any],
        team_member_getter: Callable[[], Any],
        session_id_getter: Callable[[], Optional[str]],
        wake_mailbox_callback: Optional[Callable[[], Any]] = None,
    ):
        self._get_deep_agent = deep_agent_getter
        self._get_member_name = member_name_getter
        self._update_status = status_updater
        self._update_execution = execution_updater
        self._get_team_member = team_member_getter
        self._get_session_id = session_id_getter
        self._wake_mailbox_callback = wake_mailbox_callback

        self.stream_queue: Optional[asyncio.Queue] = None
        self.agent_task: Optional[asyncio.Task] = None
        self.streaming_active: bool = False
        self.pending_interrupt_resumes: list[InteractiveInput] = []
        self.pending_inputs: list[Any] = []

    def is_agent_running(self) -> bool:
        return self.streaming_active

    def has_in_flight_round(self) -> bool:
        return self.agent_task is not None and not self.agent_task.done()

    def has_pending_interrupt(self) -> bool:
        deep_agent = self._get_deep_agent()
        session = deep_agent.loop_session if deep_agent else None
        if session is None:
            return False
        return session.get_state(INTERRUPTION_KEY) is not None

    async def start_round(self, content: Any) -> None:
        deep_agent = self._get_deep_agent()
        if deep_agent is None or self.stream_queue is None:
            return
        preview = content if isinstance(content, str) else type(content).__name__
        team_logger.info("[{}] start_agent: {:.120}", self._get_member_name() or "?", str(preview))
        self.agent_task = asyncio.create_task(
            self._run_one_round(content),
        )
        self.agent_task.add_done_callback(self._log_agent_task_exception)

    async def steer(self, content: str) -> None:
        deep_agent = self._get_deep_agent()
        if deep_agent is not None:
            team_logger.debug("[{}] steer: {:.120}", self._get_member_name() or "?", content)
            await deep_agent.steer(content)

    async def follow_up(self, content: str) -> None:
        deep_agent = self._get_deep_agent()
        if deep_agent is not None:
            team_logger.debug("[{}] follow_up: {:.120}", self._get_member_name() or "?", content)
            await deep_agent.follow_up(content)

    async def cancel_agent(self) -> None:
        await self._update_execution(ExecutionStatus.CANCEL_REQUESTED)
        if self.agent_task and not self.agent_task.done():
            await self._update_execution(ExecutionStatus.CANCELLING)
            self.agent_task.cancel()

    def close_stream(self) -> None:
        if self.stream_queue is not None:
            self.stream_queue.put_nowait(None)

    async def drain_agent_task(self) -> None:
        task = self.agent_task
        if task is None or task.done():
            return
        self.pending_inputs.clear()
        self.pending_interrupt_resumes.clear()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    def _log_agent_task_exception(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        team_logger.exception(
            "[{}] _run_one_round task crashed silently",
            self._get_member_name() or "?",
            stacktrace="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )

    async def _run_one_round(self, message: Any) -> None:
        deep_agent = self._get_deep_agent()
        if deep_agent and deep_agent.deep_config and deep_agent.deep_config.workspace:
            from openjiuwen.core.sys_operation.cwd import init_cwd

            init_root = deep_agent.deep_config.workspace.root_path
            init_cwd(init_root, workspace=init_root)

        await self._update_status(MemberStatus.READY)
        await self._update_status(MemberStatus.BUSY)
        cancelled = False
        try:
            await self._execute_round(message)
            team_member = self._get_team_member()
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
            if not cancelled:
                next_resume = self._dequeue_valid_interrupt_resume()
                if next_resume is not None and self.stream_queue is not None:
                    await self.start_round(next_resume)
                elif self.pending_inputs and self.stream_queue is not None:
                    drained = self.pending_inputs
                    self.pending_inputs = []
                    if len(drained) == 1:
                        combined = drained[0]
                    else:
                        combined = "\n\n---\n\n".join(
                            item if isinstance(item, str) else str(item) for item in drained
                        )
                    await self.start_round(combined)
                else:
                    await self._wake_mailbox_if_interrupt_cleared()
                    team_member = self._get_team_member()
                    if team_member and await team_member.status() == MemberStatus.SHUTDOWN_REQUESTED:
                        self.close_stream()

    async def _stream_one_round(self, query: Any) -> Optional[Tuple[Optional[int], str]]:
        deep_agent = self._get_deep_agent()
        inputs = {"query": query}
        error_seen = False
        error_code: Optional[int] = None
        error_text: str = ""
        self.streaming_active = True
        try:
            async for chunk in Runner.run_agent_streaming(
                deep_agent,
                inputs,
                session=self._get_session_id(),
            ):
                if error_seen:
                    continue
                detected = _detect_task_failed(chunk)
                if detected is not None:
                    error_seen = True
                    error_code, error_text = detected
                    continue
                if self.stream_queue is not None:
                    await self.stream_queue.put(chunk)
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
        if not isinstance(user_input, InteractiveInput):
            return False
        deep_agent = self._get_deep_agent()
        session = deep_agent.loop_session if deep_agent else None
        if session is None:
            return False
        state = session.get_state(INTERRUPTION_KEY)
        if state is None:
            return False
        interrupted = getattr(state, "interrupted_tools", {}) or {}
        pending_ids = set()
        for entry in interrupted.values():
            requests = getattr(entry, "interrupt_requests", {}) or {}
            pending_ids.update(requests.keys())
        if not pending_ids:
            return False
        resume_ids = set(user_input.user_inputs.keys())
        return bool(resume_ids) and resume_ids.issubset(pending_ids)

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
