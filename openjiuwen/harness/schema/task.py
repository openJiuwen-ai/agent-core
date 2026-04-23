# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Task schema definitions for DeepAgent task loop.

Provides Pydantic-based TaskPlan / TaskItem models that
replace the earlier ``TaskPlan(dict)`` placeholder.
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TodoStatus(str, Enum):
    """Lifecycle status of a single todo item."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


STATUS_ICONS = {
    TodoStatus.PENDING: "[ ]",
    TodoStatus.IN_PROGRESS: "[→]",
    TodoStatus.COMPLETED: "[√]",
    TodoStatus.CANCELLED: "[×]",
}


class TodoItem(BaseModel):
    """Unified task data structure for DeepAgent task planning.

    Attributes:
        id: Unique task identifier (UUID).
        content: Task summary description.
        activeForm: Present-tense form of content (e.g., "Translating document" for content "Translate document").
        description: Detailed task content.
        status: Current lifecycle status.
        depends_on: IDs of prerequisite tasks (reserved for future use).
        result_summary: Brief summary after completion (reserved).
        meta_data: Arbitrary metadata (reserved).
        selected_model_id: Optional model ID to use when executing this task.
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique task ID",
    )
    content: str = Field(default="", description="task summary description")
    activeForm: str = Field(default="", description="present-tense form of content")
    description: str = Field(default="", description="detailed task content")
    status: TodoStatus = Field(default=TodoStatus.PENDING)
    depends_on: List[str] = Field(default_factory=list)
    result_summary: Optional[str] = None
    meta_data: Optional[Dict[str, Any]] = None
    selected_model_id: Optional[str] = Field(
        default=None,
        description="Optional model ID to use when executing this task.",
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "id": self.id,
            "content": self.content,
            "activeForm": self.activeForm,
            "description": self.description,
            "status": self.status.value,
            "depends_on": self.depends_on,
            "result_summary": self.result_summary,
            "meta_data": self.meta_data,
            "selected_model_id": self.selected_model_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TodoItem":
        """Build from persisted dict."""
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            content=data.get("content", ""),
            activeForm=data.get("activeForm", ""),
            description=data.get("description", ""),
            status=TodoStatus(data.get("status", "pending")),
            depends_on=data.get("depends_on", []),
            result_summary=data.get("result_summary"),
            meta_data=data.get("meta_data"),
            selected_model_id=data.get("selected_model_id"),
        )

    @classmethod
    def create(
        cls,
        content: str,
        active_form: str = "",
        description: str = "",
        status: TodoStatus = TodoStatus.PENDING,
        selected_model_id: Optional[str] = None,
    ) -> "TodoItem":
        """Create a new TodoItem."""
        return cls(
            content=content,
            activeForm=active_form or f"Executing {content}",
            description=description,
            status=status,
            selected_model_id=selected_model_id,
        )


class TaskPlan(BaseModel):
    """Structured task plan for the outer task loop.

    Attributes:
        goal: High-level objective.
        tasks: Ordered list of todo items.
        current_task_id: ID of the task currently being executed.
    """

    goal: str = ""
    tasks: List[TodoItem] = Field(default_factory=list)
    current_task_id: Optional[str] = None

    def get_task(self, task_id: str) -> Optional[TodoItem]:
        """Return task by ID or None."""
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def get_next_task(self) -> Optional[TodoItem]:
        """Return the first PENDING task whose deps are met."""
        done_ids: set[str] = set()
        for t in self.tasks:
            if t.status in (TodoStatus.COMPLETED, TodoStatus.CANCELLED):
                done_ids.add(t.id)
        for t in self.tasks:
            if t.status != TodoStatus.PENDING:
                continue
            if all(d in done_ids for d in t.depends_on):
                return t
        return None

    def add_task(self, task: TodoItem) -> None:
        """Append a task item."""
        self.tasks.append(task)

    # -- mutation helpers --

    def mark_in_progress(self, task_id: str) -> None:
        """Set task status to IN_PROGRESS."""
        task = self.get_task(task_id)
        if task is not None:
            task.status = TodoStatus.IN_PROGRESS
            self.current_task_id = task_id

    def mark_completed(
        self,
        task_id: str,
        summary: str = "",
    ) -> None:
        """Set task status to COMPLETED."""
        task = self.get_task(task_id)
        if task is not None:
            task.status = TodoStatus.COMPLETED
            task.result_summary = summary
            if self.current_task_id == task_id:
                self.current_task_id = None

    def mark_cancelled(
        self,
        task_id: str,
        reason: str = "",
    ) -> None:
        """Set task status to CANCELLED."""
        task = self.get_task(task_id)
        if task is not None:
            task.status = TodoStatus.CANCELLED
            task.result_summary = reason
            if self.current_task_id == task_id:
                self.current_task_id = None

    # -- presentation --

    def get_progress_summary(self) -> str:
        """Return e.g. '3/7 completed'."""
        total = len(self.tasks)
        done = sum(1 for t in self.tasks if t.status == TodoStatus.COMPLETED)
        return f"{done}/{total} completed"

    def to_markdown(self) -> str:
        """Render plan as a markdown checklist."""
        lines = [f"## Goal: {self.goal}", ""]
        for t in self.tasks:
            if t.status == TodoStatus.COMPLETED:
                mark = "√"
            elif t.status == TodoStatus.IN_PROGRESS:
                mark = ">"
            elif t.status == TodoStatus.CANCELLED:
                mark = "×"
            else:
                mark = " "
            suffix = ""
            if t.result_summary:
                suffix = f" — {t.result_summary}"
            lines.append(
                f"- [{mark}] {t.content}{suffix}"
            )
        return "\n".join(lines)

    # -- serialization --

    def to_dict(self) -> Dict[str, Any]:
        """JSON-friendly dict for session persistence."""
        return self.model_dump(mode="python")

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "TaskPlan":
        """Build from persisted dict."""
        if not data:
            return cls()
        return cls.model_validate(data)


class ModelUsageRecord(BaseModel):
    """Accumulated token usage for one model_id within a single invoke.

    Attributes:
        model_id: The model selection ID (user-defined key).
        input_tokens: Total input tokens consumed.
        output_tokens: Total output tokens consumed.
    """

    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        """Accumulate token counts."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def __str__(self) -> str:
        return (
            f"model_id={self.model_id} "
            f"input={self.input_tokens} output={self.output_tokens}"
        )


__all__ = [
    "ModelUsageRecord",
    "STATUS_ICONS",
    "TaskPlan",
    "TodoItem",
    "TodoStatus",
]
