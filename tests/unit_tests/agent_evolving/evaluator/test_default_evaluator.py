# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for DefaultEvaluator and BaseEvaluator."""

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.agent_evolving.dataset import Case
from openjiuwen.agent_evolving.evaluator.evaluator import DefaultEvaluator, BaseEvaluator


def make_test_case(inputs=None, label=None):
    """Factory for creating test Case instances."""
    return Case(inputs=inputs or {"q": "test"}, label=label or {"ans": "expected"})


def create_evaluator():
    """Create DefaultEvaluator with mocked model."""
    from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

    with patch("openjiuwen.agent_evolving.evaluator.evaluator.Model") as mock_model:
        mock_model.return_value = MagicMock()
        evaluator = DefaultEvaluator(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        return evaluator, mock_model


class TestBaseEvaluatorAbstract:
    """Test BaseEvaluator abstract class."""

    @staticmethod
    def test_batch_evaluate_length_mismatch():
        """batch_evaluate raises when cases and predicts lengths mismatch."""
        evaluator = MagicMock(spec=BaseEvaluator)
        evaluator.evaluate = MagicMock()
        evaluator.batch_evaluate = BaseEvaluator.batch_evaluate.__get__(evaluator)

        with pytest.raises(Exception):
            evaluator.batch_evaluate([make_test_case()], [], num_parallel=1)

    @staticmethod
    def test_batch_evaluate_length_mismatch_reversed():
        """batch_evaluate raises when predicts longer than cases."""
        evaluator = MagicMock(spec=BaseEvaluator)
        evaluator.evaluate = MagicMock()
        evaluator.batch_evaluate = BaseEvaluator.batch_evaluate.__get__(evaluator)

        with pytest.raises(Exception):
            evaluator.batch_evaluate([], [{"output": "pred"}], num_parallel=1)

    @staticmethod
    def test_batch_evaluate_invalid_parallel_num_zero():
        """batch_evaluate validates num_parallel cannot be 0."""
        evaluator = MagicMock(spec=BaseEvaluator)
        evaluator.evaluate = MagicMock(return_value=MagicMock())
        evaluator.batch_evaluate = BaseEvaluator.batch_evaluate.__get__(evaluator)

        with pytest.raises(ValidationError):
            evaluator.batch_evaluate([make_test_case()], [{"output": "pred"}], num_parallel=0)

    @staticmethod
    def test_batch_evaluate_invalid_parallel_num_too_high():
        """batch_evaluate validates num_parallel max is 20."""
        evaluator = MagicMock(spec=BaseEvaluator)
        evaluator.evaluate = MagicMock(return_value=MagicMock())
        evaluator.batch_evaluate = BaseEvaluator.batch_evaluate.__get__(evaluator)

        with pytest.raises(ValidationError):
            evaluator.batch_evaluate([make_test_case()], [{"output": "pred"}], num_parallel=100)


class TestDefaultEvaluator:
    """Test DefaultEvaluator.evaluate method."""

    @staticmethod
    @pytest.fixture
    def mock_asyncio_run():
        """Create asyncio.run mock."""
        with patch("openjiuwen.agent_evolving.evaluator.evaluator.asyncio.run") as mock:
            yield mock

    @staticmethod
    def test_evaluate_parsing_error(mock_asyncio_run):
        """evaluate handles parsing error via public API."""
        mock_asyncio_run.return_value = MagicMock(content="invalid json")
        evaluator, _ = create_evaluator()
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.0)
        assert "parsing error" in result.reason

    @staticmethod
    def test_evaluate_returns_pass(mock_asyncio_run):
        """evaluate returns pass (score=1.0) when result is true."""
        mock_asyncio_run.return_value = MagicMock(content='```json\n{"result": true, "reason": "good"}\n```')
        evaluator, _ = create_evaluator()
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(1.0)
        assert result.reason == "good"

    @staticmethod
    def test_evaluate_returns_fail(mock_asyncio_run):
        """evaluate returns fail (score=0.0) when result is false."""
        mock_asyncio_run.return_value = MagicMock(content='```json\n{"result": false, "reason": "bad"}\n```')
        evaluator, _ = create_evaluator()
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.0)
        assert result.reason == "bad"

    @staticmethod
    def test_evaluate_handles_retry_on_parse_failure(mock_asyncio_run):
        """evaluate retries on first parse failure via public API."""
        mock_asyncio_run.side_effect = [
            MagicMock(content="invalid json"),
            MagicMock(content='```json\n{"result": true, "reason": "retry success"}\n```'),
        ]
        evaluator, _ = create_evaluator()
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        # After retry, should succeed
        assert result.score == pytest.approx(1.0)
        assert result.reason == "retry success"

    @staticmethod
    def test_evaluate_returns_none_on_retry_error(mock_asyncio_run):
        """evaluate returns None result when retry also fails."""
        mock_asyncio_run.side_effect = [
            MagicMock(content="invalid json"),
            Exception("retry failed"),
        ]
        evaluator, _ = create_evaluator()
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        # Should return score 0.0 when all retries fail
        assert result.score == pytest.approx(0.0)
