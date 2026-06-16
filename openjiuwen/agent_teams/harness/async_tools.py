# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Async background-tool framework, scoped to ``NativeHarness``.

A two-phase model mirroring Claude Code's background tools: a tool's
``invoke`` launches work and returns *immediately* with a ``launched`` result
so the ``tool_use``/``ToolMessage`` pair closes at once (the round is not
blocked); the real result is fed back *later* as an injected message — it never
rides back on the original ``tool_use`` (the LLM protocol forbids a suspended
``tool_result``, exactly as the Anthropic API does).

The framework lives entirely inside ``NativeHarness`` (a ``DeepAgent``
subclass) — it never touches ``TeamAgent``. Completion is injected through the
harness's own ``send(..., immediate=False)`` entry: IDLE starts a fresh round
to report, RUNNING queues a follow-up so an in-flight user turn is not
interrupted. A concrete async tool subclasses :class:`AsyncTool` and holds a
``parent_agent`` (the ``NativeHarness``) reference — the same shape
``sessions_spawn`` uses to reach the scheduler.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.logging import team_logger
from openjiuwen.harness.tools.base_tool import ToolOutput

# A completion-injection callback: hand the harness the model-facing text. Wired
# to ``NativeHarness.send(text, immediate=False)`` by the harness itself.
InjectCallback = Callable[[str], Awaitable[Any]]


def render_result_text(result: Any) -> str:
    """Render an async tool's return value to model-facing text, in full.

    No truncation — the leader receives the complete result. ``str`` passes
    through verbatim (a tool that already composed its own text); ``dict`` /
    ``list`` are pretty JSON; everything else falls back to ``str()``.

    Args:
        result: Whatever the tool's ``run_background`` returned.

    Returns:
        The full textual rendering (empty string for ``None``).
    """
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False, indent=2)
    return str(result)


@dataclass
class AsyncToolRecord:
    """Registry row for one launched async-tool task (business view)."""

    task_id: str
    tool_name: str
    description: str
    status: str = "running"  # running | completed | error
    result: Any = None
    error: str = ""


@dataclass
class AsyncToolRuntime:
    """Per-harness async background-task registry and completion injector.

    Owns the in-flight task set (so tasks are not garbage-collected mid-run and
    can be cancelled on teardown) and a registry of :class:`AsyncToolRecord`.
    On completion it renders the result and injects it via ``inject`` — the
    harness's ``send(..., immediate=False)`` entry. Zero ``TeamAgent`` coupling.
    """

    inject: InjectCallback
    registry: dict[str, AsyncToolRecord] = field(default_factory=dict)
    tasks: "set[asyncio.Task]" = field(default_factory=set)

    def has_running(self, tool_name: str) -> bool:
        """Return whether a task for ``tool_name`` is currently running."""
        return any(
            record.status == "running" and record.tool_name == tool_name
            for record in self.registry.values()
        )

    def launch(
        self,
        task_id: str,
        coro_factory: Callable[[], Awaitable[Any]],
        *,
        tool_name: str,
        description: str,
    ) -> None:
        """Schedule a background task and track it until completion.

        Args:
            task_id: Caller-generated unique id for this run.
            coro_factory: Zero-arg factory returning the coroutine to run. A
                factory (not a coroutine) keeps construction lazy and lets the
                task be created on the running loop.
            tool_name: The launching tool's name (for the completion message).
            description: Human-readable task description (for the registry).
        """
        self.registry[task_id] = AsyncToolRecord(
            task_id=task_id,
            tool_name=tool_name,
            description=description,
        )
        task = asyncio.create_task(self._run(task_id, coro_factory, tool_name))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _run(
        self,
        task_id: str,
        coro_factory: Callable[[], Awaitable[Any]],
        tool_name: str,
    ) -> None:
        """Run the task, update its record, then inject the result/error text."""
        record = self.registry.get(task_id)
        try:
            result = await coro_factory()
        except asyncio.CancelledError:
            if record is not None:
                record.status = "error"
                record.error = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - report any tool failure back
            team_logger.error(
                "[AsyncToolRuntime] task %s (%s) failed: %s",
                task_id,
                tool_name,
                exc,
                exc_info=True,
            )
            if record is not None:
                record.status = "error"
                record.error = str(exc)
            await self._inject(t("async_tool.failed", tool=tool_name, error=str(exc)))
            return
        if record is not None:
            record.status = "completed"
            record.result = result
        await self._inject(
            t("async_tool.completed", tool=tool_name, result=render_result_text(result))
        )

    async def _inject(self, text: str) -> None:
        """Best-effort completion injection; a stopped harness must not raise."""
        try:
            await self.inject(text)
        except Exception:  # noqa: BLE001 - teardown races must not surface here
            team_logger.debug("[AsyncToolRuntime] completion injection skipped", exc_info=True)

    def cancel_all(self) -> None:
        """Cancel all in-flight tasks (teardown)."""
        for task in list(self.tasks):
            if not task.done():
                task.cancel()


class AsyncTool(TeamTool):
    """Base class for two-phase async background tools.

    ``invoke`` launches ``run_background`` on the harness's runtime and returns
    a ``launched`` result immediately. The real result is injected later by the
    runtime. Subclasses implement ``run_background`` (and optionally override
    ``launched_description``); they hold ``parent_agent`` — the ``NativeHarness``
    exposing ``launch_async_tool`` — wired in at rail-init time.
    """

    def __init__(self, card: Any, parent_agent: Any, language: str = "cn") -> None:
        """Initialize the async tool.

        Args:
            card: The tool's ``ToolCard``.
            parent_agent: The owning ``NativeHarness`` (exposes
                ``launch_async_tool``). Held as a reference; only used at invoke
                time, by which point the harness has started.
            language: Language code for model-facing text.
        """
        super().__init__(card)
        self._parent_agent = parent_agent
        self._language = language

    @abstractmethod
    async def run_background(self, task_id: str, inputs: dict[str, Any]) -> Any:
        """Run the actual work in the background and return the full result.

        Args:
            task_id: The launched task's unique id.
            inputs: The tool-call arguments.

        Returns:
            The complete result (rendered to text by the runtime).
        """
        ...

    def launched_description(self, inputs: dict[str, Any]) -> str:
        """Return a short task description for the registry (override me)."""
        return self.card.name

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Launch the background task and return immediately with ``launched``."""
        task_id = uuid.uuid4().hex
        try:
            self._parent_agent.launch_async_tool(
                task_id,
                lambda: self.run_background(task_id, inputs),
                tool_name=self.card.name,
                description=self.launched_description(inputs),
            )
        except Exception as exc:  # noqa: BLE001 - never escape as an exception
            return ToolOutput(success=False, error=f"Internal error: {exc}")
        return ToolOutput(success=True, data={"status": "launched", "task_id": task_id})

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to launch async tool"
        return t("async_tool.launched", tool=self.card.name, task_id=output.data["task_id"])


__all__ = [
    "AsyncToolRecord",
    "AsyncToolRuntime",
    "AsyncTool",
    "InjectCallback",
    "render_result_text",
]
