# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TaskPlanningRail — registers todo tools on DeepAgent."""
from __future__ import annotations

from typing import Dict, Optional, List

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)
from openjiuwen.harness.prompts.sections.todo import (
    build_progress_reminder_user_prompt,
    build_todo_section,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.task import (
    ModelUsageRecord,
    TodoItem,
    TodoStatus,
)
from openjiuwen.harness.tools import TodoTool
from openjiuwen.harness.workspace.workspace import WorkspaceNode


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
        enable_progress_repeat: bool = False,
        list_tool_call_interval: int = 20,
        model_selection: Optional[Dict[Model, str]] = None,
    ) -> None:
        """Initialize TaskPlanningRail.

        Args:
            enable_progress_repeat: Whether to inject periodic progress reminders.
            list_tool_call_interval: Interval (in tool calls) for progress reminders.
            model_selection: Optional mapping of Model instance to description string.
                The model's client_id (from model_client_config) is used as the model_id
                for switching. When provided, the rail switches the inner ReActAgent's
                model before each LLM call based on the in-progress task's selected_model_id.
        """
        super().__init__()
        self.tools = None
        self.enable_progress_repeat = enable_progress_repeat
        self.list_tool_call_interval = list_tool_call_interval
        self._tool_call_counts = {}
        self._todos_cache: Dict[str, List[TodoItem]] = {}
        self.system_prompt_builder = None
        self._model_selection: Dict[Model, str] = model_selection or {}
        self._model_id_to_model: Dict[str, Model] = {}
        if model_selection:
            for model, desc in model_selection.items():
                if model.model_client_config and model.model_client_config.client_id:
                    self._model_id_to_model[model.model_client_config.client_id] = model
        self._usage_records: Dict[str, ModelUsageRecord] = {}
        self._default_llm: Optional[Model] = None

    def init(self, agent) -> None:
        """Register todo tools on the agent."""
        from openjiuwen.harness.deep_agent import DeepAgent
        from openjiuwen.harness.tools import (
            TodoCreateTool,
            TodoListTool,
            TodoModifyTool,
            TodoGetTool,
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
            (TodoGetTool, False),
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
                    name = getattr(tool.card, "name", None)
                    if name:
                        agent.ability_manager.remove(name)
                    tool_id = tool.card.id
                    if tool_id:
                        Runner.resource_mgr.remove_tool(tool_id)
        except Exception as exc:
            logger.warning("TaskPlanningRail: failed to remove tool, error: %s", exc)

    # -- hook methods --

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject task planning system prompt and switch model if needed."""
        if self.system_prompt_builder is None:
            return

        task_planning_section = build_todo_section(
            language=self.system_prompt_builder.language,
            model_selection=self._model_selection if self._model_selection else None,
        )
        if task_planning_section is not None:
            self.system_prompt_builder.add_section(task_planning_section)
        else:
            self.system_prompt_builder.remove_section(SectionName.TODO)

        if not self._model_selection:
            return

        if self._default_llm is None:
            self._default_llm = getattr(ctx.agent, "_llm", None)

        selected_model_id = await self._get_in_progress_model_id(ctx)

        if selected_model_id and selected_model_id in self._model_id_to_model:
            target_model = self._model_id_to_model[selected_model_id]
        else:
            target_model = self._default_llm

        if target_model is not None:
            ctx.agent.set_llm(target_model)
            ctx.agent.config.model_name = target_model.model_config.model_name
            logger.debug(
                "TaskPlanningRail: switched to model_id=%s", selected_model_id
            )

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Add progress reminder prompt after tool call.

        Every N tool calls (configurable via tool_call_interval), adds a user
        message prompting the model to review current task progress using
        todo_list tool.

        Also refreshes todos cache when todo tools are called.

        Args:
            ctx: Agent callback context containing inputs and messages.
        """
        tool = self._find_todo_tool()
        if tool is None:
            return

        # Refresh todos cache when todo tools are called
        if ctx.session and isinstance(ctx.inputs, ToolCallInputs):
            tool_name = ctx.inputs.tool_name
            if tool_name and tool_name.startswith("todo_"):
                session_id = ctx.session.get_session_id()
                await tool.set_file(session_id)
                try:
                    todos = await tool.load_todos()
                    self._todos_cache[session_id] = todos
                except Exception:
                    logger.debug("TaskPlanningRail: after tool call refresh cache failed")

        if not self.enable_progress_repeat or not ctx.session or not ctx.context:
            return

        session_id = ctx.session.get_session_id()
        if session_id not in self._tool_call_counts:
            self._tool_call_counts[session_id] = 0

        self._tool_call_counts[session_id] += 1
        if self._tool_call_counts[session_id] % self.list_tool_call_interval != 0:
            return

        await tool.set_file(session_id)
        try:
            todos = await tool.load_todos()
        except Exception:
            logger.debug("TaskPlanningRail: after tool call load todos failed")
            return

        if not todos:
            return

        tasks, in_progress_task = self._format_task_content(todos)
        prompt = build_progress_reminder_user_prompt(
            language=self.system_prompt_builder.language,
            tasks=tasks,
            in_progress_task=in_progress_task,
        )
        messages = ctx.context.get_messages()
        messages.append(UserMessage(content=prompt))
        ctx.context.set_messages(messages)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Accumulate token usage per model_id after each LLM call."""
        use_model = getattr(ctx.agent, "_llm", None)
        if use_model is None:
            return
        model_id = use_model.model_client_config.client_id if use_model else None
        response = getattr(ctx.inputs, "response", None)
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return

        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)
        if input_tokens == 0 and output_tokens == 0:
            return

        if model_id not in self._usage_records:
            self._usage_records[model_id] = ModelUsageRecord(model_id=model_id)
        self._usage_records[model_id].add(input_tokens, output_tokens)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Log token usage summary and clean up caches after agent invoke."""
        if self._usage_records:
            for record in self._usage_records.values():
                logger.info("TaskPlanningRail token usage: %s", record)
            self._usage_records = {}

        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()

        # Clean up todos cache
        if session_id in self._todos_cache:
            del self._todos_cache[session_id]

        # Clean up tool call counts
        if session_id in self._tool_call_counts:
            del self._tool_call_counts[session_id]

        # Clean up session resources via public interface
        tool = self._find_todo_tool()
        if tool:
            tool.cleanup_session(session_id)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Sync todo list from task_plan after each iteration."""
        await self._sync_todos_from_plan(ctx)

    # -- internal helpers --

    async def _get_in_progress_model_id(self, ctx: AgentCallbackContext) -> Optional[str]:
        """Return selected_model_id of the current in_progress todo, or None.

        Uses cached todos if available, otherwise loads from file and caches.
        """
        if ctx.session is None:
            return None
        tool = self._find_todo_tool()
        if tool is None:
            return None
        session_id = ctx.session.get_session_id()

        todos = self._todos_cache.get(session_id)
        if todos is None:
            await tool.set_file(session_id)
            try:
                todos = await tool.load_todos()
                self._todos_cache[session_id] = todos
            except Exception:
                return None

        for todo in todos:
            if todo.status == TodoStatus.IN_PROGRESS:
                return todo.selected_model_id
        return None

    async def _sync_todos_from_plan(self, ctx: AgentCallbackContext) -> None:
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
        await tool.set_file(session_id)

        try:
            todos = await tool.load_todos()
        except Exception:
            logger.debug("TaskPlanningRail: no todos to sync")
            return

        if not todos:
            return

        status_by_task_id = {
            task.id: task.status
            for task in plan.tasks
        }
        changed = False

        for todo in todos:
            desired = status_by_task_id.get(todo.id)
            if desired is None:
                continue
            if todo.status != desired:
                todo.status = desired
                changed = True

        if not changed:
            return

        await tool.save_todos(todos)
        logger.info(
            "TaskPlanningRail: synced %d todos from TaskPlan",
            len(todos),
        )

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


__all__ = [
    "TaskPlanningRail",
]
