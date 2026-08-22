# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_multi_rollout — MultiRolloutExecutor 单元测试。"""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.harness.multi_rollout.config import MultiRolloutConfig
from openjiuwen.harness.multi_rollout.executor import MultiRolloutExecutor
from openjiuwen.harness.multi_rollout.selector import (
    FirstSuccessfulSelector,
    LongestOutputSelector,
    RolloutResult,
    ShortestOutputSelector,
    get_selector,
)


class TestMultiRolloutConfig(IsolatedAsyncioTestCase):
    def test_defaults(self):
        cfg = MultiRolloutConfig()
        assert cfg.enabled is False
        assert cfg.n_rollouts == 3
        assert cfg.max_parallel == 0
        assert cfg.timeout_per_rollout == 600.0
        assert len(cfg.strategy_variants) == 3
        assert cfg.selector_kind == "first_successful"

    def test_enabled_requires_n_rollouts(self):
        cfg = MultiRolloutConfig(enabled=True, n_rollouts=1)
        executor = MultiRolloutExecutor(MagicMock(), cfg)
        assert executor.is_enabled() is False  # n_rollouts must be > 1


class TestRolloutResult(IsolatedAsyncioTestCase):
    def test_success(self):
        r = RolloutResult(result={"output": "hello"}, attempt_index=0)
        assert r.is_success is True
        assert r.output_text == "hello"

    def test_failure(self):
        r = RolloutResult(
            result=None, attempt_index=0, exception=RuntimeError("boom")
        )
        assert r.is_success is False
        assert r.output_text == ""

    def test_extract_keys(self):
        r = RolloutResult(result={"content": "c", "query": "q"}, attempt_index=0)
        assert r.output_text == "c"  # content preferred over query


class TestFirstSuccessfulSelector(IsolatedAsyncioTestCase):
    def test_selects_first_success(self):
        candidates = [
            RolloutResult(result=None, attempt_index=0, exception=RuntimeError("fail")),
            RolloutResult(result={"output": "ok"}, attempt_index=1),
            RolloutResult(result={"output": "better"}, attempt_index=2),
        ]
        sel = FirstSuccessfulSelector()
        best = sel.select(candidates)
        assert best.attempt_index == 1

    def test_all_failed_returns_first(self):
        candidates = [
            RolloutResult(result=None, attempt_index=0, exception=RuntimeError("a")),
            RolloutResult(result=None, attempt_index=1, exception=RuntimeError("b")),
        ]
        sel = FirstSuccessfulSelector()
        best = sel.select(candidates)
        assert best.attempt_index == 0

    def test_empty_raises(self):
        sel = FirstSuccessfulSelector()
        with self.assertRaises(ValueError):
            sel.select([])


class TestLongestOutputSelector(IsolatedAsyncioTestCase):
    def test_selects_longest(self):
        candidates = [
            RolloutResult(result={"output": "short"}, attempt_index=0),
            RolloutResult(result={"output": "this is much longer text"}, attempt_index=1),
            RolloutResult(result={"output": "mid"}, attempt_index=2),
        ]
        sel = LongestOutputSelector()
        best = sel.select(candidates)
        assert best.attempt_index == 1

    def test_skips_failed(self):
        candidates = [
            RolloutResult(result=None, attempt_index=0, exception=RuntimeError("fail")),
            RolloutResult(result={"output": "x"}, attempt_index=1),
        ]
        sel = LongestOutputSelector()
        best = sel.select(candidates)
        assert best.attempt_index == 1

    def test_all_failed_returns_first(self):
        candidates = [
            RolloutResult(result=None, attempt_index=0, exception=RuntimeError("a")),
        ]
        sel = LongestOutputSelector()
        best = sel.select(candidates)
        assert best.attempt_index == 0


class TestShortestOutputSelector(IsolatedAsyncioTestCase):
    def test_selects_shortest(self):
        candidates = [
            RolloutResult(result={"output": "long text here"}, attempt_index=0),
            RolloutResult(result={"output": "s"}, attempt_index=1),
            RolloutResult(result={"output": "medium"}, attempt_index=2),
        ]
        sel = ShortestOutputSelector()
        best = sel.select(candidates)
        assert best.attempt_index == 1


class TestGetSelector(IsolatedAsyncioTestCase):
    def test_known_selectors(self):
        assert isinstance(get_selector("first_successful"), FirstSuccessfulSelector)
        assert isinstance(get_selector("longest_output"), LongestOutputSelector)
        assert isinstance(get_selector("shortest_output"), ShortestOutputSelector)

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            get_selector("nonexistent")


class TestMultiRolloutExecutor(IsolatedAsyncioTestCase):
    async def test_disabled_delegates_to_parent(self):
        parent = MagicMock()
        parent.invoke = AsyncMock(return_value={"output": "parent"})
        cfg = MultiRolloutConfig(enabled=False)
        executor = MultiRolloutExecutor(parent, cfg)

        result = await executor.invoke({"query": "test"})

        assert result == {"output": "parent"}
        parent.invoke.assert_awaited_once_with({"query": "test"}, None)
        parent.create_subagent.assert_not_called()

    async def test_enabled_spawns_and_selects(self):
        parent = MagicMock()
        parent.invoke = AsyncMock(return_value={"output": "parent"})

        # Create 3 mock subagents that return different results
        subagents = []
        for i in range(3):
            sub = MagicMock()
            sub.invoke = AsyncMock(return_value={"output": f"sub-{i}"})
            subagents.append(sub)

        parent.create_subagent = MagicMock(side_effect=subagents)

        cfg = MultiRolloutConfig(enabled=True, n_rollouts=3, timeout_per_rollout=5.0)
        executor = MultiRolloutExecutor(parent, cfg)

        result = await executor.invoke({"query": "fix bug"})

        # Should create 3 subagents
        assert parent.create_subagent.call_count == 3

        # Should select first successful (default selector)
        assert result == {"output": "sub-0"}

    async def test_picks_first_success_when_some_fail(self):
        parent = MagicMock()

        subagents = []
        for i in range(3):
            sub = MagicMock()
            if i == 0:
                sub.invoke = AsyncMock(side_effect=RuntimeError("fail"))
            else:
                sub.invoke = AsyncMock(return_value={"output": f"sub-{i}"})
            subagents.append(sub)

        parent.create_subagent = MagicMock(side_effect=subagents)

        cfg = MultiRolloutConfig(enabled=True, n_rollouts=3)
        executor = MultiRolloutExecutor(parent, cfg)

        result = await executor.invoke({"query": "fix bug"})

        assert result == {"output": "sub-1"}

    async def test_all_fail_raises(self):
        parent = MagicMock()

        subagents = []
        for i in range(2):
            sub = MagicMock()
            sub.invoke = AsyncMock(side_effect=RuntimeError(f"fail-{i}"))
            subagents.append(sub)

        parent.create_subagent = MagicMock(side_effect=subagents)

        cfg = MultiRolloutConfig(enabled=True, n_rollouts=2)
        executor = MultiRolloutExecutor(parent, cfg)

        with self.assertRaises(RuntimeError) as ctx:
            await executor.invoke({"query": "fix bug"})
        assert "fail-0" in str(ctx.exception)

    async def test_strategy_prefix_injected(self):
        parent = MagicMock()

        captured_inputs = []

        async def mock_invoke(inputs):
            captured_inputs.append(inputs)
            return {"output": "ok"}

        sub = MagicMock()
        sub.invoke = AsyncMock(side_effect=mock_invoke)

        parent.create_subagent = MagicMock(return_value=sub)

        # n_rollouts must be > 1 for multi-rollout to actually run
        cfg = MultiRolloutConfig(enabled=True, n_rollouts=2)
        executor = MultiRolloutExecutor(parent, cfg)

        await executor.invoke({"query": "fix bug"})

        assert len(captured_inputs) == 2
        for inp in captured_inputs:
            query = inp["query"]
            assert "Approach:" in query
            assert "fix bug" in query
