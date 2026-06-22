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

The runtime also backs the generic control tools (``async_tasks_list`` /
``async_task_output`` / ``async_task_cancel``) via ``list_all`` / ``get`` /
``wait`` / ``cancel``, and spills oversized results to disk so a large report
does not blow the leader's context — small results still inline in full.
"""
from __future__ import annotations

import asyncio
import json
from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.id_generator import generate_id
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.logging import team_logger
from openjiuwen.harness.tools.base_tool import ToolOutput

# A completion-injection callback: hand the harness the model-facing text. Wired
# to ``NativeHarness.send(text, immediate=False)`` by the harness itself.
InjectCallback = Callable[[str], Awaitable[Any]]

# Result-spill summary length: how many leading characters of an oversized
# result stay inline (as a preview) next to the retrieval pointer.
_SPILL_SUMMARY_CHARS = 1024


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


def _write_output_file(path: Path, text: str) -> None:
    """Write spilled output to disk, creating parent dirs (runs off-loop)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@dataclass
class AsyncToolRecord:
    """Registry row for one launched async-tool task (business view)."""

    task_id: str
    tool_name: str
    description: str
    status: str = "running"  # running | completed | error
    result: Any = None
    error: str = ""
    output_file: str | None = None  # set when an oversized result spills to disk


@dataclass
class AsyncToolRuntime:
    """Per-harness async background-task registry and completion injector.

    Owns the in-flight task map (so tasks are not garbage-collected mid-run and
    can be cancelled on teardown or by id) and a registry of
    :class:`AsyncToolRecord`. On completion it renders the result and injects it
    via ``inject`` — the harness's ``send(..., immediate=False)`` entry. Zero
    ``TeamAgent`` coupling.
    """

    inject: InjectCallback
    registry: dict[str, AsyncToolRecord] = field(default_factory=dict)
    # Oversized-result spill. ``output_dir_resolver`` is injected by the host
    # (TeamToolRail) and resolved lazily at completion time, since the session
    # id it needs is only available once a round runs; None keeps the original
    # full-inline behavior. A result whose text exceeds ``spill_threshold`` is
    # written to disk and the injected text becomes a summary + retrieval hint.
    output_dir_resolver: "Callable[[], Path | None] | None" = None
    spill_threshold: int = 32768
    # task_id -> Task / completion Event. The id-keyed task map (not a bare set)
    # lets ``cancel`` / ``wait`` address one task; the per-task Event wakes a
    # blocking ``wait`` the moment the record reaches a terminal state.
    _tasks: "dict[str, asyncio.Task]" = field(default_factory=dict)
    _events: "dict[str, asyncio.Event]" = field(default_factory=dict)

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
        self._events[task_id] = asyncio.Event()
        task = asyncio.create_task(self._run(task_id, coro_factory, tool_name))
        self._tasks[task_id] = task
        task.add_done_callback(lambda _t, _id=task_id: self._tasks.pop(_id, None))

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
            self._signal(task_id)
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
            self._signal(task_id)
            await self._inject(t("async_tool.failed", tool=tool_name, error=str(exc)))
            return
        if record is not None:
            record.status = "completed"
            record.result = result
        result_text = render_result_text(result)
        if record is not None:
            result_text = await self._maybe_spill(task_id, record, result_text)
        self._signal(task_id)
        await self._inject(
            t("async_tool.completed", tool=tool_name, result=result_text)
        )

    def _signal(self, task_id: str) -> None:
        """Wake any ``wait`` blocked on this task — its record is now terminal."""
        event = self._events.get(task_id)
        if event is not None:
            event.set()

    async def _maybe_spill(
        self,
        task_id: str,
        record: AsyncToolRecord,
        text: str,
    ) -> str:
        """Spill an oversized result to disk; return the model-facing text.

        Small results (``len <= spill_threshold``) inline in full, preserving
        the complete-feedback behavior. Oversized results are written to the
        task's output file off the event loop, and the returned text becomes a
        leading summary plus a retrieval pointer (``async_task_output``). A
        resolver that is unset or yields no directory falls back to full inline,
        and a write failure degrades to inline rather than dropping the result.

        Args:
            task_id: The completed task's id (names the output file).
            record: The task record; ``output_file`` is set on a successful spill.
            text: The fully rendered result text.

        Returns:
            The text to inject — either the full result or a summary + pointer.
        """
        if len(text) <= self.spill_threshold or self.output_dir_resolver is None:
            return text
        out_dir = self.output_dir_resolver()
        if out_dir is None:
            return text
        path = out_dir / f"{task_id}.output"
        try:
            await asyncio.to_thread(_write_output_file, path, text)
        except Exception:  # noqa: BLE001 - a spill failure degrades to inline
            team_logger.warning(
                "[AsyncToolRuntime] spill failed for %s; inlining full result",
                task_id,
                exc_info=True,
            )
            return text
        record.output_file = str(path)
        summary = text[:_SPILL_SUMMARY_CHARS]
        notice = t("async_tool.spilled_notice", path=str(path), task_id=task_id)
        return f"{summary}\n\n{notice}"

    def get(self, task_id: str) -> "AsyncToolRecord | None":
        """Return the record for ``task_id``, or None if unknown."""
        return self.registry.get(task_id)

    def list_all(self) -> "list[AsyncToolRecord]":
        """Return all task records in launch order."""
        return list(self.registry.values())

    async def cancel(self, task_id: str) -> bool:
        """Cancel one task by id and mark its record. Return False if unknown.

        Idempotent on an already-finished task (returns True, leaves the
        terminal record intact). The underlying ``asyncio.Task`` cancellation
        takes effect at its next await point; the record is marked here so
        ``get`` / ``list_all`` reflect the cancel immediately.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False
        if not task.done():
            task.cancel()
        record = self.registry.get(task_id)
        if record is not None and record.status == "running":
            record.status = "error"
            record.error = "cancelled"
        self._signal(task_id)
        return True

    async def wait(self, task_id: str, timeout: float) -> "AsyncToolRecord | None":
        """Block until ``task_id`` is terminal or ``timeout`` seconds elapse.

        Returns the record immediately if it is already terminal, or None if the
        task is unknown. On timeout it returns the still-running record rather
        than raising, so a caller can report "still running" without
        special-casing the exception.

        Args:
            task_id: The task to wait on.
            timeout: Maximum wait in seconds.

        Returns:
            The (possibly still-running) record, or None for an unknown id.
        """
        record = self.registry.get(task_id)
        if record is None:
            return None
        if record.status != "running":
            return record
        event = self._events.get(task_id)
        if event is None:
            return record
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        return self.registry.get(task_id)

    async def _inject(self, text: str) -> None:
        """Best-effort completion injection; a stopped harness must not raise."""
        try:
            await self.inject(text)
        except Exception:  # noqa: BLE001 - teardown races must not surface here
            team_logger.debug("[AsyncToolRuntime] completion injection skipped", exc_info=True)

    def cancel_all(self) -> None:
        """Cancel all in-flight tasks (teardown)."""
        for task in list(self._tasks.values()):
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
        task_id = generate_id(self.card.name)
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
