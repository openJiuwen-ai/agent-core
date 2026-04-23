# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import json
import os.path
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, List, Optional, Dict

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import tool_logger, LogEventType
from openjiuwen.core.foundation.tool import Tool, ToolCard, Input, Output
from openjiuwen.core.session.agent import Session
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.schema.task import (
    STATUS_ICONS,
    TodoItem,
    TodoStatus,
)


class TodoLockManager:
    """Manages operation locks for todo tools, keyed by session_id.

    Ensures mutual exclusion between set_file and file I/O operations
    within the same session. Different sessions use different locks
    and do not block each other.

    All tools sharing the same lock manager will coordinate their operations
    for the same session, preventing race conditions when multiple tools
    try to access or modify the file path simultaneously.
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

        Use this for both set_file and file I/O operations to ensure
        they are mutually exclusive for the same session.
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
        self._file = os.path.join(self.workspace, "./session_id/todo.json")
        self._lock_manager = lock_manager if lock_manager else TodoLockManager()
        self._current_session_id: Optional[str] = None

    async def load_todos(self) -> List[TodoItem]:
        """Load todo items from session-specific JSON file.

        Returns:
            List of TodoItem instances loaded from file.

        Raises:
            ToolError: If file loading/parsing fails.
        """
        if self._current_session_id is None:
            raise build_error(
                StatusCode.TOOL_TODOS_LOAD_FAILED,
                reason="Session ID not set, call set_file first"
            )
        async with self._lock_manager.operation(self._current_session_id):
            file_path = os.path.abspath(self._file)
            if not os.path.isfile(file_path):
                raise build_error(
                    StatusCode.TOOL_TODOS_LOAD_FAILED,
                    reason=f"Todo file not found: {file_path}"
                )

            read_res = await self.fs.read_file(self._file, mode="text")
            if read_res.code != 0:
                raise build_error(
                    StatusCode.TOOL_TODOS_LOAD_FAILED,
                    reason="Failed to load todo list, because read_file fail"
                )

            data = json.loads(read_res.data.content)
            todos = [TodoItem.from_dict(item) for item in data]
            tool_logger.info(
                "Successfully loaded todo items",
                event_type=LogEventType.TOOL_CALL_END
            )
            return todos

    async def save_todos(self, todos: List[TodoItem]):
        """Save todo items to session-specific JSON file.

        Args:
            todos: List of TodoItem instances to persist.

        Raises:
            ToolError: If file writing fails.
        """
        if self._current_session_id is None:
            raise build_error(
                StatusCode.TOOL_TODOS_SAVE_FAILED,
                reason="Session ID not set, call set_file first"
            )
        async with self._lock_manager.operation(self._current_session_id):
            data = [todo.to_dict() for todo in todos]
            json_content = json.dumps(data, ensure_ascii=False, indent=2)
            write_res = await self.fs.write_file(self._file, json_content, mode="text")
            if write_res.code == 0:
                tool_logger.info(
                    "Successfully saved todo items",
                    event_type=LogEventType.TOOL_CALL_END
                )
            else:
                tool_logger.error(
                    "Failed to save todo list, because write_file fail",
                    event_type=LogEventType.TOOL_CALL_ERROR
                )
                raise build_error(
                    StatusCode.TOOL_TODOS_SAVE_FAILED,
                    reason="Failed to save todo list, because write_file fail"
                )

    async def set_file(self, session_id: str):
        """Set file to session-specific JSON file with lock protection.

        This method acquires the operation lock for the session before
        modifying the file path, ensuring it doesn't conflict with
        ongoing file operations from other tools.

        Args:
            session_id: Unique identifier of the session, used as the JSON file name.
        """
        if session_id:
            async with self._lock_manager.operation(session_id):
                self._current_session_id = session_id
                self._file = os.path.join(self.workspace, f"./{session_id}/todo.json")

    def cleanup_session(self, session_id: str) -> None:
        """Clean up resources for a session (call when session ends).

        Args:
            session_id: The session ID to clean up.
        """
        self._lock_manager.cleanup_session(session_id)


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

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        """Asynchronous invocation handler for TodoCreate tool operations

        Args:
            inputs: Input parameters dictionary containing task creation data
            **kwargs:：Additional operation parameters (supports session_id override)

        Returns:
            Execution result with human-readable creation message

        Raises:
            ToolError: If invalid parameters or operation fails
        """
        session = kwargs.get("session", None)
        if session and isinstance(session, Session) and session.get_session_id() not in self._file:
            await self.set_file(session.get_session_id())

        results = dict()
        try:
            tasks_input = inputs.get("tasks")
            if tasks_input and isinstance(tasks_input, list):
                results["message"] = await self._create_from_list(tasks_input)
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

    async def _create_from_list(self, tasks_data: List[Dict[str, Any]]) -> str:
        """Create todo items from a JSON array of task objects."""
        if not tasks_data:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="Task list cannot be empty"
            )
        new_todos = []
        for i, task_data in enumerate(tasks_data):
            content = task_data.get("content", "")
            if not content:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Task at index {i} is missing a 'content' field"
                )
            active_form = task_data.get("activeForm", "")
            if not active_form:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Task at index {i} is missing a 'activeForm' field"
                )
            description = task_data.get("description", "")
            if not description:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Task at index {i} is missing a 'description' field"
                )
            status = TodoStatus.IN_PROGRESS if i == 0 else TodoStatus.PENDING
            new_todos.append(TodoItem.create(
                content=content,
                active_form=active_form,
                description=description,
                status=status,
                selected_model_id=task_data.get("selected_model_id"),
            ))

        await self.save_todos(new_todos)
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
        if session and isinstance(session, Session) and session.get_session_id() not in self._file:
            await self.set_file(session.get_session_id())

        try:
            tasks = await self.load_todos()
            active_tasks = [
                t for t in tasks
                if t.status not in (TodoStatus.CANCELLED, TodoStatus.COMPLETED)
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

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        session = kwargs.get("session", None)
        if session and isinstance(session, Session) and session.get_session_id() not in self._file:
            await self.set_file(session.get_session_id())

        task_id = inputs.get("id")
        if not task_id:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="Task ID is required"
            )

        try:
            tasks = await self.load_todos()
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

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        session = kwargs.get("session", None)
        if session and isinstance(session, Session) and session.get_session_id() not in self._file:
            await self.set_file(session.get_session_id())

        results = dict()
        try:
            action = inputs.get("action")
            if not action:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason="Invalid input: 'action' field is required"
                )

            current_todos = await self.load_todos()

            if action == "delete":
                ids = inputs.get("ids")
                if not ids or not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                    raise build_error(
                        StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                        reason="Invalid input for delete action: 'ids' must be a non-empty list of task IDs"
                    )
                results["message"] = await self._delete_todos(ids, current_todos)
            elif action == "cancel":
                ids = inputs.get("ids")
                if not ids or not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                    raise build_error(
                        StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                        reason="Invalid input for cancel action: 'ids' must be a non-empty list of task IDs"
                    )
                results["message"] = await self._cancel_todos(ids, current_todos)
            elif action == "update":
                todos_data = inputs.get("todos")
                results["message"] = await self._update_todos(todos_data, current_todos)
            elif action == "append":
                todos_data = inputs.get("todos")
                results["message"] = await self._append_todos(todos_data, current_todos)
            elif action == "insert_after":
                todo_data = inputs.get("todo_data")
                self._validate_todo_data_structure(todo_data)
                target_id = todo_data["target_id"]
                insert_todos = todo_data["items"]
                results["message"] = await self._insert_after_todos(target_id, insert_todos, current_todos)
            elif action == "insert_before":
                todo_data = inputs.get("todo_data")
                self._validate_todo_data_structure(todo_data)
                target_id = todo_data["target_id"]
                insert_todos = todo_data["items"]
                results["message"] = await self._insert_before_todos(target_id, insert_todos, current_todos)
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

    def _validate_single_in_progress(self, todos_data: List[TodoItem]):
        in_progress_count = sum(1 for todo in todos_data if todo.status == TodoStatus.IN_PROGRESS)
        if in_progress_count > 1:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="More than one task is marked as 'in_progress' (only one allowed)"
            )

    def _validate_single_todo_item(self, todo_data: Dict):
        validation_errors = []
        required_fields = ["content", "activeForm", "description", "status", "id"]
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

    def _convert_to_todo_item(self, todo_data: Dict) -> TodoItem:
        item = TodoItem.create(
            content=todo_data["content"],
            active_form=todo_data.get("activeForm", ""),
            description=todo_data.get("description", ""),
            status=TodoStatus(todo_data["status"]),
            selected_model_id=todo_data.get("selected_model_id"),
        )
        if "id" in todo_data:
            item.id = todo_data["id"]
        return item

    async def _delete_todos(self, ids: List[str], current_todos: List[TodoItem]) -> str:
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
        await self.save_todos(remaining_todos)
        return f"Successfully deleted {deleted_count} task(s) (IDs: {', '.join(delete_ids)})"

    async def _cancel_todos(self, ids: List[str], current_todos: List[TodoItem]) -> str:
        cancelled_count = 0
        cancelled_ids = []
        for todo in current_todos:
            if todo.id in ids:
                todo.status = TodoStatus.CANCELLED
                cancelled_count += 1
                cancelled_ids.append(todo.id)
        if cancelled_count == 0:
            return f"No tasks cancelled: None of the provided IDs ({', '.join(ids)}) were found"
        await self.save_todos(current_todos)
        return f"Successfully cancelled {cancelled_count} task(s) (IDs: {', '.join(cancelled_ids)})"

    async def _update_todos(self, todos_data: List[Dict], current_todos: List[TodoItem]) -> str:
        todo_map = {todo.id: todo for todo in current_todos}
        updated_count = 0
        for todo_data in todos_data:
            todo_id = todo_data.get("id")
            if not todo_id:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason="Batch update failed: Missing required field: 'id'"
                )
            if todo_id not in todo_map:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Batch update failed: Task with ID '{todo_id}' not found"
                )
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
            updated_count += 1
        self._validate_single_in_progress(current_todos)
        await self.save_todos(current_todos)
        return f"Successfully updated {updated_count} task(s)"

    async def _append_todos(self, todos_data: List[Dict], current_todos: List[TodoItem]) -> str:
        todo_ids = {todo.id for todo in current_todos}
        for todo_data in todos_data:
            self._validate_single_todo_item(todo_data)
            todo_id = todo_data.get("id")
            if todo_id in todo_ids:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Batch append failed: Task with ID '{todo_id}' is duplicated"
                )
            current_todos.append(self._convert_to_todo_item(todo_data))
            todo_ids.add(todo_id)
        self._validate_single_in_progress(current_todos)
        await self.save_todos(current_todos)
        return f"Successfully appended {len(todos_data)} task(s)"

    async def _insert_after_todos(self, target_id: str, insert_todos_data: List[Dict],
                                  current_todos: List[TodoItem]) -> str:
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
            insert_todos.append(self._convert_to_todo_item(todo_data))
            existing_ids.add(todo_id)
        updated_todos = (
            current_todos[:target_index + 1] + insert_todos + current_todos[target_index + 1:]
        )
        self._validate_single_in_progress(updated_todos)
        await self.save_todos(updated_todos)
        return f"Successfully inserted {len(insert_todos)} task(s) after target task, id: '{target_id}'"

    async def _insert_before_todos(self, target_id: str, insert_todos_data: List[Dict],
                                   current_todos: List[TodoItem]) -> str:
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
            insert_todos.append(self._convert_to_todo_item(todo_data))
            existing_ids.add(todo_id)
        updated_todos = (
            current_todos[:target_index] + insert_todos + current_todos[target_index:]
        )
        self._validate_single_in_progress(updated_todos)
        await self.save_todos(updated_todos)
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
