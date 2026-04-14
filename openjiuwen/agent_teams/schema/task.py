# coding: utf-8
"""Task view response schemas for view_task tool."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TaskSummary(BaseModel):
    """Lightweight task summary returned by list/claimable actions."""

    task_id: str
    title: str
    status: str
    assignee: Optional[str] = None
    blocked_by: list[str] = Field(default_factory=list)


class TaskDetail(BaseModel):
    """Full task detail returned by get action.

    ``updated_at`` is the millisecond wall-clock timestamp of the last
    status transition. Its semantic meaning depends on ``status`` —
    when status=claimed it is the claim time, when status=completed it
    is the completion time, etc.
    """

    task_id: str
    title: str
    content: str
    status: str
    assignee: Optional[str] = None
    blocked_by: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    updated_at: Optional[int] = None


class TaskListResult(BaseModel):
    """Response for list/claimable actions."""

    tasks: list[TaskSummary]
    count: int
