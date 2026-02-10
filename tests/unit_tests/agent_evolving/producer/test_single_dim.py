# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for producer protocol and SingleDimProducer."""

from unittest.mock import MagicMock

from openjiuwen.agent_evolving.producer.protocol import UpdateProducer
from openjiuwen.agent_evolving.producer.single_dim import SingleDimProducer


def make_single_dim_producer():
    """Factory for creating SingleDimProducer instances."""
    return SingleDimProducer(optimizer=MagicMock())


def make_mock_optimizer(bind_return=3, update_return=None):
    """Factory for creating mock optimizers."""
    mock = MagicMock()
    mock.bind.return_value = bind_return
    if update_return is not None:
        mock.update.return_value = update_return
    return mock


class TestUpdateProducerProtocol:
    """Test UpdateProducer protocol (interface tests)."""

    @staticmethod
    def test_protocol_defines_required_methods():
        """Protocol defines required methods."""
        assert hasattr(UpdateProducer, "bind")
        assert hasattr(UpdateProducer, "produce")
        assert hasattr(UpdateProducer, "get_state")
        assert hasattr(UpdateProducer, "load_state")


class TestSingleDimProducer:
    """Test SingleDimProducer class."""

    @staticmethod
    def test_bind_delegates_to_optimizer():
        """bind() delegates to optimizer."""
        mock_optimizer = make_mock_optimizer(bind_return=3)
        producer = SingleDimProducer(optimizer=mock_optimizer)

        operators = {"op1": MagicMock(), "op2": MagicMock()}
        result = producer.bind(operators=operators, targets=["target1"])

        assert result == 3
        mock_optimizer.bind.assert_called_once()

    @staticmethod
    def test_bind_with_none_targets():
        """bind() with None targets uses config.get('targets')."""
        mock_optimizer = make_mock_optimizer(bind_return=2)
        producer = SingleDimProducer(optimizer=mock_optimizer)

        operators = {"op1": MagicMock()}
        producer.bind(
            operators=operators,
            targets=None,
        )

        call_kwargs = mock_optimizer.bind.call_args.kwargs
        assert "targets" in call_kwargs

    @staticmethod
    def test_produce_calls_optimizer_chain():
        """produce() calls optimizer chain: add_trajectory -> backward -> update."""
        expected_updates = {("op1", "target"): "new_value"}
        mock_optimizer = make_mock_optimizer(update_return=expected_updates)
        producer = SingleDimProducer(optimizer=mock_optimizer)

        trajectories = [MagicMock(), MagicMock()]
        evaluated_cases = [MagicMock()]

        result = producer.produce(trajectories=trajectories, evaluated_cases=evaluated_cases, config={})

        assert mock_optimizer.add_trajectory.call_count == 2
        mock_optimizer.backward.assert_called_once_with(evaluated_cases)
        mock_optimizer.update.assert_called_once()
        assert result == expected_updates

    @staticmethod
    def test_produce_empty_trajectories():
        """produce() with empty trajectories."""
        mock_optimizer = make_mock_optimizer(update_return={})
        producer = SingleDimProducer(optimizer=mock_optimizer)

        producer.produce(trajectories=[], evaluated_cases=[], config={})

        mock_optimizer.add_trajectory.assert_not_called()
        mock_optimizer.backward.assert_called_once()
        mock_optimizer.update.assert_called_once()

    @staticmethod
    def test_get_state_returns_empty_dict():
        """get_state() returns empty dict (BaseOptimizer has no stable state)."""
        producer = make_single_dim_producer()
        assert producer.get_state() == {}

    @staticmethod
    def test_load_state_is_noop():
        """load_state() is a no-op."""
        producer = make_single_dim_producer()
        producer.load_state({"key": "value"})

    @staticmethod
    def test_produce_preserves_trajectory_order():
        """produce() adds trajectories in order."""
        mock_optimizer = make_mock_optimizer(update_return={})
        producer = SingleDimProducer(optimizer=mock_optimizer)

        traj1 = MagicMock()
        traj2 = MagicMock()
        producer.produce(trajectories=[traj1, traj2], evaluated_cases=[], config={})

        calls = mock_optimizer.add_trajectory.call_args_list
        assert calls[0][0][0] is traj1
        assert calls[1][0][0] is traj2

    @staticmethod
    def test_produce_returns_updates():
        """produce() returns updates from optimizer.update()."""
        expected_updates = {("op1", "prompt"): "new prompt"}
        mock_optimizer = make_mock_optimizer(update_return=expected_updates)
        producer = SingleDimProducer(optimizer=mock_optimizer)

        result = producer.produce(trajectories=[], evaluated_cases=[], config={})

        assert result is expected_updates
