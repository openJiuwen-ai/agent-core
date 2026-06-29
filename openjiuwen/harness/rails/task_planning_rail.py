# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TaskPlanningRail — registers todo tools on DeepAgent."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)
from openjiuwen.harness.prompts.sections.todo import (
    build_progress_reminder_user_prompt,
    build_todo_advance_reminder_user_prompt,
    build_todo_section,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.task import (
    TaskItem,
    TaskPlan,
    TaskStatus,
)
from openjiuwen.harness.tools.todo import (
    TodoItem,
    TodoStatus,
    TodoTool,
    create_todos_tool,
)
from openjiuwen.harness.tools.todo_resume import (
    TODO_RESUME_SNAPSHOT_PENDING_KEY,
    build_interrupt_resume_todo_reminder,
)
from openjiuwen.harness.workspace.workspace import WorkspaceNode
from openjiuwen.core.context_engine.processor.compressor.util import shorten_session_label

_TODO_PROGRESS_REMINDER_KEY = "todo_progress_reminder"
_TODO_SESSION_ID_KEY = "todo_session_id"
_TODO_TOOL_NAMES = frozenset({
    "todo_create", "todo_start", "todo_complete", "todo_complete_batch",
    "todo_insert", "todo_remove", "todo_modify", "todo_list",
})


class TaskPlanningRailConfig(BaseModel):
    """``react.task_planning`` yaml section."""

    model_config = ConfigDict(extra="ignore")

    enable_progress_repeat: bool = True
    list_tool_call_interval: int = Field(default=20, ge=1)


def resolve_task_planning_rail_kwargs(
    react_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build ``TaskPlanningRail`` constructor kwargs from ``react.task_planning`` config."""
    if not isinstance(react_config, dict):
        return {}
    raw = react_config.get("task_planning")
    if not isinstance(raw, dict):
        return {}
    return TaskPlanningRailConfig.model_validate(raw).model_dump()


def _is_progress_reminder_for_session(message: BaseMessage, session_id: str) -> bool:
    metadata = getattr(message, "metadata", None) or {}
    return (
        metadata.get(_TODO_PROGRESS_REMINDER_KEY) is True
        and metadata.get(_TODO_SESSION_ID_KEY) == session_id
    )


class TaskPlanningRail(DeepAgentRail):
    """Rail that registers todo tools on the agent.

    After the first task-loop iteration, bridges the
    LLM-created todo list into a ``TaskPlan`` so the
    outer loop can schedule subsequent steps.

    Attributes:
        priority: Execution priority (90 = high).
    """

    priority = 90

    def __init__(
        self,
        enable_progress_repeat: bool = True,
        list_tool_call_interval: int = 20,
    ) -> None:
        """Initialize TaskPlanningRail.

        Args:
            enable_progress_repeat: Check if progress repeat is enabled.
            list_tool_call_interval: Interval for progress reminder prompts (default: 20).
        """
        super().__init__()
        self.tools = None
        self.enable_progress_repeat = enable_progress_repeat
        self.list_tool_call_interval = list_tool_call_interval
        self._tool_call_counts = {}
        self._pending_progress_reminder: Dict[str, str] = {}
        self._pending_advance_reminder: Dict[str, str] = {}
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        """Register todo tools on the agent."""
        from openjiuwen.harness.deep_agent import (
            DeepAgent,
        )
        from openjiuwen.harness.tools.todo import (
            TodoCreateTool,
            TodoListTool,
            TodoModifyTool,
        )

        if not (
            isinstance(agent, DeepAgent)
            and agent.deep_config
            and hasattr(agent, "ability_manager")
        ):
            return

        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

        if not self.sys_operation:
            self.set_sys_operation(agent.deep_config.sys_operation)
        if not self.workspace:
            self.set_workspace(agent.deep_config.workspace)

        workspace_dir = str(self.workspace.get_node_path(WorkspaceNode.TODO))
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        language = self.system_prompt_builder.language if self.system_prompt_builder else "cn"

        tool_configs = [
            (TodoCreateTool, False),
            (TodoListTool, False),
            (TodoModifyTool, False),
        ]

        existing_tools = []
        for ability in agent.ability_manager.list():
            if isinstance(ability, ToolCard):
                tool_instance = Runner.resource_mgr.get_tool(tool_id=ability.id)
                if tool_instance:
                    for i, (tool_class, found) in enumerate(tool_configs):
                        if isinstance(tool_instance, tool_class):
                            tool_configs[i] = (tool_class, True)
                            existing_tools.append(tool_instance)
                            break

        tools = existing_tools.copy()
        try:
            for tool_class, found in tool_configs:
                if not found:
                    new_tool = tool_class(self.sys_operation, workspace_dir, language, agent_id)
                    Runner.resource_mgr.add_tool(new_tool)
                    agent.ability_manager.add(new_tool.card)
                    tools.append(new_tool)
            self.tools = tools
        except Exception as exc:
            logger.warning("TaskPlanningRail: failed to add tool, error: %s", exc)

    def uninit(self, agent) -> None:
        """Remove todo tools from the agent."""
        try:
            if self.system_prompt_builder:
                self.system_prompt_builder.remove_section("todo")
            if self.tools and hasattr(agent, "ability_manager"):
                for tool in self.tools:
                    name = getattr(tool.card, 'name', None)
                    if name:
                        agent.ability_manager.remove(name)
                    tool_id = tool.card.id
                    if tool_id:
                        Runner.resource_mgr.remove_tool(tool_id)
        except Exception as exc:
            logger.warning("TaskPlanningRail: failed to remove tool, error: %s", exc)

    # -- hook methods --
    async def before_model_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Inject task planning system prompt before model call."""
        if self.system_prompt_builder is None:
            return

        task_planning_section = build_todo_section(
            language=self.system_prompt_builder.language
        )
        if task_planning_section is not None:
            self.system_prompt_builder.add_section(task_planning_section)
        else:
            self.system_prompt_builder.remove_section(SectionName.TODO)

        await self._inject_pending_progress_reminder(ctx)
        await self._inject_interrupt_resume_todo_snapshot(ctx)

    async def _inject_interrupt_resume_todo_snapshot(
        self, ctx: AgentCallbackContext
    ) -> None:
        """On first model call after interrupt-resume, inject structured todo snapshot."""
        if not ctx.session or not ctx.context:
            return

        session_id = ctx.session.get_session_id()
        pending = ctx.session.get_state(TODO_RESUME_SNAPSHOT_PENDING_KEY)
        if pending is not True:
            return

        tool = self._find_todo_tool()
        if tool is None or self.system_prompt_builder is None:
            logger.warning(
                "TaskPlanningRail: interrupt-resume pending but todo tool unavailable session_id=%s; "
                "clearing latch (supplementary prompt already injected)",
                session_id,
            )
            ctx.session.update_state({TODO_RESUME_SNAPSHOT_PENDING_KEY: False})
            return

        todos = await self._load_session_todos(session_id, tool)
        if not todos:
            logger.warning(
                "TaskPlanningRail: interrupt-resume pending but no todos loaded session_id=%s; "
                "clearing latch (supplementary prompt already injected)",
                session_id,
            )
            ctx.session.update_state({TODO_RESUME_SNAPSHOT_PENDING_KEY: False})
            return

        tasks, in_progress_task = self._format_task_content(todos)
        language = self.system_prompt_builder.language
        prompt = build_interrupt_resume_todo_reminder(
            language,
            tasks=tasks,
            in_progress_task=in_progress_task,
        )

        session_label = shorten_session_label(session_id)
        content = f"[TODO · session={session_label}]\n{prompt}"
        metadata = {
            _TODO_PROGRESS_REMINDER_KEY: True,
            _TODO_SESSION_ID_KEY: session_id,
            "interrupt_resume_todo_snapshot": True,
        }
        messages = [
            message
            for message in ctx.context.get_messages()
            if not _is_progress_reminder_for_session(message, session_id)
        ]
        messages.append(UserMessage(content=content, metadata=metadata))
        ctx.context.set_messages(messages)
        ctx.session.update_state({TODO_RESUME_SNAPSHOT_PENDING_KEY: False})
        logger.info(
            "TaskPlanningRail: injected interrupt-resume todo snapshot session_id=%s",
            session_id,
        )

    async def after_tool_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Schedule reminders for the next model call.

        Advance reminder (gap: pending without in_progress) runs only after
        todo tools to avoid parallel non-todo after chains contending on
        ``todo.json`` file locks.

        Progress repeat (optional) counts all tool calls; every N calls it
        loads todos and stores a user reminder for ``before_model_call``.

        Args:
            ctx: Agent callback context containing inputs and messages.
        """
        if ctx.session is None:
            return

        tool_name = ""
        if isinstance(ctx.inputs, ToolCallInputs):
            tool_name = ctx.inputs.tool_name or ""
        if tool_name in _TODO_TOOL_NAMES:
            await self._maybe_schedule_advance_reminder(ctx)

        session_id = ctx.session.get_session_id()
        if session_id in self._pending_advance_reminder:
            return

        if not self.enable_progress_repeat:
            return

        tool = self._find_todo_tool()
        if tool is None:
            return

        if session_id not in self._tool_call_counts:
            self._tool_call_counts[session_id] = 0

        self._tool_call_counts[session_id] += 1
        if self._tool_call_counts[session_id] % self.list_tool_call_interval != 0:
            return

        await self._schedule_progress_reminder(ctx, session_id, tool)

    async def after_invoke(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Clean up tool call count after agent invoke"""
        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()
        if session_id in self._tool_call_counts:
            del self._tool_call_counts[session_id]
        self._pending_progress_reminder.pop(session_id, None)
        self._pending_advance_reminder.pop(session_id, None)

    async def after_task_iteration(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Bridge todo list to TaskPlan after iteration."""
        await self._bridge_todos_to_plan(ctx)
        await self._sync_todos_from_plan(ctx)

    # -- internal helpers --

    async def _bridge_todos_to_plan(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Convert LLM-created todos into a TaskPlan.

        Guards:
            - ctx.session must exist
            - state.task_plan must be empty (no re-entry)
            - A TodoTool must be registered
            - At least one PENDING todo must exist
        """
        if ctx.session is None:
            return

        state = ctx.agent.load_state(ctx.session)  # type: ignore[attr-defined]

        if (
            state.task_plan is not None
            and len(state.task_plan.tasks) > 0
        ):
            return

        tool = self._find_todo_tool()
        if tool is None:
            return

        session_id = ctx.session.get_session_id()
        file_path = tool.file_path_for_session(session_id)

        try:
            todos = await tool.load_todos(file_path)
        except Exception:
            logger.debug(
                "TaskPlanningRail: no todos to bridge"
            )
            return

        if not todos:
            return

        has_pending = any(
            t.status == TodoStatus.PENDING for t in todos
        )
        if not has_pending:
            return

        plan = TaskPlan(goal="bridged from todo list")
        for todo in todos:
            if todo.status == TodoStatus.COMPLETED:
                task_status = TaskStatus.COMPLETED
            elif todo.status == TodoStatus.IN_PROGRESS:
                task_status = TaskStatus.IN_PROGRESS
            elif todo.status == TodoStatus.CANCELLED:
                task_status = TaskStatus.FAILED
            else:
                task_status = TaskStatus.PENDING

            plan.add_task(
                TaskItem(
                    id=todo.id,
                    title=todo.content,
                    status=task_status,
                )
            )

        state.task_plan = plan
        ctx.agent.save_state(ctx.session, state)  # type: ignore[attr-defined]
        logger.info(
            "TaskPlanningRail: bridged %d todos "
            "into TaskPlan (%s)",
            len(todos),
            plan.get_progress_summary(),
        )

    async def _sync_todos_from_plan(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Sync Todo file statuses from current TaskPlan.

        This keeps todo persistence and task-plan status aligned.
        Without this sync, a task can be marked completed in
        TaskPlan while still being ``in_progress`` in todo file,
        which later causes todo validation conflicts.
        """
        if ctx.session is None:
            return

        state = ctx.agent.load_state(ctx.session)  # type: ignore[attr-defined]
        plan = state.task_plan
        if plan is None or len(plan.tasks) == 0:
            return

        tool = self._find_todo_tool()
        if tool is None:
            return

        session_id = ctx.session.get_session_id()
        file_path = tool.file_path_for_session(session_id)

        try:
            todos = await tool.load_todos(file_path)
        except Exception:
            logger.debug(
                "TaskPlanningRail: no todos for sync"
            )
            return

        if not todos:
            return

        status_by_task_id = {
            task.id: self._to_todo_status(task.status)
            for task in plan.tasks
        }
        changed = False
        now = datetime.now(timezone.utc).isoformat()

        for todo in todos:
            desired = status_by_task_id.get(todo.id)
            if desired is None:
                continue
            if todo.status != desired:
                todo.status = desired
                todo.updatedAt = now
                changed = True

        if not changed:
            return

        await tool.save_todos(todos, file_path)
        logger.info(
            "TaskPlanningRail: synced %d todos from TaskPlan",
            len(todos),
        )

    @staticmethod
    def _to_todo_status(status: TaskStatus) -> TodoStatus:
        """Map TaskPlan status to Todo status."""
        if status == TaskStatus.PENDING:
            return TodoStatus.PENDING
        if status == TaskStatus.IN_PROGRESS:
            return TodoStatus.IN_PROGRESS
        if status == TaskStatus.FAILED:
            return TodoStatus.CANCELLED
        return TodoStatus.COMPLETED

    async def _inject_pending_progress_reminder(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Inject a deferred progress reminder after tool results are in context."""
        if not ctx.session or not ctx.context:
            return

        session_id = ctx.session.get_session_id()
        prompt = self._pending_advance_reminder.pop(session_id, None)
        if not prompt:
            prompt = self._pending_progress_reminder.pop(session_id, None)
        if not prompt:
            return

        session_label = shorten_session_label(session_id)
        content = f"[TODO · session={session_label}]\n{prompt}"
        metadata = {
            _TODO_PROGRESS_REMINDER_KEY: True,
            _TODO_SESSION_ID_KEY: session_id,
        }
        messages = [
            message
            for message in ctx.context.get_messages()
            if not _is_progress_reminder_for_session(message, session_id)
        ]
        messages.append(UserMessage(content=content, metadata=metadata))
        ctx.context.set_messages(messages)

    def _find_todo_tool(self) -> Optional[TodoTool]:
        """Return the first TodoTool in self.tools."""
        if not self.tools:
            return None
        for tool in self.tools:
            if isinstance(tool, TodoTool):
                return tool
        return None

    def _format_task_content(self, todos: List[TodoItem]):
        """Format todos into a readable task content string.

        Args:
            todos: List of TodoItem objects to format.

        Returns:
            A tuple of (tasks, in_progress_task) where:
            - tasks: String showing all tasks with id, status, and content
            - in_progress_task: String content of the currently executing task (empty if none)
        """
        todos_str = []
        in_progress_str = ""
        for todo in todos:
            if todo.status == TodoStatus.IN_PROGRESS:
                in_progress_str = todo.content
            line = f"id: {todo.id} |status: {todo.status} |content: {todo.content}"
            todos_str.append(line)

        return "\n".join(todos_str), in_progress_str

    @staticmethod
    def _has_pending_without_in_progress(todos: List[TodoItem]) -> bool:
        has_pending = any(t.status == TodoStatus.PENDING for t in todos)
        has_in_progress = any(t.status == TodoStatus.IN_PROGRESS for t in todos)
        return has_pending and not has_in_progress

    async def _load_session_todos(
        self,
        session_id: str,
        tool: TodoTool,
    ) -> List[TodoItem]:
        file_path = tool.file_path_for_session(session_id)
        try:
            return await tool.load_todos(file_path)
        except Exception:
            logger.debug("TaskPlanningRail: after tool call load todos failed")
            return []

    async def _maybe_schedule_advance_reminder(
        self, ctx: AgentCallbackContext
    ) -> None:
        tool = self._find_todo_tool()
        if tool is None or self.system_prompt_builder is None:
            return

        session_id = ctx.session.get_session_id()
        todos = await self._load_session_todos(session_id, tool)
        if not todos:
            self._pending_advance_reminder.pop(session_id, None)
            return

        if not self._has_pending_without_in_progress(todos):
            self._pending_advance_reminder.pop(session_id, None)
            return

        tasks, _ = self._format_task_content(todos)
        language = self.system_prompt_builder.language
        prompt = build_todo_advance_reminder_user_prompt(
            language=language,
            tasks=tasks,
        )
        self._pending_advance_reminder[session_id] = prompt
        self._pending_progress_reminder.pop(session_id, None)

    async def _schedule_progress_reminder(
        self,
        ctx: AgentCallbackContext,
        session_id: str,
        tool: TodoTool,
    ) -> None:
        todos = await self._load_session_todos(session_id, tool)
        if not todos:
            return

        if self._has_pending_without_in_progress(todos):
            return

        tasks, in_progress_task = self._format_task_content(todos)
        prompt = build_progress_reminder_user_prompt(
            language=self.system_prompt_builder.language,
            tasks=tasks,
            in_progress_task=in_progress_task,
        )
        self._pending_progress_reminder[session_id] = prompt
        logger.debug(
            "TaskPlanningRail: scheduled progress reminder session_id=%s "
            "tool_call_count=%d",
            session_id,
            self._tool_call_counts[session_id],
        )


__all__ = [
    "TaskPlanningRail",
    "TaskPlanningRailConfig",
    "resolve_task_planning_rail_kwargs",
]
