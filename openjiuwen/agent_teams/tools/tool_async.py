# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Generic async-tool control tools: list / output / cancel.

These operate on the harness's :class:`AsyncToolRuntime` — the registry behind
two-phase :class:`AsyncTool`s such as swarmflow. Unlike ``AsyncTool`` they are
ordinary synchronous tools: they query / act on the runtime and return at once
(no two-phase launch). Each holds ``parent_agent`` (the ``NativeHarness``) and
reaches the runtime via ``parent_agent.async_tool_runtime``, the same instance
the launching tools use — so a task launched by swarmflow is visible here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openjiuwen.agent_teams.harness.async_tools import (
    AsyncToolRecord,
    render_result_text,
)
from openjiuwen.agent_teams.tools.locales import Translator
from openjiuwen.agent_teams.tools.tool_base import TeamTool
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

# Blocking ``async_task_output`` wait bounds (ms). The ceiling stops a wedged
# task from pinning the caller's round indefinitely.
_DEFAULT_OUTPUT_WAIT_MS = 30000
_MAX_OUTPUT_WAIT_MS = 600000


def _record_brief(record: AsyncToolRecord) -> dict[str, Any]:
    """Project a record to its routing/identity fields for the list view."""
    return {
        "task_id": record.task_id,
        "tool_name": record.tool_name,
        "status": record.status,
        "description": record.description,
    }


def _read_output_file(path: str) -> str:
    """Read a spilled output file (runs off the event loop)."""
    return Path(path).read_text(encoding="utf-8")


class AsyncTasksListTool(TeamTool):
    """List all async background tasks tracked by this harness."""

    def __init__(self, parent_agent: Any, t: Translator, language: str = "cn") -> None:
        super().__init__(
            ToolCard(
                id="team.async_tasks_list",
                name="async_tasks_list",
                description=t("async_tasks_list"),
            )
        )
        self._parent_agent = parent_agent
        self._t = t
        self._language = language

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        """List every tracked task with its status."""
        try:
            records = self._parent_agent.async_tool_runtime.list_all()
        except Exception as exc:  # noqa: BLE001 - always return a ToolOutput
            team_logger.error("[async_tasks_list] failed: %s", exc, exc_info=True)
            return ToolOutput(success=False, error=f"Internal error: {exc}")
        return ToolOutput(success=True, data={"tasks": [_record_brief(r) for r in records]})

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to list async tasks"
        tasks = output.data.get("tasks", [])
        if not tasks:
            return "No async tasks."
        return "\n".join(
            f"task_id={x['task_id']} | tool={x['tool_name']} | "
            f"status={x['status']} | {x['description']}"
            for x in tasks
        )


class AsyncTaskOutputTool(TeamTool):
    """Retrieve a background task's full output, optionally blocking for it."""

    def __init__(self, parent_agent: Any, t: Translator, language: str = "cn") -> None:
        super().__init__(
            ToolCard(
                id="team.async_task_output",
                name="async_task_output",
                description=t("async_task_output"),
            )
        )
        self._parent_agent = parent_agent
        self._t = t
        self._language = language
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("async_task_output", "task_id")},
                "block": {"type": "boolean", "description": t("async_task_output", "block")},
                "timeout": {"type": "integer", "description": t("async_task_output", "timeout")},
            },
            "required": ["task_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Fetch the task's output; ``block=true`` waits until it is terminal."""
        task_id = (inputs.get("task_id") or "").strip()
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")
        runtime = self._parent_agent.async_tool_runtime
        try:
            if bool(inputs.get("block", False)):
                raw_timeout = inputs.get("timeout") or _DEFAULT_OUTPUT_WAIT_MS
                timeout_ms = min(int(raw_timeout), _MAX_OUTPUT_WAIT_MS)
                record = await runtime.wait(task_id, timeout_ms / 1000)
            else:
                record = runtime.get(task_id)
        except Exception as exc:  # noqa: BLE001 - always return a ToolOutput
            team_logger.error("[async_task_output] failed: %s", exc, exc_info=True)
            return ToolOutput(success=False, error=f"Internal error: {exc}")
        if record is None:
            return ToolOutput(success=False, error=f"Task '{task_id}' not found")
        result_text = await self._resolve_output(record)
        return ToolOutput(
            success=True,
            data={
                "task_id": record.task_id,
                "status": record.status,
                "result": result_text,
                "error": record.error,
            },
        )

    async def _resolve_output(self, record: AsyncToolRecord) -> str:
        """Return full text — read the spill file if present, else the memory result."""
        if record.output_file:
            try:
                return await asyncio.to_thread(_read_output_file, record.output_file)
            except Exception:  # noqa: BLE001 - degrade to the in-memory render
                team_logger.warning(
                    "[async_task_output] read spill failed for %s",
                    record.task_id,
                    exc_info=True,
                )
        return render_result_text(record.result)

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to get async task output"
        data = output.data
        task_id = data["task_id"]
        status = data.get("status")
        if status == "error":
            return f"[task {task_id}] status=error\n{data.get('error') or ''}"
        if status == "running":
            return f"[task {task_id}] status=running (not finished yet)"
        return f"[task {task_id}] status={status}\n{data.get('result') or ''}"


class AsyncTaskCancelTool(TeamTool):
    """Cancel a still-running background async task by id."""

    def __init__(self, parent_agent: Any, t: Translator, language: str = "cn") -> None:
        super().__init__(
            ToolCard(
                id="team.async_task_cancel",
                name="async_task_cancel",
                description=t("async_task_cancel"),
            )
        )
        self._parent_agent = parent_agent
        self._t = t
        self._language = language
        self.card.input_params = {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": t("async_task_cancel", "task_id")},
            },
            "required": ["task_id"],
        }

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        """Cancel the task; error when the id is unknown."""
        task_id = (inputs.get("task_id") or "").strip()
        if not task_id:
            return ToolOutput(success=False, error="'task_id' is required")
        try:
            ok = await self._parent_agent.async_tool_runtime.cancel(task_id)
        except Exception as exc:  # noqa: BLE001 - always return a ToolOutput
            team_logger.error("[async_task_cancel] failed: %s", exc, exc_info=True)
            return ToolOutput(success=False, error=f"Internal error: {exc}")
        if not ok:
            return ToolOutput(success=False, error=f"Task '{task_id}' not found")
        return ToolOutput(success=True, data={"task_id": task_id, "status": "cancelled"})

    def map_result(self, output: ToolOutput) -> str:
        if not output.success:
            return output.error or "Failed to cancel async task"
        return f"[task {output.data['task_id']}] cancelled."


__all__ = ["AsyncTasksListTool", "AsyncTaskOutputTool", "AsyncTaskCancelTool"]
