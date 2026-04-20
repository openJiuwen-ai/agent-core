# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for updater protocol and SingleDimUpdater."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.agent_evolving.updater.protocol import Updater
from openjiuwen.agent_evolving.updater.single_dim import SingleDimUpdater


def make_single_dim_updater():
    """Factory for creating SingleDimUpdater instances."""
    return SingleDimUpdater(optimizer=MagicMock())


def make_mock_optimizer(bind_return=3, step_return=None):
    """Factory for creating mock optimizers (backward is async)."""
    mock = MagicMock()
    mock.bind.return_value = bind_return
    mock.backward = AsyncMock(return_value=None)
    if step_return is not None:
        mock.step.return_value = step_return
    return mock


class TestUpdaterProtocol:
    """Test Updater protocol (interface tests)."""

    @staticmethod
    def test_protocol_defines_required_methods():
        """Protocol defines required methods."""
        assert hasattr(Updater, "bind")
        assert hasattr(Updater, "update")
        assert hasattr(Updater, "get_state")
        assert hasattr(Updater, "load_state")


class TestSingleDimUpdater:
    """Test SingleDimUpdater class."""

    @staticmethod
    def test_bind_delegates_to_optimizer():
        """bind() delegates to optimizer."""
        mock_optimizer = make_mock_optimizer(bind_return=3)
        updater = SingleDimUpdater(optimizer=mock_optimizer)

        operators = {"op1": MagicMock(), "op2": MagicMock()}
        result = updater.bind(operators=operators, targets=["target1"])

        assert result == 3
        mock_optimizer.bind.assert_called_once()

    @staticmethod
    def test_bind_with_none_targets():
        """bind() with None targets uses config.get('targets')."""
        mock_optimizer = make_mock_optimizer(bind_return=2)
        updater = SingleDimUpdater(optimizer=mock_optimizer)

        operators = {"op1": MagicMock()}
        updater.bind(
            operators=operators,
            targets=None,
        )

        call_kwargs = mock_optimizer.bind.call_args.kwargs
        assert "targets" in call_kwargs

    @staticmethod
    def test_update_calls_optimizer_chain():
        """update() calls optimizer chain: add_trajectory -> backward -> step."""
        expected_updates = {("op1", "target"): "new_value"}
        mock_optimizer = make_mock_optimizer(step_return=expected_updates)
        updater = SingleDimUpdater(optimizer=mock_optimizer)

        trajectories = [MagicMock(), MagicMock()]
        evaluated_cases = []  # empty since backward now takes signals

        result = asyncio.run(
            updater.update(trajectories=trajectories, evaluated_cases=evaluated_cases, config={})
        )

        assert mock_optimizer.add_trajectory.call_count == 2
        mock_optimizer.backward.assert_called_once()
        mock_optimizer.step.assert_called_once()
        assert result == expected_updates

    @staticmethod
    def test_update_empty_trajectories():
        """update() with empty trajectories."""
        mock_optimizer = make_mock_optimizer(step_return={})
        updater = SingleDimUpdater(optimizer=mock_optimizer)

        asyncio.run(updater.update(trajectories=[], evaluated_cases=[], config={}))

        mock_optimizer.add_trajectory.assert_not_called()
        mock_optimizer.backward.assert_called_once()
        mock_optimizer.step.assert_called_once()

    @staticmethod
    def test_get_state_returns_empty_dict():
        """get_state() returns empty dict (BaseOptimizer has no stable state)."""
        updater = make_single_dim_updater()
        assert updater.get_state() == {}

    @staticmethod
    def test_load_state_is_noop():
        """load_state() is a no-op."""
        updater = make_single_dim_updater()
        updater.load_state({"key": "value"})

    @staticmethod
    def test_update_preserves_trajectory_order():
        """update() adds trajectories in order."""
        mock_optimizer = make_mock_optimizer(step_return={})
        updater = SingleDimUpdater(optimizer=mock_optimizer)

        traj1 = MagicMock()
        traj2 = MagicMock()
        asyncio.run(updater.update(trajectories=[traj1, traj2], evaluated_cases=[], config={}))

        calls = mock_optimizer.add_trajectory.call_args_list
        assert calls[0][0][0] is traj1
        assert calls[1][0][0] is traj2

    @staticmethod
    def test_update_returns_updates():
        """update() returns updates from optimizer.step()."""
        expected_updates = {("op1", "prompt"): "new prompt"}
        mock_optimizer = make_mock_optimizer(step_return=expected_updates)
        updater = SingleDimUpdater(optimizer=mock_optimizer)

        result = asyncio.run(updater.update(trajectories=[], evaluated_cases=[], config={}))

        assert result is expected_updates
