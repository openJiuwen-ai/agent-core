# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task schemas: view responses plus task-graph mutation specs and results."""
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
    ``data`` optionally carries operation facts the tool layer renders
    (e.g. the vote tally after a recorded review vote, F_62).
    """

    ok: bool
    reason: str = ""
    data: dict[str, Any] | None = None

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def success(cls, data: dict[str, Any] | None = None) -> "TaskOpResult":
        return cls(ok=True, data=data)

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
    """Lightweight task summary returned by list/claimable actions.

    ``updated_at`` is the millisecond wall-clock timestamp of the last
    status transition (same semantic as ``TaskDetail.updated_at``). It is a
    lightweight routing/identity field (one int, like status/assignee), not
    a heavy field like content — it lets the list view render a relative
    time so an idle member can tell which task has been stalling.
    """

    task_id: str
    title: str
    status: str
    assignee: Optional[str] = None
    blocked_by: list[str] = Field(default_factory=list)
    updated_at: Optional[int] = None


class TaskDetail(BaseModel):
    """Full task detail returned by get action.

    ``updated_at`` is the millisecond wall-clock timestamp of the last
    status transition. Its semantic meaning depends on ``status`` —
    when status=in_progress it is the start time, when status=completed it
    is the completion time, etc.
    """

    task_id: str
    title: str
    content: str
    status: str
    assignee: Optional[str] = None
    reviewer: list[str] = Field(default_factory=list)
    review_round: int = 0
    max_review_rounds: Optional[int] = None
    blocked_by: list[str] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    updated_at: Optional[int] = None


class TaskListResult(BaseModel):
    """Response for list/claimable actions."""

    tasks: list[TaskSummary]
    count: int


@dataclass(frozen=True, slots=True)
class TaskGraphSpec:
    """One task in a ``TeamTaskManager.add_graph`` batch.

    ``depends_on`` edges may reference tasks created in the same batch
    (forward references) or already-existing tasks. ``depended_by`` edges
    must reference already-existing tasks only — an in-batch edge has
    exactly one representation (``depends_on`` on the dependent task), so
    the tool boundary rejects in-batch ``depended_by`` targets as
    redundant before the batch reaches this layer.

    ``assignee`` pre-assigns the task as part of the same atomic mutation
    (scheduled dispatch): an unblocked pre-assigned task lands as
    ``PENDING`` with the assignee on record (assigned, not yet started), a
    blocked one keeps the assignee until its dependencies resolve. Left
    ``None``, the task is claimable by any member.

    ``reviewer`` names the verify-gate reviewers (member names, leader-assigned,
    may be several). A task carrying reviewers routes through ``IN_REVIEW`` on
    completion; left empty it completes directly. Orthogonal to ``assignee``
    (the author) — a reviewer must not be the author.
    """

    title: str
    content: str
    task_id: str | None = None
    depends_on: tuple[str, ...] = ()
    depended_by: tuple[str, ...] = ()
    assignee: str | None = None
    reviewer: tuple[str, ...] = ()
    # Per-task review-round ceiling (F_62, scheduled dispatch); None uses the
    # team default. Meaningful only when ``reviewer`` is non-empty.
    max_review_rounds: int | None = None


@dataclass(frozen=True, slots=True)
class TaskGraphResult:
    """Outcome of ``TeamTaskManager.add_graph``.

    The batch is atomic: on success ``tasks`` carries every created task
    (statuses reflect the post-mutation refresh pass); on failure nothing
    was created and ``reason`` carries the real cause from the graph
    mutation (cycle, missing endpoint, duplicate id, ...).
    """

    ok: bool
    reason: str = ""
    tasks: list[Any] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok

    @classmethod
    def success(cls, tasks: list[Any]) -> "TaskGraphResult":
        """Build a successful result carrying the created tasks."""
        return cls(ok=True, tasks=tasks)

    @classmethod
    def fail(cls, reason: str) -> "TaskGraphResult":
        """Build a failure result carrying the human-readable cause."""
        return cls(ok=False, reason=reason)


@dataclass(frozen=True, slots=True)
class NewTaskSpec:
    """A task to be created via ``mutate_dependency_graph``.

    Edges are passed separately via the ``add_edges`` argument so a single
    atomic mutation can both insert nodes and wire them up. ``initial_status``
    is the caller-supplied seed; the post-mutation refresh pass may flip it
    between ``PENDING`` and ``BLOCKED`` based on the resulting edge set — it
    only ever touches ``PENDING`` / ``BLOCKED`` rows, so a pre-assigned task
    seeded ``PENDING`` keeps its owner (and its BLOCKED flip) through the
    mutation.
    """

    task_id: str
    title: str
    content: str
    initial_status: str
    assignee: str | None = None
    # JSON-encoded reviewer member-name list (or None); written verbatim to the
    # ``reviewer`` column by ``_stage_new_tasks``.
    reviewer: str | None = None
    # Per-task review-round ceiling (F_62); None falls back to the team-level
    # ``TeamAgentSpec.default_max_review_rounds`` at judgement time.
    max_review_rounds: int | None = None


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
