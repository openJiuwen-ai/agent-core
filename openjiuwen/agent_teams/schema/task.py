# coding: utf-8
"""Task view response schemas for view_task tool."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class TaskOpResult:
    """Outcome of a task mutation with the failure reason preserved.

    Task manager write-path methods return this instead of a bare ``bool``
    so that tool wrappers can surface the real reason back to the LLM
    rather than dropping it into the log sink. ``__bool__`` falls through
    to ``ok`` so legacy ``if result: ...`` call sites keep working.
    """

    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def success(cls) -> "TaskOpResult":
        return cls(ok=True)

    @classmethod
    def fail(cls, reason: str) -> "TaskOpResult":
        return cls(ok=False, reason=reason)


@dataclass(frozen=True, slots=True)
class TaskCreateResult:
    """Outcome of a task-creation mutation.

    Carries the created ``task`` on success and a human-readable failure
    ``reason`` otherwise. ``__getattr__`` transparently delegates missing
    attribute lookups to the wrapped task, so existing call sites that
    treat the return value like a bare ``TeamTaskBase`` (``result.title``,
    ``result.task_id``) keep working without an unwrap. Callers that
    care about failure reasons check ``.ok`` / ``.reason`` directly.

    Typed as ``Any`` instead of ``TeamTaskBase`` to avoid a cross-package
    import cycle (models → schema).
    """

    task: Any = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.task is not None

    def __bool__(self) -> bool:
        return self.task is not None

    def __getattr__(self, name: str) -> Any:
        # Only called when normal lookup misses — i.e. `name` is neither a
        # slot (task / reason), a method, nor a property. Delegate to the
        # wrapped task so `result.title`, `result.task_id`, etc. work.
        task = object.__getattribute__(self, "task")
        if task is None:
            reason = object.__getattribute__(self, "reason")
            raise AttributeError(
                f"TaskCreateResult has no attribute {name!r}; "
                f"task creation failed: {reason}"
            )
        return getattr(task, name)

    @classmethod
    def success(cls, task: Any) -> "TaskCreateResult":
        return cls(task=task)

    @classmethod
    def fail(cls, reason: str) -> "TaskCreateResult":
        return cls(task=None, reason=reason)


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


@dataclass(frozen=True, slots=True)
class NewTaskSpec:
    """A task to be created via ``mutate_dependency_graph``.

    Edges are passed separately via the ``add_edges`` argument so a single
    atomic mutation can both insert nodes and wire them up. ``initial_status``
    is the caller-supplied seed; the post-mutation refresh pass may flip it
    between ``PENDING`` and ``BLOCKED`` based on the resulting edge set.
    """

    task_id: str
    title: str
    content: str
    initial_status: str


@dataclass(frozen=True, slots=True)
class GraphMutationResult:
    """Outcome of ``mutate_dependency_graph``.

    On failure ``reason`` carries a human-readable cause (cycle, missing
    endpoint, terminal-status target). ``refreshed_tasks`` contains the
    tasks whose status was flipped during the post-mutation refresh pass.
    """

    ok: bool
    reason: str = ""
    refreshed_tasks: list[Any] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def success(cls, refreshed_tasks: Optional[list[Any]] = None) -> "GraphMutationResult":
        """Build a successful result, optionally with the refreshed tasks list."""
        return cls(ok=True, refreshed_tasks=refreshed_tasks or [])

    @classmethod
    def fail(cls, reason: str) -> "GraphMutationResult":
        """Build a failure result carrying the human-readable cause."""
        return cls(ok=False, reason=reason)
