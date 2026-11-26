# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session todo storage and whole-list replacement tool."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, AsyncIterator
from uuid import NAMESPACE_URL, uuid5

from filelock import FileLock

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool import Input, Output, Tool
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.schema.task import TodoItem, TodoStatus

_WRITABLE_FIELDS = frozenset({"id", "content", "status", "description", "selected_model_id"})
_ACTIVE_STATUSES = frozenset({TodoStatus.PENDING, TodoStatus.IN_PROGRESS})


class TodoWriteTool(Tool):
    """Replace a session's full ordered todo list using its latest revision."""

    def __init__(self, workspace: str | Path, language: str = "cn", agent_id: str | None = None) -> None:
        super().__init__(build_tool_card("todo_write", "TodoWriteTool", language, agent_id=agent_id))
        self.workspace = Path(workspace).resolve()
        self.language = language

    def _path(self, session_id: str) -> Path:
        path = (self.workspace / session_id / "todo.json").resolve()
        if not session_id or not path.is_relative_to(self.workspace):
            raise ValueError("Invalid todo session ID")
        return path

    @staticmethod
    def _read(path: Path) -> tuple[list[TodoItem], str | None]:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return [], None
        records = json.loads(data)
        if not isinstance(records, list):
            raise ValueError("Invalid persisted todo list")
        todos = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError("Invalid persisted todo item")
            if not record.get("id"):
                record = {**record, "id": str(uuid5(NAMESPACE_URL, f"{path.parent.name}/todo/{index}"))}
            todos.append(TodoItem.from_dict(record))
        return todos, hashlib.sha256(data).hexdigest()

    @staticmethod
    def _save(path: Path, todos: list[TodoItem]) -> str:
        data = json.dumps([todo.to_dict() for todo in todos], ensure_ascii=False, indent=2).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            try:
                file.write(data)
                file.close()
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _snapshot(todos: list[TodoItem], revision: str | None) -> dict[str, Any]:
        return {
            "revision": revision,
            "todos": [{"id": todo.id, "content": todo.content, "status": todo.status.value} for todo in todos],
        }

    async def load_todos(self, session_id: str) -> list[TodoItem]:
        """Read persisted items, including metadata used by the task loop."""
        todos, _ = await asyncio.to_thread(self._read, self._path(session_id))
        return todos

    async def load_snapshot(self, session_id: str) -> dict[str, Any] | None:
        """Read the complete model-visible list and the version of that exact list."""
        todos, revision = await asyncio.to_thread(self._read, self._path(session_id))
        return self._snapshot(todos, revision) if revision is not None else None

    @staticmethod
    def _build_todos(items: list[dict[str, Any]], current: list[TodoItem]) -> list[TodoItem]:
        existing = {todo.id: todo for todo in current}
        todos = []
        seen = set()
        in_progress = 0
        for item in items:
            if not isinstance(item, dict) or item.keys() - _WRITABLE_FIELDS:
                raise ValueError("Invalid todo fields")
            todo_id = item.get("id")
            content = item.get("content")
            if not isinstance(todo_id, str) or not todo_id.strip() or todo_id in seen:
                raise ValueError("Each todo needs a unique nonempty id")
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"Todo {todo_id} needs nonempty content")
            status = TodoStatus(item.get("status"))
            in_progress += status == TodoStatus.IN_PROGRESS
            if in_progress > 1:
                raise ValueError("At most one todo can be in_progress")
            previous = existing.get(todo_id)
            data = previous.to_dict() if previous else {"activeForm": content}
            todos.append(TodoItem.model_validate({**data, **item}))
            seen.add(todo_id)
        for todo in todos:
            if set(todo.depends_on) - seen:
                raise ValueError(f"Todo {todo.id} references a missing dependency")
        return todos

    def _replace(self, path: Path, items: list[dict[str, Any]], revision: str | None) -> dict[str, Any]:
        with FileLock(path.with_suffix(".lock"), timeout=30):
            current, latest = self._read(path)
            if revision != latest:
                message = (
                    "待办版本已变化。请在此最新列表上合并修改后重试。"
                    if self.language != "en"
                    else "The todo list changed. Merge your changes into this latest list and retry."
                )
                return {"conflict": True, "message": message, **self._snapshot(current, latest)}
            todos = self._build_todos(items, current)
            return self._snapshot(todos, self._save(path, todos))

    def _update_statuses(
        self, path: Path, statuses: dict[str, TodoStatus] | None,
    ) -> list[TodoItem]:
        with FileLock(path.with_suffix(".lock"), timeout=30):
            todos, _ = self._read(path)
            changed = False
            for todo in todos:
                if statuses is None:
                    target = TodoStatus.CANCELLED if todo.status in _ACTIVE_STATUSES else todo.status
                else:
                    target = statuses.get(todo.id, todo.status)
                if target != todo.status:
                    todo.status = target
                    changed = True
            if changed:
                self._save(path, todos)
            return todos

    async def sync_statuses(self, session_id: str, statuses: dict[str, TodoStatus]) -> list[TodoItem]:
        """Apply task-loop statuses to the latest stored list by ID."""
        return await asyncio.to_thread(self._update_statuses, self._path(session_id), statuses)

    async def cancel_pending(self, session_id: str) -> list[TodoItem]:
        """Cancel active todos without changing completed or already cancelled items."""
        return await asyncio.to_thread(self._update_statuses, self._path(session_id), None)

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        session = kwargs.get("session")
        if session is None or not session.get_session_id():
            raise build_error(StatusCode.TOOL_TODOS_INVOKE_FAILED, reason="Session ID is required")

        items = inputs.get("todos")
        revision = inputs.get("revision")
        if inputs.keys() - {"todos", "revision"} or not isinstance(items, list):
            raise build_error(StatusCode.TOOL_TODOS_VALIDATION_INVALID, reason="Provide the complete todos list")
        if revision is not None and not isinstance(revision, str):
            raise build_error(StatusCode.TOOL_TODOS_VALIDATION_INVALID, reason="Provide a valid revision")
        try:
            return await asyncio.to_thread(self._replace, self._path(session.get_session_id()), items, revision)
        except ValueError as exc:
            raise build_error(StatusCode.TOOL_TODOS_VALIDATION_INVALID, reason=str(exc)) from exc
        except OSError as exc:
            raise build_error(StatusCode.TOOL_TODOS_SAVE_FAILED, reason=str(exc)) from exc

    def render_for_llm(self, output: dict[str, Any]) -> str:
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"))

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)
