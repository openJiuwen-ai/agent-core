# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for BudgetNoticeRail and its loop-budget plumbing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.budget_notice import (
    build_budget_notice_section,
)
from openjiuwen.harness.rails import BudgetNoticeRail
from openjiuwen.harness.schema.stop_condition import (
    BudgetLimit,
    CompletionPromiseEvaluator,
    MaxRoundsEvaluator,
    TokenBudgetEvaluator,
    TimeoutEvaluator,
)
from openjiuwen.harness.task_loop.loop_coordinator import LoopCoordinator


class _FakePromptBuilder:
    """Minimal SystemPromptBuilder stand-in that records section changes."""

    def __init__(self, language: str = "en") -> None:
        self.language = language
        self.sections: dict[str, object] = {}
        self.added: list[str] = []
        self.removed: list[str] = []

    def add_section(self, section) -> None:
        self.sections[section.name] = section
        self.added.append(section.name)

    def remove_section(self, name) -> None:
        self.sections.pop(name, None)
        self.removed.append(name)


def _make_rail(
    builder: _FakePromptBuilder,
    coordinator: LoopCoordinator | None,
    **kwargs,
) -> BudgetNoticeRail:
    rail = BudgetNoticeRail(**kwargs)
    agent = SimpleNamespace(
        system_prompt_builder=builder,
        loop_coordinator=coordinator,
    )
    rail.init(agent)
    return rail


def _ctx(builder: _FakePromptBuilder, coordinator: LoopCoordinator | None):
    return SimpleNamespace(
        agent=SimpleNamespace(loop_coordinator=coordinator),
    )


class TestBudgetLimitPlumbing(IsolatedAsyncioTestCase):
    """Evaluators expose their limits; the coordinator aggregates them."""

    def test_resource_evaluators_expose_budget_limits(self) -> None:
        self.assertEqual(
            MaxRoundsEvaluator(20).budget(), BudgetLimit("rounds", 20.0)
        )
        self.assertEqual(
            TokenBudgetEvaluator(1000).budget(), BudgetLimit("tokens", 1000.0)
        )
        self.assertEqual(
            TimeoutEvaluator(60).budget(), BudgetLimit("seconds", 60.0)
        )

    def test_predicate_evaluator_has_no_budget(self) -> None:
        self.assertIsNone(CompletionPromiseEvaluator("done").budget())

    def test_coordinator_aggregates_budget_limits(self) -> None:
        coordinator = LoopCoordinator(
            [
                MaxRoundsEvaluator(20),
                TokenBudgetEvaluator(1000),
                TimeoutEvaluator(60),
                CompletionPromiseEvaluator("done"),
            ]
        )
        self.assertEqual(
            coordinator.budget_limits(),
            (
                BudgetLimit("rounds", 20.0),
                BudgetLimit("tokens", 1000.0),
                BudgetLimit("seconds", 60.0),
            ),
        )

    def test_coordinator_usage_accessors(self) -> None:
        coordinator = LoopCoordinator([TokenBudgetEvaluator(1000)])
        coordinator.reset()
        coordinator.increment_iteration()
        coordinator.add_token_usage(300)
        self.assertEqual(coordinator.current_iteration, 1)
        self.assertEqual(coordinator.token_usage, 300)
        self.assertGreaterEqual(coordinator.elapsed_seconds, 0.0)


class TestBudgetNoticeSection(IsolatedAsyncioTestCase):
    """Section builder renders only when there is a notice, with i18n."""

    def test_empty_notices_returns_none(self) -> None:
        self.assertIsNone(build_budget_notice_section("en", []))

    def test_renders_english_and_chinese(self) -> None:
        notice = {"kind": "tokens", "used": 850, "limit": 1000, "remaining": 150}
        en = build_budget_notice_section("en", [notice])
        cn = build_budget_notice_section("cn", [notice])
        self.assertIsNotNone(en)
        self.assertIsNotNone(cn)
        assert en is not None and cn is not None
        self.assertEqual(en.name, SectionName.BUDGET_NOTICE)
        self.assertIn("Token budget", en.render("en"))
        self.assertIn("Token 预算", cn.render("cn"))


class TestBudgetNoticeRail(IsolatedAsyncioTestCase):
    """Rail injects a notice exactly when a loop budget is near its limit."""

    async def test_warns_when_tokens_near_limit(self) -> None:
        builder = _FakePromptBuilder()
        coordinator = LoopCoordinator([TokenBudgetEvaluator(1000)])
        coordinator.reset()
        coordinator.add_token_usage(900)  # 10% left, default token ratio 15%
        rail = _make_rail(builder, coordinator)
        await rail.before_model_call(_ctx(builder, coordinator))
        self.assertIn(SectionName.BUDGET_NOTICE, builder.sections)

    async def test_no_warning_when_budget_is_healthy(self) -> None:
        builder = _FakePromptBuilder()
        coordinator = LoopCoordinator([TokenBudgetEvaluator(1000)])
        coordinator.reset()
        coordinator.add_token_usage(100)  # 90% left
        rail = _make_rail(builder, coordinator)
        await rail.before_model_call(_ctx(builder, coordinator))
        self.assertNotIn(SectionName.BUDGET_NOTICE, builder.sections)

    async def test_round_remaining_absolute_threshold(self) -> None:
        builder = _FakePromptBuilder()
        coordinator = LoopCoordinator([MaxRoundsEvaluator(20)])
        coordinator.reset()
        for _ in range(12):
            coordinator.increment_iteration()  # 8 rounds left
        rail = _make_rail(builder, coordinator, round_remaining=10)
        await rail.before_model_call(_ctx(builder, coordinator))
        self.assertIn(SectionName.BUDGET_NOTICE, builder.sections)

    async def test_disabled_rail_never_injects(self) -> None:
        builder = _FakePromptBuilder()
        coordinator = LoopCoordinator([MaxRoundsEvaluator(20)])
        coordinator.reset()
        for _ in range(19):
            coordinator.increment_iteration()
        rail = _make_rail(builder, coordinator, enabled=False)
        await rail.before_model_call(_ctx(builder, coordinator))
        self.assertNotIn(SectionName.BUDGET_NOTICE, builder.sections)

    async def test_stale_section_removed_when_budget_recovers(self) -> None:
        builder = _FakePromptBuilder()
        coordinator = LoopCoordinator([TokenBudgetEvaluator(1000)])
        coordinator.reset()
        coordinator.add_token_usage(900)
        rail = _make_rail(builder, coordinator)
        await rail.before_model_call(_ctx(builder, coordinator))
        self.assertIn(SectionName.BUDGET_NOTICE, builder.sections)

        # Budget no longer configured -> stale section must be removed.
        rail._remove_section()
        await rail.before_model_call(_ctx(builder, LoopCoordinator([])))
        self.assertNotIn(SectionName.BUDGET_NOTICE, builder.sections)

    async def test_before_invoke_clears_stale_notice(self) -> None:
        builder = _FakePromptBuilder()
        coordinator = LoopCoordinator([TokenBudgetEvaluator(1000)])
        coordinator.reset()
        coordinator.add_token_usage(950)
        rail = _make_rail(builder, coordinator)
        await rail.before_model_call(_ctx(builder, coordinator))
        self.assertIn(SectionName.BUDGET_NOTICE, builder.sections)
        await rail.before_invoke(_ctx(builder, coordinator))
        self.assertNotIn(SectionName.BUDGET_NOTICE, builder.sections)

    async def test_uninit_removes_section(self) -> None:
        builder = _FakePromptBuilder()
        coordinator = LoopCoordinator([TokenBudgetEvaluator(1000)])
        coordinator.reset()
        coordinator.add_token_usage(950)
        rail = _make_rail(builder, coordinator)
        await rail.before_model_call(_ctx(builder, coordinator))
        rail.uninit(SimpleNamespace(system_prompt_builder=builder))
        self.assertNotIn(SectionName.BUDGET_NOTICE, builder.sections)
