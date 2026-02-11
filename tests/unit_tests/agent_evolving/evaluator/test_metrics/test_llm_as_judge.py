# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for LLMAsJudgeMetric - LLM-based evaluation metric."""

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge import LLMAsJudgeMetric


def _wrap_response(json_str: str) -> str:
    """Wrap JSON in the format LLM would return."""
    return f"```json\n{json_str}\n```"


def _make_response(result_value: str) -> str:
    """Create a response with result field."""
    return _wrap_response(f'{{"result": {result_value}}}')


class TestLLMAsJudgeMetricCompute:
    """Test LLMAsJudgeMetric.compute method via public API."""

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.asyncio.run")
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_compute_model_error(mock_model, mock_run):
        """compute returns 0.0 on model invocation error."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        mock_run.side_effect = Exception("model error")

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        result = metric.compute("prediction", "label", question="question")
        assert result == 0.0

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.asyncio.run")
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_compute_returns_true(mock_model, mock_run):
        """compute returns 1.0 when LLM judges as true."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        mock_run.return_value = MagicMock(content=_make_response("true"))

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        result = metric.compute("prediction", "label", question="question")
        assert result == 1.0

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.asyncio.run")
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_compute_returns_false(mock_model, mock_run):
        """compute returns 0.0 when LLM judges as false."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        mock_run.return_value = MagicMock(content=_make_response("false"))

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        result = metric.compute("prediction", "label", question="question")
        assert result == 0.0

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.asyncio.run")
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_compute_with_none_question(mock_model, mock_run):
        """compute handles None question."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        mock_run.return_value = MagicMock(content=_make_response("true"))

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        result = metric.compute("prediction", "label", question=None)
        assert result == 1.0

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.asyncio.run")
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_compute_with_string_true(mock_model, mock_run):
        """compute parses 'true' string result."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        mock_run.return_value = MagicMock(content=_make_response('"true"'))

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        result = metric.compute("prediction", "label")
        assert result == 1.0

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.asyncio.run")
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_compute_with_invalid_json(mock_model, mock_run):
        """compute returns 0.0 on invalid JSON response."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        mock_run.return_value = MagicMock(content="not json")

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        result = metric.compute("prediction", "label")
        assert result == 0.0

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.asyncio.run")
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_compute_with_string_false(mock_model, mock_run):
        """compute parses 'false' string result."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        mock_run.return_value = MagicMock(content=_make_response('"false"'))

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        result = metric.compute("prediction", "label")
        assert result == 0.0

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.asyncio.run")
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_compute_with_whitespace_in_result(mock_model, mock_run):
        """compute handles whitespace in string result."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        mock_run.return_value = MagicMock(content=_make_response('"  true  "'))

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        result = metric.compute("prediction", "label")
        assert result == 1.0


class TestLLMAsJudgeMetricProperties:
    """Test LLMAsJudgeMetric properties through public API."""

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_name_property(mock_model):
        """Metric name is 'llm_as_judge'."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        assert metric.name == "llm_as_judge"

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_higher_is_better_property(mock_model):
        """Higher is better returns True."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        assert metric.higher_is_better is True


class TestLLMAsJudgeMetricInit:
    """Test LLMAsJudgeMetric initialization."""

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_init_with_empty_metrics(mock_model):
        """Initialize with empty user_metrics."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
        )
        assert getattr(metric, "_model") is not None
        assert hasattr(metric, "_template")

    @staticmethod
    @patch("openjiuwen.agent_evolving.evaluator.metrics.llm_as_judge.Model")
    def test_init_with_custom_metrics(mock_model):
        """Initialize with custom user_metrics."""
        from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

        metric = LLMAsJudgeMetric(
            model_config=ModelRequestConfig(model="test"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI", api_key="test", api_base="https://test.example.com"
            ),
            user_metrics="custom_metric",
        )
        assert getattr(metric, "_model") is not None
