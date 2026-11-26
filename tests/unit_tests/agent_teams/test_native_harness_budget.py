# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""NativeHarness budget integration points (supervisor path).

The team supervisor bypasses ``DeepAgent._run_task_loop``; these tests pin
the two integration points added there: round-boundary tagging and the
evaluator-chain refresh after rail wiring.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails.task_completion_rail import TaskCompletionRail


class _Coordinator:
    def __init__(self, should_continue: bool, stop_reason=None):
        self._should_continue = should_continue
        self.stop_reason = stop_reason
        self.current_iteration = 3
        self.evaluators = None

    def should_continue(self) -> bool:
        return self._should_continue

    def set_evaluators(self, evaluators) -> None:
        self.evaluators = evaluators


def _fake_harness(*, should_continue: bool, stop_reason=None):
    coordinator = _Coordinator(should_continue, stop_reason)
    rail = TaskCompletionRail(iteration_budget=200, enable_budget_wrap_up=True)
    fake = SimpleNamespace(
        loop_coordinator=coordinator,
        _task_completion_rail=rail,
        _tag_budget_stop_result=lambda result, reason=None: (
            DeepAgent._tag_budget_stop_result(
                SimpleNamespace(_last_loop_stop_reason=None),
                result,
                reason,
            )
        ),
    )
    return fake, coordinator, rail


def test_round_boundary_tags_budget_stop():
    fake, _, _ = _fake_harness(
        should_continue=False, stop_reason="IterationBudgetExhaustedEvaluator",
    )
    result = NativeHarness._tag_budget_result_at_boundary(
        fake, {"output": "summary", "result_type": "answer"},
    )
    assert result["stop_reason"] == "max_rounds"


def test_round_boundary_tags_total_budget():
    fake, _, _ = _fake_harness(
        should_continue=False, stop_reason="RoundBudgetExhaustedEvaluator",
    )
    result = NativeHarness._tag_budget_result_at_boundary(
        fake, {"output": "summary", "result_type": "answer"},
    )
    assert result["stop_reason"] == "budget_total"


def test_round_boundary_keeps_intermediate_round_untagged():
    fake, _, _ = _fake_harness(should_continue=True)
    original = {"output": "working", "result_type": "answer"}
    assert NativeHarness._tag_budget_result_at_boundary(fake, original) is original


def test_refresh_loop_evaluators_rebuilds_chain():
    fake, coordinator, rail = _fake_harness(should_continue=True)
    NativeHarness.refresh_loop_evaluators(fake)
    names = [e.name for e in coordinator.evaluators]
    assert "IterationBudgetExhaustedEvaluator" in names


@pytest.mark.asyncio
async def test_settle_refreshes_budget_state_before_evaluating():
    """纯文本轮（零 tick）也要在结算点刷新耗尽标志。"""
    calls: list[str] = []

    class _Rail:
        def refresh_budget_state(self):
            calls.append("refresh")

    class _Coord:
        stop_reason = "RoundBudgetExhaustedEvaluator"
        current_iteration = 1
        is_aborted = False

        def increment_iteration(self):
            pass

        def set_last_result(self, result):
            pass

        def get_state(self):
            return None

        def should_continue(self):
            assert calls == ["refresh"]  # 探针先于评估器
            return False

    transitions: list[object] = []

    async def _emit_round(*_args, **_kwargs):
        pass

    async def _transition(state):
        transitions.append(state)

    async def _drain(_session):
        return None

    fake = SimpleNamespace(
        _st=SimpleNamespace(active=None),
        _session=object(),
        loop_coordinator=_Coord(),
        load_state=lambda _session: SimpleNamespace(stop_condition_state=None),
        save_state=lambda _session, _st: None,
        _emit_round=_emit_round,
        _transition=_transition,
        _task_completion_rail=_Rail(),
        _drain_pending_follow_ups=_drain,
        _has_remaining_tasks=lambda _session: False,
    )
    active = SimpleNamespace(
        graceful_abort=False,
        original_query="q",
        failure_retry=False,
        pause_requested=False,
    )
    cmd = SimpleNamespace(round_id=1, result={"output": "x"}, error=None)

    await NativeHarness._settle_round_done(fake, active, cmd)

    assert calls == ["refresh"]
    assert transitions


async def _run_settle_with_rail(rail, *, should_continue: bool):
    class _Coord:
        stop_reason = "RoundBudgetExhaustedEvaluator"
        current_iteration = 1
        is_aborted = False

        def increment_iteration(self):
            pass

        def set_last_result(self, result):
            pass

        def get_state(self):
            return None

        def should_continue(self):
            return should_continue

    async def _emit_round(*_args, **_kwargs):
        pass

    async def _transition(_state):
        pass

    async def _drain(_session):
        return None

    fake = SimpleNamespace(
        _st=SimpleNamespace(active=None),
        _session=object(),
        loop_coordinator=_Coord(),
        load_state=lambda _session: SimpleNamespace(stop_condition_state=None),
        save_state=lambda _session, _st: None,
        _emit_round=_emit_round,
        _transition=_transition,
        _task_completion_rail=rail,
        _drain_pending_follow_ups=_drain,
        _has_remaining_tasks=lambda _session: False,
    )
    active = SimpleNamespace(
        graceful_abort=False,
        original_query="q",
        failure_retry=False,
        pause_requested=False,
    )
    cmd = SimpleNamespace(round_id=1, result={"output": "x"}, error=None)
    await NativeHarness._settle_round_done(fake, active, cmd)


@pytest.mark.asyncio
async def test_settle_calls_member_repool_on_total_exhaustion():
    calls: list[str] = []

    class _Rail:
        total_budget_exhausted = True

        def refresh_budget_state(self):
            pass

        async def on_total_budget_exhausted(self):
            calls.append("repool")

    await _run_settle_with_rail(_Rail(), should_continue=False)
    assert calls == ["repool"]


@pytest.mark.asyncio
async def test_settle_skips_repool_when_budget_not_exhausted():
    calls: list[str] = []

    class _Rail:
        total_budget_exhausted = False

        def refresh_budget_state(self):
            pass

        async def on_total_budget_exhausted(self):
            calls.append("repool")

    await _run_settle_with_rail(_Rail(), should_continue=False)
    assert calls == []


@pytest.mark.parametrize("async_prepare", [True, False])
@pytest.mark.asyncio
async def test_wrap_up_round_uses_budget_event_query(async_prepare):
    """收尾轮用专用 team-event 驱动，不复用上一轮的事件批次。"""
    started: list = []

    class _Rail:
        budget_wrap_up_pending = True
        budget_wrap_up_consumed = False
        total_budget_exhausted = True
        iteration_cap_hit = False

        def refresh_budget_state(self):
            pass

    payload = {
        "repooled": ["p1-data"],
        "counts": {"pending": 2, "blocked": 6, "completed": 0},
    }
    if async_prepare:
        async def _prepare(self):
            return payload
    else:
        def _prepare(self):
            return payload
    _Rail.prepare_wrap_up_round = _prepare

    class _Coord:
        stop_reason = None
        current_iteration = 1
        is_aborted = False

        def increment_iteration(self):
            pass

        def set_last_result(self, result):
            pass

        def get_state(self):
            return None

        def should_continue(self):
            return True

    async def _emit_round(*_args, **_kwargs):
        pass

    async def _transition(_state):
        pass

    async def _drain(_session):
        return None

    fake = SimpleNamespace(
        _st=SimpleNamespace(active=None),
        _session=object(),
        loop_coordinator=_Coord(),
        load_state=lambda _session: SimpleNamespace(stop_condition_state=None),
        save_state=lambda _session, _st: None,
        _emit_round=_emit_round,
        _transition=_transition,
        _task_completion_rail=_Rail(),
        _drain_pending_follow_ups=_drain,
        _has_remaining_tasks=lambda _session: False,
        _start_round=lambda query: (
                started.append(query) or SimpleNamespace(round_id=99)
        ),
    )
    active = SimpleNamespace(
        graceful_abort=False,
        original_query=["stale task-board event"],
        failure_retry=False,
        pause_requested=False,
    )
    cmd = SimpleNamespace(round_id=4, result={"output": "x"}, error=None)

    await NativeHarness._settle_round_done(fake, active, cmd)

    assert len(started) == 1
    assert started[0].startswith('<team-event kind="budget-wrap-up">')
    assert "p1-data" in started[0]
    assert "待办 2" in started[0]


def test_pre_wrap_up_control_payload_is_suppressed():
    fake, _, rail = _fake_harness(should_continue=True)
    rail.budget_tick = lambda: 1
    rail._iteration_cap_hit = True
    assert rail.budget_wrap_up_pending
    assert rail.should_suppress_budget_result(
        {"output": "x", "result_type": "answer", "budget_stop": "iteration_budget"}
    )
    # 收尾轮之后不再抑制（该轮结果就是用户可见的总结）
    rail._wrap_up_consumed = True
    assert not rail.should_suppress_budget_result(
        {"output": "x", "budget_stop": "wrap_up_grace"}
    )
