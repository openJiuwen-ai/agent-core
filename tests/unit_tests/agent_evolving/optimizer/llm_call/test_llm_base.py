# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for LLMCallOptimizerBase - LLM dimension optimizer base class."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_evolving.optimizer.llm_call.base import LLMCallOptimizerBase


def make_mock_llm_operator(tunables=None, op_id="llm_op"):
    """Factory for creating mock LLM operators."""
    op = MagicMock()
    op.operator_id = op_id
    op.get_tunables.return_value = tunables or {"system_prompt": "prompt"}
    op.get_state.return_value = {"system_prompt": "You are helpful."}
    return op


class TestLLMCallOptimizerBaseInit:
    """Test LLMCallOptimizerBase initialization."""

    @staticmethod
    def test_domain_is_llm():
        """Domain is 'llm'."""
        optimizer = LLMCallOptimizerBase()
        assert optimizer.domain == "llm"

    @staticmethod
    def test_default_targets_system_and_user_prompt():
        """Default targets include system_prompt and user_prompt."""
        optimizer = LLMCallOptimizerBase()
        targets = optimizer.default_targets()
        assert "system_prompt" in targets
        assert "user_prompt" in targets


class TestLLMCallOptimizerBaseFilterOperators:
    """Test filter_operators() method."""

    @staticmethod
    def test_filter_matches_prompt_targets():
        """Filter operators with prompt tunables."""
        optimizer = LLMCallOptimizerBase()
        op1 = make_mock_llm_operator({"system_prompt": "prompt"})
        op2 = make_mock_llm_operator({"user_prompt": "prompt"})
        op3 = make_mock_llm_operator({"other": "value"})
        operators = {"op1": op1, "op2": op2, "op3": op3}

        result = optimizer.filter_operators(operators, ["system_prompt", "user_prompt"])

        assert "op1" in result
        assert "op2" in result
        assert "op3" not in result

    @staticmethod
    def test_filter_empty_targets():
        """Filter with empty targets returns empty dict."""
        optimizer = LLMCallOptimizerBase()
        operators = {"op1": make_mock_llm_operator()}

        result = optimizer.filter_operators(operators, [])

        assert result == {}

    @staticmethod
    def test_filter_skips_no_tunables():
        """Skip operators with no tunables."""
        optimizer = LLMCallOptimizerBase()
        op = make_mock_llm_operator()
        op.get_tunables.return_value = {}
        operators = {"op1": op}

        result = optimizer.filter_operators(operators, targets=["system_prompt"])

        assert result == {}


class TestLLMCallOptimizerBaseBind:
    """Test bind() method via public API."""

    @staticmethod
    def test_bind_with_llm_operators():
        """Bind LLM operators matching prompt targets."""
        optimizer = LLMCallOptimizerBase()
        op1 = make_mock_llm_operator({"system_prompt": "prompt"})
        op2 = make_mock_llm_operator({"tool_description": "desc"})
        operators = {"op1": op1, "op2": op2}

        count = optimizer.bind(operators)

        assert count == 1

    @staticmethod
    def test_bind_with_no_matching_operators():
        """Bind with no matching operators returns zero."""
        optimizer = LLMCallOptimizerBase()
        op = make_mock_llm_operator({"other": "value"})
        operators = {"op1": op}

        count = optimizer.bind(operators)

        assert count == 0
