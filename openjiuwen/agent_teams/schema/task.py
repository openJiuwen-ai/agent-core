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
    """Full task detail returned by get action."""

    task_id: str
    title: str
    content: str
    status: str
    assignee: Optional[str] = None
    blocked_by: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    completed_at: Optional[int] = None


class TaskListResult(BaseModel):
    """Response for list/claimable actions."""

    tasks: list[TaskSummary]
    count: int
