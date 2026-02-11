# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for ExactMatchMetric."""

import pytest

from openjiuwen.agent_evolving.evaluator.metrics.exact_match import ExactMatchMetric


def make_exact_match_metric(normalize=False):
    """Factory for creating ExactMatchMetric instances."""
    return ExactMatchMetric(normalize=normalize)


class TestExactMatchMetric:
    """Test ExactMatchMetric."""

    @staticmethod
    def test_identical_strings_match():
        """Identical strings match."""
        metric = make_exact_match_metric(normalize=False)
        assert metric.compute("hello", "hello") == 1.0

    @staticmethod
    def test_different_strings_no_match():
        """Different strings don't match."""
        metric = make_exact_match_metric(normalize=False)
        assert metric.compute("hello", "world") == 0.0

    @staticmethod
    def test_case_sensitive_by_default():
        """Case sensitive by default (normalize=False)."""
        metric = make_exact_match_metric(normalize=False)
        assert metric.compute("Hello", "hello") == 0.0

    @staticmethod
    def test_normalize_ignores_case():
        """Normalize ignores case."""
        metric = make_exact_match_metric(normalize=True)
        assert metric.compute("Hello", "hello") == 1.0

    @staticmethod
    def test_normalize_ignores_whitespace():
        """Normalize ignores whitespace."""
        metric = make_exact_match_metric(normalize=True)
        assert metric.compute("hello world", "hello   world") == 1.0

    @staticmethod
    def test_normalize_ignores_leading_trailing():
        """Normalize ignores leading/trailing whitespace."""
        metric = make_exact_match_metric(normalize=True)
        assert metric.compute(" hello ", "hello") == 1.0

    @staticmethod
    def test_normalize_converts_tabs_newlines():
        """Normalize converts tabs and newlines to spaces."""
        metric = make_exact_match_metric(normalize=True)
        assert metric.compute("hello\nworld", "hello world") == 1.0
        assert metric.compute("hello\tworld", "hello world") == 1.0

    @staticmethod
    def test_normalize_collapses_spaces():
        """Normalize collapses multiple spaces."""
        metric = make_exact_match_metric(normalize=True)
        assert metric.compute("hello     world", "hello world") == 1.0

    @staticmethod
    def test_normalize_handles_none():
        """Normalize handles None."""
        metric = make_exact_match_metric(normalize=True)
        assert metric.compute(None, "") == 0.0
        assert metric.compute("x", None) == 0.0

    @staticmethod
    def test_normalize_with_numeric_values():
        """Normalize works with numeric values."""
        metric = make_exact_match_metric(normalize=True)
        assert metric.compute(123, 123) == 1.0
        assert metric.compute("123", 123) == 1.0

    @staticmethod
    def test_normalize_with_mixed_types():
        """Normalize works with mixed types."""
        metric = make_exact_match_metric(normalize=True)
        assert metric.compute("True", True) == 1.0
        assert metric.compute("False", False) == 1.0

    @staticmethod
    def test_normalize_with_float_strings():
        """Normalize works with float-like strings."""
        metric = make_exact_match_metric(normalize=True)
        assert metric.compute("1.5", 1.5) == 1.0

    @staticmethod
    def test_name_property():
        """Name is 'exact_match'."""
        assert ExactMatchMetric().name == "exact_match"

    @staticmethod
    def test_higher_is_better_property():
        """higher_is_better is True."""
        assert ExactMatchMetric().higher_is_better is True

    @staticmethod
    def test_compute_accepts_kwargs():
        """compute accepts kwargs gracefully."""
        metric = ExactMatchMetric()
        assert metric.compute("a", "a", extra_param="ignored") == 1.0

    @staticmethod
    def test_normalize_empty_strings():
        """Normalize considers empty strings equal."""
        metric = make_exact_match_metric(normalize=True)
        assert metric.compute("", "") == 1.0

    @staticmethod
    def test_normalize_handles_special_chars():
        """Normalize handles special characters - different special chars are different."""
        metric = make_exact_match_metric(normalize=True)
        # Special chars are preserved, different special chars don't match
        assert metric.compute("hello!", "hello.") == 0.0

    @staticmethod
    def test_normalize_handles_unicode():
        """Normalize handles unicode characters."""
        metric = make_exact_match_metric(normalize=True)
        assert metric.compute("café", "Café") == 1.0
