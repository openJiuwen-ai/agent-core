# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for MetricEvaluator, DefaultEvaluator and aggregate functions."""

from typing import Any
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio

import pytest

from openjiuwen.agent_evolving.dataset import Case
from openjiuwen.agent_evolving.evaluator.evaluator import (
    BaseEvaluator,
    DefaultEvaluator,
    MetricEvaluator,
    _agg_score,
)
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig


def make_test_case(inputs=None, label=None, case_id=None):
    """Factory for creating test Case instances."""
    return Case(
        inputs=inputs or {"q": "test question"},
        label=label or {"ans": "expected answer"},
        case_id=case_id or "test_case_id",
    )


def make_mock_model_client():
    """Create mock model client config."""
    return ModelClientConfig(
        client_provider="OpenAI",
        api_key="test_key",
        api_base="https://test.api.com",
    )


def make_mock_model_config():
    """Create mock model request config."""
    return ModelRequestConfig(model="test-model")


def make_mock_metric(name: str = "test_metric", return_value: Any = 1.0):
    """Factory for creating mock metrics."""
    metric = MagicMock()
    metric.name = name
    metric.compute.return_value = return_value
    return metric


def make_mock_metric_with_dict(name: str = "multi_metric", scores=None):
    """Factory for creating mock metrics that return dict scores."""
    if scores is None:
        scores = {"score_a": 1.0, "score_b": 0.5}
    metric = MagicMock()
    metric.name = name
    metric.compute.return_value = scores
    return metric


def make_mock_metric_with_string(name: str = "test_metric", return_value: str = "0.75"):
    """Factory for creating mock metrics that return string scores."""
    metric = MagicMock()
    metric.name = name
    metric.compute.return_value = return_value
    return metric


class TestAggScore:
    """Test _agg_score function."""

    @staticmethod
    def test_mean_single_value():
        """Mean of single value is the value itself."""
        assert _agg_score([0.5], aggregate="mean") == pytest.approx(0.5)

    @staticmethod
    def test_mean_calculation():
        """Mean calculation."""
        assert _agg_score([0.5, 1.0, 0.0], aggregate="mean") == pytest.approx(0.5)

    @staticmethod
    def test_mean_empty_list():
        """Empty list returns 0.0."""
        assert _agg_score([], aggregate="mean") == pytest.approx(0.0)

    @staticmethod
    def test_first_returns_first():
        """First aggregation returns first element."""
        assert _agg_score([0.2, 0.8, 0.5], aggregate="first") == pytest.approx(0.2)

    @staticmethod
    def test_first_empty_list():
        """Empty list returns 0.0."""
        assert _agg_score([], aggregate="first") == pytest.approx(0.0)

    @staticmethod
    def test_default_is_mean():
        """Default aggregation is mean."""
        assert _agg_score([1.0, 2.0, 3.0]) == pytest.approx(2.0)

    @staticmethod
    def test_invalid_aggregate():
        """Invalid aggregate defaults to mean."""
        assert _agg_score([1.0, 2.0, 3.0], aggregate="invalid") == pytest.approx(2.0)


class TestMetricEvaluator:
    """Test MetricEvaluator class."""

    @staticmethod
    def test_single_metric():
        """Single metric evaluation."""
        metric = make_mock_metric(return_value=0.8)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.8)
        assert result.per_metric == {"test_metric": 0.8}
        metric.compute.assert_called_once()

    @staticmethod
    def test_multiple_metrics():
        """Multiple metrics aggregated."""
        metric1 = make_mock_metric("metric1", 0.6)
        metric2 = make_mock_metric("metric2", 0.8)
        evaluator = MetricEvaluator(metrics=[metric1, metric2])
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.7)
        assert result.per_metric == {"metric1": 0.6, "metric2": 0.8}

    @staticmethod
    def test_metric_returns_dict():
        """Metric can return dict with multiple scores."""
        metric = make_mock_metric_with_dict("multi_metric", {"score_a": 1.0, "score_b": 0.5})
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.per_metric == {"score_a": 1.0, "score_b": 0.5}
        assert result.score == pytest.approx(0.75)

    @staticmethod
    def test_empty_metrics():
        """Empty metrics list returns 0."""
        evaluator = MetricEvaluator(metrics=[])
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.0)
        assert result.per_metric is None

    @staticmethod
    def test_aggregate_first():
        """First aggregation mode."""
        metric = make_mock_metric(return_value=0.9)
        evaluator = MetricEvaluator(metrics=metric, aggregate="first")
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.9)

    @staticmethod
    def test_converts_numeric_strings():
        """Metric converts numeric strings to floats."""
        metric = make_mock_metric_with_string(return_value="0.75")
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.75)

    @staticmethod
    def test_converts_literal_float():
        """Metric converts literal float correctly."""
        metric = make_mock_metric(return_value=0.75)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.75)

    @staticmethod
    def test_metric_kwargs_passed():
        """Case and question passed to metric."""
        metric = make_mock_metric()
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        evaluator.evaluate(case, {"output": "pred"})

        call_kwargs = metric.compute.call_args.kwargs
        assert "question" in call_kwargs
        assert "case" in call_kwargs


class TestMetricEvaluatorEdgeCases:
    """Edge case tests for MetricEvaluator."""

    @staticmethod
    def test_evaluate_with_empty_prediction():
        """Test evaluation with empty prediction dict."""
        metric = make_mock_metric(return_value=1.0)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(1.0)
        metric.compute.assert_called_once()

    @staticmethod
    def test_evaluate_with_empty_dict_prediction():
        """Test evaluation with empty dict prediction."""
        metric = make_mock_metric(return_value=0.5)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {})

        assert result.score == pytest.approx(0.5)

    @staticmethod
    def test_evaluate_with_special_chars_in_prediction():
        """Test evaluation with special characters in prediction."""
        metric = make_mock_metric(return_value=1.0)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        special_pred = {"output": "Hello 世界! 🌍 中文测试"}
        result = evaluator.evaluate(case, special_pred)

        assert result.score == pytest.approx(1.0)
        call_args = metric.compute.call_args
        assert "question" in str(call_args)

    @staticmethod
    def test_evaluate_with_very_long_prediction():
        """Test evaluation with very long prediction."""
        metric = make_mock_metric(return_value=0.8)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        long_pred = {"output": "a" * 10000}
        result = evaluator.evaluate(case, long_pred)

        assert result.score == pytest.approx(0.8)

    @staticmethod
    def test_evaluate_multiple_metrics_one_fails():
        """Test when one metric returns invalid value."""
        metric1 = make_mock_metric("good", 0.9)
        bad_metric = make_mock_metric("bad", "invalid_string_not_a_number")
        evaluator = MetricEvaluator(metrics=[metric1, bad_metric])
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.per_metric is not None
        assert result.per_metric.get("good") == pytest.approx(0.9)

    @staticmethod
    def test_evaluate_with_nested_case_inputs():
        """Test evaluation with deeply nested case inputs."""
        metric = make_mock_metric(return_value=1.0)
        evaluator = MetricEvaluator(metrics=metric)
        case = Case(
            inputs={
                "query": "test",
                "metadata": {
                    "source": "test",
                    "version": 1,
                    "nested": {"deep": {"value": True}},
                },
            },
            label={"answer": "expected"},
        )
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(1.0)

    @staticmethod
    def test_evaluate_with_numeric_label():
        """Test evaluation with numeric label in dict."""
        metric = make_mock_metric(return_value=0.7)
        evaluator = MetricEvaluator(metrics=metric)
        case = Case(inputs={"q": "test"}, label={"answer": 42})
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.7)


class TestMetricEvaluatorBoundary:
    """Boundary value tests for MetricEvaluator."""

    @staticmethod
    def test_score_boundary_zero():
        """Test score of exactly 0.0."""
        metric = make_mock_metric("zero", 0.0)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.0)

    @staticmethod
    def test_score_boundary_one():
        """Test score of exactly 1.0."""
        metric = make_mock_metric("one", 1.0)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(1.0)

    @staticmethod
    def test_score_exactly_half():
        """Test score of exactly 0.5."""
        metric = make_mock_metric("half", 0.5)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.5)

    @staticmethod
    def test_score_fractional_boundary():
        """Test fractional score boundaries."""
        metric = make_mock_metric("fraction", 0.999999)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.999999)

    @staticmethod
    def test_very_small_positive_score():
        """Test very small positive score."""
        metric = make_mock_metric("tiny", 0.000001)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.000001)

    @staticmethod
    def test_score_over_one_not_clamped():
        """Test score > 1.0 is NOT automatically clamped (known behavior)."""
        metric = make_mock_metric("large", 999.0)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(999.0)

    @staticmethod
    def test_negative_score_not_clamped():
        """Test negative score is NOT automatically clamped (known behavior)."""
        metric = make_mock_metric("negative", -5.0)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(-5.0)

    @staticmethod
    def test_zero_as_valid_value():
        """Test zero as a valid metric value."""
        metric = make_mock_metric("zero_val", 0.0)
        evaluator = MetricEvaluator(metrics=metric)
        case = make_test_case()
        result = evaluator.evaluate(case, {"output": "pred"})

        assert result.score == pytest.approx(0.0)
        assert result.per_metric == {"zero_val": 0.0}


def _create_mock_model(response_content):
    """Helper to create mock model with given response."""
    mock_model = MagicMock()
    mock_response = MagicMock()
    mock_response.content = response_content
    mock_model.invoke = AsyncMock(return_value=mock_response)
    return mock_model


class TestDefaultEvaluatorErrors:
    """Error handling tests for DefaultEvaluator (LLM-as-judge)."""

    @staticmethod
    def test_evaluate_handles_llm_timeout():
        """Test LLM timeout handling."""
        with patch("openjiuwen.agent_evolving.evaluator.evaluator.Model") as mock_model_class:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(side_effect=asyncio.TimeoutError("Request timeout"))
            mock_model_class.return_value = mock_model

            evaluator = DefaultEvaluator(
                model_config=make_mock_model_config(),
                model_client_config=make_mock_model_client(),
            )
            case = make_test_case()
            result = evaluator.evaluate(case, {"output": "model prediction"})

            assert result.score == pytest.approx(0.0)

    @staticmethod
    def test_evaluate_handles_connection_error():
        """Test connection error handling."""
        with patch("openjiuwen.agent_evolving.evaluator.evaluator.Model") as mock_model_class:
            mock_model = MagicMock()
            mock_model.invoke = AsyncMock(side_effect=ConnectionError("Connection refused"))
            mock_model_class.return_value = mock_model

            evaluator = DefaultEvaluator(
                model_config=make_mock_model_config(),
                model_client_config=make_mock_model_client(),
            )
            case = make_test_case()
            result = evaluator.evaluate(case, {"output": "model prediction"})

            assert result.score == pytest.approx(0.0)

    @staticmethod
    def test_evaluate_handles_json_parse_error():
        """Test JSON parse error handling (non-JSON LLM response)."""
        with patch("openjiuwen.agent_evolving.evaluator.evaluator.Model") as mock_model_class:
            mock_model = _create_mock_model("This is not JSON at all")
            mock_model_class.return_value = mock_model

            evaluator = DefaultEvaluator(
                model_config=make_mock_model_config(),
                model_client_config=make_mock_model_client(),
            )
            case = make_test_case()
            result = evaluator.evaluate(case, {"output": "model prediction"})

            assert result.score == pytest.approx(0.0)

    @staticmethod
    def test_evaluate_handles_empty_response():
        """Test handling of empty LLM response."""
        with patch("openjiuwen.agent_evolving.evaluator.evaluator.Model") as mock_model_class:
            mock_model = _create_mock_model("")
            mock_model_class.return_value = mock_model

            evaluator = DefaultEvaluator(
                model_config=make_mock_model_config(),
                model_client_config=make_mock_model_client(),
            )
            case = make_test_case()
            result = evaluator.evaluate(case, {"output": "model prediction"})

            assert result.score == pytest.approx(0.0)

    @staticmethod
    def test_evaluate_handles_result_string_true():
        """Test 'true' string in result field."""
        with patch("openjiuwen.agent_evolving.evaluator.evaluator.Model") as mock_model_class:
            mock_model = _create_mock_model('```json{"result": "true", "reason": "Correct answer"}```')
            mock_model_class.return_value = mock_model

            evaluator = DefaultEvaluator(
                model_config=make_mock_model_config(),
                model_client_config=make_mock_model_client(),
            )
            case = make_test_case()
            result = evaluator.evaluate(case, {"output": "model prediction"})

            assert result.score == pytest.approx(1.0)

    @staticmethod
    def test_evaluate_handles_result_string_false():
        """Test 'false' string in result field."""
        with patch("openjiuwen.agent_evolving.evaluator.evaluator.Model") as mock_model_class:
            mock_model = _create_mock_model('```json{"result": "false", "reason": "Wrong answer"}```')
            mock_model_class.return_value = mock_model

            evaluator = DefaultEvaluator(
                model_config=make_mock_model_config(),
                model_client_config=make_mock_model_client(),
            )
            case = make_test_case()
            result = evaluator.evaluate(case, {"output": "model prediction"})

            assert result.score == pytest.approx(0.0)

    @staticmethod
    def test_evaluate_handles_result_string_yes():
        """Test 'yes' string in result field (should be treated as false)."""
        with patch("openjiuwen.agent_evolving.evaluator.evaluator.Model") as mock_model_class:
            mock_model = _create_mock_model('```json{"result": "yes", "reason": "Looks good"}```')
            mock_model_class.return_value = mock_model

            evaluator = DefaultEvaluator(
                model_config=make_mock_model_config(),
                model_client_config=make_mock_model_client(),
            )
            case = make_test_case()
            result = evaluator.evaluate(case, {"output": "model prediction"})

            assert result.score == pytest.approx(0.0)

    @staticmethod
    def test_evaluate_handles_result_boolean_true():
        """Test boolean True in result field."""
        with patch("openjiuwen.agent_evolving.evaluator.evaluator.Model") as mock_model_class:
            mock_model = _create_mock_model('```json{"result": true, "reason": "Correct"}```')
            mock_model_class.return_value = mock_model

            evaluator = DefaultEvaluator(
                model_config=make_mock_model_config(),
                model_client_config=make_mock_model_client(),
            )
            case = make_test_case()
            result = evaluator.evaluate(case, {"output": "model prediction"})

            assert result.score == pytest.approx(1.0)

    @staticmethod
    def test_evaluate_handles_result_boolean_false():
        """Test boolean False in result field."""
        with patch("openjiuwen.agent_evolving.evaluator.evaluator.Model") as mock_model_class:
            mock_model = _create_mock_model('```json{"result": false", "reason": "Incorrect"}```')
            mock_model_class.return_value = mock_model

            evaluator = DefaultEvaluator(
                model_config=make_mock_model_config(),
                model_client_config=make_mock_model_client(),
            )
            case = make_test_case()
            result = evaluator.evaluate(case, {"output": "model prediction"})

            assert result.score == pytest.approx(0.0)


class TestBatchEvaluateEdgeCases:
    """Edge case tests for batch_evaluate method."""

    @staticmethod
    def test_batch_evaluate_empty_cases_list():
        """Test batch_evaluate with empty cases list.

        Note: batch_evaluate with empty cases list raises ValueError
        because num_workers = min(num_parallel, 0) = 0 which is invalid.
        """

        class RealEvaluator(BaseEvaluator):
            def evaluate(self, case, predict):
                return MagicMock(score=1.0)

        real_eval = RealEvaluator()

        with pytest.raises(ValueError, match="max_workers must be greater than 0"):
            real_eval.batch_evaluate([], [], num_parallel=1)

    @staticmethod
    def test_batch_evaluate_single_case():
        """Test batch_evaluate with single case."""

        class RealEvaluator(BaseEvaluator):
            def evaluate(self, case, predict):
                return MagicMock(score=0.8)

        real_eval = RealEvaluator()
        cases = [make_test_case()]
        predicts = [{"output": "pred"}]
        result = real_eval.batch_evaluate(cases, predicts, num_parallel=1)

        assert len(result) == 1
        assert result[0].score == pytest.approx(0.8)

    @staticmethod
    def test_batch_evaluate_length_mismatch():
        """Test batch_evaluate raises error on length mismatch."""

        class RealEvaluator(BaseEvaluator):
            def evaluate(self, case, predict):
                return MagicMock(score=0.8)

        real_eval = RealEvaluator()
        cases = [make_test_case(), make_test_case()]
        predicts = [{"output": "pred"}]

        with pytest.raises(Exception):
            real_eval.batch_evaluate(cases, predicts, num_parallel=2)

    @staticmethod
    def test_batch_evaluate_parallel_processing():
        """Test batch_evaluate with parallel processing."""
        call_order = []

        class TrackingEvaluator(BaseEvaluator):
            def evaluate(self, case, predict):
                call_order.append(case.case_id)
                return MagicMock(score=0.9)

        evaluator = TrackingEvaluator()
        cases = [make_test_case(case_id=f"case_{i}") for i in range(3)]
        predicts = [{"output": f"pred_{i}"} for i in range(3)]

        result = evaluator.batch_evaluate(cases, predicts, num_parallel=2)

        assert len(result) == 3
        assert all(r.score == pytest.approx(0.9) for r in result)
