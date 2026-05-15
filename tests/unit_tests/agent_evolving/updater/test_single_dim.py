# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for updater protocol and SingleDimUpdater."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
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
        assert hasattr(Updater, "process")
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

    @staticmethod
    def test_process_uses_signal_first_flow():
        """process() consumes signals directly and keeps trajectory ordering."""
        expected_updates = {("op1", "prompt"): "new prompt"}
        mock_optimizer = make_mock_optimizer(step_return=expected_updates)
        updater = SingleDimUpdater(optimizer=mock_optimizer)

        trajectories = [MagicMock(), MagicMock()]
        signals = [
            EvolutionSignal(
                signal_type="low_score",
                section="Troubleshooting",
                excerpt="score=0.00",
            )
        ]

        result = asyncio.run(updater.process(trajectories=trajectories, signals=signals, config={}))

        assert mock_optimizer.add_trajectory.call_count == 2
        mock_optimizer.backward.assert_called_once_with(signals)
        mock_optimizer.step.assert_called_once()
        assert result is expected_updates

    @staticmethod
    def test_update_adapts_evaluated_cases_to_process():
        """update() remains a compatibility wrapper over process()."""
        expected_updates = {("op1", "prompt"): "new prompt"}
        mock_optimizer = make_mock_optimizer(step_return=expected_updates)
        updater = SingleDimUpdater(optimizer=mock_optimizer)

        case = Case(
            inputs={"query": "q"},
            label={"answer": "a"},
            case_id="case-1",
        )
        evaluated_case = EvaluatedCase(case=case, answer={"output": "pred"}, score=0.0)

        result = asyncio.run(updater.update(trajectories=[], evaluated_cases=[evaluated_case], config={}))

        assert mock_optimizer.backward.call_count == 1
        assert result is expected_updates

    @staticmethod
    def test_update_respects_score_threshold_from_config():
        """update() should preserve offline filtering config during signal conversion."""
        expected_updates = {("op1", "prompt"): "new prompt"}
        mock_optimizer = make_mock_optimizer(step_return=expected_updates)
        updater = SingleDimUpdater(optimizer=mock_optimizer)

        case = Case(inputs={"query": "q"}, label={"answer": "a"}, case_id="case-1")
        high_score = EvaluatedCase(case=case, answer={"output": "good"}, score=1.0)
        low_score = EvaluatedCase(case=case, answer={"output": "bad"}, score=0.0)

        result = asyncio.run(
            updater.update(
                trajectories=[],
                evaluated_cases=[high_score, low_score],
                config={"score_threshold": 1.0},
            )
        )

        mock_optimizer.backward.assert_called_once()
        passed_signals = mock_optimizer.backward.call_args.args[0]
        assert len(passed_signals) == 1
        assert passed_signals[0].signal_type == "low_score"
        assert result is expected_updates

    @staticmethod
    def test_process_is_accepted_by_protocol_mock():
        """Process entry should be available on Updater-compatible mocks."""
        mock_updater = MagicMock(spec=Updater)
        mock_updater.process = AsyncMock(return_value={})

        result = asyncio.run(
            mock_updater.process(
                trajectories=[],
                signals=[
                    EvolutionSignal(
                        signal_type="low_score",
                        section="Troubleshooting",
                        excerpt="score=0.00",
                    )
                ],
                config={},
            )
        )

        assert result == {}
