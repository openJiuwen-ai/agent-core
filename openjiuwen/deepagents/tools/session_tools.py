# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session tools for async subagent spawning."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator, Dict, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.controller.schema.task import Task as CoreTask, TaskStatus as CoreTaskStatus
from openjiuwen.core.foundation.tool import Input, Output, Tool, ToolCard
from openjiuwen.deepagents.tools.base_tool import ToolOutput
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.session.agent import Session
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card

if TYPE_CHECKING:
    from openjiuwen.deepagents.deep_agent import DeepAgent


SESSION_SPAWN_TASK_TYPE = "session_spawn_task"


@dataclass
class SessionTaskRow:
    """Session task row (business view)."""

    task_id: str
    sub_session_id: str
    description: str
    status: str
    result: str = ""
    error: str = ""


class SessionToolkit:
    """Session task registry."""

    def __init__(self) -> None:
        self._rows: Dict[str, SessionTaskRow] = {}

    def upsert_running(
        self, task_id: str, sub_session_id: str, description: str
    ) -> None:
        """Insert or update task as running."""
        self._rows[task_id] = SessionTaskRow(
            task_id=task_id,
            sub_session_id=sub_session_id,
            description=description,
            status="running",
        )

    def mark_completed(self, task_id: str, result: str) -> None:
        """Mark task as completed with result."""
        if task_id in self._rows:
            self._rows[task_id].status = "completed"
            self._rows[task_id].result = result

    def mark_failed(self, task_id: str, error: str) -> None:
        """Mark task as failed with error."""
        if task_id in self._rows:
            self._rows[task_id].status = "error"
            self._rows[task_id].error = error

    def mark_canceled(self, task_id: str) -> None:
        """Mark task as canceled."""
        if task_id in self._rows:
            self._rows[task_id].status = "canceled"

    def list_all(self) -> List[SessionTaskRow]:
        """List all tasks."""
        return list(self._rows.values())

    def get(self, task_id: str) -> Optional[SessionTaskRow]:
        """Get task by id."""
        return self._rows.get(task_id)

    def clear(self) -> None:
        """Clear all tracked session task rows."""
        self._rows.clear()


class SessionsListTool(Tool):
    """List registered async spawn tasks in SessionToolkit."""

    def __init__(self, toolkit: SessionToolkit, language: str = "cn") -> None:
        super().__init__(
            build_tool_card("sessions_list", "sessions_list", language)
        )
        self._toolkit = toolkit
        self._language = language

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """List all session tasks."""
        lines = []
        tasks = self._toolkit.list_all()
        for task in tasks:
            lines.append(
                f"task_id={task.task_id} | "
                f"description={task.description} | "
                f"status={task.status} | "
                f"result={task.result} | "
                f"error={task.error}"
            )

        if lines:
            data = "\n".join(lines)
        else:
            data = ("当前会话没有后台子任务"
                if self._language == "cn"
                else "No background tasks for this session"
            )

        return ToolOutput(success=True, data=data)

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass


class SessionsCancelTool(Tool):
    """Cancel async session spawn task."""

    def __init__(
        self,
        parent_agent: "DeepAgent",
        toolkit: SessionToolkit,
        language: str = "cn",
    ) -> None:
        super().__init__(
            build_tool_card(
                name="sessions_cancel",
                tool_id="sessions_cancel",
                language=language,
            )
        )
        self._parent_agent = parent_agent
        self._toolkit = toolkit
        self._language = language

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """Cancel async spawn task.

        Args:
            inputs: Input containing task_id.
            **kwargs: Additional parameters, including 'session'.

        Returns:
            ToolOutput with cancellation result.

        Raises:
            ToolError: If cancellation fails.
        """
        if isinstance(inputs, dict):
            task_id = inputs.get("task_id")
        else:
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason=f"Invalid inputs type: {type(inputs)}",
            )

        if not task_id:
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="task_id is required",
            )

        # Validate task exists in toolkit
        task = self._toolkit.get(task_id)
        if task is None:
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason=f"Task {task_id} not found",
            )

        # Get scheduler from parent agent
        controller = self._parent_agent.loop_controller
        if controller is None:
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="loop_controller not available",
            )

        scheduler = controller.task_scheduler
        if scheduler is None:
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="task_scheduler not available",
            )

        # Cancel directly via scheduler (synchronous blocking until cancellation completes)
        success = await scheduler.cancel_task(task_id)

        if not success:
            return ToolOutput(
                success=False,
                data={
                    "task_id": task_id,
                    "status": task.status,
                    "message": (
                        f"任务 {task_id} 取消失败"
                        if self._language == "cn"
                        else f"Task {task_id} cancel failed"
                    ),
                },
            )

        # Update toolkit status
        self._toolkit.mark_canceled(task_id)

        logger.info(f"[SessionsCancelTool] Cancelled task_id={task_id}")

        return ToolOutput(
            success=True,
            data={
                "task_id": task_id,
                "status": "canceled",
                "message": (
                    f"任务 {task_id} 取消成功"
                    if self._language == "cn"
                    else f"Task {task_id} cancel success"
                ),
            },
        )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass


class SessionsSpawnTool(Tool):
    """Async submit SESSION_SPAWN task."""

    def __init__(
        self,
        parent_agent: "DeepAgent",
        toolkit: SessionToolkit,
        language: str = "cn",
        available_agents: str = "",
    ) -> None:
        super().__init__(
            build_tool_card(
                name="sessions_spawn",
                tool_id="sessions_spawn",
                language=language,
                format_args={"available_agents": available_agents} if available_agents else None,
            )
        )
        self._parent_agent = parent_agent
        self._toolkit = toolkit
        self._language = language

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        """Submit async spawn task."""
        dc = self._parent_agent.deep_config
        if dc is None or not dc.enable_task_loop:
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="enable_task_loop is required for session spawn",
            )

        handler = self._parent_agent.event_handler
        tm = getattr(handler, "task_manager", None) if handler else None
        if handler is None or tm is None:
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="task loop handler/task_manager not available",
            )

        if isinstance(inputs, dict):
            subagent_type = inputs.get("subagent_type")
            task_description = inputs.get("task_description")
        else:
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason=f"Invalid inputs type: {type(inputs)}",
            )

        task_id = uuid.uuid4().hex
        parent_session = kwargs.get("session", None)
        if not isinstance(parent_session, Session):
            raise build_error(
                StatusCode.TOOL_SESSION_TOOL_INVOKED,
                reason="SessionSpawnTool requires a valid session in kwargs",
            )
        parent_session_id = parent_session.get_session_id()
        sub_session_id = f"{parent_session_id}_sub_{secrets.token_hex(4)}"

        await tm.add_task(
            CoreTask(
                session_id=parent_session_id,
                task_id=task_id,
                task_type=SESSION_SPAWN_TASK_TYPE,
                description=task_description,
                status=CoreTaskStatus.SUBMITTED,
                metadata={
                    "subagent_type": subagent_type,
                    "task_description": task_description,
                    "sub_session_id": sub_session_id,
                },
            )
        )

        self._toolkit.upsert_running(task_id, sub_session_id, task_description)

        logger.info(
            f"[SessionsSpawnTool] Submitted task_id={task_id}, "
            f"sub_session_id={sub_session_id}, subagent_type={subagent_type}"
        )

        return ToolOutput(
            success=True,
            data={
                "status": "pending",
                "message": (
                    f"子任务 {task_description} 已提交后台执行，你可以继续发送其他问题"
                    if self._language == "cn"
                    else f"Task {task_description} submitted to background, you can continue to send other questions"
                ),
            },
        )

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass
    

def build_session_tools(
    parent_agent: "DeepAgent",
    toolkit: SessionToolkit,
    language: str = "cn",
    available_agents: str = "",
) -> List[Tool]:
    """Build session tools (list, spawn, and cancel).
    
    Args:
        parent_agent: Parent DeepAgent instance.
        toolkit: SessionToolkit for tracking tasks.
        language: Language for tool descriptions ('cn' or 'en').
        available_agents: Formatted string describing available subagent types.
    
    Returns:
        List of session tools.
    """
    return [
        SessionsListTool(toolkit=toolkit, language=language),
        SessionsSpawnTool(
            parent_agent=parent_agent,
            toolkit=toolkit,
            language=language,
            available_agents=available_agents,
        ),
        SessionsCancelTool(
            parent_agent=parent_agent,
            toolkit=toolkit,
            language=language,
        ),
    ]


__all__ = [
    "SESSION_SPAWN_TASK_TYPE",
    "SessionTaskRow",
    "SessionToolkit",
    "SessionsListTool",
    "SessionsSpawnTool",
    "SessionsCancelTool",
    "build_session_tools",
]
