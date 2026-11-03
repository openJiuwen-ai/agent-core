# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Run-owned subagent executions that outlive an individual wait call."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import math
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from time import monotonic
from typing import Any, Awaitable, Callable
from uuid import uuid4

from openjiuwen.core.common.logging import logger

_TERMINAL = frozenset({"completed", "failed", "timed_out", "cancelled"})


@dataclass(frozen=True)
class SubagentTaskScope:
    owner_id: str
    parent_session_id: str
    run_id: str


@dataclass(frozen=True)
class TaskRequest:
    subagent_type: str
    task_description: str
    browser_capabilities: tuple[str, ...] | None = None


@dataclass
class SubagentExecution:
    scope: SubagentTaskScope
    task_id: str
    dispatch_tool_call_id: str
    dispatch_index: int
    request: TaskRequest
    child_session_id: str
    subagent: Any = None
    resources_prepared: bool = False
    affinity_enabled: bool = False
    raw_result: dict | None = None


@dataclass
class TaskBatchReceipt:
    fingerprint: str
    submitted: list[dict]
    wait_ids: list[str]


@dataclass
class TaskWaitResult:
    ready: list[dict]
    pending_ids: list[str]
    timed_out: bool = False
    interruptions: dict[str, dict] = field(default_factory=dict)


@dataclass
class _Record:
    execution: SubagentExecution
    execute: Callable[[Any], Awaitable[dict]]
    cleanup: Callable[[], Awaitable[None]]
    remaining_s: float
    status: str = "running"
    error: dict | None = None
    task: asyncio.Task | None = None
    cleanup_task: asyncio.Task | None = None
    delivered: bool = False

    def snapshot(self) -> dict:
        execution = self.execution
        result = execution.raw_result or {}
        return {
            "task_id": execution.task_id,
            "dispatch_tool_call_id": execution.dispatch_tool_call_id,
            "dispatch_index": execution.dispatch_index,
            "status": self.status,
            "result_type": result.get("result_type"),
            "output": result.get("output", ""),
            "error": self.error,
        }


@dataclass
class _Run:
    scope: SubagentTaskScope
    records: dict[str, _Record] = field(default_factory=dict)
    receipts: dict[str, TaskBatchReceipt] = field(default_factory=dict)
    closed: bool = False
    closing: bool = False
    close_task: asyncio.Task | None = None
    close_report: dict | None = None


class AsyncSubagentTaskManager:
    """One parent's bounded in-process task groups; never cancels a worker on wait timeout."""

    def __init__(
        self, owner_id: str, *, task_timeout_s: float = 1800, max_tasks: int = 128, drain_timeout_s: float = 1.5
    ) -> None:
        self.owner_id = owner_id
        self.task_timeout_s = task_timeout_s
        self.max_tasks = max_tasks
        self.drain_timeout_s = drain_timeout_s
        self._runs: dict[str, _Run] = {}
        self._sessions: dict[str, str] = {}
        self._current: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"task_group_run_{id(self)}", default=None
        )
        self._changed = asyncio.Event()
        self._draining: set[asyncio.Task] = set()
        self._task_listeners: dict[object, Callable[[SubagentTaskScope, dict], None]] = {}

    def add_task_listener(self, listener: Callable[[SubagentTaskScope, dict], None]) -> Callable[[], None]:
        """Observe state changes without consuming results.

        Listeners run synchronously on the owning event loop and must not block. Schedule I/O outside
        the callback; exceptions are isolated. The returned unsubscribe is idempotent.
        """
        key = object()
        self._task_listeners[key] = listener

        def unsubscribe() -> None:
            self._task_listeners.pop(key, None)

        return unsubscribe

    def _publish(self, record):
        for listener in tuple(self._task_listeners.values()):
            try:
                listener(record.execution.scope, deepcopy(record.snapshot()))
            except Exception:
                logger.exception("[TaskGroup] task=%s state listener failed", record.execution.task_id)

    def _notify(self):
        previous, self._changed = self._changed, asyncio.Event()
        previous.set()

    def enter_run(self, parent_session_id: str | None, *, resume: bool = False) -> tuple[str, contextvars.Token]:
        sid = parent_session_id or ""
        previous = self._sessions.get(sid) if sid else None
        if resume:
            if previous is None and not sid:
                candidates = [key for key, run in self._runs.items() if not run.closed]
                previous = candidates[0] if len(candidates) == 1 else None
            if previous is None:
                raise ValueError("task_not_found: no resumable task group")
            run_id = previous
        else:
            if previous and not self._runs[previous].closed:
                raise ValueError("scope_busy: task group is still active")
            run_id = uuid4().hex
            self._runs[run_id] = _Run(SubagentTaskScope(self.owner_id, sid, run_id))
            if sid:
                self._sessions[sid] = run_id
        return run_id, self._current.set(run_id)

    def leave_run(self, run_id: str, token: contextvars.Token) -> None:
        self._current.reset(token)
        run = self._runs.get(run_id)
        if run and run.closed:
            self._release(run_id)

    def _release(self, run_id: str):
        run = self._runs.get(run_id)
        if run is None or not run.closed:
            return
        for record in run.records.values():
            for task in (record.task, record.cleanup_task):
                if task and not task.done():
                    return
        if self._sessions.get(run.scope.parent_session_id) == run_id:
            self._sessions.pop(run.scope.parent_session_id, None)
        self._runs.pop(run_id, None)

    def scope_for_session(self, parent_session_id: str) -> SubagentTaskScope:
        run_id = self._current.get() or self._sessions.get(parent_session_id)
        if run_id is None:
            run_id, token = self.enter_run(parent_session_id)
            self._current.reset(token)
        run = self._runs.get(run_id)
        if run is None or run.closed or run.closing:
            raise ValueError("task_not_found: closed task group")
        if not run.scope.parent_session_id:
            if parent_session_id in self._sessions and self._sessions[parent_session_id] != run_id:
                raise ValueError("scope_busy: session has another task group")
            run.scope = replace(run.scope, parent_session_id=parent_session_id)
            self._sessions[parent_session_id] = run_id
        if run.scope.parent_session_id != parent_session_id:
            raise ValueError("task_not_found")
        return run.scope

    def scope_for_run(self, run_id: str) -> SubagentTaskScope | None:
        run = self._runs.get(run_id)
        return run.scope if run else None

    def scope_if_active(self, parent_session_id: str | None = None) -> SubagentTaskScope | None:
        run_id = self._sessions.get(parent_session_id) if parent_session_id is not None else self._current.get()
        run = self._runs.get(run_id) if run_id else None
        if run is None and parent_session_id is None:
            candidates = [item for item in self._runs.values() if not item.closed]
            run = candidates[0] if len(candidates) == 1 else None
        return run.scope if run and not run.closed else None

    def _run(self, scope: SubagentTaskScope) -> _Run:
        run = self._runs.get(scope.run_id)
        if run is None or run.closed or run.closing:
            raise ValueError("task_not_found")
        if run.scope != scope:
            raise ValueError("task_not_found")
        return run

    def _records(self, scope, task_ids):
        run = self._run(scope)
        if not task_ids or any(not isinstance(key, str) or key not in run.records for key in task_ids):
            raise ValueError("task_not_found")
        return [run.records[key] for key in dict.fromkeys(task_ids)]

    def submit_batch(
        self,
        scope: SubagentTaskScope,
        tool_call_id: str,
        requests: list[TaskRequest],
        *,
        existing_ids: list[str],
        executor_factory: Callable[[SubagentExecution], tuple[Callable, Callable]],
    ) -> TaskBatchReceipt:
        run = self._run(scope)
        if existing_ids:
            self._records(scope, existing_ids)
        fingerprint = hashlib.sha256(
            json.dumps(
                {"tasks": [asdict(request) for request in requests], "existing": sorted(set(existing_ids))},
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if tool_call_id in run.receipts:
            receipt = run.receipts[tool_call_id]
            if receipt.fingerprint != fingerprint:
                raise ValueError("dispatch_conflict: this call already submitted different tasks")
            return receipt
        if not tool_call_id or not requests:
            raise ValueError("invalid_arguments: a stable call id and nonempty tasks are required")
        if len(run.records) + len(requests) > self.max_tasks:
            raise ValueError("task_capacity_exceeded")
        pending = []
        for index, request in enumerate(requests):
            task_id = uuid4().hex
            suffix = hashlib.sha256(f"{scope.run_id}:{tool_call_id}:{index}".encode()).hexdigest()[:24]
            execution = SubagentExecution(
                scope,
                task_id,
                tool_call_id,
                index,
                request,
                f"{scope.parent_session_id}_sub_{request.subagent_type}_{suffix}",
            )
            execute, cleanup = executor_factory(execution)
            pending.append(_Record(execution, execute, cleanup, self.task_timeout_s))
        submitted = [
            {"index": record.execution.dispatch_index, "task_id": record.execution.task_id} for record in pending
        ]
        receipt = TaskBatchReceipt(
            fingerprint, submitted, list(dict.fromkeys([item["task_id"] for item in submitted] + list(existing_ids)))
        )
        run.receipts[tool_call_id] = receipt
        for record in pending:
            run.records[record.execution.task_id] = record
            self._start(record)
        return receipt

    def _start(self, record, query=None):
        record.status = "running"
        task = asyncio.create_task(self._execute(record, query), name=f"subagent:{record.execution.task_id}")
        record.task = task
        task.add_done_callback(lambda completed: self._worker_done(record, completed))
        self._publish(record)
        self._notify()

    def _worker_done(self, record, task):
        if not task.cancelled():
            task.exception()  # Retrieve failures even when no caller waits for this task.
        if task.cancelled() and record.status == "cancelling" and record.cleanup_task is None:
            self._keep(self._finish(record, "cancelled"))
        self._release(record.execution.scope.run_id)

    def _keep(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._draining.add(task)
        task.add_done_callback(self._draining.discard)
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return task

    async def _execute(self, record, query):
        started = monotonic()
        status = "failed"
        cancelled = False
        try:
            async with asyncio.timeout(max(0, record.remaining_s)):
                result = await record.execute(query)
            if not isinstance(result, dict):
                raise TypeError("subagent result must be a dict")
            record.execution.raw_result = result
            if result.get("result_type") == "interrupt" and "interrupt_ids" in result:
                record.status = "waiting_approval"
                self._publish(record)
                self._notify()
                return
            status = "failed" if result.get("result_type") == "error" else "completed"
            if status == "failed":
                record.error = {
                    "code": "subagent_error",
                    "message": str(result.get("error") or result.get("output") or "Subagent failed"),
                }
        except TimeoutError:
            status = "timed_out"
            record.error = {"code": "task_timeout", "message": "Subagent execution budget exhausted"}
        except asyncio.CancelledError:
            status, cancelled = "cancelled", True
        except Exception as exc:
            record.error = {"code": "subagent_error", "message": str(exc)}
        finally:
            record.remaining_s = max(0, record.remaining_s - (monotonic() - started))
            if record.status != "waiting_approval":
                await self._finish(record, status)
        if cancelled:
            raise asyncio.CancelledError()

    async def _finish(self, record, status):
        if record.status in _TERMINAL:
            return
        if record.cleanup_task is None:
            record.cleanup_task = asyncio.create_task(record.cleanup())
        try:
            await asyncio.shield(record.cleanup_task)
        except asyncio.CancelledError:
            record.status = "cancelling"
            self._keep(self._finish(record, "cancelled"))
            raise
        except Exception as exc:
            status = "failed"
            record.error = {"code": "cleanup_failed", "message": str(exc)}
            logger.exception("[TaskGroup] task=%s cleanup failed", record.execution.task_id)
        record.status = status
        self._publish(record)
        self._notify()
        self._release(record.execution.scope.run_id)

    async def wait_any(self, scope: SubagentTaskScope, task_ids: list[str], timeout_s: float) -> TaskWaitResult:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise ValueError("invalid_timeout")
        if not math.isfinite(timeout_s) or timeout_s < 0:
            raise ValueError("invalid_timeout")
        records = self._records(scope, task_ids)
        deadline = monotonic() + timeout_s
        while True:
            notification = self._changed
            ready = [record.snapshot() for record in records if record.status in _TERMINAL]
            pending = [record.execution.task_id for record in records if record.status not in _TERMINAL]
            interrupts = {
                record.execution.task_id: record.execution.raw_result
                for record in records
                if record.status == "waiting_approval"
            }
            if ready or interrupts:
                return TaskWaitResult(ready, pending, interruptions=interrupts)
            remaining = deadline - monotonic()
            if remaining <= 0:
                return TaskWaitResult([], pending, timed_out=True)
            try:
                await asyncio.wait_for(notification.wait(), remaining)
            except TimeoutError:
                return TaskWaitResult([], pending, timed_out=True)

    async def resume(self, scope: SubagentTaskScope, task_id: str, user_input: Any) -> dict:
        (record,) = self._records(scope, [task_id])
        if record.status == "waiting_approval":
            self._start(record, user_input)
        return record.snapshot()

    def interrupted_records(self, scope: SubagentTaskScope) -> list[_Record]:
        return [record for record in self._run(scope).records.values() if record.status == "waiting_approval"]

    def mark_delivered(self, scope: SubagentTaskScope, task_ids: list[str]) -> None:
        for record in self._records(scope, task_ids) if task_ids else []:
            record.delivered = True

    async def cancel(self, scope: SubagentTaskScope, task_ids: list[str]) -> list[dict]:
        return await self._cancel_records(self._records(scope, task_ids))

    async def _cancel_records(self, records):
        tasks = []
        for record in records:
            if record.status in _TERMINAL:
                continue
            record.status = "cancelling"
            if record.task and not record.task.done():
                record.task.cancel()
                tasks.append(record.task)
            else:
                tasks.append(self._keep(self._finish(record, "cancelled")))
        if tasks:
            await asyncio.wait(tasks, timeout=self.drain_timeout_s)
        return [record.snapshot() for record in records]

    async def close_run(self, scope: SubagentTaskScope, reason: str) -> dict:
        run = self._runs.get(scope.run_id)
        if run is None:
            return {"tasks": [], "unfinished": [], "uncollected": [], "cleanup_pending_ids": [], "reason": reason}
        if run.scope != scope:
            raise ValueError("task_not_found")
        if run.close_task is None:
            run.closing = True  # Block new dispatch before the first cleanup await.
            run.close_task = self._keep(self._close_run(run, reason))
        return await asyncio.shield(run.close_task)

    async def _close_run(self, run, reason):
        unfinished = [key for key, record in run.records.items() if record.status not in _TERMINAL]
        uncollected = [key for key, record in run.records.items() if not record.delivered]
        if unfinished:
            await self._cancel_records([run.records[key] for key in unfinished])
        run.closed = True
        if self._sessions.get(run.scope.parent_session_id) == run.scope.run_id:
            self._sessions.pop(run.scope.parent_session_id, None)
        run.close_report = {
            "tasks": [record.snapshot() for record in run.records.values()],
            "unfinished": unfinished,
            "uncollected": uncollected,
            "cleanup_pending_ids": [key for key, record in run.records.items() if record.status not in _TERMINAL],
            "reason": reason,
        }
        report = run.close_report
        self._release(run.scope.run_id)
        return report

    def request_close_all(self, reason: str = "unloaded") -> None:
        """Keep teardown alive when a synchronous rail unload cannot await it."""
        for run in list(self._runs.values()):
            if not run.closed:
                self._keep(self.close_run(run.scope, reason))
