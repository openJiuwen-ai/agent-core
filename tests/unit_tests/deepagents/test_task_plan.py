# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for TaskPlan / TaskItem Pydantic models."""
from __future__ import annotations

from openjiuwen.deepagents.schema.task import (
    TaskItem,
    TaskPlan,
    TaskStatus,
)


def test_task_item_defaults() -> None:
    """TaskItem has sensible defaults."""
    item = TaskItem(title="do something")
    assert item.status == TaskStatus.PENDING
    assert item.depends_on == []
    assert item.result_summary is None
    assert len(item.id) == 8


def test_task_plan_add_and_get() -> None:
    """add_task / get_task round-trip."""
    plan = TaskPlan(goal="test goal")
    t = TaskItem(id="a1", title="step 1")
    plan.add_task(t)
    assert plan.get_task("a1") is t
    assert plan.get_task("missing") is None


def test_get_next_task_respects_deps() -> None:
    """get_next_task skips tasks with unmet deps."""
    t1 = TaskItem(id="t1", title="first")
    t2 = TaskItem(
        id="t2", title="second", depends_on=["t1"]
    )
    t3 = TaskItem(id="t3", title="third")
    plan = TaskPlan(goal="g", tasks=[t1, t2, t3])

    nxt = plan.get_next_task()
    assert nxt is not None
    assert nxt.id == "t1"

    plan.mark_completed("t1", "done")
    nxt = plan.get_next_task()
    assert nxt is not None
    assert nxt.id == "t2"


def test_mark_in_progress() -> None:
    """mark_in_progress sets status and current_task_id."""
    plan = TaskPlan(
        tasks=[TaskItem(id="x", title="task")]
    )
    plan.mark_in_progress("x")
    assert plan.get_task("x").status == TaskStatus.IN_PROGRESS
    assert plan.current_task_id == "x"


def test_mark_completed_clears_current() -> None:
    """mark_completed resets current_task_id."""
    plan = TaskPlan(
        tasks=[TaskItem(id="x", title="task")]
    )
    plan.mark_in_progress("x")
    plan.mark_completed("x", "all good")
    task = plan.get_task("x")
    assert task.status == TaskStatus.COMPLETED
    assert task.result_summary == "all good"
    assert plan.current_task_id is None


def test_mark_failed() -> None:
    """mark_failed sets FAILED status."""
    plan = TaskPlan(
        tasks=[TaskItem(id="x", title="task")]
    )
    plan.mark_in_progress("x")
    plan.mark_failed("x", "oops")
    task = plan.get_task("x")
    assert task.status == TaskStatus.FAILED
    assert task.result_summary == "oops"


def test_progress_summary() -> None:
    """get_progress_summary returns correct string."""
    plan = TaskPlan(
        tasks=[
            TaskItem(id="a", title="a"),
            TaskItem(id="b", title="b"),
            TaskItem(id="c", title="c"),
        ]
    )
    plan.mark_completed("a")
    plan.mark_completed("b")
    assert plan.get_progress_summary() == "2/3 completed"


def test_to_markdown() -> None:
    """to_markdown renders checklist."""
    plan = TaskPlan(
        goal="build it",
        tasks=[
            TaskItem(id="a", title="step 1"),
            TaskItem(id="b", title="step 2"),
        ],
    )
    plan.mark_completed("a", "done")
    md = plan.to_markdown()
    assert "[x] step 1" in md
    assert "[ ] step 2" in md
    assert "## Goal: build it" in md


def test_to_dict_from_dict_roundtrip() -> None:
    """Serialization round-trip preserves data."""
    plan = TaskPlan(
        goal="roundtrip",
        tasks=[
            TaskItem(id="r1", title="first"),
            TaskItem(
                id="r2",
                title="second",
                depends_on=["r1"],
            ),
        ],
    )
    plan.mark_completed("r1", "ok")

    data = plan.to_dict()
    restored = TaskPlan.from_dict(data)

    assert restored.goal == "roundtrip"
    assert len(restored.tasks) == 2
    assert restored.tasks[0].status == TaskStatus.COMPLETED
    assert restored.tasks[1].depends_on == ["r1"]


def test_from_dict_empty() -> None:
    """from_dict with None/empty returns empty plan."""
    assert TaskPlan.from_dict(None).goal == ""
    assert TaskPlan.from_dict({}).goal == ""
