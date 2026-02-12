# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for Metric base class and interface."""

import pytest

from openjiuwen.agent_evolving.evaluator.metrics.base import Metric, MetricResult


class ConcreteMetric(Metric):
    """Concrete implementation for testing."""

    @property
    def name(self) -> str:
        return "test_metric"

    def compute(self, prediction, label, **kwargs) -> MetricResult:
        return 1.0 if prediction == label else 0.0


class TestMetricBase:
    """Test Metric base class."""

    @staticmethod
    def test_name_property():
        """Concrete metric has name property."""
        assert ConcreteMetric().name == "test_metric"

    @staticmethod
    def test_higher_is_better_default_true():
        """higher_is_better defaults to True."""
        assert ConcreteMetric().higher_is_better is True

    @staticmethod
    def test_compute_batch_returns_scores():
        """compute_batch returns list of scores."""
        metric = ConcreteMetric()
        results = metric.compute_batch(["a", "b", "a"], ["a", "a", "a"])
        assert results == [1.0, 0.0, 1.0]

    @staticmethod
    def test_compute_batch_empty():
        """compute_batch with empty lists."""
        assert ConcreteMetric().compute_batch([], []) == []
