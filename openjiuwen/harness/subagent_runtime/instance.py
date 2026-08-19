# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Single subagent instance with a serial asyncio worker."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.harness.subagent_lifecycle import (
    cleanup_subagent_task_resources,
    prepare_subagent_task_resources,
)
from openjiuwen.harness.subagent_runtime.models import (
    ShutdownOp,
    SubagentOp,
    SubagentStatus,
    SubagentStatusKind,
    UserInputOp,
)
from openjiuwen.harness.subagent_runtime.status import StatusChannel, StatusReceiver
from openjiuwen.harness.subagent_runtime.stream_output import TurnOutputAggregator


async def _close_session_quietly(session: Any) -> None:
    close_stream = getattr(session, "close_stream", None)
    if not callable(close_stream):
        return
    try:
        result = close_stream()
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:
        logger.debug(
            "Failed to close subagent session stream quietly: %s",
            exc,
            exc_info=True,
        )


class SubagentInstance:
    """One live subagent with a per-turn session factory and serial worker."""

    def __init__(
        self,
        *,
        subagent_id: str,
        subagent_type: str,
        display_name: str,
        role: str,
        parent_session_id: str,
        agent: Any,
        session_factory: Callable[[], Any],
        running_semaphore: asyncio.Semaphore,
        on_chunk: Callable[[Any], Awaitable[None]] | None = None,
        turn_timeout_s: float | None = None,
        include_parent_session_id: bool = False,
        on_turn_start: Callable[[], None] | None = None,
        on_turn_finished: Callable[[bool], Awaitable[None]] | None = None,
        on_status_changed: Callable[[SubagentStatus], Awaitable[None]] | None = None,
        on_turn_stream_start: Callable[[UserInputOp], Awaitable[None]] | None = None,
        on_turn_stream_end: Callable[[UserInputOp, TurnOutputAggregator], Awaitable[None]] | None = None,
    ) -> None:
        self.subagent_id = subagent_id
        self.subagent_type = subagent_type
        self.display_name = display_name
        self.role = role
        self.parent_session_id = parent_session_id

        self.status = StatusChannel()
        self.last_output: str | None = None
        self.last_task_id: str | None = None
        self.current_task_id: str | None = None

        self._agent = agent
        self._session_factory = session_factory
        self._on_chunk = on_chunk
        self._turn_timeout_s = turn_timeout_s
        self._include_parent_session_id = include_parent_session_id
        self._on_turn_start = on_turn_start
        self._on_turn_finished = on_turn_finished
        self._on_status_changed = on_status_changed
        self._on_turn_stream_start = on_turn_stream_start
        self._on_turn_stream_end = on_turn_stream_end

        self._ops: asyncio.Queue[SubagentOp] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._current_run: asyncio.Task[None] | None = None
        self._running_semaphore = running_semaphore
        self._interrupt_requested = False
        self._closed = False

    def agent_status(self) -> SubagentStatus:
        return self.status.current()

    def revision(self) -> int:
        return self.status.version()

    def subscribe_status(self) -> StatusReceiver:
        return self.status.subscribe()

    async def enqueue(self, op: SubagentOp) -> None:
        await self._ops.put(op)

    async def interrupt(self) -> bool:
        run = self._current_run
        if run is None or run.done():
            return False
        self._interrupt_requested = True
        run.cancel()
        await asyncio.wait({run})
        return True

    async def start_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_main())

    async def shutdown(self, reason: str) -> None:
        await self.interrupt()
        await self.enqueue(ShutdownOp(reason=reason))
        if self._worker_task is not None:
            await self._worker_task

    def is_evictable(self) -> bool:
        if self._closed:
            return False
        kind = self.status.current().kind
        if kind == SubagentStatusKind.RUNNING:
            return False
        if self._current_run is not None and not self._current_run.done():
            if kind in {SubagentStatusKind.RUNNING, SubagentStatusKind.PENDING_INIT}:
                return False
        return self._ops.empty()

    def is_closed(self) -> bool:
        return self._closed

    def has_pending_work(self) -> bool:
        """Return True when a turn is active or queued after a prior final status."""
        if self._closed:
            return False
        if not self._ops.empty():
            return True
        run = self._current_run
        if run is not None and not run.done():
            return True
        kind = self.status.current().kind
        return kind in {SubagentStatusKind.PENDING_INIT, SubagentStatusKind.RUNNING}

    async def _set_status(self, status: SubagentStatus) -> None:
        await self.status.set(status)
        if self._on_status_changed is not None:
            await self._on_status_changed(status)

    async def _worker_main(self) -> None:
        while True:
            op = await self._ops.get()
            try:
                if isinstance(op, ShutdownOp):
                    await self._handle_shutdown(op.reason)
                    return
                await self._handle_user_input(op)
            finally:
                self._ops.task_done()

    async def _handle_user_input(self, op: UserInputOp) -> None:
        self.current_task_id = op.task_id

        async with self._running_semaphore:
            self._interrupt_requested = False
            await self._set_status(SubagentStatus.running())
            self._current_run = asyncio.create_task(self._run_one_turn(op))
            try:
                if self._turn_timeout_s and self._turn_timeout_s > 0:
                    await asyncio.wait_for(self._current_run, timeout=self._turn_timeout_s)
                else:
                    await self._current_run
            except asyncio.TimeoutError:
                await self._on_turn_timeout()
            except asyncio.CancelledError as exc:
                await self._on_turn_cancelled(exc)
            except Exception as exc:
                logger.warning(
                    "[SubagentInstance] turn failed: subagent_id=%s error=%s",
                    self.subagent_id,
                    exc,
                    exc_info=True,
                )
                if not self.status.current().is_final():
                    await self._set_status(SubagentStatus.errored(str(exc)))
            finally:
                self._current_run = None

    async def _on_turn_timeout(self) -> None:
        if not self.status.current().is_final():
            await self._set_status(
                SubagentStatus.errored("turn timeout", code="TIMEOUT"),
            )

    async def _on_turn_cancelled(self, exc: asyncio.CancelledError) -> None:
        if not self.status.current().is_final():
            await self._set_status(SubagentStatus.interrupted())

        if self._interrupt_requested:
            self._interrupt_requested = False
            return

        run = self._current_run
        if run is not None and not run.done():
            run.cancel()
        raise exc

    def _build_stream_inputs(self, op: UserInputOp) -> dict[str, str]:
        inputs: dict[str, str] = {
            "query": op.query,
            "conversation_id": self.subagent_id,
        }
        if self._include_parent_session_id:
            inputs["parent_session_id"] = self.parent_session_id
        return inputs

    async def _run_one_turn(self, op: UserInputOp) -> None:
        session = self._session_factory()
        aggregator = TurnOutputAggregator()
        succeeded = False
        try:
            await session.pre_run()
            await prepare_subagent_task_resources(self._agent)
            if self._on_turn_start is not None:
                self._on_turn_start()
            if self._on_turn_stream_start is not None:
                await self._on_turn_stream_start(op)
            inputs = self._build_stream_inputs(op)
            gen = self._agent.stream(inputs, session=session)
            async with contextlib.aclosing(gen):
                async for chunk in gen:
                    aggregator.consume(chunk)
                    if self._on_chunk is not None:
                        await self._on_chunk(chunk)
            # Drain the turn tail before settling: the terminal status doubles as
            # the turn-end signal, so nothing may be emitted after it.
            if self._on_turn_stream_end is not None:
                await self._on_turn_stream_end(op, aggregator)
            await self._settle_turn(op, aggregator)
            succeeded = not aggregator.is_error() and self.status.current().kind is SubagentStatusKind.COMPLETED
        except BaseError as exc:
            await self._set_status(
                SubagentStatus.errored(str(exc), code=exc.status.name),
            )
            raise
        except Exception as exc:
            await self._set_status(SubagentStatus.errored(str(exc)))
            raise
        finally:
            await self._finalize_turn(session, succeeded=succeeded)

    async def _settle_turn(self, op: UserInputOp, aggregator: TurnOutputAggregator) -> None:
        output = aggregator.output()
        if aggregator.is_error():
            await self._set_status(
                SubagentStatus.errored(output or "subagent stream reported error"),
            )
            return
        self.last_output = output
        self.last_task_id = op.task_id
        await self._set_status(SubagentStatus.completed(output))

    async def _finalize_turn(self, session: Any, *, succeeded: bool) -> None:
        task = asyncio.create_task(
            self._finalize_turn_inner(session, succeeded=succeeded),
        )
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)

    async def _finalize_turn_inner(self, session: Any, *, succeeded: bool) -> None:
        await cleanup_subagent_task_resources(self._agent)
        await _close_session_quietly(session)
        if self._on_turn_finished is not None:
            await self._on_turn_finished(succeeded)

    async def _handle_shutdown(self, reason: str) -> None:
        if self._closed:
            return
        run = self._current_run
        if run is not None and not run.done():
            self._interrupt_requested = True
            run.cancel()
            await asyncio.wait({run})
        await self._set_status(SubagentStatus.closed(reason))
        await self.status.close()
        self._closed = True
