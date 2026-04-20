# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for openjiuwen.core.operator.tool_call module."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.operator.tool_call import ToolCallOperator


class TestToolCallOperator:
    """Tests for ToolCallOperator class."""

    @staticmethod
    def test_operator_id():
        """Test operator_id property."""
        op = ToolCallOperator(operator_id="test_tool")
        assert op.operator_id == "test_tool"

    @staticmethod
    def test_get_tunables_without_descriptions():
        """Test get_tunables returns empty without descriptions."""
        op = ToolCallOperator(operator_id="test_tool")
        tunables = op.get_tunables()
        assert tunables == {}

    @staticmethod
    def test_get_tunables_with_descriptions():
        """Test get_tunables returns tool_description with descriptions."""
        op = ToolCallOperator(
            operator_id="test_tool",
            descriptions={"tool1": "Description 1", "tool2": "Description 2"},
        )
        tunables = op.get_tunables()
        assert "tool_description" in tunables
        assert tunables["tool_description"].kind == "text"

    @staticmethod
    def test_set_parameter_tool_description():
        """Test set_parameter for tool_description triggers callback."""
        callback = MagicMock()
        op = ToolCallOperator(operator_id="test_tool", on_parameter_updated=callback)
        op.set_parameter(
            "tool_description",
            {
                "tool1": "Updated description 1",
                "tool2": "Updated description 2",
            },
        )
        callback.assert_called_once_with(
            "tool_description",
            {"tool1": "Updated description 1", "tool2": "Updated description 2"},
        )

    @staticmethod
    def test_set_parameter_unknown_target():
        """Test set_parameter ignores unknown targets."""
        callback = MagicMock()
        op = ToolCallOperator(operator_id="test_tool", on_parameter_updated=callback)
        # Should not raise and not trigger callback
        op.set_parameter("unknown", "value")
        callback.assert_not_called()

    @staticmethod
    def test_set_parameter_invalid_value():
        """Test set_parameter ignores non-dict values."""
        callback = MagicMock()
        op = ToolCallOperator(operator_id="test_tool", on_parameter_updated=callback)
        op.set_parameter("tool_description", "not a dict")
        callback.assert_not_called()

    @staticmethod
    def test_get_state():
        """Test get_state returns tool_description."""
        op = ToolCallOperator(
            operator_id="test_tool",
            descriptions={"tool1": "Description 1"},
        )
        state = op.get_state()
        assert "tool_description" in state
        assert state["tool_description"] == {"tool1": "Description 1"}

    @staticmethod
    def test_load_state():
        """Test load_state restores tool_description."""
        op = ToolCallOperator(operator_id="test_tool")
        op.load_state({"tool_description": {"tool1": "loaded desc"}})
        assert op.get_state()["tool_description"] == {"tool1": "loaded desc"}


class TestToolCallOperatorCallbacks:
    """Tests for callback functionality."""

    @staticmethod
    def test_set_parameter_triggers_callback():
        """Test set_parameter triggers on_parameter_updated callback."""
        callback = MagicMock()

        op = ToolCallOperator(
            operator_id="test_tool",
            on_parameter_updated=callback,
        )
        op.set_parameter("tool_description", {"tool1": "new desc"})

        callback.assert_called_once_with("tool_description", {"tool1": "new desc"})

    @staticmethod
    def test_load_state_triggers_callback():
        """Test load_state triggers on_parameter_updated callback."""
        callback = MagicMock()

        op = ToolCallOperator(
            operator_id="test_tool",
            on_parameter_updated=callback,
        )
        op.load_state({"tool_description": {"tool1": "loaded desc"}})

        # Callback should be triggered
        callback.assert_called_once_with("tool_description", {"tool1": "loaded desc"})
