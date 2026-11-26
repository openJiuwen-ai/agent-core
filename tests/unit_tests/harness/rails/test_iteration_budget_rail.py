# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Iteration budget tests:
TaskCompletionRail counts INNER ReAct iterations (every model call),
force-finishes at the cap, grants the leader one wrap-up outer round."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Optional

import pytest

from openjiuwen.core.single_agent.prompts.builder import SystemPromptBuilder
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.task_completion_rail import (
    _WRAP_UP_GRACE_ITERATIONS,
    TaskCompletionRail,
)
from openjiuwen.harness.schema.stop_condition import StopEvaluationContext


def _make_ctx(builder_language: str = "cn") -> AgentCallbackContext:
    builder = SystemPromptBuilder(language=builder_language)
    agent = SimpleNamespace(
        card=SimpleNamespace(name="leader"),
        system_prompt_builder=builder,
    )
    return AgentCallbackContext(agent=agent, inputs={})


def _builder(ctx: AgentCallbackContext) -> SystemPromptBuilder:
    return ctx.agent.system_prompt_builder


class TestSoftHints:
    """hint at remaining<=3, mandatory wrap-up past the cap; section
    channel only (nothing touches query/history)."""

    @pytest.mark.asyncio
    async def test_no_budget_no_injection(self):
        rail = TaskCompletionRail()
        ctx = _make_ctx()
        for _ in range(5):
            await rail.after_react_iteration(ctx)
        assert not _builder(ctx).has_section(SectionName.ITERATION_BUDGET)
        assert rail.build_evaluators() == []

    @pytest.mark.asyncio
    async def test_hint_and_final_stretch_thresholds(self):
        rail = TaskCompletionRail(iteration_budget=5)
        ctx = _make_ctx()
        builder = _builder(ctx)

        await rail.after_react_iteration(ctx)  # count 1, remaining 4
        assert not builder.has_section(SectionName.ITERATION_BUDGET)

        hint_texts = set()
        for _ in (3, 2, 1):  # counts 2..4 — same fixed two-state text
            await rail.after_react_iteration(ctx)
            section = builder.get_section(SectionName.ITERATION_BUDGET)
            assert section is not None
            text = section.render("cn")
            assert "迭代预算告急" in text
            assert "预算即将用完" in text
            hint_texts.add(text)
        # 文案不随剩余轮数变化：系统提示/前缀缓存在告急窗口内保持稳定
        assert len(hint_texts) == 1

        await rail.after_react_iteration(ctx)  # count 5, remaining 0
        section = builder.get_section(SectionName.ITERATION_BUDGET)
        assert "最后一轮" in section.render("cn")

    @pytest.mark.asyncio
    async def test_hint_removed_when_budget_cleared(self):
        rail = TaskCompletionRail(iteration_budget=3)
        ctx = _make_ctx()
        await rail.after_react_iteration(ctx)
        await rail.after_react_iteration(ctx)
        assert _builder(ctx).has_section(SectionName.ITERATION_BUDGET)
        rail.iteration_budget = None  # hot path: budget removed
        await rail.after_react_iteration(ctx)
        assert not _builder(ctx).has_section(SectionName.ITERATION_BUDGET)

    @pytest.mark.asyncio
    async def test_cycle_reset_not_before_invoke(self):
        """Cycle boundary resets the budget; BEFORE_INVOKE must not.

        团队链路的 BEFORE_INVOKE 是每个外层轮触发一次——在那里重置会让
        main/total 预算退化为每轮一份，收尾轮入口标志也会被清零。
        """
        rail = TaskCompletionRail(iteration_budget=5)
        ctx = _make_ctx()
        for _ in range(3):
            await rail.after_react_iteration(ctx)
        assert rail._iteration_count == 3
        await rail.before_invoke(ctx)  # 外层轮边界：不重置
        assert rail._iteration_count == 3
        rail.reset_budget_cycle()  # 周期边界：重置
        assert rail._iteration_count == 0
        assert rail._iteration_cap_hit is False
        assert rail._budget_total_exhausted is False
        assert rail._wrap_up_consumed is False

    def test_cycle_reset_log_samples_every_10_cycles(self, caplog):
        """reset 日志按周期采样（每 10 个周期一条 INFO），避免逐周期刷屏。"""
        rail = TaskCompletionRail(iteration_budget=5)
        logger_name = "openjiuwen.harness.rails.task_completion_rail"
        with caplog.at_level(logging.INFO, logger=logger_name):
            for _ in range(9):
                rail.reset_budget_cycle()
            assert not [
                r for r in caplog.records
                if "iteration budget cycle reset" in r.getMessage()
            ]
            rail.reset_budget_cycle()  # 第 10 个周期
        sampled = [
            r for r in caplog.records
            if "iteration budget cycle reset" in r.getMessage()
        ]
        assert len(sampled) == 1
        assert sampled[0].levelno == logging.INFO
        assert "cycle=10" in sampled[0].getMessage()

    @pytest.mark.asyncio
    async def test_before_invoke_clears_stale_final_section(self):
        """A previous cycle's final-round hint must not leak forward."""
        rail = TaskCompletionRail(iteration_budget=5)
        ctx = _make_ctx()
        for _ in range(5):
            await rail.after_react_iteration(ctx)
        assert "最后一轮" in _builder(ctx).get_section(
            SectionName.ITERATION_BUDGET
        ).render("cn")
        rail.reset_budget_cycle()
        await rail.before_invoke(ctx)
        assert not _builder(ctx).has_section(SectionName.ITERATION_BUDGET)


class TestCapAndForceFinish:
    """内层口径硬闸：触顶即 request_force_finish，下一迭代边界收场。"""

    @pytest.mark.asyncio
    async def test_cap_hit_force_finishes_once(self):
        rail = TaskCompletionRail(iteration_budget=2)
        ctx = _make_ctx()
        await rail.after_react_iteration(ctx)
        await rail.after_react_iteration(ctx)
        assert rail._iteration_cap_hit is False
        assert not ctx.has_force_finish_request
        await rail.after_react_iteration(ctx)  # count 3 > budget 2
        assert rail._iteration_cap_hit is True
        assert ctx.has_force_finish_request
        request = ctx.consume_force_finish()
        assert request.result["budget_stop"] == "iteration_budget"
        # 已请求后不重复请求
        await rail.after_react_iteration(ctx)
        assert not ctx.has_force_finish_request

    @pytest.mark.asyncio
    async def test_evaluator_stops_outer_loop_after_cap(self):
        rail = TaskCompletionRail(iteration_budget=2)
        evaluator = [
            e for e in rail.build_evaluators()
            if e.name == "IterationBudgetExhaustedEvaluator"
        ][0]
        ctx = _make_ctx()
        await rail.after_react_iteration(ctx)
        assert not evaluator.should_stop(StopEvaluationContext())
        for _ in range(2):
            await rail.after_react_iteration(ctx)
        assert rail._iteration_cap_hit
        assert evaluator.should_stop(StopEvaluationContext())


class TestBudgetTick:
    """tick 挂点 + 耗尽评估器（标志读取，不重复计数）。"""

    @pytest.mark.asyncio
    async def test_tick_sets_exhaustion_flag_and_evaluator_stops(self):
        rail = TaskCompletionRail()
        budget = {"remaining": 5}
        rail.budget_tick = lambda: budget["remaining"]
        names = [e.name for e in rail.build_evaluators()]
        assert names == ["RoundBudgetExhaustedEvaluator"]
        evaluator = rail.build_evaluators()[0]

        ctx = _make_ctx()
        await rail.after_react_iteration(ctx)
        assert rail._budget_total_exhausted is False
        assert not evaluator.should_stop(StopEvaluationContext())

        budget["remaining"] = 0
        await rail.after_react_iteration(ctx)
        assert rail._budget_total_exhausted is True
        assert evaluator.should_stop(StopEvaluationContext())

    @pytest.mark.asyncio
    async def test_total_exhaustion_force_finishes_once(self):
        """总量触顶就地止血：下个内层迭代边界收场（不等外层评估器）。"""
        rail = TaskCompletionRail()
        rail.budget_tick = lambda: 0
        ctx = _make_ctx()
        await rail.after_react_iteration(ctx)
        assert rail._budget_total_exhausted
        assert ctx.has_force_finish_request
        assert ctx.consume_force_finish().result["budget_stop"] == "budget_total"
        await rail.after_react_iteration(ctx)  # 不重复请求
        assert not ctx.has_force_finish_request

    @pytest.mark.asyncio
    async def test_total_force_finish_rearms_per_outer_round(self):
        """耗尽后每个外层轮重新武装一次止血，而不是只认首次跨零。"""
        rail = TaskCompletionRail()
        rail.budget_tick = lambda: 0
        ctx = _make_ctx()

        await rail.before_invoke(ctx)  # round 1
        await rail.after_react_iteration(ctx)
        assert ctx.consume_force_finish().result["budget_stop"] == "budget_total"

        await rail.before_invoke(ctx)  # round 2 — re-armed
        await rail.after_react_iteration(ctx)
        assert ctx.has_force_finish_request

    @pytest.mark.asyncio
    async def test_refresh_budget_state_uses_probe_without_tick(self):
        """结算点探针只置位耗尽标志，不计数。"""
        ticks: list[int] = []
        rail = TaskCompletionRail()
        rail.budget_tick = lambda: ticks.append(1) or 5
        rail.budget_probe = lambda: 0

        rail.refresh_budget_state()

        assert rail.total_budget_exhausted is True
        assert ticks == []

    @pytest.mark.asyncio
    async def test_tick_failure_is_fail_open(self):
        rail = TaskCompletionRail()

        def _boom() -> Optional[int]:
            raise RuntimeError("ledger exploded")

        rail.budget_tick = _boom
        ctx = _make_ctx()
        await rail.after_react_iteration(ctx)  # must not raise
        assert rail._budget_total_exhausted is False

    def test_member_pool_exhausted_wraps_up_while_reserve_remains(self):
        """成员池到顶即欠收尾轮，即使 leader 预留仍有余量。"""
        rail = TaskCompletionRail(enable_budget_wrap_up=True)
        rail.budget_tick = lambda: 10  # leader 预留仍可用
        assert not rail.budget_wrap_up_pending

        rail.mark_member_budget_exhausted()
        assert rail.budget_wrap_up_pending
        evaluator = [
            e for e in rail.build_evaluators()
            if e.name == "RoundBudgetExhaustedEvaluator"
        ][0]
        assert not evaluator.should_stop(StopEvaluationContext())
        rail._wrap_up_consumed = True
        assert evaluator.should_stop(StopEvaluationContext())

    def test_no_hooks_no_evaluators(self):
        assert TaskCompletionRail(max_rounds=3).build_evaluators()[0].name == (
            "MaxRoundsEvaluator"
        )
        assert TaskCompletionRail().build_evaluators() == []


class TestWrapUpRound:
    """触顶后一次收尾外层轮 + 宽限迭代数 + 扩张工具闸。"""

    @pytest.mark.asyncio
    async def test_wrap_up_entry_once_and_callback(self):
        calls = []

        async def _on_wrap_up() -> list[str]:
            calls.append(1)
            return ["t1", "t2"]

        rail = TaskCompletionRail(iteration_budget=2, enable_budget_wrap_up=True)
        rail.on_budget_wrap_up = _on_wrap_up
        ctx = _make_ctx()

        await rail.before_task_iteration(ctx)  # round 1 starts
        assert rail._in_wrap_up_round is False
        for _ in range(3):  # inner iterations exceed budget
            await rail.after_react_iteration(ctx)
        assert rail._iteration_cap_hit
        assert rail.budget_wrap_up_pending

        await rail.before_task_iteration(ctx)  # round 2 = wrap-up round
        assert rail._in_wrap_up_round is True
        assert calls == [1]
        text = _builder(ctx).get_section(SectionName.ITERATION_BUDGET).render("cn")
        assert "预算用完，进入收尾" in text
        assert "t1, t2" in text  # unfinished task list is injected

        await rail.before_task_iteration(ctx)  # no re-entry
        assert calls == [1]

    @pytest.mark.asyncio
    async def test_prepare_wrap_up_round_is_one_shot_and_skips_section(self):
        """触发侧预消费收尾槽后，fallback 入口不再回调/挂 section。"""
        calls: list[int] = []

        async def _on_wrap_up():
            calls.append(1)
            return {
                "repooled": ["t1"],
                "counts": {"pending": 1, "blocked": 2, "completed": 0},
            }

        rail = TaskCompletionRail(enable_budget_wrap_up=True)
        rail.on_budget_wrap_up = _on_wrap_up
        rail.mark_member_budget_exhausted()
        ctx = _make_ctx()

        payload = await rail.prepare_wrap_up_round()
        assert payload["repooled"] == ["t1"]
        assert rail._in_wrap_up_round is True
        assert rail.budget_wrap_up_consumed is True

        assert await rail.prepare_wrap_up_round() is None  # 一次性
        await rail.before_task_iteration(ctx)  # fallback 入口：已消费即跳过
        assert calls == [1]
        assert not _builder(ctx).has_section(SectionName.ITERATION_BUDGET)

    @pytest.mark.asyncio
    async def test_wrap_up_prompt_uses_plain_words_and_board_counts(self):
        """收尾提示说人话：退回待办 + 任务板计数，不写“回池”。"""

        async def _on_wrap_up():
            return {
                "repooled": ["p1-policy", "p1-data"],
                "counts": {"pending": 2, "blocked": 6, "completed": 0},
            }

        rail = TaskCompletionRail(enable_budget_wrap_up=True)
        rail.on_budget_wrap_up = _on_wrap_up
        rail.mark_member_budget_exhausted()
        ctx = _make_ctx()
        await rail.before_task_iteration(ctx)

        assert rail._wrap_up_repooled == ["p1-policy", "p1-data"]
        assert rail._wrap_up_task_counts["blocked"] == 6
        text = _builder(ctx).get_section(
            SectionName.ITERATION_BUDGET
        ).render("cn")
        assert "退回待办清单" in text
        assert "待办 2" in text
        assert "被前置任务卡住 6" in text
        assert "回池" not in text
        assert "进行中" in text  # 明确要求别把没做完的写成进行中

    @pytest.mark.asyncio
    async def test_wrap_up_entry_on_total_exhaustion(self):
        rail = TaskCompletionRail(enable_budget_wrap_up=True)
        rail.budget_tick = lambda: 0  # total exhausted immediately
        ctx = _make_ctx()
        await rail.before_task_iteration(ctx)  # round 1 (nothing yet)
        assert rail._in_wrap_up_round is False
        await rail.after_react_iteration(ctx)  # tick -> exhausted
        await rail.before_task_iteration(ctx)  # round 2 = wrap-up
        assert rail._in_wrap_up_round is True

    @pytest.mark.asyncio
    async def test_wrap_up_entry_on_member_pool_exhaustion(self):
        """成员池到顶即进入收尾轮（leader 预留仍在）。"""
        rail = TaskCompletionRail(enable_budget_wrap_up=True)
        rail.budget_tick = lambda: 10  # 预留仍可用，绝对 total 未耗尽
        ctx = _make_ctx()
        await rail.before_task_iteration(ctx)
        assert rail._in_wrap_up_round is False
        rail.mark_member_budget_exhausted()
        await rail.before_task_iteration(ctx)
        assert rail._in_wrap_up_round is True
        assert rail._wrap_up_consumed is True

    @pytest.mark.asyncio
    async def test_wrap_up_grace_force_finish(self):
        rail = TaskCompletionRail(iteration_budget=1, enable_budget_wrap_up=True)
        ctx = _make_ctx()
        await rail.before_task_iteration(ctx)
        for _ in range(2):
            await rail.after_react_iteration(ctx)
        ctx.consume_force_finish()  # cap force finish
        await rail.before_task_iteration(ctx)  # wrap-up round
        assert rail._in_wrap_up_round
        for _ in range(_WRAP_UP_GRACE_ITERATIONS + 1):
            await rail.after_react_iteration(ctx)
        assert ctx.has_force_finish_request
        assert ctx.consume_force_finish().result["budget_stop"] == (
            "wrap_up_grace"
        )

    @pytest.mark.asyncio
    async def test_tool_gate_blocks_expansion_only_in_wrap_up(self):
        rail = TaskCompletionRail(iteration_budget=1, enable_budget_wrap_up=True)
        ctx = _make_ctx()

        def _tool_ctx(name: str, args=None) -> AgentCallbackContext:
            return AgentCallbackContext(
                agent=ctx.agent,
                inputs=ToolCallInputs(
                    tool_call=SimpleNamespace(id="tc1", name=name),
                    tool_name=name,
                    tool_args=args,
                ),
            )

        outside = _tool_ctx("spawn_teammate")
        await rail.before_tool_call(outside)
        assert "_skip_tool" not in outside.extra

        await rail.before_task_iteration(ctx)
        for _ in range(2):
            await rail.after_react_iteration(ctx)
        await rail.before_task_iteration(ctx)  # wrap-up round
        assert rail._in_wrap_up_round

        blocked = _tool_ctx("spawn_teammate")
        await rail.before_tool_call(blocked)
        assert blocked.extra["_skip_tool"] is True
        assert blocked.inputs.tool_msg is not None

        blocked_create = _tool_ctx("create_task")
        await rail.before_tool_call(blocked_create)
        assert blocked_create.extra["_skip_tool"] is True

        reassign = _tool_ctx("update_task", {"task_id": "t1", "assignee": "m2"})
        await rail.before_tool_call(reassign)
        assert reassign.extra.get("_skip_tool") is True

        add_dep = _tool_ctx(
            "update_task", {"task_id": "t1", "add_blocked_by": ["t2"]},
        )
        await rail.before_tool_call(add_dep)
        assert add_dep.extra.get("_skip_tool") is True

        status_only = _tool_ctx("update_task", {"task_id": "t1", "status": "COMPLETED"})
        await rail.before_tool_call(status_only)
        assert "_skip_tool" not in status_only.extra

        allowed = _tool_ctx("read_file")
        await rail.before_tool_call(allowed)
        assert "_skip_tool" not in allowed.extra

    @pytest.mark.asyncio
    async def test_tool_gate_activates_after_member_pool_exhausted(self):
        """成员池到顶后，leader 预留轮也不得再派活/加依赖。"""
        rail = TaskCompletionRail(enable_budget_wrap_up=True)
        ctx = _make_ctx()

        def _tool_ctx(name: str, args=None) -> AgentCallbackContext:
            return AgentCallbackContext(
                agent=ctx.agent,
                inputs=ToolCallInputs(
                    tool_call=SimpleNamespace(id="tc1", name=name),
                    tool_name=name,
                    tool_args=args,
                ),
            )

        before = _tool_ctx("create_task")
        await rail.before_tool_call(before)
        assert "_skip_tool" not in before.extra

        rail.mark_member_budget_exhausted()
        assert rail._in_wrap_up_round is False

        blocked_calls = [
            ("create_task", None),
            ("send_message", None),
            ("spawn_teammate", None),
            ("update_task", {"task_id": "t1", "assignee": "m2"}),
            ("update_task", {"task_id": "t1", "reviewer": "m3"}),
            ("update_task", {"task_id": "t1", "add_blocked_by": ["t2"]}),
            ("update_task", {"task_id": "t1", "remove_blocked_by": ["t2"]}),
        ]
        for name, args in blocked_calls:
            item = _tool_ctx(name, args)
            await rail.before_tool_call(item)
            assert item.extra.get("_skip_tool") is True, name

        allowed_calls = [
            ("read_file", None),
            ("view_task", {"action": "list"}),
            ("update_task", {"task_id": "t1", "status": "cancelled"}),
        ]
        for name, args in allowed_calls:
            item = _tool_ctx(name, args)
            await rail.before_tool_call(item)
            assert "_skip_tool" not in item.extra, name


class TestBudgetStopTagging:
    """_tag_budget_stop_result mapping (deep_agent)."""

    def _tag(self, stop_reason: Optional[str], result):
        from openjiuwen.harness.deep_agent import DeepAgent

        fake_self = SimpleNamespace(_last_loop_stop_reason=stop_reason)
        return DeepAgent._tag_budget_stop_result(fake_self, result)

    def test_iteration_cap_tagged_max_rounds(self):
        result = self._tag("IterationBudgetExhaustedEvaluator", {"output": "x"})
        assert result["stop_reason"] == "max_rounds"

    def test_budget_total_tagged(self):
        result = self._tag("RoundBudgetExhaustedEvaluator", {"output": "x"})
        assert result["stop_reason"] == "budget_total"

    def test_other_reasons_untouched(self):
        assert self._tag(None, {"output": "x"}) == {"output": "x"}
        assert self._tag("IterationBudgetExhaustedEvaluator", None) is None


class TestConfigChain:
    """field defaults and spec serialization."""

    def test_config_defaults_none(self):
        from openjiuwen.harness.schema.config import DeepAgentConfig

        assert DeepAgentConfig().task_loop_iteration_budget is None

    def test_spec_field_round_trip(self):
        from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec

        spec = DeepAgentSpec(task_loop_iteration_budget=200)
        assert spec.task_loop_iteration_budget == 200
        dumped = spec.model_dump_json()
        assert "task_loop_iteration_budget" in dumped
