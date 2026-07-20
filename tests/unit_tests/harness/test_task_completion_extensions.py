# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for task completion loop extension points."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.goal.schema import (
    GoalAssessment,
    GoalAssessmentStatus,
    GoalRecord,
)
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


@pytest.mark.asyncio
async def test_submit_goal_report_does_not_force_finish() -> None:
    """Accepted submit_goal_report must not terminate the attempt early.

    Assessment runs in after_task_iteration via GoalReportSink; the tool
    hook itself must leave the model a final-note / further-tool chance.
    """
    rail = TaskCompletionRail()
    rail.set_goal_manager(object())
    rail._is_goal_round = True
    ctx = AgentCallbackContext(
        agent=object(),
        inputs=ToolCallInputs(
            tool_name="submit_goal_report",
            tool_result={"result": "report_accepted", "status": "continue"},
        ),
    )

    await rail.after_tool_call(ctx)

    assert ctx.consume_force_finish() is None


@pytest.mark.asyncio
async def test_tool_after_goal_report_is_allowed() -> None:
    """A later tool call after an accepted report is not force-finished."""
    rail = TaskCompletionRail()
    rail.set_goal_manager(object())
    rail._is_goal_round = True
    ctx = AgentCallbackContext(
        agent=object(),
        inputs=ToolCallInputs(tool_name="write_file"),
    )

    await rail.before_tool_call(ctx)

    assert ctx.consume_force_finish() is None


@pytest.mark.asyncio
async def test_goal_report_outside_goal_round_does_not_force_finish() -> None:
    """submit_goal_report outside a goal round must not force-finish."""
    rail = TaskCompletionRail()
    rail.set_goal_manager(object())
    ctx = AgentCallbackContext(
        agent=object(),
        inputs=ToolCallInputs(
            tool_name="submit_goal_report",
            tool_result={"result": "report_accepted", "status": "continue"},
        ),
    )

    await rail.after_tool_call(ctx)

    assert ctx.consume_force_finish() is None


@pytest.mark.asyncio
async def test_rejected_goal_report_does_not_force_finish() -> None:
    """Malformed submit_goal_report must not terminate the attempt early."""
    rail = TaskCompletionRail()
    rail.set_goal_manager(object())
    rail._is_goal_round = True
    ctx = AgentCallbackContext(
        agent=object(),
        inputs=ToolCallInputs(
            tool_name="submit_goal_report",
            tool_result={"result": "report_rejected", "error": "missing status"},
        ),
    )

    await rail.after_tool_call(ctx)

    assert ctx.consume_force_finish() is None


@pytest.mark.asyncio
async def test_terminal_goal_report_invokes_transcript_assessor(monkeypatch) -> None:
    """HYBRID terminal reports must be verified by the transcript assessor."""
    rail = TaskCompletionRail()
    rail.set_goal_manager(object())
    record = GoalRecord.create(session_id="s1", objective="查杭州天气")
    report = GoalAssessment(
        status=GoalAssessmentStatus.COMPLETE,
        evidence="天气已查询并返回给用户",
    )
    calls = []

    async def fake_invoke(record_arg, ctx):
        calls.append((record_arg, ctx))
        return '{"status":"complete","evidence":"verified"}'

    monkeypatch.setattr(rail, "_invoke_transcript_assessor", fake_invoke)
    ctx = AgentCallbackContext(agent=object(), inputs=object())

    result = await rail._maybe_invoke_transcript_assessor(record, report, ctx)

    assert result == '{"status":"complete","evidence":"verified"}'
    assert len(calls) == 1
    assert calls[0][0] is record
    assert calls[0][1] is ctx


@pytest.mark.asyncio
async def test_continue_goal_report_does_not_invoke_transcript_by_default(monkeypatch) -> None:
    """CONTINUE reports stay on the low-cost path unless spot-checking is configured."""
    rail = TaskCompletionRail()
    rail.set_goal_manager(object())
    record = GoalRecord.create(session_id="s1", objective="完成任务")
    report = GoalAssessment(
        status=GoalAssessmentStatus.CONTINUE,
        evidence="仍有剩余工作",
    )
    called = False

    async def fake_invoke(*_args, **_kwargs):
        nonlocal called
        called = True
        return '{"status":"complete","evidence":"verified"}'

    monkeypatch.setattr(rail, "_invoke_transcript_assessor", fake_invoke)
    ctx = AgentCallbackContext(agent=object(), inputs=object())

    result = await rail._maybe_invoke_transcript_assessor(record, report, ctx)

    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_attempt_context_uses_latest_model_window_without_duplication() -> None:
    """Goal assessment uses the latest model window instead of accumulating copies."""
    rail = TaskCompletionRail()
    rail.set_goal_manager(object())
    rail._is_goal_round = True

    first_ctx = AgentCallbackContext(
        agent=object(),
        inputs=SimpleNamespace(
            messages=[{"role": "user", "content": "old request"}],
            response={"role": "assistant", "content": "old response"},
        ),
    )
    await rail.after_model_call(first_ctx)

    second_ctx = AgentCallbackContext(
        agent=object(),
        inputs=SimpleNamespace(
            messages=[
                {"role": "user", "content": "current weather request"},
                {
                    "role": "tool",
                    "content": "Hangzhou weather: 36C cloudy humidity 53%",
                },
            ],
            response={
                "role": "assistant",
                "content": "submitted complete goal report",
            },
        ),
    )
    await rail.after_model_call(second_ctx)

    transcript = rail._extract_attempt_context(
        AgentCallbackContext(agent=object(), inputs=object())
    )

    assert "Hangzhou weather: 36C" in transcript
    assert "submitted complete goal report" in transcript
    assert "old request" not in transcript
    assert "old response" not in transcript


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
    handler = type(
        "Handler",
        (),
        {"interaction_queues": queues},
    )()
    controller.set_event_handler(handler)

    controller.enqueue_follow_up("confirm completion")

    assert controller.has_follow_up()
    assert controller.drain_follow_up() == ["confirm completion"]
