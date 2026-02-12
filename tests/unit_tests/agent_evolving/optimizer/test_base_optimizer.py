# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for BaseOptimizer - simplified optimizer base class."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer, TextualParameter


def make_mock_operator(tunables=None, op_id="test_op"):
    """Factory for creating mock operators."""
    op = MagicMock()
    op.operator_id = op_id
    op.get_tunables.return_value = tunables or {"system_prompt": "prompt"}
    op.get_state.return_value = {"system_prompt": "You are helpful."}
    return op


class TestBaseOptimizerBind:
    """Test bind() method via public API."""

    @staticmethod
    def test_bind_with_operators_returns_count():
        """Bind operators matching targets, returns count."""
        optimizer = BaseOptimizer()
        op1 = make_mock_operator({"system_prompt": "prompt"})
        operators = {"op1": op1}

        count = optimizer.bind(operators, targets=["system_prompt"])

        assert count == 1

    @staticmethod
    def test_bind_filters_non_matching():
        """Bind filters operators that don't match targets."""
        optimizer = BaseOptimizer()
        op1 = make_mock_operator({"system_prompt": "prompt"})
        op2 = make_mock_operator({"other": "value"})
        operators = {"op1": op1, "op2": op2}

        count = optimizer.bind(operators, targets=["system_prompt"])

        assert count == 1

    @staticmethod
    def test_bind_with_none_returns_zero():
        """Bind with None operators returns zero."""
        optimizer = BaseOptimizer()
        count = optimizer.bind(None)
        assert count == 0


class TestBaseOptimizerFilterOperators:
    """Test filter_operators() method via public API."""

    @staticmethod
    def test_filter_matches_targets():
        """Filter operators with matching tunables."""
        optimizer = BaseOptimizer()
        op1 = make_mock_operator({"system_prompt": "prompt"})
        op2 = make_mock_operator({"user_prompt": "prompt"})
        operators = {"op1": op1, "op2": op2}

        result = optimizer.filter_operators(operators, ["system_prompt"])

        assert "op1" in result
        assert "op2" not in result

    @staticmethod
    def test_filter_empty_targets():
        """Filter with empty targets returns empty dict."""
        optimizer = BaseOptimizer()
        operators = {"op1": make_mock_operator()}

        result = optimizer.filter_operators(operators, [])

        assert result == {}

    @staticmethod
    def test_filter_multiple_targets():
        """Filter operators matching any target."""
        optimizer = BaseOptimizer()
        op1 = make_mock_operator({"system_prompt": "prompt"})
        op2 = make_mock_operator({"user_prompt": "prompt"})
        operators = {"op1": op1, "op2": op2}

        result = optimizer.filter_operators(operators, ["system_prompt", "user_prompt"])
        assert "op1" in result
        assert "op2" in result

    @staticmethod
    def test_filter_skips_no_tunables():
        """Skip operators with no tunables."""
        optimizer = BaseOptimizer()
        op = make_mock_operator()
        op.get_tunables.return_value = {}
        operators = {"op1": op}

        result = optimizer.filter_operators(operators, targets=["system_prompt"])

        assert result == {}


class TestBaseOptimizerTrajectories:
    """Test trajectory caching methods via public API."""

    @staticmethod
    def test_add_trajectory():
        """Add trajectory to cache."""
        optimizer = BaseOptimizer()
        traj = MagicMock()

        optimizer.add_trajectory(traj)

        trajectories = optimizer.get_trajectories()
        assert traj in trajectories

    @staticmethod
    def test_get_trajectories_returns_copy():
        """Get copy of cached trajectories."""
        optimizer = BaseOptimizer()
        traj1 = MagicMock()
        traj2 = MagicMock()
        optimizer.add_trajectory(traj1)
        optimizer.add_trajectory(traj2)

        result = optimizer.get_trajectories()

        assert len(result) == 2
        assert result is not optimizer.get_trajectories()

    @staticmethod
    def test_clear_trajectories():
        """Clear cached trajectories."""
        optimizer = BaseOptimizer()
        optimizer.add_trajectory(MagicMock())
        optimizer.add_trajectory(MagicMock())

        optimizer.clear_trajectories()

        assert optimizer.get_trajectories() == []


class TestBaseOptimizerUpdate:
    """Test update() method behavior via public API."""

    @staticmethod
    def test_update_raises_without_parameters():
        """Raises error if no parameters bound."""
        optimizer = BaseOptimizer()
        optimizer.bind({})

        with pytest.raises(Exception):
            optimizer.update()


class TestBaseOptimizerParameters:
    """Test parameters() method via public API."""

    @staticmethod
    def test_parameters_returns_copy():
        """Returns copy of parameters dict."""
        optimizer = BaseOptimizer()
        optimizer.bind({"op1": make_mock_operator()}, targets=["system_prompt"])

        result = optimizer.parameters()

        assert "op1" in result
        assert result is not optimizer.parameters()


class TestTextualParameter:
    """Test TextualParameter class via public API."""

    @staticmethod
    def test_init_with_operator_id():
        """Init with operator_id."""
        param = TextualParameter(operator_id="test_op")
        assert param.operator_id == "test_op"
        assert param.get_gradient("anything") is None
        assert param.get_description() == ""

    @staticmethod
    def test_set_and_get_gradient():
        """Set and get gradient."""
        param = TextualParameter(operator_id="op1")
        param.set_gradient("system_prompt", "improved prompt")
        assert param.get_gradient("system_prompt") == "improved prompt"

    @staticmethod
    def test_get_missing_gradient():
        """Missing gradient returns None."""
        param = TextualParameter(operator_id="op1")
        assert param.get_gradient("missing") is None

    @staticmethod
    def test_set_description():
        """Set and get description."""
        param = TextualParameter(operator_id="op1")
        param.set_description("Test optimizer param")
        assert param.get_description() == "Test optimizer param"

    @staticmethod
    def test_multiple_gradients():
        """Store multiple gradients."""
        param = TextualParameter(operator_id="op1")
        param.set_gradient("system_prompt", "sys grad")
        param.set_gradient("user_prompt", "usr grad")

        assert param.get_gradient("system_prompt") == "sys grad"
        assert param.get_gradient("user_prompt") == "usr grad"
