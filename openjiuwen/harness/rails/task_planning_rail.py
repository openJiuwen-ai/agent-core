# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TaskPlanningRail — registers todo tools on DeepAgent."""
from __future__ import annotations

from typing import Any, Dict, Optional, List

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.prompts.sections import SectionName
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
    ModelUsageRecord,
    TodoItem,
    TodoStatus,
)
from openjiuwen.harness.tools import TodoCreateTool, TodoTool
from openjiuwen.harness.workspace.workspace import WorkspaceNode

_TODO_PROGRESS_REMINDER_KEY = "todo_progress_reminder"  # also tags advance injects (ent name)
_TODO_SESSION_ID_KEY = "todo_session_id"


def _todo_session_label(session_id: str) -> str:
    """Compact session id for reminder prefixes (align with ent shorten_session_label)."""
    normalized = (session_id or "").strip()
    if not normalized:
        return "unknown"
    if len(normalized) <= 32:
        return normalized
    if "_" in normalized:
        tail = normalized.rsplit("_", 1)[-1]
        if tail:
            return tail
    return normalized[-32:]


def _is_todo_reminder_for_session(message: BaseMessage, session_id: str) -> bool:
    metadata = getattr(message, "metadata", None) or {}
    return (
        metadata.get(_TODO_PROGRESS_REMINDER_KEY) is True
        and metadata.get(_TODO_SESSION_ID_KEY) == session_id
    )


def _parse_true_false(value: Any, *, default: bool) -> bool:
    """Parse ``enable_progress_repeat``: only ``true`` / ``false`` (bool or str).

    Allowed: YAML bool, or strings ``\"true\"`` / ``\"false\"`` (case-insensitive).
    Anything else falls back to ``default``.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    logger.warning(
        "Invalid enable_progress_repeat=%r; allowed: true|false. Using default=%s",
        value,
        default,
    )
    return default


def _parse_positive_int(value: Any, *, default: int) -> int:
    """Parse ``list_tool_call_interval``: only int / digit string, ``>= 1``.

    Bool is rejected (``True`` is a subclass of ``int`` in Python). Invalid
    values fall back to ``default``.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        logger.warning(
            "Invalid list_tool_call_interval=%r; allowed: positive int (>=1). "
            "Using default=%s",
            value,
            default,
        )
        return default
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdigit():
            parsed = int(normalized)
            if parsed >= 1:
                return parsed
    logger.warning(
        "Invalid list_tool_call_interval=%r; allowed: positive int (>=1). "
        "Using default=%s",
        value,
        default,
    )
    return default


def resolve_task_planning_rail_kwargs(
    react_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build ``TaskPlanningRail`` constructor kwargs from ``react.task_planning``.

    Returns an empty dict when the section is absent so callers keep rail defaults.
    When the section is present, both known fields are returned (with defaults for
    omitted keys) so env/YAML overrides are applied explicitly.

    Allowed values:
    - ``enable_progress_repeat``: ``true`` / ``false`` (bool or string)
    - ``list_tool_call_interval``: positive int ``>= 1`` (int or digit string)
    """
    if not isinstance(react_config, dict):
        return {}
    raw = react_config.get("task_planning")
    if not isinstance(raw, dict):
        return {}

    return {
        "enable_progress_repeat": _parse_true_false(
            raw.get("enable_progress_repeat", True),
            default=True,
        ),
        "list_tool_call_interval": _parse_positive_int(
            raw.get("list_tool_call_interval", 20),
            default=20,
        ),
    }


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
        # Deferred injection slots (enterprise): store in after_tool_call,
        # inject+pop in before_model_call (no steering).
        self._pending_progress_reminder: Dict[str, str] = {}
        self._pending_advance_reminder: Dict[str, str] = {}
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
                    agent.ability_manager.add_ability(new_tool.card, new_tool)
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
                        agent.ability_manager.remove_ability(name)
        except Exception as exc:
            logger.warning("TaskPlanningRail: failed to remove tool, error: %s", exc)

    # -- hook methods --

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject deferred todo reminders and task planning system prompt.

        Inject runs even when ``system_prompt_builder`` is missing so a progress
        reminder scheduled with language fallback is not stranded in the pending
        dict (enterprise returns early before inject; we keep inject first).
        """
        await self._inject_pending_todo_reminder(ctx)

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
        """Schedule advance / progress reminders after tool call.

        Enterprise-aligned (no steering): write pending dicts in this hook;
        ``before_model_call`` injects at most one UserMessage (advance preferred),
        replacing any prior todo reminder for the session via metadata.

        Advance: only after ``todo_*`` when pending-without-in_progress; clears
        any pending progress. While advance is pending in the dict, progress
        counting/scheduling is skipped (dict gate).

        Progress (repeat): every N tool calls when enabled; skipped only while
        advance is still pending inject — not blocked by todo gap status alone.

        Args:
            ctx: Agent callback context containing inputs and messages.
        """
        tool = self._find_todo_tool()
        if tool is None:
            return

        if ctx.session and isinstance(ctx.inputs, ToolCallInputs):
            tool_name = ctx.inputs.tool_name
            if tool_name and tool_name.startswith("todo_"):
                session_id = ctx.session.get_session_id()
                if tool_name == "todo_create" and ctx.exception is None:
                    # todo_create 整表覆盖后旧 plan 任务 ID 全部失配，
                    # _sync_plan_from_todos 会跳过它们；若不取消，外层
                    # task loop 仍会调度旧任务并把新输入改写为旧任务内容。
                    # 仅在 todo_create 成功时取消：失败时 todo.json 未被覆盖，
                    # 旧 plan 仍有效，误取消会导致 in-flight 任务丢失
                    self._cancel_stale_plan_tasks(ctx)
                try:
                    todos = await tool.load_todos(session_id)
                    self._todos_cache[session_id] = todos
                except Exception:
                    logger.debug("TaskPlanningRail: after tool call refresh cache failed")
                    todos = []
                await self._maybe_schedule_advance_reminder(ctx, session_id, todos)
                await self._sync_plan_from_todos(ctx)

        if not ctx.session:
            return

        session_id = ctx.session.get_session_id()
        if session_id in self._pending_advance_reminder:
            return

        if not self.enable_progress_repeat or not ctx.context:
            return

        if session_id not in self._tool_call_counts:
            self._tool_call_counts[session_id] = 0

        self._tool_call_counts[session_id] += 1
        if self._tool_call_counts[session_id] % self.list_tool_call_interval != 0:
            return

        await self._schedule_progress_reminder(ctx, session_id, tool)

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

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """invoke 开始时重置 todo_create 的本轮标记（无数据副作用）。

        与 TodoTool._created_in_invoke 配合实现"同一 invoke 内至多一次
        todo_create"：此处仅重置内存标记，不清理任何 todo / TaskPlan 数据，
        因此 resume（InteractiveInput）、follow-up、计划续跑场景下的
        in-flight 任务不受影响；上一轮中止残留的任务由新请求真正调用
        todo_create 时整表覆盖自然清理（见 TodoCreateTool 的 guard）。
        """
        if ctx.session is None:
            return
        tool = self._find_todo_create_tool()
        if tool is None:
            return
        tool.reset_invoke_marker(ctx.session.get_session_id())

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

        self._pending_progress_reminder.pop(session_id, None)
        self._pending_advance_reminder.pop(session_id, None)

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
            try:
                todos = await tool.load_todos(session_id)
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

        try:
            todos = await tool.load_todos(session_id)
        except Exception:
            logger.debug("TaskPlanningRail: no todos to sync")
            return

        if not todos:
            return

        # Sync todo.json FROM TaskPlan (but don't regress terminal states)
        status_by_task_id = {
            task.id: task.status
            for task in plan.tasks
        }
        changed = False

        for todo in todos:
            desired = status_by_task_id.get(todo.id)
            if desired is None:
                continue
            # 不回退已终态的任务：completed/cancelled 不应被覆盖为 in_progress/pending
            if todo.status in (TodoStatus.COMPLETED, TodoStatus.CANCELLED):
                continue
            if todo.status != desired:
                todo.status = desired
                changed = True

        if not changed:
            return

        await tool.save_todos(session_id, todos)
        logger.info(
            "TaskPlanningRail: synced %d todos from TaskPlan",
            len(todos),
        )

    async def _sync_plan_from_todos(self, ctx: AgentCallbackContext) -> None:
        """Sync TaskPlan FROM todo.json (terminal states only).

        Ensures TaskPlan reflects LLM's todo_modify updates immediately,
        preventing the outer loop from re-processing completed tasks.
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
        try:
            todos = await tool.load_todos(session_id)
        except Exception as exc:
            logger.warning(
                "TaskPlanningRail: failed to load todos from session %s, error: %s",
                session_id,
                exc,
            )
            return

        if not todos:
            return

        todo_status_by_id = {todo.id: todo.status for todo in todos}
        plan_changed = False
        for task in plan.tasks:
            todo_status = todo_status_by_id.get(task.id)
            if todo_status is None:
                continue
            # 只同步终态，避免外层循环重复处理已完成/已取消的任务
            if todo_status not in (TodoStatus.COMPLETED, TodoStatus.CANCELLED):
                continue
            if task.status != todo_status:
                task.status = todo_status
                plan_changed = True

        if plan_changed:
            ctx.agent.save_state(ctx.session, state)
            logger.info(
                "TaskPlanningRail: synced tasks from todos to TaskPlan",
            )

    def _find_todo_tool(self) -> Optional[TodoTool]:
        """Return the first TodoTool in self.tools."""
        if not self.tools:
            return None
        for tool in self.tools:
            if isinstance(tool, TodoTool):
                return tool
        return None

    def _find_todo_create_tool(self) -> Optional[TodoCreateTool]:
        """Return the TodoCreateTool instance in self.tools.

        `reset_invoke_marker` must hit the exact TodoCreateTool instance that
        sets `_created_in_invoke` in `_create_from_list`; using the first
        generic TodoTool would silently reset a different instance's marker
        (e.g. after tool order changes or instance rebuilds), leaving the
        create guard permanently stuck for the session.
        """
        if not self.tools:
            return None
        for tool in self.tools:
            if isinstance(tool, TodoCreateTool):
                return tool
        return None

    def _cancel_stale_plan_tasks(self, ctx: AgentCallbackContext) -> None:
        """同步取消 TaskPlan 中的非终态任务，与 todo.json 保持一致。

        外层 task loop 只读 state.task_plan 不读 todo.json：若仅清理
        todo.json，旧 plan 的 pending 任务仍会被调度并把用户新输入改写
        为旧任务内容，与 todo 侧已取消的状态自相矛盾。
        """
        load_state = getattr(ctx.agent, "load_state", None)
        save_state = getattr(ctx.agent, "save_state", None)
        if not callable(load_state) or not callable(save_state):
            return
        try:
            state = load_state(ctx.session)
        except Exception:
            logger.debug("TaskPlanningRail: no agent state to cancel stale plan")
            return
        plan = getattr(state, "task_plan", None)
        if plan is None or not plan.tasks:
            return
        cancelled = [
            task for task in plan.tasks
            if task.status in (TodoStatus.PENDING, TodoStatus.IN_PROGRESS)
        ]
        if not cancelled:
            return
        for task in cancelled:
            task.status = TodoStatus.CANCELLED
        save_state(ctx.session, state)
        logger.info(
            "TaskPlanningRail: cancelled %d stale task(s) in TaskPlan",
            len(cancelled),
        )

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

    async def _inject_pending_todo_reminder(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Inject deferred advance/progress as UserMessage (enterprise inject+pop).

        Prefers advance over progress. Only pops **trailing** same-session
        reminders before append so earlier history stays byte-stable for
        prompt/KV cache; buried reminders are left as-is.
        """
        if not ctx.session or not ctx.context:
            return

        session_id = ctx.session.get_session_id()
        prompt = self._pending_advance_reminder.pop(session_id, None)
        if not prompt:
            prompt = self._pending_progress_reminder.pop(session_id, None)
        if not prompt:
            return

        content = f"[TODO · session={_todo_session_label(session_id)}]\n{prompt}"
        metadata = {
            _TODO_PROGRESS_REMINDER_KEY: True,
            _TODO_SESSION_ID_KEY: session_id,
        }
        messages = list(ctx.context.get_messages())
        while messages and _is_todo_reminder_for_session(messages[-1], session_id):
            messages.pop()
        messages.append(UserMessage(content=content, metadata=metadata))
        ctx.context.set_messages(messages)
        logger.debug(
            "TaskPlanningRail: injected todo reminder session_id=%s",
            session_id,
        )

    async def _schedule_progress_reminder(
        self,
        ctx: AgentCallbackContext,
        session_id: str,
        tool: TodoTool,
    ) -> None:
        """Store progress reminder for before_model_call inject."""
        try:
            todos = await tool.load_todos(session_id)
        except Exception:
            logger.debug("TaskPlanningRail: after tool call load todos failed")
            return

        if not todos:
            return

        tasks, in_progress_task = self._format_task_content(todos)
        language = (
            self.system_prompt_builder.language
            if self.system_prompt_builder
            else "cn"
        )
        prompt = build_progress_reminder_user_prompt(
            language=language,
            tasks=tasks,
            in_progress_task=in_progress_task,
        )
        self._pending_progress_reminder[session_id] = prompt
        logger.debug(
            "TaskPlanningRail: scheduled progress reminder session_id=%s "
            "tool_call_count=%d",
            session_id,
            self._tool_call_counts.get(session_id, 0),
        )

    async def _maybe_schedule_advance_reminder(
        self,
        ctx: AgentCallbackContext,
        session_id: str,
        todos: List[TodoItem],
    ) -> None:
        """Store/clear advance reminder; clears pending progress when gap opens.

        Matches enterprise: no schedule (and no clear) when system_prompt_builder
        is missing — before_model_call also no-ops without it.
        """
        if self.system_prompt_builder is None:
            return

        if not todos:
            self._pending_advance_reminder.pop(session_id, None)
            return

        if not self._has_pending_without_in_progress(todos):
            self._pending_advance_reminder.pop(session_id, None)
            return

        tasks, _ = self._format_task_content(todos)
        prompt = build_todo_advance_reminder_user_prompt(
            language=self.system_prompt_builder.language,
            tasks=tasks,
        )
        self._pending_advance_reminder[session_id] = prompt
        self._pending_progress_reminder.pop(session_id, None)
        logger.debug(
            "TaskPlanningRail: scheduled advance reminder session_id=%s",
            session_id,
        )


__all__ = [
    "TaskPlanningRail",
    "resolve_task_planning_rail_kwargs",
]
