# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for MemoryOptimizerBase - Memory dimension optimizer base class."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_evolving.optimizer.memory_call.base import MemoryOptimizerBase


def make_mock_memory_operator(tunables=None, op_id="memory_op"):
    """Factory for creating mock Memory operators."""
    op = MagicMock()
    op.operator_id = op_id
    op.get_tunables.return_value = tunables or {"enabled": True, "max_retries": 3}
    op.get_state.return_value = {"enabled": True, "max_retries": 3}
    return op


class TestMemoryOptimizerBaseInit:
    """Test MemoryOptimizerBase initialization."""

    @staticmethod
    def test_domain_is_memory():
        """Domain is 'memory'."""
        optimizer = MemoryOptimizerBase()
        assert optimizer.domain == "memory"

    @staticmethod
    def test_default_targets_enabled_and_max_retries():
        """Default targets are enabled and max_retries."""
        optimizer = MemoryOptimizerBase()
        targets = optimizer.default_targets()
        assert "enabled" in targets
        assert "max_retries" in targets


class TestMemoryOptimizerBaseFilterOperators:
    """Test filter_operators() method."""

    @staticmethod
    def test_filter_matches_memory_targets():
        """Filter operators with memory tunables."""
        optimizer = MemoryOptimizerBase()
        op1 = make_mock_memory_operator({"enabled": True, "max_retries": 3})
        op2 = make_mock_memory_operator({"system_prompt": "prompt"})
        operators = {"op1": op1, "op2": op2}

        result = optimizer.filter_operators(operators, ["enabled", "max_retries"])

        assert "op1" in result
        assert "op2" not in result

    @staticmethod
    def test_filter_empty_targets():
        """Filter with empty targets returns empty dict."""
        optimizer = MemoryOptimizerBase()
        operators = {"op1": make_mock_memory_operator()}

        result = optimizer.filter_operators(operators, [])

        assert result == {}

    @staticmethod
    def test_filter_skips_no_tunables():
        """Skip operators with no tunables."""
        optimizer = MemoryOptimizerBase()
        op = make_mock_memory_operator()
        op.get_tunables.return_value = {}
        operators = {"op1": op}

        result = optimizer.filter_operators(operators, targets=["enabled"])

        assert result == {}

    @staticmethod
    def test_filter_with_partial_targets():
        """Filter operators with partial target match."""
        optimizer = MemoryOptimizerBase()
        op1 = make_mock_memory_operator({"enabled": True}, op_id="op1")
        op2 = make_mock_memory_operator({"max_retries": 3}, op_id="op2")
        operators = {"op1": op1, "op2": op2}

        result = optimizer.filter_operators(operators, ["enabled", "max_retries"])

        assert "op1" in result
        assert "op2" in result


class TestMemoryOptimizerBaseBind:
    """Test bind() method via public API."""

    @staticmethod
    def test_bind_with_memory_operators():
        """Bind Memory operators matching memory targets."""
        optimizer = MemoryOptimizerBase()
        op1 = make_mock_memory_operator({"enabled": True, "max_retries": 3})
        op2 = make_mock_memory_operator({"system_prompt": "prompt"})
        operators = {"op1": op1, "op2": op2}

        count = optimizer.bind(operators)

        assert count == 1

    @staticmethod
    def test_bind_with_no_matching_operators():
        """Bind with no matching operators returns zero."""
        optimizer = MemoryOptimizerBase()
        op = make_mock_memory_operator({"other": "value"})
        operators = {"op1": op}

        count = optimizer.bind(operators)

        assert count == 0

    @staticmethod
    def test_bind_with_multiple_matching():
        """Bind multiple matching operators."""
        optimizer = MemoryOptimizerBase()
        op1 = make_mock_memory_operator({"enabled": True}, op_id="op1")
        op2 = make_mock_memory_operator({"max_retries": 3}, op_id="op2")
        operators = {"op1": op1, "op2": op2}

        count = optimizer.bind(operators)

        assert count == 2


class TestMemoryOptimizerBaseDefaultTargets:
    """Test default_targets() behavior."""

    @staticmethod
    def test_default_targets_returns_list():
        """default_targets returns a list."""
        optimizer = MemoryOptimizerBase()
        result = optimizer.default_targets()
        assert isinstance(result, list)

    @staticmethod
    def test_default_targets_contains_enabled():
        """default_targets contains 'enabled'."""
        optimizer = MemoryOptimizerBase()
        result = optimizer.default_targets()
        assert "enabled" in result

    @staticmethod
    def test_default_targets_contains_max_retries():
        """default_targets contains 'max_retries'."""
        optimizer = MemoryOptimizerBase()
        result = optimizer.default_targets()
        assert "max_retries" in result

    @staticmethod
    def test_default_targets_count():
        """default_targets has two targets."""
        optimizer = MemoryOptimizerBase()
        result = optimizer.default_targets()
        assert len(result) == 2
