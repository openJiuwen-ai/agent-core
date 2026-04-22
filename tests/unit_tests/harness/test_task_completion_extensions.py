# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for task completion loop extension points."""
# pylint: disable=protected-access
from __future__ import annotations

import pytest

from openjiuwen.harness.rails.task_completion_rail import (
    TaskCompletionRail,
    extract_promise_block,
    promise_matches,
)
from openjiuwen.harness.schema.stop_condition import (
    CompletionPromiseEvaluator,
    StopEvaluationContext,
)
from openjiuwen.harness.task_loop.loop_queues import LoopQueues
from openjiuwen.harness.task_loop.task_loop_controller import (
    TaskLoopController,
)


class _Coordinator:
    def __init__(self) -> None:
        self.evaluator = CompletionPromiseEvaluator(
            "all_tasks_completed"
        )

    def get_completion_promise_evaluator(
        self,
    ) -> CompletionPromiseEvaluator:
        return self.evaluator


def _ctx_with_output(output: str):
    coordinator = _Coordinator()
    agent = type(
        "Agent",
        (),
        {"loop_coordinator": coordinator},
    )()
    inputs = type(
        "Inputs",
        (),
        {"result": {"output": output}},
    )()
    ctx = type("Ctx", (), {"agent": agent, "inputs": inputs})()
    return ctx, coordinator.evaluator


def test_promise_block_can_include_evidence_lines() -> None:
    """A promise block may start with the token and include details."""
    text = """done
<promise>all_tasks_completed
Completed tasks:
- created output
</promise>
"""
    block = extract_promise_block(text)

    assert block is not None
    assert promise_matches(block, "all_tasks_completed")
    assert not promise_matches(block, "different_token")


@pytest.mark.asyncio
async def test_task_completion_rail_rejects_details_by_default() -> None:
    """Default behavior remains exact promise token matching."""
    rail = TaskCompletionRail(
        completion_promise="all_tasks_completed",
    )
    ctx, evaluator = _ctx_with_output(
        """<promise>all_tasks_completed
Completed tasks:
- created output
</promise>"""
    )

    await rail.after_task_iteration(ctx)

    assert not evaluator.should_stop(StopEvaluationContext())


@pytest.mark.asyncio
async def test_task_completion_rail_accepts_details_when_enabled() -> None:
    """Detailed promise blocks require an explicit opt-in."""
    rail = TaskCompletionRail(
        completion_promise="all_tasks_completed",
        allow_promise_details=True,
    )
    ctx, evaluator = _ctx_with_output(
        """<promise>all_tasks_completed
Completed tasks:
- created output
</promise>"""
    )

    await rail.after_task_iteration(ctx)

    assert evaluator.should_stop(StopEvaluationContext())


def test_task_completion_rail_builds_multi_confirmation_evaluator() -> None:
    """TaskCompletionRail forwards required confirmation count."""
    rail = TaskCompletionRail(
        completion_promise="all_tasks_completed",
        required_confirmations=2,
    )
    evaluator = rail.build_evaluators()[0]

    assert isinstance(evaluator, CompletionPromiseEvaluator)
    evaluator.notify_fulfilled("all_tasks_completed")
    assert not evaluator.should_stop(StopEvaluationContext())
    evaluator.notify_fulfilled("all_tasks_completed")
    assert evaluator.should_stop(StopEvaluationContext())


def test_completion_promise_evaluator_requires_consecutive_confirmations() -> None:
    """notify_absent resets the consecutive confirmation streak."""
    evaluator = CompletionPromiseEvaluator(
        "all_tasks_completed",
        required_confirmations=2,
    )

    evaluator.notify_fulfilled("all_tasks_completed")
    evaluator.notify_absent()
    evaluator.notify_fulfilled("all_tasks_completed")
    assert not evaluator.should_stop(StopEvaluationContext())

    evaluator.notify_fulfilled("all_tasks_completed")
    assert evaluator.should_stop(StopEvaluationContext())


def test_completion_promise_evaluator_absent_clears_state() -> None:
    """notify_absent clears fulfilled state and matched text."""
    evaluator = CompletionPromiseEvaluator(
        "all_tasks_completed",
        required_confirmations=2,
    )

    evaluator.notify_fulfilled("all_tasks_completed")
    evaluator.notify_fulfilled("all_tasks_completed")
    assert evaluator.should_stop(StopEvaluationContext())

    evaluator.notify_absent()
    assert not evaluator.should_stop(StopEvaluationContext())
    state = evaluator.get_state()
    assert state is not None
    assert state["confirmation_count"] == 0
    assert state["matched_text"] == ""


def test_task_loop_controller_can_enqueue_follow_up() -> None:
    """Rails can enqueue follow-up messages through the controller."""
    controller = TaskLoopController()
    queues = LoopQueues()
    controller._event_handler = type(  # pylint: disable=protected-access
        "Handler",
        (),
        {"interaction_queues": queues},
    )()

    controller.enqueue_follow_up("confirm completion")

    assert controller.has_follow_up()
    assert controller.drain_follow_up() == ["confirm completion"]
