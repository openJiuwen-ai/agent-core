# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import os.path
import uuid as _uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, List, Optional, Dict

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import tool_logger, LogEventType
from openjiuwen.core.foundation.tool import Tool, ToolCard, Input, Output
from openjiuwen.core.session.agent import Session
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.todo_store import (
    TodoItems, current_transaction, read_snapshot, transact, todo_operation, observe_plan, _TRANSACTION,
)
from openjiuwen.harness.schema.task import (
    STATUS_ICONS,
    TodoItem,
    TodoStatus,
)

# Session-state key holding the current todo generation token. The host
# (agent adapter) bumps it at the start of each fresh (non-resume) user turn;
# these tools stamp newly created/updated items with it so broadcast layers can
# filter entries from superseded generations (request isolation).
#
# Cross-repo contract: agent-core only reads/stamps/filters — it never bumps
# the token, because only the host knows the fresh-turn boundary (a resume
# replay must keep the original generation's todos visible). Host-side closure
# (bump + broadcast filtering + tests) lives in jiuwenswarm: see
# agents/harness/common/tools/todo_resume.py, server/runtime/agent_adapter/
# stale_todo_cleanup_helpers.py and tests/unit_tests/agentserver/rails/
# test_todo_updated_stale_filter.py.
TODO_GENERATION_TOKEN_SESSION_KEY = "todo_generation_token"


class TodoLockManager:
    """Manages operation locks for todo tools, keyed by session_id.

    Ensures mutual exclusion for file I/O operations within the same session.
    Different sessions use different locks and do not block each other.

    All tools sharing the same lock manager will coordinate their operations
    for the same session, preventing race conditions when multiple tools
    try to access or modify the file simultaneously.
    """

    def __init__(self):
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create lock for a specific session."""
        async with self._global_lock:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = asyncio.Lock()
            return self._session_locks[session_id]

    @asynccontextmanager
    async def operation(self, session_id: str):
        """Acquire the operation lock for a specific session.

        Args:
            session_id: The session ID to lock operations for.
        """
        lock = await self._get_session_lock(session_id)
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def cleanup_session(self, session_id: str) -> None:
        """Remove lock for a session (cleanup when session ends)."""
        if session_id in self._session_locks:
            del self._session_locks[session_id]


class TodoTool(Tool):
    """Base class for Todo tools with common persistence operations"""

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        pass

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass

    def __init__(
        self,
        card: ToolCard,
        operation: SysOperation,
        workspace: Optional[str] = None,
        lock_manager: Optional[TodoLockManager] = None,
    ):
        """Initialize TodoTool with persistence layer.

        Args:
            card: Tool metadata card with description and parameter schema.
            operation: System operation for file system access.
            workspace: Path for file system operations.
            lock_manager: Shared lock manager for coordinating operations across
                all todo tools. If not provided, a new one is created.
                Tools should share the same lock manager instance.
        """
        super().__init__(card)
        self.workspace = workspace if workspace else "./"
        self.fs = operation.fs()
        self._lock_manager = lock_manager if lock_manager else TodoLockManager()
        # 本 invoke 内是否已执行过 todo_create（由 TaskPlanningRail 在
        # before_invoke 重置，用于"同一 invoke 至多一次整表重建"的 guard）
        self._created_in_invoke: set = set()
        # Host-owned lifecycle evidence; never supplied by model arguments.
        self.snapshot_guard: Callable[..., Awaitable[None]] | None = None

    def _get_file_path(self, session_id: str) -> str:
        """Get the file path for a given session_id.

        Args:
            session_id: The session ID to get file path for.

        Returns:
            Full file path for the session's todo.json file.
        """
        return os.path.join(self.workspace, f"./{session_id}/todo.json")

    async def load_todos(self, session_id: str) -> List[TodoItem]:
        tx = current_transaction(self, session_id)
        if tx is not None:
            if tx.pending is not None:
                from dataclasses import replace
                return TodoItems(replace(tx.snapshot, todos=tx.pending))
            return tx.items
        try:
            snapshot = await read_snapshot(self.fs, os.path.abspath(self._get_file_path(session_id)))
            from openjiuwen.harness.tools.todo_store import check_snapshot_lifecycle
            await check_snapshot_lifecycle(self, session_id, snapshot)
        except Exception as exc:
            raise build_error(StatusCode.TOOL_TODOS_LOAD_FAILED, reason=str(exc)) from exc
        if not snapshot.exists:
            raise build_error(StatusCode.TOOL_TODOS_LOAD_FAILED, reason="Todo file not found")
        observe_plan(self, session_id, snapshot)
        return TodoItems(snapshot)

    async def save_todos(self, session_id: str, todos: List[TodoItem]):
        tx = current_transaction(self, session_id)
        if tx is not None:
            tx.pending = tx.items.records(todos)
            return
        if not isinstance(todos, TodoItems):
            raise TypeError("Todo writes require a loaded baseline or an update transaction")
        desired = todos.records(todos)
        original = {t["id"]: t for t in todos.snapshot.todos}
        wanted = {t["id"]: t for t in desired}
        async def apply():
            current = current_transaction(self, session_id)
            merged = []
            for row in current.snapshot.todos:
                ident = row["id"]
                if ident in original and ident not in wanted:
                    continue
                patch = {k: v for k, v in wanted.get(ident, {}).items()
                         if k not in original.get(ident, {}) or v != original[ident][k]}
                merged.append({**row, **patch})
            existing = {t["id"] for t in merged}
            if any(i not in existing and wanted[i] != original[i] for i in wanted.keys() & original.keys()):
                raise ValueError("Updated Todo no longer exists")
            for row in desired:
                if row["id"] not in original:
                    if row["id"] in existing:
                        raise ValueError("Duplicate Todo id")
                    merged.append(row)
            current.pending = merged
        try:
            await transact(self, session_id, apply, expected_plan=todos.snapshot.plan_id)
        except Exception as exc:
            raise build_error(StatusCode.TOOL_TODOS_SAVE_FAILED, reason=str(exc)) from exc

    async def update_todos(self, session_id: str, callback, **kwargs):
        """Apply a host mutation to the latest items within the same transaction."""
        async def apply():
            items = await self.load_todos(session_id)
            result = await callback(items)
            await self.save_todos(session_id, items)
            return result
        return await transact(self, session_id, apply, **kwargs)

    def reset_invoke_marker(self, session_id: str) -> None:
        """Mark the start of a new invoke: forget "already created" state.

        由 TaskPlanningRail.before_invoke 调用，配合 guard 实现"同一
        invoke 内至多一次 todo_create"。本方法仅重置内存标记，不清理
        任何数据：resume / follow-up / 计划续跑场景的 in-flight 任务
        不受影响，上一轮中止残留的任务由新请求真正调用 todo_create 时
        整表覆盖自然清理。
        """
        self._created_in_invoke.discard(session_id)

    def cleanup_session(self, session_id: str) -> None:
        """Clean up resources for a session (call when session ends).

        Args:
            session_id: The session ID to clean up resources for.
        """
        self._lock_manager.cleanup_session(session_id)
        self._created_in_invoke.discard(session_id)

    @staticmethod
    def _session_generation_token(session: Any) -> Optional[str]:
        """Read the current todo generation token from the session, if any.

        Fail-open: sessions without ``get_state`` (or with a non-string value)
        yield None, and unstamped/None tokens are never filtered anywhere.
        """
        getter = getattr(session, "get_state", None)
        if not callable(getter):
            return None
        try:
            value = getter(TODO_GENERATION_TOKEN_SESSION_KEY)
        except Exception:
            return None
        return value if isinstance(value, str) and value else None


class TodoCreateTool(TodoTool):
    """Todo Create Tool - Create new todo items with session."""

    def __init__(
        self,
        operation: SysOperation,
        workspace: Optional[str] = None,
        language: str = "cn",
        agent_id: Optional[str] = None,
        lock_manager: Optional[TodoLockManager] = None,
    ):
        super().__init__(
            build_tool_card("todo_create", "TodoCreateTool", language, agent_id=agent_id),
            operation,
            workspace,
            lock_manager,
        )

    @todo_operation
    async def invoke(self, inputs: Input, **kwargs) -> Output:
        """Asynchronous invocation handler for TodoCreate tool operations

        Args:
            inputs: Input parameters dictionary containing task creation data
            **kwargs: Additional operation parameters (supports session_id override)

        Returns:
            Execution result with human-readable creation message

        Raises:
            ToolError: If invalid parameters or operation fails
        """
        session = kwargs.get("session", None)
        session_id = session.get_session_id() if session and hasattr(session, "get_session_id") else None
        if session_id is None:
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason="Session ID is required"
            )

        results = dict()
        try:
            tasks_input = inputs.get("tasks")
            if tasks_input and isinstance(tasks_input, list):
                generation_token = self._session_generation_token(session)
                results["message"] = await self._create_from_list(
                    session_id, tasks_input, generation_token=generation_token,
                    plan_action=inputs.get("plan_action")
                )
                return results

            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason="'tasks' parameter is required and must be a JSON array"
            )

        except Exception as e:
            tool_logger.error(
                "Todo create tool invocation failed",
                event_type=LogEventType.TOOL_CALL_ERROR,
                exception=str(e)
            )
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason=str(e)
            ) from e

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        """Stream response handler (not supported for TodoCreate tool)"""
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)

    def _format_create_result(self, todos: List[TodoItem]) -> str:
        """Format task creation result into human-readable string

        Args:
            todos: List of created TodoItem instances

        Returns:
            Formatted success message with task details
        """
        result = f"Successfully created {len(todos)} task(s):\n"
        for todo in todos:
            status_icon = STATUS_ICONS.get(todo.status, "[ ]")
            model_info = f" (model: {todo.selected_model_id})" if todo.selected_model_id else ""
            result += f"  {status_icon} task_id: {todo.id} , content: {todo.content}{model_info}\n"
        first_task = todos[0].content if todos else ""
        result += f"\nNext step: Immediately execute task '{first_task}'"
        return result.strip()

    async def _reject_recreate_within_invoke(self, session_id: str) -> None:
        """Reject a second todo_create within the same invoke.

        同一 invoke 内重复 todo_create 会整表覆盖并改写全部任务 ID，导致
        下游任务跟踪错乱（task.start/task.update 事件与已累积的 task-run
        段失配，前端任务列表乱序），故每 invoke 至多允许一次整表重建。
        本 invoke 尚未创建过时直接放行：上一轮中止残留的未完成任务会被
        新任务表整体覆盖而自然清理，新请求不再被残留数据阻塞。
        """
        if session_id not in self._created_in_invoke:
            return
        raise build_error(
            StatusCode.TOOL_TODOS_VALIDATION_INVALID,
            reason=(
                "todo_create has already rebuilt the task list once in this "
                "round; rebuilding again would replace all task ids and break "
                "task tracking. Use todo_modify (action: append/insert_after/"
                "insert_before) to add tasks, or todo_modify (action: update) "
                "to update existing tasks instead."
            ),
        )

    async def _create_from_list(
        self,
        session_id: str,
        tasks_data: List[Dict[str, Any]],
        *,
        generation_token: Optional[str] = None,
        plan_action: str | None = None,
    ) -> str:
        """Create todo items from a JSON array of task objects."""
        if not tasks_data:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="Task list cannot be empty"
            )
        tx = current_transaction(self, session_id)
        if tx is None:
            return await transact(self, session_id, lambda: self._create_from_list(
                session_id, tasks_data, generation_token=generation_token, plan_action=plan_action))
        if plan_action not in (None, "new", "rebuild"):
            raise ValueError("plan_action must be new or rebuild")
        await self._reject_recreate_within_invoke(session_id)
        if plan_action is None and any(t.get("status", "pending") not in ("completed", "cancelled")
                                       for t in tx.snapshot.todos):
            raise ValueError("An active plan exists; use todo_modify or explicitly choose plan_action=new/rebuild")
        new_todos = []
        seen_ids: set = set()
        for i, task_data in enumerate(tasks_data):
            content = task_data.get("content", "")
            if not content:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Task at index {i} is missing a 'content' field"
                )
            active_form = task_data.get("activeForm", "")
            description = task_data.get("description", "")
            # Prefer model-provided id; fall back to uuid only when absent
            task_id = str(task_data.get("id") or "").strip()
            if not task_id:
                task_id = str(_uuid.uuid4())
            if task_id in seen_ids:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Duplicate task id '{task_id}' at index {i}"
                )
            seen_ids.add(task_id)
            status = TodoStatus.IN_PROGRESS if i == 0 else TodoStatus.PENDING
            new_todos.append(
                TodoItem(
                    id=task_id,
                    content=content,
                    activeForm=active_form,
                    description=description,
                    status=status,
                    selected_model_id=task_data.get("selected_model_id"),
                    generation_token=generation_token,
                )
            )

        await self.save_todos(session_id, new_todos)
        tx.new_plan = _uuid.uuid4().hex
        tx.created = True
        tool_logger.info("Created todo items from JSON array", event_type=LogEventType.TOOL_CALL_END)
        return self._format_create_result(new_todos)


class TodoListTool(TodoTool):
    """Todo List Tool - Returns active (non-completed, non-cancelled) todo items."""

    def __init__(
        self,
        operation: SysOperation,
        workspace: Optional[str] = None,
        language: str = "cn",
        agent_id: Optional[str] = None,
        lock_manager: Optional[TodoLockManager] = None,
    ):
        super().__init__(
            build_tool_card("todo_list", "TodoListTool", language, agent_id=agent_id),
            operation,
            workspace,
            lock_manager,
        )

    @todo_operation
    async def invoke(self, inputs: Input, **kwargs) -> Output:
        """Asynchronous invocation handler for TodoCreate tool operations

        Args:
            inputs: Input parameters dictionary (no additional params required)
            **kwargs: Additional operation parameters (supports session_id override)

        Returns:
            Execution result with formatted todo list

        Raises:
            ToolError: If no todos exist or operation fails
        """
        session = kwargs.get("session", None)
        session_id = session.get_session_id() if session and hasattr(session, "get_session_id") else None
        if session_id is None:
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason="Session ID is required"
            )

        try:
            tasks = await self.load_todos(session_id)
            active_tasks = [
                t for t in tasks
                if t.status not in (TodoStatus.CANCELLED, TodoStatus.COMPLETED)
            ]
            # 请求隔离：过滤已被新一轮请求取代的旧代条目，让 LLM 视图与
            # 前端广播（todo.updated / task.update）一致。未打标条目放行。
            generation_token = self._session_generation_token(session)
            tx = current_transaction(self, session_id)
            if generation_token and tx is not None and not tx.snapshot.versioned:
                active_tasks = [
                    t for t in active_tasks
                    if not t.generation_token or t.generation_token == generation_token
                ]
            simplified = [
                {
                    "id": t.id,
                    "content": t.content,
                    "status": t.status.value,
                    "depends_on": t.depends_on,
                }
                for t in active_tasks
            ]
            return {"tasks": simplified}
        except Exception as e:
            tool_logger.error(
                "Todo list tool invocation failed",
                event_type=LogEventType.TOOL_CALL_ERROR,
                exception=str(e)
            )
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason=str(e)
            ) from e

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        """Stream response handler (not supported for TodoList tool)"""
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


class TodoGetTool(TodoTool):
    """Todo Get Tool - Returns full details of a single task by ID."""

    def __init__(
        self,
        operation: SysOperation,
        workspace: Optional[str] = None,
        language: str = "cn",
        agent_id: Optional[str] = None,
        lock_manager: Optional[TodoLockManager] = None,
    ):
        super().__init__(
            build_tool_card("todo_get", "TodoGetTool", language, agent_id=agent_id),
            operation,
            workspace,
            lock_manager,
        )

    @todo_operation
    async def invoke(self, inputs: Input, **kwargs) -> Output:
        session = kwargs.get("session", None)
        session_id = session.get_session_id() if session and hasattr(session, "get_session_id") else None
        if session_id is None:
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason="Session ID is required"
            )

        task_id = inputs.get("id")
        if not task_id:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="Task ID is required"
            )

        try:
            tasks = await self.load_todos(session_id)
            for task in tasks:
                if task.id == task_id:
                    return {"todo": task.to_dict()}
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason=f"Task with id '{task_id}' not found"
            )
        except Exception as e:
            tool_logger.error(
                "Todo get tool invocation failed",
                event_type=LogEventType.TOOL_CALL_ERROR,
                exception=str(e)
            )
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason=str(e)
            ) from e

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


class TodoModifyTool(TodoTool):
    """
    Todo Modify Tool

    update/delete/cancel/append/insert_after/insert_before action operations todo items with session
    """

    def __init__(
        self,
        operation: SysOperation,
        workspace: Optional[str] = None,
        language: str = "cn",
        agent_id: Optional[str] = None,
        lock_manager: Optional[TodoLockManager] = None,
    ):
        super().__init__(
            build_tool_card("todo_modify", "TodoModifyTool", language, agent_id=agent_id),
            operation,
            workspace,
            lock_manager,
        )

    @todo_operation
    async def invoke(self, inputs: Input, **kwargs) -> Output:
        session = kwargs.get("session", None)
        session_id = session.get_session_id() if session and hasattr(session, "get_session_id") else None
        if session_id is None:
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason="Session ID is required"
            )

        results = dict()
        try:
            action = inputs.get("action")
            if not action:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason="Invalid input: 'action' field is required"
                )

            current_todos = await self.load_todos(session_id)
            generation_token = self._session_generation_token(session)

            if action == "delete":
                ids = inputs.get("ids")
                if not ids or not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                    raise build_error(
                        StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                        reason="Invalid input for delete action: 'ids' must be a non-empty list of task IDs"
                    )
                results["message"] = await self._delete_todos(session_id, ids, current_todos)
            elif action == "cancel":
                ids = inputs.get("ids")
                if not ids or not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                    raise build_error(
                        StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                        reason="Invalid input for cancel action: 'ids' must be a non-empty list of task IDs"
                    )
                results["message"] = await self._cancel_todos(session_id, ids, current_todos)
            elif action == "update":
                todos_data = inputs.get("todos")
                results["message"] = await self._update_todos(
                    session_id, todos_data, current_todos, generation_token=generation_token
                )
            elif action == "append":
                todos_data = inputs.get("todos")
                results["message"] = await self._append_todos(
                    session_id, todos_data, current_todos, generation_token=generation_token
                )
            elif action == "insert_after":
                todo_data = inputs.get("todo_data")
                self._validate_todo_data_structure(todo_data)
                target_id = todo_data["target_id"]
                insert_todos = todo_data["items"]
                results["message"] = await self._insert_after_todos(
                    session_id, target_id, insert_todos, current_todos,
                    generation_token=generation_token,
                )
            elif action == "insert_before":
                todo_data = inputs.get("todo_data")
                self._validate_todo_data_structure(todo_data)
                target_id = todo_data["target_id"]
                insert_todos = todo_data["items"]
                results["message"] = await self._insert_before_todos(
                    session_id, target_id, insert_todos, current_todos,
                    generation_token=generation_token,
                )
            else:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Invalid action: {action}"
                )
            return results
        except Exception as e:
            tool_logger.error(
                "Todo modify tool invocation failed",
                event_type=LogEventType.TOOL_CALL_ERROR,
                exception=str(e)
            )
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason=str(e)
            ) from e

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)

    def _validate_todo_data_structure(self, todo_data: Dict):
        """Validate structure of todo_data object for insert_after/insert_before actions

        Args:
            todo_data: {"target_id": str, "items": [todo_list]} object to validate

        Raises:
            ToolError: If todo_data is invalid (missing fields or wrong type)
        """
        if not todo_data or not isinstance(todo_data, dict):
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="Invalid input for insert action: 'todo_data' must be an object with 'target_id' and 'items'"
            )

        target_id = todo_data.get("target_id")
        insert_todos = todo_data.get("items")

        if not target_id or not isinstance(target_id, str):
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="Invalid input: todo_data 'target_id' must be a non-empty string"
            )
        if not insert_todos or not isinstance(insert_todos, list):
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="Invalid input: todo_data 'items' must be a non-empty list of todo objects"
            )
        for todo_item in insert_todos:
            self._validate_single_todo_item(todo_item)

    def _validate_target_task_status(self, target_id: str, current_todos: List[TodoItem],
                                     allowed_statuses: List[str]):
        target_index = -1
        target_todo = None
        for idx, todo in enumerate(current_todos):
            if todo.id == target_id:
                target_index = idx
                target_todo = todo
                break
        if not target_todo:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason=f"Target task with ID '{target_id}' not found in current todo list"
            )
        if target_todo.status not in allowed_statuses:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason=f"Target task status '{target_todo.status}' doesn't allow insertion."
            )
        return target_index

    def _validate_single_in_progress(
        self,
        todos_data: List[TodoItem],
        generation_token: Optional[str] = None,
    ):
        # Superseded-generation leftovers stay on disk with their old
        # in_progress status (P1 filters at the broadcast/view layer instead
        # of cancelling on disk) and must not consume the single in_progress
        # slot of the current generation. Mirror the TodoListTool view filter
        # semantics; when no token is present keep the legacy behavior.
        visible_todos = todos_data
        tx = _TRANSACTION.get()
        if tx is not None and tx.snapshot.versioned:
            generation_token = None
        if generation_token:
            visible_todos = [
                todo for todo in todos_data
                if not todo.generation_token or todo.generation_token == generation_token
            ]
        in_progress_count = sum(1 for todo in visible_todos if todo.status == TodoStatus.IN_PROGRESS)
        if in_progress_count > 1:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="More than one task is marked as 'in_progress' (only one allowed)"
            )

    def _validate_single_todo_item(self, todo_data: Dict):
        validation_errors = []
        # description / activeForm 保持可选（缺省自动兜底为空串），
        # 避免因缺字段导致 append/insert 失败、与后续工具轮合并。
        required_fields = ["content", "status", "id"]
        for field in required_fields:
            if field not in todo_data:
                validation_errors.append(f"Missing required field: '{field}'")
        try:
            TodoStatus(todo_data.get("status", ""))
        except ValueError:
            validation_errors.append(
                f"Invalid status '{todo_data.get('status')}'. Valid values: {[v.value for v in TodoStatus]}")
        if validation_errors:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason=f"Todo data validation error: {'; '.join(validation_errors)}"
            )

    def _convert_to_todo_item(
        self, todo_data: Dict, generation_token: Optional[str] = None
    ) -> TodoItem:
        return TodoItem(
            id=str(todo_data.get("id") or "").strip() or str(_uuid.uuid4()),
            content=todo_data["content"],
            activeForm=todo_data.get("activeForm", ""),
            description=todo_data.get("description", ""),
            status=TodoStatus(todo_data["status"]),
            selected_model_id=todo_data.get("selected_model_id"),
            generation_token=generation_token,
        )

    async def _delete_todos(self, session_id: str, ids: List[str], current_todos: List[TodoItem]) -> str:
        deleted_count = 0
        remaining_todos = []
        delete_ids = set(ids)
        for todo in current_todos:
            if todo.id in delete_ids:
                deleted_count += 1
            else:
                remaining_todos.append(todo)
        if deleted_count == 0:
            return f"No tasks deleted: None of the provided IDs ({', '.join(ids)}) were found"
        await self.save_todos(session_id, remaining_todos)
        return f"Successfully deleted {deleted_count} task(s) (IDs: {', '.join(delete_ids)})"

    async def _cancel_todos(self, session_id: str, ids: List[str], current_todos: List[TodoItem]) -> str:
        cancelled_count = 0
        cancelled_ids = []
        for todo in current_todos:
            if todo.id in ids:
                todo.status = TodoStatus.CANCELLED
                cancelled_count += 1
                cancelled_ids.append(todo.id)
        if cancelled_count == 0:
            return f"No tasks cancelled: None of the provided IDs ({', '.join(ids)}) were found"
        await self.save_todos(session_id, current_todos)
        return f"Successfully cancelled {cancelled_count} task(s) (IDs: {', '.join(cancelled_ids)})"

    async def _update_todos(
        self,
        session_id: str,
        todos_data: List[Dict],
        current_todos: List[TodoItem],
        *,
        generation_token: Optional[str] = None,
    ) -> str:
        todo_map = {todo.id: todo for todo in current_todos}
        updated_count = 0
        errors: list[str] = []
        for todo_data in todos_data:
            todo_id = todo_data.get("id")
            if not todo_id:
                errors.append(f"Missing required field 'id' in: {todo_data}")
                continue
            if todo_id not in todo_map:
                errors.append(f"Task with ID '{todo_id}' not found in current todo list")
                continue
            current_todo = todo_map[todo_id]
            if "content" in todo_data:
                current_todo.content = todo_data["content"]
            if "activeForm" in todo_data:
                current_todo.activeForm = todo_data["activeForm"]
            if "description" in todo_data:
                current_todo.description = todo_data["description"]
            if "status" in todo_data:
                current_todo.status = TodoStatus(todo_data["status"])
            if "selected_model_id" in todo_data:
                current_todo.selected_model_id = todo_data["selected_model_id"]
            # 本轮触碰即归本轮：update 重打 generation_token，延续旧机制
            # "复活/改写的条目对当前轮可见"的语义（token 为空时保留原标记）。
            if generation_token:
                current_todo.generation_token = generation_token
            updated_count += 1
        self._validate_single_in_progress(current_todos, generation_token)
        await self.save_todos(session_id, current_todos)
        if errors:
            result_msg = f"Updated {updated_count} task(s). {len(errors)} item(s) failed: {'; '.join(errors)}"
        else:
            result_msg = f"Successfully updated {updated_count} task(s)"
        return result_msg

    async def _append_todos(
        self,
        session_id: str,
        todos_data: List[Dict],
        current_todos: List[TodoItem],
        *,
        generation_token: Optional[str] = None,
    ) -> str:
        todo_ids = {todo.id for todo in current_todos}
        for todo_data in todos_data:
            self._validate_single_todo_item(todo_data)
            todo_id = todo_data.get("id")
            if todo_id in todo_ids:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Batch append failed: Task with ID '{todo_id}' is duplicated"
                )
            current_todos.append(
                self._convert_to_todo_item(todo_data, generation_token)
            )
            todo_ids.add(todo_id)
        self._validate_single_in_progress(current_todos, generation_token)
        await self.save_todos(session_id, current_todos)
        return f"Successfully appended {len(todos_data)} task(s)"

    async def _insert_after_todos(
        self,
        session_id: str,
        target_id: str,
        insert_todos_data: List[Dict],
        current_todos: List[TodoItem],
        *,
        generation_token: Optional[str] = None,
    ) -> str:
        target_index = self._validate_target_task_status(
            target_id, current_todos, [TodoStatus.IN_PROGRESS, TodoStatus.PENDING]
        )
        existing_ids = {todo.id for todo in current_todos}
        insert_todos = []
        for todo_data in insert_todos_data:
            todo_id = todo_data["id"]
            if todo_id in existing_ids:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Insert failed: Task with ID '{todo_id}' already exists"
                )
            insert_todos.append(
                self._convert_to_todo_item(todo_data, generation_token)
            )
            existing_ids.add(todo_id)
        updated_todos = (
            current_todos[:target_index + 1] + insert_todos + current_todos[target_index + 1:]
        )
        self._validate_single_in_progress(updated_todos, generation_token)
        await self.save_todos(session_id, updated_todos)
        return f"Successfully inserted {len(insert_todos)} task(s) after target task, id: '{target_id}'"

    async def _insert_before_todos(
        self,
        session_id: str,
        target_id: str,
        insert_todos_data: List[Dict],
        current_todos: List[TodoItem],
        *,
        generation_token: Optional[str] = None,
    ) -> str:
        target_index = self._validate_target_task_status(
            target_id, current_todos, [TodoStatus.PENDING]
        )
        existing_ids = {todo.id for todo in current_todos}
        insert_todos = []
        for todo_data in insert_todos_data:
            todo_id = todo_data["id"]
            if todo_id in existing_ids:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Insert failed: Task with ID '{todo_id}' already exists"
                )
            insert_todos.append(
                self._convert_to_todo_item(todo_data, generation_token)
            )
            existing_ids.add(todo_id)
        updated_todos = (
            current_todos[:target_index] + insert_todos + current_todos[target_index:]
        )
        self._validate_single_in_progress(updated_todos, generation_token)
        await self.save_todos(session_id, updated_todos)
        return f"Successfully inserted {len(insert_todos)} task(s) before target task, id: '{target_id}'"


def create_todos_tool(
    operation: SysOperation,
    workspace: Optional[str] = None,
    language: str = "cn",
    agent_id: Optional[str] = None,
) -> List[TodoTool]:
    """Create a set of todo tools that share the same lock manager.


    Args:
        operation: System operation for file system access.
        workspace: Path for file system operations.
        language: Language for tool descriptions.
        agent_id: Optional agent identifier.

    Returns:
        List of TodoTool instances sharing the same lock manager.
    """
    shared_lock_manager = TodoLockManager()
    return [
        TodoCreateTool(operation, workspace, language, agent_id, shared_lock_manager),
        TodoListTool(operation, workspace, language, agent_id, shared_lock_manager),
        TodoGetTool(operation, workspace, language, agent_id, shared_lock_manager),
        TodoModifyTool(operation, workspace, language, agent_id, shared_lock_manager),
    ]
