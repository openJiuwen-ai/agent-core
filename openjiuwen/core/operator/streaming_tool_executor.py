# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Streaming tool executor with concurrency control.

Schedules ToolCall executions as they stream in from an LLM, applying these
rules per tool:

1. If no tools are currently executing, start immediately.
2. If currently executing tools are all concurrency-safe AND the incoming
   tool is also concurrency-safe, start in parallel.
3. Otherwise, queue and wait.

Results are returned in the order tools were added (matching LLM tool_call
order), regardless of completion order.

The executor does not run tools directly — it accepts an ``executor_fn``
(typically wrapping ``AbilityManager.execute_single``) so that
BEFORE_TOOL_CALL / AFTER_TOOL_CALL / ON_TOOL_EXCEPTION rails remain fully
effective.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, List, Optional, Set, Tuple

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall


# Type alias for the concurrency-check predicate injected into
# ``StreamingToolExecutor``.  Accepts a ``ToolCall``, returns ``True`` when the
# tool is safe to run concurrently with other in-flight tools.
ConcurrencyCheck = Callable[[ToolCall], bool]


def is_concurrency_safe(_tool_call: ToolCall) -> bool:
    """Return whether *tool_call* is safe to execute concurrently.

    .. note::
       **当前版本对所有工具返回 ``True``（全部允许并发）。**

       后续如需引入并发安全分级，可在此处扩展判断逻辑，例如：

       * 读取 ``tool_call.metadata.get("concurrency_safe")`` 元数据字段
       * 根据 tool name / category 维护白名单或黑名单
       * 通过 ``ToolCard`` 的字段做更细粒度的判断（只读 vs 写、是否有共享资源）

       该方法也可作为 ``StreamingToolExecutor(concurrency_check=...)`` 的
       默认值注入，便于测试时替换为自定义 predicate。
    """
    return True


class _Status(str, Enum):
    QUEUED = "queued"
    EXECUTING = "executing"
    DONE = "done"


@dataclass
class _Tracked:
    tool_call: ToolCall
    is_safe: bool
    status: _Status = _Status.QUEUED
    task: Optional[asyncio.Task] = None
    result: Any = None
    error: Optional[BaseException] = None


class StreamingToolExecutor:
    """Schedule and execute tool calls with concurrency control.

    Args:
        executor_fn: 实际执行 tool 的异步函数，通常包装 ``AbilityManager.execute_single``。
        tag: 日志标签，用于区分不同 agent 实例。
        concurrency_check: 可选的并发安全判定函数。接受 ``ToolCall``，返回
            ``True`` 表示该工具可与其他工具并发执行。默认使用
            :func:`is_concurrency_safe`（当前版本始终返回 ``True``）。
            测试时可注入自定义 predicate 来构造 safe/unsafe 场景。

    Typical usage from a ReAct loop::

        async def _exec(tc):
            return await ability_manager.execute_single(
                parent_ctx=ctx, tool_call=tc, session=session, tag=tag,
            )

        executor = StreamingToolExecutor(executor_fn=_exec, tag="agent-1")
        # As each LLM tool_call streams in fully formed:
        executor.add(tool_call_1)
        executor.add(tool_call_2)
        # After the stream ends:
        ordered_results = await executor.wait_all()
        # ordered_results: [(tool_call, value), ...] in add() order.
        # ``value`` is the executor_fn return value, or a BaseException
        # instance if the task raised (e.g. CancelledError).
    """

    def __init__(
        self,
        executor_fn: Callable[[ToolCall], Awaitable[Any]],
        *,
        tag: str = "",
        concurrency_check: Optional[ConcurrencyCheck] = None,
    ):
        self._executor_fn = executor_fn
        self._tag = tag
        self._concurrency_check: ConcurrencyCheck = (
            concurrency_check if concurrency_check is not None
            else is_concurrency_safe
        )
        self._tools: List[_Tracked] = []
        self._added_keys: Set[Tuple[Optional[str], Optional[int]]] = set()
        self._cancelled = False

    def add(self, tool_call: ToolCall) -> None:
        """Add a tool call to the queue and try to start it immediately.

        Re-adding the same (id, index) is a no-op (deduplication for
        streaming scenarios where the same tool_call may be observed
        multiple times before being fully formed).
        """
        if self._cancelled:
            logger.debug(
                "[%s] StreamingToolExecutor cancelled; ignoring add(%s)",
                self._tag, tool_call.name,
            )
            return

        key = (tool_call.id, tool_call.index)
        if key in self._added_keys:
            return
        self._added_keys.add(key)

        tracked = _Tracked(
            tool_call=tool_call,
            is_safe=self._concurrency_check(tool_call),
        )
        self._tools.append(tracked)
        logger.debug(
            "[%s] StreamingToolExecutor added %s (id=%s, safe=%s)",
            self._tag, tool_call.name, tool_call.id, tracked.is_safe,
        )
        self._process_queue()

    def is_added(self, tool_call: ToolCall) -> bool:
        """Return True if ``tool_call`` (matched by id+index) was already added."""
        return (tool_call.id, tool_call.index) in self._added_keys

    def cancel_all(self) -> None:
        """Cancel all queued and executing tools.

        Subsequent ``add`` calls are silently dropped. ``wait_all`` still
        works and will return ``CancelledError`` for affected tools.
        """
        self._cancelled = True
        for t in self._tools:
            if t.task is not None and not t.task.done():
                t.task.cancel()

    async def wait_all(self) -> List[Tuple[ToolCall, Any]]:
        """Wait until every added tool reaches a terminal state.

        Returns ``[(tool_call, value), ...]`` in the order tools were
        added. ``value`` is either the executor_fn return value or a
        ``BaseException`` instance when the underlying task raised /
        was cancelled.
        """
        while True:
            pending = [
                t.task for t in self._tools
                if t.task is not None and not t.task.done()
            ]
            if not pending:
                break
            # When each task's ``_run`` finishes, its ``finally`` block has
            # already called ``_process_queue`` and may have created new
            # tasks. The next loop iteration will pick them up.
            await asyncio.gather(*pending, return_exceptions=True)

        results: List[Tuple[ToolCall, Any]] = []
        for t in self._tools:
            if t.status == _Status.DONE:
                value: Any = t.error if t.error is not None else t.result
                results.append((t.tool_call, value))
            else:
                err: BaseException = asyncio.CancelledError(
                    "Tool cancelled before execution",
                )
                results.append((t.tool_call, err))
        return results

    def _can_execute(self, tracked: _Tracked) -> bool:
        executing = [t for t in self._tools if t.status == _Status.EXECUTING]
        if not executing:
            return True
        if tracked.is_safe and all(t.is_safe for t in executing):
            return True
        return False

    def _process_queue(self) -> None:
        if self._cancelled:
            return
        for tracked in self._tools:
            if tracked.status != _Status.QUEUED:
                continue
            if self._can_execute(tracked):
                self._start(tracked)
            else:
                # Strict FIFO: do not let later (safe) tools jump ahead of
                # an earlier blocked one — keeps execution order aligned
                # with the LLM-emitted tool_call order.
                break

    def _start(self, tracked: _Tracked) -> None:
        tracked.status = _Status.EXECUTING

        async def _run() -> None:
            try:
                tracked.result = await self._executor_fn(tracked.tool_call)
            except asyncio.CancelledError as e:
                tracked.error = e
                tracked.status = _Status.DONE
                raise
            except BaseException as e:
                tracked.error = e
            finally:
                if tracked.status != _Status.DONE:
                    tracked.status = _Status.DONE
                # Synchronously try to start the next runnable tools so a
                # subsequent ``wait_all`` iteration sees the new tasks.
                self._process_queue()

        tracked.task = asyncio.create_task(_run())
        logger.info(
            "[%s] StreamingToolExecutor started %s (id=%s, safe=%s)",
            self._tag, tracked.tool_call.name, tracked.tool_call.id,
            tracked.is_safe,
        )
