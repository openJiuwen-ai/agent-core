# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for openjiuwen.core.operator.memory_call module."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.operator.memory_call import MemoryCallOperator


class TestMemoryCallOperator:
    """Tests for MemoryCallOperator class."""

    @pytest.fixture
    def operator(self):
        """Create a MemoryCallOperator instance."""
        return MemoryCallOperator()

    @staticmethod
    def test_operator_id_default(operator):
        """Test default operator_id."""
        assert operator.operator_id == "memory_call"

    @staticmethod
    def test_operator_id_custom():
        """Test custom operator_id."""
        op = MemoryCallOperator(operator_id="custom_memory")
        assert op.operator_id == "custom_memory"

    @staticmethod
    def test_get_tunables(operator):
        """Test get_tunables returns enabled and max_retries."""
        tunables = operator.get_tunables()
        assert "enabled" in tunables
        assert "max_retries" in tunables
        assert tunables["enabled"].kind == "discrete"
        assert tunables["max_retries"].kind == "discrete"

    @staticmethod
    def test_get_tunables_constraints(operator):
        """Test tunable constraints are correctly set."""
        tunables = operator.get_tunables()
        assert tunables["enabled"].constraint == {"type": "bool"}
        assert tunables["max_retries"].constraint == {"type": "int", "min": 0, "max": 5}

    @staticmethod
    def test_set_parameter_enabled(operator):
        """Test set_parameter for enabled."""
        operator.set_parameter("enabled", False)
        assert operator.get_state()["enabled"] is False
        operator.set_parameter("enabled", True)
        assert operator.get_state()["enabled"] is True

    @staticmethod
    def test_set_parameter_max_retries(operator):
        """Test set_parameter for max_retries."""
        operator.set_parameter("max_retries", 3)
        assert operator.get_state()["max_retries"] == 3

    @staticmethod
    def test_set_parameter_max_retries_clamped(operator):
        """Test set_parameter clamps max_retries to 0-5."""
        operator.set_parameter("max_retries", 10)
        assert operator.get_state()["max_retries"] == 5
        operator.set_parameter("max_retries", -1)
        assert operator.get_state()["max_retries"] == 0

    @staticmethod
    def test_get_state(operator):
        """Test get_state returns enabled and max_retries."""
        state = operator.get_state()
        assert "enabled" in state
        assert "max_retries" in state
        assert state["enabled"] is True
        assert state["max_retries"] == 0

    @staticmethod
    def test_get_state_with_custom_values():
        """Test get_state with custom values."""
        op = MemoryCallOperator()
        op.load_state({"enabled": False, "max_retries": 3})
        state = op.get_state()
        assert state["enabled"] is False
        assert state["max_retries"] == 3

    @staticmethod
    def test_load_state(operator):
        """Test load_state restores state."""
        operator.load_state({"enabled": False, "max_retries": 2})
        state = operator.get_state()
        assert state["enabled"] is False
        assert state["max_retries"] == 2

    @staticmethod
    def test_load_state_partial(operator):
        """Test load_state with partial state."""
        operator.load_state({"enabled": False})
        state = operator.get_state()
        assert state["enabled"] is False
        assert state["max_retries"] == 0

    @staticmethod
    def test_load_state_clamped_retries():
        """Test load_state clamps max_retries to 0-5."""
        op = MemoryCallOperator()
        op.load_state({"max_retries": 10})
        assert op.get_state()["max_retries"] == 5
        op.load_state({"max_retries": -1})
        assert op.get_state()["max_retries"] == 0


class TestMemoryCallOperatorCallbacks:
    """Tests for callback functionality."""

    @staticmethod
    def test_set_parameter_triggers_callback():
        """Test set_parameter triggers on_parameter_updated callback."""
        callback = MagicMock()
        op = MemoryCallOperator(on_parameter_updated=callback)
        op.set_parameter("enabled", False)
        callback.assert_called_once_with("enabled", False)

    @staticmethod
    def test_set_parameter_max_retries_triggers_callback():
        """Test set_parameter for max_retries triggers callback."""
        callback = MagicMock()
        op = MemoryCallOperator(on_parameter_updated=callback)
        op.set_parameter("max_retries", 3)
        callback.assert_called_once_with("max_retries", 3)

    @staticmethod
    def test_load_state_triggers_callback():
        """Test load_state triggers on_parameter_updated callback."""
        callback = MagicMock()
        op = MemoryCallOperator(on_parameter_updated=callback)
        op.load_state({"enabled": False, "max_retries": 2})
        # Callback should be triggered for both parameters
        assert callback.call_count == 2
        callback.assert_any_call("enabled", False)
        callback.assert_any_call("max_retries", 2)

    @staticmethod
    def test_set_parameter_unknown_target():
        """Test set_parameter ignores unknown targets."""
        op = MemoryCallOperator()
        # Should not raise
        op.set_parameter("unknown", "value")
