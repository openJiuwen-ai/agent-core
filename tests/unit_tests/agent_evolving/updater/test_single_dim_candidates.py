# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for SingleDimUpdater candidate-list passthrough and state delegation.

Covers the contract that SkillDocumentOptimizer relies on:
- process() and update() pass through list returns (candidate gate)
- process() and update() pass through dict returns (standard path)
- get_state/load_state delegate with non-empty state
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.agent_evolving.optimizer import BaseOptimizer
from openjiuwen.agent_evolving.updater.single_dim import SingleDimUpdater


def _make_mock_optimizer(step_return=None):
    """Factory for mock optimizer with async backward."""
    mock = MagicMock()
    mock.bind.return_value = 1
    mock.backward = AsyncMock(return_value=None)
    if step_return is not None:
        mock.step.return_value = step_return
    return mock


# ── process() return type passthrough ────────────────────────────────────


class TestProcessReturnType:
    @staticmethod
    def test_returns_dict_when_optimizer_returns_dict():
        """process() passes through dict from optimizer.step()."""
        updates = {("op1", "target"): "value"}
        opt = _make_mock_optimizer(step_return=updates)
        updater = SingleDimUpdater(optimizer=opt)

        result = asyncio.run(updater.process(trajectories=[], signals=[], config={}))

        assert isinstance(result, dict)
        assert result == updates

    @staticmethod
    def test_returns_list_when_optimizer_returns_list():
        """process() passes through list from optimizer.step() (candidate gate)."""
        candidates = [
            {("op1", "target"): "base_value"},
            {("op1", "target"): "new_value"},
        ]
        opt = _make_mock_optimizer(step_return=candidates)
        updater = SingleDimUpdater(optimizer=opt)

        result = asyncio.run(updater.process(trajectories=[], signals=[], config={}))

        assert isinstance(result, list)
        assert len(result) == 2

    @staticmethod
    def test_list_candidates_have_correct_tuple_keys():
        """List candidates use (str, str) tuple keys."""
        candidates = [
            {("skill_document_test", "skill_content"): "base skill"},
            {("skill_document_test", "skill_content"): "updated skill"},
        ]
        opt = _make_mock_optimizer(step_return=candidates)
        updater = SingleDimUpdater(optimizer=opt)

        result = asyncio.run(updater.process(trajectories=[], signals=[], config={}))

        assert isinstance(result, list)
        for candidate in result:
            for key in candidate:
                assert isinstance(key, tuple)
                assert len(key) == 2
                assert isinstance(key[0], str)
                assert isinstance(key[1], str)

    @staticmethod
    def test_single_candidate_list():
        """process() passes through a single-element candidate list (R3: no change)."""
        candidates = [{("op1", "target"): "unchanged"}]
        opt = _make_mock_optimizer(step_return=candidates)
        updater = SingleDimUpdater(optimizer=opt)

        result = asyncio.run(updater.process(trajectories=[], signals=[], config={}))

        assert isinstance(result, list)
        assert len(result) == 1


# ── update() return type passthrough ─────────────────────────────────────


class TestUpdateReturnType:
    @staticmethod
    def test_returns_dict_passthrough():
        """update() passes through dict from optimizer.step()."""
        updates = {("op1", "prompt"): "new prompt"}
        opt = _make_mock_optimizer(step_return=updates)
        updater = SingleDimUpdater(optimizer=opt)

        result = asyncio.run(updater.update(trajectories=[], evaluated_cases=[], config={}))

        assert isinstance(result, dict)
        assert result is updates

    @staticmethod
    def test_returns_list_passthrough():
        """update() passes through list from optimizer.step()."""
        candidates = [
            {("op1", "skill_content"): "base"},
            {("op1", "skill_content"): "candidate"},
        ]
        opt = _make_mock_optimizer(step_return=candidates)
        updater = SingleDimUpdater(optimizer=opt)

        result = asyncio.run(updater.update(trajectories=[], evaluated_cases=[], config={}))

        assert isinstance(result, list)
        assert result is candidates

    @staticmethod
    def test_list_with_two_candidates():
        """update() returns two-element list (base + modified)."""
        candidates = [
            {("skill_document_my_skill", "skill_content"): "original skill"},
            {("skill_document_my_skill", "skill_content"): "improved skill"},
        ]
        opt = _make_mock_optimizer(step_return=candidates)
        updater = SingleDimUpdater(optimizer=opt)

        result = asyncio.run(updater.update(trajectories=[], evaluated_cases=[], config={}))

        assert isinstance(result, list)
        assert len(result) == 2
        # Base candidate
        base_key = ("skill_document_my_skill", "skill_content")
        assert result[0][base_key] == "original skill"
        # Modified candidate
        assert result[1][base_key] == "improved skill"


# ── State delegation ─────────────────────────────────────────────────────


class TestStateDelegation:
    @staticmethod
    def test_get_state_returns_optimizer_state():
        """get_state() delegates and returns non-empty optimizer state."""
        state_data = {
            "global_step": 5,
            "step_buffer": [{"step": 0, "n_edits": 3}],
            "meta_skill_context": "focus on errors",
        }
        opt = _make_mock_optimizer()
        opt.get_state.return_value = state_data
        updater = SingleDimUpdater(optimizer=opt)

        result = updater.get_state()

        assert result == state_data
        assert result["global_step"] == 5
        opt.get_state.assert_called_once()

    @staticmethod
    def test_load_state_delegates_to_optimizer():
        """load_state() delegates state dict to optimizer."""
        state_data = {"global_step": 10, "scheduler": {"current_step": 3}}
        opt = _make_mock_optimizer()
        updater = SingleDimUpdater(optimizer=opt)

        updater.load_state(state_data)

        opt.load_state.assert_called_once_with(state_data)

    @staticmethod
    def test_get_state_empty_for_base_optimizer():
        """Real BaseOptimizer.get_state() returns {} (backward compat)."""

        class PlainOptimizer(BaseOptimizer):
            domain = "test"

            async def _backward(self, signals):
                pass

            def _step(self):
                return {}

        updater = SingleDimUpdater(optimizer=PlainOptimizer())
        assert updater.get_state() == {}

    @staticmethod
    def test_load_state_noop_for_base_optimizer():
        """Real BaseOptimizer.load_state() is a no-op (backward compat)."""

        class PlainOptimizer(BaseOptimizer):
            domain = "test"

            async def _backward(self, signals):
                pass

            def _step(self):
                return {}

        plain = PlainOptimizer()
        updater = SingleDimUpdater(optimizer=plain)
        updater.load_state({"key": "value"})
        # Should not raise; state remains empty
        assert updater.get_state() == {}
