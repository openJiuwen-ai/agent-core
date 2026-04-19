# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for openjiuwen.core.operator.llm_call module."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.operator.llm_call import LLMCallOperator


class TestLLMCallOperator:
    """Tests for LLMCallOperator class."""

    @pytest.fixture
    def operator(self):
        """Create a LLMCallOperator instance."""
        return LLMCallOperator(
            system_prompt="You are a helpful assistant.",
            user_prompt="Answer: {{query}}",
            freeze_user_prompt=False,
        )

    @staticmethod
    def test_operator_id_default(operator):
        """Test default operator_id."""
        assert operator.operator_id == "llm_call"

    @staticmethod
    def test_operator_id_custom():
        """Test custom operator_id."""
        op = LLMCallOperator(
            system_prompt="sys",
            user_prompt="{{query}}",
            operator_id="custom_id",
        )
        assert op.operator_id == "custom_id"

    @staticmethod
    def test_get_tunables_both_prompts():
        """Test get_tunables returns both prompts when not frozen."""
        op = LLMCallOperator(
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_user_prompt=False,
        )
        tunables = op.get_tunables()
        assert "system_prompt" in tunables
        assert "user_prompt" in tunables
        assert tunables["system_prompt"].kind == "prompt"
        assert tunables["user_prompt"].kind == "prompt"

    @staticmethod
    def test_get_tunables_frozen_system_prompt():
        """Test get_tunables excludes frozen system prompt."""
        op = LLMCallOperator(
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_system_prompt=True,
            freeze_user_prompt=False,
        )
        tunables = op.get_tunables()
        assert "system_prompt" not in tunables
        assert "user_prompt" in tunables

    @staticmethod
    def test_get_tunables_frozen_user_prompt():
        """Test get_tunables excludes frozen user prompt."""
        op = LLMCallOperator(
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_user_prompt=True,
        )
        tunables = op.get_tunables()
        assert "system_prompt" in tunables
        assert "user_prompt" not in tunables

    @staticmethod
    def test_get_tunables_both_frozen():
        """Test get_tunables returns empty dict when both frozen."""
        op = LLMCallOperator(
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_system_prompt=True,
            freeze_user_prompt=True,
        )
        tunables = op.get_tunables()
        assert tunables == {}

    @staticmethod
    def test_set_parameter_system_prompt(operator):
        """Test set_parameter for system_prompt."""
        operator.set_parameter("system_prompt", "New system prompt")
        assert operator.get_state()["system_prompt"] == "New system prompt"

    @staticmethod
    def test_set_parameter_user_prompt(operator):
        """Test set_parameter for user_prompt."""
        operator.set_parameter("user_prompt", "New: {{query}}")
        assert operator.get_state()["user_prompt"] == "New: {{query}}"

    @staticmethod
    def test_set_parameter_frozen_system_prompt():
        """Test set_parameter ignores frozen system prompt."""
        op = LLMCallOperator(
            system_prompt="original",
            user_prompt="{{query}}",
            freeze_system_prompt=True,
        )
        original = op.get_state()["system_prompt"]
        op.set_parameter("system_prompt", "New prompt")
        assert op.get_state()["system_prompt"] == original

    @staticmethod
    def test_set_parameter_frozen_user_prompt():
        """Test set_parameter ignores frozen user prompt."""
        op = LLMCallOperator(
            system_prompt="sys",
            user_prompt="original {{query}}",
            freeze_user_prompt=True,
        )
        original = op.get_state()["user_prompt"]
        op.set_parameter("user_prompt", "New: {{query}}")
        assert op.get_state()["user_prompt"] == original

    @staticmethod
    def test_get_state(operator):
        """Test get_state returns prompt contents."""
        state = operator.get_state()
        assert "system_prompt" in state
        assert "user_prompt" in state
        assert state["system_prompt"] == "You are a helpful assistant."
        assert state["user_prompt"] == "Answer: {{query}}"

    @staticmethod
    def test_load_state(operator):
        """Test load_state restores prompt contents."""
        operator.load_state(
            {
                "system_prompt": "Loaded system",
                "user_prompt": "Loaded: {{query}}",
            }
        )
        assert operator.get_state()["system_prompt"] == "Loaded system"
        assert operator.get_state()["user_prompt"] == "Loaded: {{query}}"

    @staticmethod
    def test_load_state_triggers_callback():
        """Test load_state triggers on_parameter_updated callback."""
        callback = MagicMock()
        op = LLMCallOperator(
            system_prompt="original system",
            user_prompt="original {{query}}",
            on_parameter_updated=callback,
        )
        op.load_state(
            {
                "system_prompt": "Loaded system",
                "user_prompt": "Loaded: {{query}}",
            }
        )
        # Callback should be triggered for both parameters
        assert callback.call_count == 2
        callback.assert_any_call("system_prompt", "Loaded system")
        callback.assert_any_call("user_prompt", "Loaded: {{query}}")

    @staticmethod
    def test_load_state_partial(operator):
        """Test load_state with partial state."""
        operator.load_state({"system_prompt": "Partial load"})
        # user_prompt should remain unchanged
        assert operator.get_state()["user_prompt"] == "Answer: {{query}}"

    @staticmethod
    def test_get_freeze_system_prompt():
        """Test get_freeze_system_prompt getter."""
        op = LLMCallOperator(
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_system_prompt=True,
        )
        assert op.get_freeze_system_prompt() is True

    @staticmethod
    def test_get_freeze_user_prompt():
        """Test get_freeze_user_prompt getter."""
        op = LLMCallOperator(
            system_prompt="sys",
            user_prompt="{{query}}",
            freeze_user_prompt=True,
        )
        assert op.get_freeze_user_prompt() is True

    @staticmethod
    def test_set_freeze_system_prompt(operator):
        """Test set_freeze_system_prompt setter."""
        operator.set_freeze_system_prompt(True)
        assert operator.get_freeze_system_prompt() is True

    @staticmethod
    def test_set_freeze_user_prompt(operator):
        """Test set_freeze_user_prompt setter."""
        operator.set_freeze_user_prompt(True)
        assert operator.get_freeze_user_prompt() is True

    @staticmethod
    def test_on_parameter_updated_callback():
        """Test on_parameter_updated callback is invoked."""
        callback = MagicMock()
        op = LLMCallOperator(
            system_prompt="sys",
            user_prompt="{{query}}",
            on_parameter_updated=callback,
        )
        op.set_parameter("system_prompt", "New prompt")
        callback.assert_called_once_with("system_prompt", "New prompt")
