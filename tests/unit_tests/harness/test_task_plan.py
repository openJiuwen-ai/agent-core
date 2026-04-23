# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for TaskPlan / TodoItem Pydantic models."""
from __future__ import annotations

from openjiuwen.harness.schema.task import (
    TodoItem,
    TodoStatus,
    TaskPlan,
)


def test_todo_item_defaults() -> None:
    """TodoItem has sensible defaults."""
    item = TodoItem(content="do something")
    assert item.status == TodoStatus.PENDING
    assert item.depends_on == []
    assert item.result_summary is None
    assert len(item.id) == 36


def test_todo_item_create() -> None:
    """TodoItem.create factory works."""
    item = TodoItem.create(content="test task")
    assert item.content == "test task"
    assert item.activeForm == "Executing test task"
    assert item.status == TodoStatus.PENDING


def test_todo_item_with_model_id() -> None:
    """TodoItem can have selected_model_id."""
    item = TodoItem.create(content="smart task", selected_model_id="smart")
    assert item.selected_model_id == "smart"


def test_task_plan_add_and_get() -> None:
    """add_task / get_task round-trip."""
    plan = TaskPlan(goal="test goal")
    t = TodoItem(id="a1", content="step 1")
    plan.add_task(t)
    assert plan.get_task("a1") is t
    assert plan.get_task("missing") is None


def test_get_next_task_respects_deps() -> None:
    """get_next_task skips tasks with unmet deps."""
    t1 = TodoItem(id="t1", content="first", status=TodoStatus.PENDING)
    t2 = TodoItem(id="t2", content="second", depends_on=["t1"], status=TodoStatus.PENDING)
    t3 = TodoItem(id="t3", content="third", status=TodoStatus.PENDING)
    plan = TaskPlan(goal="g", tasks=[t1, t2, t3])

    nxt = plan.get_next_task()
    assert nxt is not None
    assert nxt.id == "t1"

    t1.status = TodoStatus.COMPLETED
    nxt = plan.get_next_task()
    assert nxt is not None
    assert nxt.id == "t2"


def test_get_next_task_skips_completed() -> None:
    """get_next_task skips completed and cancelled tasks."""
    t1 = TodoItem(id="t1", content="first", status=TodoStatus.COMPLETED)
    t2 = TodoItem(id="t2", content="second", status=TodoStatus.CANCELLED)
    t3 = TodoItem(id="t3", content="third", status=TodoStatus.PENDING)
    plan = TaskPlan(goal="g", tasks=[t1, t2, t3])

    nxt = plan.get_next_task()
    assert nxt is not None
    assert nxt.id == "t3"


def test_progress_summary() -> None:
    """get_progress_summary returns correct string."""
    plan = TaskPlan(
        tasks=[
            TodoItem(id="a", content="a", status=TodoStatus.COMPLETED),
            TodoItem(id="b", content="b", status=TodoStatus.COMPLETED),
            TodoItem(id="c", content="c", status=TodoStatus.PENDING),
        ]
    )
    assert plan.get_progress_summary() == "2/3 completed"


def test_to_dict_from_dict_roundtrip() -> None:
    """Serialization round-trip preserves data."""
    plan = TaskPlan(
        goal="roundtrip",
        tasks=[
            TodoItem(id="r1", content="first", status=TodoStatus.COMPLETED),
            TodoItem(
                id="r2",
                content="second",
                depends_on=["r1"],
                status=TodoStatus.PENDING,
            ),
        ],
    )

    data = plan.to_dict()
    restored = TaskPlan.from_dict(data)

    assert restored.goal == "roundtrip"
    assert len(restored.tasks) == 2
    assert restored.tasks[0].status == TodoStatus.COMPLETED
    assert restored.tasks[1].depends_on == ["r1"]


def test_from_dict_empty() -> None:
    """from_dict with None/empty returns empty plan."""
    assert TaskPlan.from_dict(None).goal == ""
    assert TaskPlan.from_dict({}).goal == ""


def test_todo_item_to_dict() -> None:
    """TodoItem.to_dict works correctly."""
    item = TodoItem(
        id="test-id",
        content="test",
        activeForm="Testing",
        description="desc",
        status=TodoStatus.IN_PROGRESS,
        selected_model_id="smart",
    )
    d = item.to_dict()
    assert d["id"] == "test-id"
    assert d["content"] == "test"
    assert d["activeForm"] == "Testing"
    assert d["description"] == "desc"
    assert d["status"] == "in_progress"
    assert d["selected_model_id"] == "smart"


def test_todo_item_from_dict() -> None:
    """TodoItem.from_dict works correctly."""
    data = {
        "id": "test-id",
        "content": "test",
        "activeForm": "Testing",
        "description": "desc",
        "status": "in_progress",
        "depends_on": ["other-id"],
        "selected_model_id": "smart",
    }
    item = TodoItem.from_dict(data)
    assert item.id == "test-id"
    assert item.content == "test"
    assert item.status == TodoStatus.IN_PROGRESS
    assert item.selected_model_id == "smart"