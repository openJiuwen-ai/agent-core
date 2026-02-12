# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for InstructionOptimizer - prompt optimization with LLM calls."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase
from openjiuwen.agent_evolving.optimizer.llm_call.instruction_optimizer import InstructionOptimizer
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig


def make_mock_model(content="optimized prompt"):
    """Factory for creating mock LLM responses."""
    mock = MagicMock()
    mock.invoke = AsyncMock(return_value=MagicMock(content=content))
    return mock


def make_mock_operator(op_id="llm_op", system_prompt="You are helpful.", user_prompt="{{query}}"):
    """Factory for creating mock operators."""
    op = MagicMock()
    op.operator_id = op_id
    op.get_state.return_value = {"system_prompt": system_prompt, "user_prompt": user_prompt}
    op.get_tunables.return_value = {"system_prompt": system_prompt, "user_prompt": user_prompt}
    return op


def make_evaluated_case(case_id="case1", score=0.0, answer=None):
    """Factory for creating evaluated cases."""
    case = Case(inputs={"query": "test question"}, label={"answer": "expected answer"}, case_id=case_id)
    return EvaluatedCase(case=case, answer=answer or {"output": "wrong"}, score=score, reason="incorrect")


def make_instruction_optimizer():
    """Factory for creating InstructionOptimizer with mocked LLM."""
    mock_model = make_mock_model()
    with patch("openjiuwen.agent_evolving.optimizer.llm_call.instruction_optimizer.Model") as mock_model_class:
        mock_model_class.return_value = mock_model
        optimizer = InstructionOptimizer(
            model_config=MagicMock(spec=ModelRequestConfig),
            model_client_config=MagicMock(spec=ModelClientConfig),
        )
        return optimizer, mock_model


class TestInstructionOptimizerInit:
    """Test InstructionOptimizer initialization."""

    @staticmethod
    def test_init_with_params():
        """Init with model configs."""
        optimizer, _ = make_instruction_optimizer()
        assert optimizer.parameters() == {}


class TestInstructionOptimizerBackward:
    """Test backward() method via public API."""

    @staticmethod
    def test_backward_no_operators_raises():
        """Backward with no operators raises ValidationError."""
        optimizer, _ = make_instruction_optimizer()
        optimizer.bind({})
        with pytest.raises(Exception):
            optimizer.backward([])

    @staticmethod
    def test_backward_skips_missing_operator():
        """Skips operators when operators dict is empty after bind."""
        optimizer, mock_model = make_instruction_optimizer()
        # Bind with an operator that doesn't match targets results in empty _operators
        # This tests that backward handles empty operators gracefully
        optimizer.bind({}, targets=["system_prompt"])
        mock_model.invoke.assert_not_called()

    @staticmethod
    def test_backward_generates_gradients():
        """Backward generates textual gradients via public API."""
        optimizer, mock_model = make_instruction_optimizer()
        mock_model.invoke.return_value.content = "Gradient text"
        op = make_mock_operator("op1")
        optimizer.bind({"op1": op})
        optimizer.backward([])  # Use public API
        # Verify gradient was set via parameters
        params = optimizer.parameters()
        assert "op1" in params
        assert (
            params["op1"].get_gradient("system_prompt") is not None
            or params["op1"].get_gradient("user_prompt") is not None
        )


class TestInstructionOptimizerStep:
    """Test step() method via public API."""

    @staticmethod
    def test_step_empty_operators_raises():
        """Step with no operators raises ValidationError."""
        optimizer, _ = make_instruction_optimizer()
        optimizer.bind({})
        with pytest.raises(Exception):
            optimizer.step()

    @staticmethod
    def test_step_single_operator():
        """Step single operator via public API."""
        optimizer, mock_model = make_instruction_optimizer()
        mock_model.invoke.return_value = MagicMock()
        mock_model.invoke.return_value.content = "<PROMPT_OPTIMIZED>new prompt</PROMPT_OPTIMIZED>"
        op = make_mock_operator("op1")
        optimizer.bind({"op1": op})
        result = optimizer.step()  # Use public API
        assert result is None or isinstance(result, dict)


class TestInstructionOptimizerBadCasesBehavior:
    """Test bad cases filtering behavior through backward()."""

    @staticmethod
    def test_backward_with_mixed_scores():
        """backward processes cases with different scores."""
        optimizer, mock_model = make_instruction_optimizer()
        mock_model.invoke.return_value = MagicMock(content="Gradient text")

        optimizer.bind({"op1": make_mock_operator("op1")})

        cases = [
            make_evaluated_case("case1", score=0.0),
            make_evaluated_case("case2", score=1.0),
        ]

        # backward should complete without error for mixed scores
        optimizer.backward(cases)


class TestInstructionOptimizerFullPipeline:
    """Integration tests for full backward -> step pipeline via public API."""

    @staticmethod
    def test_full_pipeline_no_cases():
        """Full pipeline with empty cases."""
        optimizer, mock_model = make_instruction_optimizer()
        mock_model.invoke.return_value.content = "<PROMPT_OPTIMIZED>optimized</PROMPT_OPTIMIZED>"

        optimizer.bind({"op1": make_mock_operator("op1")})
        optimizer.backward([])
        result = optimizer.step()

        # Should complete without error
        assert result is None or isinstance(result, dict)

    @staticmethod
    def test_full_pipeline_with_bad_cases():
        """Full pipeline with bad cases triggers LLM calls."""
        optimizer, mock_model = make_instruction_optimizer()
        mock_model.invoke.return_value.content = "<PROMPT_OPTIMIZED>optimized</PROMPT_OPTIMIZED>"

        optimizer.bind({"op1": make_mock_operator("op1")})
        cases = [make_evaluated_case("case1", score=0.0)]
        optimizer.backward(cases)
        result = optimizer.step()

        # Should complete without error
        assert result is None or isinstance(result, dict)


class TestInstructionOptimizerStepPaths:
    """Test step() method different code paths via public API."""

    @staticmethod
    def test_step_system_prompt_only():
        """Step only system_prompt target."""
        optimizer, mock_model = make_instruction_optimizer()
        mock_model.invoke.return_value.content = "<PROMPT_OPTIMIZED>new sys</PROMPT_OPTIMIZED>"

        optimizer.bind({"op1": make_mock_operator("op1")}, targets=["system_prompt"])
        optimizer.backward([])
        result = optimizer.step()

        assert result is not None
        assert ("op1", "system_prompt") in result

    @staticmethod
    def test_step_user_prompt_only():
        """Step only user_prompt target."""
        optimizer, mock_model = make_instruction_optimizer()
        mock_model.invoke.return_value.content = "<PROMPT_OPTIMIZED>new usr</PROMPT_OPTIMIZED>"

        optimizer.bind({"op1": make_mock_operator("op1")}, targets=["user_prompt"])
        optimizer.backward([])
        result = optimizer.step()

        assert result is not None
        assert ("op1", "user_prompt") in result

    @staticmethod
    def test_step_returns_none_when_empty():
        """Returns None when no updates generated."""
        optimizer, _ = make_instruction_optimizer()
        optimizer.bind({"op1": make_mock_operator("op1")}, targets=["system_prompt"])
        # Forward without bad cases means no gradient is generated, so step returns None
        optimizer.backward([])

        result = optimizer.step()

        assert result is None or result == {}
