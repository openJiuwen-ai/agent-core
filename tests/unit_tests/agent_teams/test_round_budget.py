# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Round budget ledger + kernel wiring tests."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.coordination.kernel import CoordinationKernel
from openjiuwen.agent_teams.agent.round_budget import RoundBudgetLedger
from openjiuwen.agent_teams.schema.team import TeamRole


class TestRoundBudgetLedger:
    def test_tick_counts_per_member_and_remaining(self):
        ledger = RoundBudgetLedger(5, session_id="s1")
        assert ledger.tick("leader") == 4
        assert ledger.tick("m1") == 3
        assert ledger.tick("m1") == 2  # same member accumulates
        assert ledger.detail() == {"leader": 1, "m1": 2}
        assert ledger.used == 3
        assert not ledger.exhausted

    def test_exhaustion_one_shot(self):
        ledger = RoundBudgetLedger(2, session_id="s1")
        assert ledger.tick("a") == 1
        assert ledger.tick("b") == 0
        assert ledger.exhausted
        # further ticks keep counting but do not re-fire
        assert ledger.tick("a") == -1
        assert ledger.used == 3

    def test_uncapped_never_exhausts(self):
        ledger = RoundBudgetLedger(None, session_id="s1")
        for _ in range(100):
            assert ledger.tick("a") is None
        assert ledger.used == 100
        assert not ledger.exhausted

    def test_exhaustion_callback_fires_once(self):
        calls: List[int] = []
        ledger = RoundBudgetLedger(1, on_exhausted=lambda: calls.append(1))
        ledger.tick("a")
        ledger.tick("b")
        ledger.tick("c")
        assert calls == [1]

    def test_exhaustion_callback_failure_does_not_break_tick(self):
        def _boom():
            raise RuntimeError("callback exploded")

        ledger = RoundBudgetLedger(1, on_exhausted=_boom)
        assert ledger.tick("a") == 0
        assert ledger.exhausted

    def test_leader_reserve_protects_leader_share(self):
        ledger = RoundBudgetLedger(
            100, leader_name="leader", leader_reserve=20,
        )
        for _ in range(80):
            ledger.tick("m1")
        assert ledger.exhausted  # 成员池到顶
        assert ledger.remaining("m1") == 0
        assert ledger.remaining("leader") == 20  # 预留仍可用
        for _ in range(20):
            ledger.tick("leader")
        assert ledger.remaining("leader") == 0

    def test_leader_reserve_formula(self):
        from openjiuwen.agent_teams.agent.coordination.kernel import (
            _leader_reserve_for,
        )

        assert _leader_reserve_for(None) == 0
        # 极小额度：不预留，至少给成员留 1 次迭代（P>=1）
        assert _leader_reserve_for(0) == 0
        assert _leader_reserve_for(1) == 0
        assert _leader_reserve_for(2) == 1
        assert _leader_reserve_for(3) == 1
        assert _leader_reserve_for(4) == 2
        assert _leader_reserve_for(20) == 10  # 不超过 limit//2
        assert _leader_reserve_for(30) == 15
        assert _leader_reserve_for(39) == 19
        assert _leader_reserve_for(40) == 20  # 20% 线（floor 20）开始平走
        assert _leader_reserve_for(50) == 20
        assert _leader_reserve_for(100) == 20
        assert _leader_reserve_for(200) == 20
        assert _leader_reserve_for(300) == 30
        assert _leader_reserve_for(500) == 30
        assert _leader_reserve_for(1000) == 30  # 预留封顶 30

    def test_close_is_terminal_and_idempotent(self):
        ledger = RoundBudgetLedger(10, session_id="s1")
        ledger.tick("a")
        ledger.close("round_finalize")
        ledger.close("round_finalize")
        assert ledger.tick("a") is None


def _make_kernel(
        *,
        role: TeamRole = TeamRole.LEADER,
        max_total_rounds: Optional[int] = 500,
) -> CoordinationKernel:
    host = SimpleNamespace(
        role=role,
        spec=SimpleNamespace(max_total_rounds=max_total_rounds),
        member_name="leader" if role == TeamRole.LEADER else "m1",
    )
    return CoordinationKernel(host)


class TestOpenRoundBudget:
    """计数：leader-only / 追问清零 / 纯恢复延续 / None 不启用。"""

    def test_member_never_opens_ledger(self):
        kernel = _make_kernel(role=TeamRole.TEAMMATE)
        kernel.open_round_budget(new_round=True)
        assert kernel.round_budget_ledger is None

    def test_none_limit_disables(self):
        kernel = _make_kernel(max_total_rounds=None)
        kernel.open_round_budget(new_round=True)
        assert kernel.round_budget_ledger is None

    def test_new_round_resets_and_closes_previous(self):
        kernel = _make_kernel()
        kernel.open_round_budget(new_round=True)
        first = kernel.round_budget_ledger
        first.tick("leader")
        kernel.open_round_budget(new_round=True)
        second = kernel.round_budget_ledger
        assert second is not first
        assert second.used == 0
        assert first._closed  # 追问清零：旧账本弃置

    def test_empty_query_keeps_ledger(self):
        kernel = _make_kernel()
        kernel.open_round_budget(new_round=True)
        first = kernel.round_budget_ledger
        first.tick("leader")
        kernel.open_round_budget(new_round=False)  # 纯恢复/无输入周期
        assert kernel.round_budget_ledger is first
        assert not first._closed

    def test_open_round_budget_sets_leader_reserve(self):
        kernel = _make_kernel(max_total_rounds=50)
        kernel.open_round_budget(new_round=True)
        ledger = kernel.round_budget_ledger
        assert ledger.leader_name == "leader"
        assert ledger.leader_reserve == 20
        for _ in range(30):  # 成员池 = 50 - 20
            ledger.tick("m1")
        assert ledger.exhausted
        assert ledger.remaining("leader") == 20

    def test_tiny_limit_keeps_member_slot(self):
        """limit=1：R=0、P=1，成员还能跑一次；不会出现 P<=0。"""
        kernel = _make_kernel(max_total_rounds=1)
        kernel.open_round_budget(new_round=True)
        ledger = kernel.round_budget_ledger
        assert ledger.leader_reserve == 0
        assert ledger.remaining("m1") == 1
        assert ledger.tick("m1") == 0  # 成员用掉唯一一次
        assert ledger.exhausted
        assert ledger.remaining("leader") == 0

    def test_non_positive_limit_is_clamped(self):
        for value in (0, -5):
            kernel = _make_kernel(max_total_rounds=value)
            kernel.open_round_budget(new_round=True)
            ledger = kernel.round_budget_ledger
            assert ledger is not None
            assert ledger.limit == 1
            assert ledger.leader_reserve == 0
            assert ledger.remaining("m1") == 1

    def test_paused_followup_resets(self):
        kernel = _make_kernel()
        kernel.open_round_budget(new_round=True)
        first = kernel.round_budget_ledger
        kernel._lifecycle_state = "paused"
        kernel.open_round_budget(new_round=True)  # 暂停后追问
        assert kernel.round_budget_ledger is not first


class TestOnBudgetWrapUp:
    """先停成员在途轮、再回池任务，顺序钉死。"""

    @pytest.mark.asyncio
    async def test_stop_members_before_repool(self):
        events: List[str] = []
        # repooled ids are returned to the rail for the wrap-up prompt

        running_controller = SimpleNamespace(
            is_agent_running=lambda: True,
            cancel_agent=AsyncMock(
                side_effect=lambda: events.append("cancel_m1")
            ),
        )
        idle_controller = SimpleNamespace(
            is_agent_running=lambda: False,
            cancel_agent=AsyncMock(),
        )
        handles = {
            "m1": SimpleNamespace(agent_ref=SimpleNamespace(
                stream_controller=running_controller,
            )),
            "m2": SimpleNamespace(agent_ref=SimpleNamespace(
                stream_controller=idle_controller,
            )),
            "m3": SimpleNamespace(agent_ref=None),  # subprocess: skipped
        }
        task = SimpleNamespace(task_id="t1", status="in_progress")

        async def _get_tasks(team_name, status=None):
            events.append("list_tasks")
            return [task]

        async def _reset(task_id):
            events.append(f"reset_{task_id}")
            return task

        backend = SimpleNamespace(
            team_name="team1",
            db=SimpleNamespace(task=SimpleNamespace(
                get_team_tasks=_get_tasks,
                reset_task=_reset,
            )),
        )
        host = SimpleNamespace(
            role=TeamRole.LEADER,
            spec=SimpleNamespace(max_total_rounds=500),
            member_name="leader",
            spawn_manager=SimpleNamespace(spawned_handles=handles),
            infra=SimpleNamespace(team_backend=backend),
        )
        kernel = CoordinationKernel(host)
        payload = await kernel._on_budget_wrap_up()

        assert events == ["cancel_m1", "list_tasks", "reset_t1"]
        assert payload["repooled"] == ["t1"]
        assert payload["counts"]["pending"] == 1
        idle_controller.cancel_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_member_cancel_failure_does_not_abort_repool(self):
        failing = SimpleNamespace(
            is_agent_running=lambda: True,
            cancel_agent=AsyncMock(side_effect=RuntimeError("boom")),
        )
        handles = {"m1": SimpleNamespace(
            agent_ref=SimpleNamespace(stream_controller=failing),
        )}

        async def _get_tasks(team_name, status=None):
            return []

        backend = SimpleNamespace(
            team_name="team1",
            db=SimpleNamespace(task=SimpleNamespace(
                get_team_tasks=_get_tasks,
                reset_task=AsyncMock(),
            )),
        )
        host = SimpleNamespace(
            role=TeamRole.LEADER,
            spec=SimpleNamespace(max_total_rounds=500),
            member_name="leader",
            spawn_manager=SimpleNamespace(spawned_handles=handles),
            infra=SimpleNamespace(team_backend=backend),
        )
        kernel = CoordinationKernel(host)
        await kernel._on_budget_wrap_up()  # must not raise

    @pytest.mark.asyncio
    async def test_wrap_up_lists_already_repooled_pending_tasks(self):
        """成员自助回池后：无主 PENDING 也要进入收尾清单。"""
        pending = SimpleNamespace(task_id="t9", status="pending", assignee=None)

        async def _get_tasks(team_name, status=None):
            return [pending]

        backend = SimpleNamespace(
            team_name="team1",
            db=SimpleNamespace(task=SimpleNamespace(
                get_team_tasks=_get_tasks,
                reset_task=AsyncMock(),
            )),
        )
        host = SimpleNamespace(
            role=TeamRole.LEADER,
            spec=SimpleNamespace(max_total_rounds=500),
            member_name="leader",
            spawn_manager=SimpleNamespace(spawned_handles={}),
            infra=SimpleNamespace(team_backend=backend),
        )
        kernel = CoordinationKernel(host)
        payload = await kernel._on_budget_wrap_up()
        assert payload["repooled"] == ["t9"]
        assert payload["counts"]["pending"] == 1

    @pytest.mark.asyncio
    async def test_wrap_up_counts_reflect_board(self):
        """收尾提示计数：退回待办算待办，blocked/completed 按原状态。"""
        tasks = [
            SimpleNamespace(task_id="t1", status="in_progress", assignee="m1"),
            SimpleNamespace(task_id="t2", status="blocked", assignee="m2"),
            SimpleNamespace(task_id="t3", status="completed", assignee="m3"),
            SimpleNamespace(task_id="t4", status="pending", assignee=None),
        ]

        async def _get_tasks(team_name, status=None):
            return tasks

        backend = SimpleNamespace(
            team_name="team1",
            db=SimpleNamespace(task=SimpleNamespace(
                get_team_tasks=_get_tasks,
                reset_task=AsyncMock(),
            )),
        )
        host = SimpleNamespace(
            role=TeamRole.LEADER,
            spec=SimpleNamespace(max_total_rounds=500),
            member_name="leader",
            spawn_manager=SimpleNamespace(spawned_handles={}),
            infra=SimpleNamespace(team_backend=backend),
        )
        payload = await CoordinationKernel(host)._on_budget_wrap_up()
        assert payload["repooled"] == ["t1", "t4"]
        assert payload["counts"] == {
            "pending": 2,
            "blocked": 1,
            "completed": 1,
            "in_progress": 0,
        }


class TestMemberTotalBudgetRepool:
    """成员 total 耗尽停轮时自助回池（只回池）。"""

    def _kernel(self, reset_calls: List[str]):
        tasks = [
            SimpleNamespace(task_id="t1", status="in_progress"),
            SimpleNamespace(task_id="t2", status="completed"),
        ]

        async def _get_by_assignee(team_name, member_name):
            return tasks

        async def _reset(task_id):
            reset_calls.append(task_id)

        backend = SimpleNamespace(
            team_name="team1",
            db=SimpleNamespace(task=SimpleNamespace(
                get_tasks_by_assignee=_get_by_assignee,
                reset_task=_reset,
            )),
        )
        host = SimpleNamespace(
            role=TeamRole.TEAMMATE,
            member_name="m1",
            infra=SimpleNamespace(team_backend=backend),
        )
        return CoordinationKernel(host)

    @pytest.mark.asyncio
    async def test_repools_own_inflight_once(self):
        reset_calls: List[str] = []
        kernel = self._kernel(reset_calls)
        await kernel._on_total_budget_exhausted()
        await kernel._on_total_budget_exhausted()  # 一次性闸
        assert reset_calls == ["t1"]  # completed 不回池

    @pytest.mark.asyncio
    async def test_leader_is_not_wired_for_self_repool(self):
        reset_calls: List[str] = []
        kernel = self._kernel(reset_calls)
        kernel._host.role = TeamRole.LEADER
        await kernel._on_total_budget_exhausted()
        assert reset_calls == []

    @pytest.mark.asyncio
    async def test_repool_failure_is_fail_open(self):
        async def _boom(team_name, member_name):
            raise RuntimeError("db down")

        host = SimpleNamespace(
            role=TeamRole.TEAMMATE,
            member_name="m1",
            infra=SimpleNamespace(team_backend=SimpleNamespace(
                team_name="team1",
                db=SimpleNamespace(task=SimpleNamespace(
                    get_tasks_by_assignee=_boom,
                    reset_task=AsyncMock(),
                )),
            )),
        )
        kernel = CoordinationKernel(host)
        await kernel._on_total_budget_exhausted()  # must not raise


class TestMemberExhaustionChain:
    """摘牌(ERROR)→回池→通报→leader 名册；一次性闸；复牌。"""

    def _member_kernel(self, events):
        backend = SimpleNamespace(
            team_name="team1",
            resolve_leader_member_name=AsyncMock(return_value="leader"),
            message_manager=SimpleNamespace(
                send_message=AsyncMock(
                    side_effect=lambda **kw: events.append(("mailbox", kw))
                )
            ),
            db=SimpleNamespace(task=SimpleNamespace(
                get_tasks_by_assignee=AsyncMock(return_value=[
                    SimpleNamespace(task_id="t1", status="in_progress"),
                    SimpleNamespace(task_id="t2", status="completed"),
                ]),
                reset_task=AsyncMock(
                    side_effect=lambda tid: events.append(("reset", tid))
                ),
            )),
        )
        notified = []
        host = SimpleNamespace(
            role=TeamRole.TEAMMATE,
            spec=SimpleNamespace(max_total_rounds=500),
            member_name="m1",
            infra=SimpleNamespace(team_backend=backend),
            _exhaustion_notify=lambda name: notified.append(name),
        )
        return CoordinationKernel(host), notified

    @pytest.mark.asyncio
    async def test_exhaustion_chain_order_and_once(self):
        events = []
        kernel, notified = self._member_kernel(events)
        await kernel.on_member_exhausted("iteration_budget")
        await kernel.on_member_exhausted("model_retry")  # 一次性闸
        kinds = [e[0] for e in events]
        # 摘牌（内存名册）先于回池（防双执行竞态钉案）；不写成员状态，
        # 因此没有 status 事件。
        assert kinds == ["reset", "mailbox"]
        assert events[0] == ("reset", "t1")  # 只在途任务回池
        assert notified == ["m1"]

    @pytest.mark.asyncio
    async def test_exhaustion_reported_resets_per_run_cycle(self):
        """成员上报闸按运行周期重置：第二次耗尽仍能收口。"""
        events = []
        kernel, notified = self._member_kernel(events)
        await kernel.on_member_exhausted("iteration_budget")
        await kernel.on_member_exhausted("iteration_budget")  # 同周期一次
        assert notified == ["m1"]
        kernel._exhaustion_reported = False  # 下一个 coordination.start
        await kernel.on_member_exhausted("iteration_budget")
        assert notified == ["m1", "m1"]

    @pytest.mark.asyncio
    async def test_leader_role_never_self_marks(self):
        kernel = _make_kernel()
        await kernel.on_member_exhausted("iteration_budget")
        assert kernel._round_exhausted_members == set()

    @pytest.mark.asyncio
    async def test_mark_and_revive_cycle(self):
        kernel = _make_kernel()
        kernel.mark_member_exhausted("m1")
        kernel.mark_member_exhausted("m1")  # 去重
        assert kernel._round_exhausted_members == {"m1"}
        # 有事件循环时接管提示阶段武装；无循环上下文则静默让位判死兜底
        assert kernel._takeover_nudge_task is not None
        kernel._cancel_takeover_nudge()
        assert kernel._takeover_nudge_task is None
        # 新回合复牌 = 清回合级名册（不写成员状态）
        await kernel._revive_round_exhausted_members()
        assert kernel._round_exhausted_members == set()


class TestRailWiring:
    """coordination.start 的接线：重置/回调/门控/评估器刷新。"""

    def _kernel(self, role: TeamRole):
        from openjiuwen.harness.rails.task_completion_rail import (
            TaskCompletionRail,
        )

        rail = TaskCompletionRail(iteration_budget=5)
        rail._iteration_count = 3  # 上一周期残留

        refreshed = []
        harness = SimpleNamespace(
            find_rails=lambda _t: [rail],
            refresh_loop_evaluators=lambda: refreshed.append(1),
        )
        controller = SimpleNamespace()
        task_manager = SimpleNamespace(
            gate=None,
            set_member_exhausted_gate=lambda g: setattr(
                task_manager, "gate", g,
            ),
        )
        ledger = RoundBudgetLedger(50, session_id="s1")
        host = SimpleNamespace(
            role=role,
            member_name="leader" if role is TeamRole.LEADER else "m1",
            spec=SimpleNamespace(max_total_rounds=50),
            _budget_ledger_getter=(
                None if role is TeamRole.LEADER else lambda: ledger
            ),
            resources=SimpleNamespace(harness=harness),
            stream_controller=controller,
            task_manager=task_manager,
        )
        return CoordinationKernel(host), rail, controller, task_manager, refreshed

    def test_leader_wiring_resets_and_enables_wrap_up(self):
        kernel, rail, controller, tm, refreshed = self._kernel(TeamRole.LEADER)
        kernel._wire_round_budget_rails(
            SimpleNamespace(
                find_rails=lambda _t: [rail],
                refresh_loop_evaluators=lambda: refreshed.append(1),
            )
        )
        assert rail._iteration_count == 0  # 周期重置
        assert rail.enable_budget_wrap_up is True
        assert rail.on_budget_wrap_up == kernel._on_budget_wrap_up
        assert rail.budget_tick is not None
        assert rail.budget_probe is not None
        assert controller.on_round_exhausted is not None
        assert tm.gate("m1") is False
        assert refreshed == [1]

    def test_member_wiring_connects_exhaustion_callbacks(self):
        kernel, rail, controller, tm, refreshed = self._kernel(TeamRole.TEAMMATE)
        kernel._wire_round_budget_rails(
            SimpleNamespace(
                find_rails=lambda _t: [rail],
                refresh_loop_evaluators=lambda: refreshed.append(1),
            )
        )
        assert rail.on_iteration_cap == kernel._on_iteration_cap
        assert rail.budget_probe is not None
        assert rail.enable_budget_wrap_up is False
        assert controller.on_round_exhausted == kernel._on_round_exhausted
        assert tm.gate("m1") is False  # 未上报前放行
        kernel._exhaustion_reported = True
        assert tm.gate("m1") is True


class TestIsRoundBudgetExhausted:
    """members read the leader ledger through the spawn getter."""

    def _probe(self, getter, own_ledger=None, *, with_attr=True):
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent

        fake = SimpleNamespace(
            _coordination=SimpleNamespace(round_budget_ledger=own_ledger),
            # 假体上直接给同名属性：真实 TeamAgent 走同名 property
            round_budget_ledger=own_ledger,
        )
        if with_attr:
            fake._budget_ledger_getter = getter
        return TeamAgent.is_round_budget_exhausted(fake)

    def test_callable_getter_wins_over_own_ledger(self):
        leader_ledger = RoundBudgetLedger(1, session_id="s1")
        leader_ledger.tick("m1")  # exhausted
        own_ledger = RoundBudgetLedger(10, session_id="s1")
        assert self._probe(lambda: leader_ledger, own_ledger) is True

    def test_non_callable_getter_falls_back_to_own_ledger(self):
        own_ledger = RoundBudgetLedger(1, session_id="s1")
        own_ledger.tick("leader")
        assert self._probe("not-callable", own_ledger) is True

    def test_missing_attr_and_no_ledger_reports_false(self):
        assert self._probe(None, None, with_attr=False) is False


class TestBudgetWrapUpWake:
    """账本耗尽上升沿唤醒空闲 leader 进入收尾轮。"""

    def _kernel(self, *, in_flight: bool, consumed: bool = False):
        async def _prepare():
            return {
                "repooled": ["p1-data"],
                "counts": {"pending": 2, "blocked": 1, "completed": 0},
            }

        rail = SimpleNamespace(
            budget_wrap_up_consumed=consumed,
            prepare_wrap_up_round=_prepare,
        )
        sent: List[Any] = []

        class _Harness:
            def find_rails(self, _rail_type):
                return [rail]

            async def send(self, content):
                sent.append(content)

        host = SimpleNamespace(
            role=TeamRole.LEADER,
            member_name="leader",
            spec=SimpleNamespace(max_total_rounds=1),
            resources=SimpleNamespace(harness=_Harness()),
            has_in_flight_round=lambda: in_flight,
        )
        kernel = CoordinationKernel(host)
        kernel._lifecycle_state = "running"
        kernel.open_round_budget(new_round=True)
        return kernel, sent

    @pytest.mark.asyncio
    async def test_idle_leader_is_woken_with_wrap_up_event(self):
        from openjiuwen.agent_teams.i18n import get_language, set_language

        old_language = get_language()
        set_language("cn")
        try:
            kernel, sent = self._kernel(in_flight=False)
            kernel.round_budget_ledger.tick("m1")  # limit=1 -> exhausted
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            set_language(old_language)

        assert len(sent) == 1
        assert isinstance(sent[0], str)
        assert '<team-event kind="budget-wrap-up">' in sent[0]
        assert "预算收尾" in sent[0]
        assert "p1-data" in sent[0]  # 事件带任务板详情
        assert "待办 2" in sent[0]

    @pytest.mark.asyncio
    async def test_running_leader_is_not_woken(self):
        kernel, sent = self._kernel(in_flight=True)
        kernel.round_budget_ledger.tick("m1")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert sent == []

    @pytest.mark.asyncio
    async def test_consumed_wrap_up_is_not_rewoken(self):
        kernel, sent = self._kernel(in_flight=False, consumed=True)
        kernel.round_budget_ledger.tick("m1")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert sent == []


class TestDispatchGate:
    """派发硬闸：ERROR 成员拒派/拒认领。"""

    @pytest.mark.asyncio
    async def test_validate_owner_rejects_exhausted(self):
        from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager

        # 耗尽由回合级名册门控判定，与成员状态（ERROR=崩溃/复活）解耦
        member = SimpleNamespace(status="ready", mode="build_mode")
        tm = TeamTaskManager.__new__(TeamTaskManager)
        tm.db = SimpleNamespace(
            member=SimpleNamespace(get_member=AsyncMock(return_value=member))
        )
        tm.team_name = "team1"
        tm._resolve_leader_member_name = AsyncMock(return_value="leader")
        tm.set_member_exhausted_gate(lambda name: name == "m1")
        result = await tm._validate_task_owner("m1")
        assert not result.ok
        assert "exhausted" in result.reason
        # 非耗尽成员照常放行
        assert (await tm._validate_task_owner("m2")).ok

    @pytest.mark.asyncio
    async def test_error_status_member_is_not_gated(self):
        """ERROR（崩溃/复活语义）成员不被耗尽闸误拒。"""
        from openjiuwen.agent_teams.tools.task_manager import TeamTaskManager

        member = SimpleNamespace(status="error", mode="build_mode")
        tm = TeamTaskManager.__new__(TeamTaskManager)
        tm.db = SimpleNamespace(
            member=SimpleNamespace(get_member=AsyncMock(return_value=member))
        )
        tm.team_name = "team1"
        tm._resolve_leader_member_name = AsyncMock(return_value="leader")
        tm.set_member_exhausted_gate(lambda name: False)
        assert (await tm._validate_task_owner("m1")).ok
