# coding: utf-8
"""Tests for user/goal scheduling in EventManager."""
from __future__ import annotations

from openjiuwen.harness.task_loop.event_manager import EventManager
from openjiuwen.harness.schema.interaction import RoundWorkItem


def _goal(*, goal_id: str = "goal-1", revision: int = 0) -> RoundWorkItem:
    return RoundWorkItem.goal(
        inputs={"query": "continue the goal"},
        goal_id=goal_id,
        revision=revision,
        session_id="session-1",
    )


def test_user_work_has_priority_over_goal_work() -> None:
    manager = EventManager()
    goal = _goal()
    user = RoundWorkItem.user(request_id="request-1", inputs={"query": "answer this"})

    assert manager.push_goal(goal)
    manager.push_user(user)

    assert manager.next_work() is user
    manager.mark_started(user)
    manager.mark_finished(user)
    assert manager.next_work() is goal


def test_goal_work_is_deduplicated_across_queued_dequeued_and_active_states() -> None:
    manager = EventManager()
    goal = _goal()

    assert manager.push_goal(goal)
    assert not manager.push_goal(_goal())

    dequeued = manager.next_work()
    assert dequeued is goal
    assert not manager.push_goal(_goal())

    manager.mark_started(goal)
    assert not manager.push_goal(_goal())
    manager.mark_finished(goal)
    assert manager.push_goal(_goal())


def test_discard_goal_work_only_removes_pending_matching_goal() -> None:
    manager = EventManager()
    manager.push_goal(_goal(goal_id="replace-me"))
    manager.push_goal(_goal(goal_id="keep-me"))

    assert manager.discard_goal_work(session_id="session-1", goal_id="replace-me") == 1

    remaining = manager.next_work()
    assert remaining is not None
    assert remaining.context["goal_id"] == "keep-me"


def test_work_items_copy_host_inputs_and_derive_query_from_them() -> None:
    inputs = {"query": "hello", "trusted_dirs": ["/work"]}
    work = RoundWorkItem.user(request_id="r", inputs=inputs, is_follow_up=True, reset_loop=False)
    inputs["query"] = "mutated"

    assert work.query == "hello"
    assert work.is_follow_up
    assert not work.reset_loop
