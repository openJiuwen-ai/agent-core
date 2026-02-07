# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for CaseLoader and related utilities."""
import pytest

from openjiuwen.agent_evolving.dataset import Case, CaseLoader
from openjiuwen.agent_evolving.dataset.case_loader import shuffle_cases, split_cases


def make_cases(count, start_id=0):
    """Factory for creating test case lists."""
    return [
        Case(inputs={"id": i}, label={"a": "b"})
        for i in range(start_id, start_id + count)
    ]


class TestShuffleCases:
    """Test shuffle_cases function."""

    @classmethod
    def test_deterministic_with_same_seed(cls):
        """Same seed produces same order."""
        cases = make_cases(5)
        result1 = shuffle_cases(cases, seed=42)
        result2 = shuffle_cases(cases, seed=42)
        assert [c.inputs["id"] for c in result1] == [c.inputs["id"] for c in result2]

    @classmethod
    def test_returns_new_list(cls):
        """Returns new list, original unchanged."""
        cases = make_cases(5)
        original_ids = [id(c) for c in cases]
        result = shuffle_cases(cases, seed=0)
        assert [id(c) for c in cases] == original_ids
        assert id(result) != id(cases)

    @classmethod
    def test_different_seeds_different_orders(cls):
        """Different seeds produce different orders."""
        cases = make_cases(10)
        result1 = shuffle_cases(cases, seed=1)
        result2 = shuffle_cases(cases, seed=2)
        assert [c.inputs["id"] for c in result1] != [c.inputs["id"] for c in result2]

    @classmethod
    def test_empty_list(cls):
        """Empty list returns empty."""
        assert shuffle_cases([], seed=0) == []

    @classmethod
    def test_single_element(cls):
        """Single element list."""
        cases = make_cases(1)
        result = shuffle_cases(cases, seed=0)
        assert len(result) == 1


class TestSplitCases:
    """Test split_cases function."""

    @classmethod
    def test_half_split(cls):
        """50% split."""
        cases = make_cases(10)
        left, right = split_cases(cases, 0.5)
        assert len(left) == 5
        assert len(right) == 5

    @classmethod
    def test_zero_ratio(cls):
        """ratio=0 returns empty left."""
        cases = make_cases(10)
        left, right = split_cases(cases, 0.0)
        assert len(left) == 0
        assert len(right) == 10

    @classmethod
    def test_one_ratio(cls):
        """ratio=1 returns empty right."""
        cases = make_cases(10)
        left, right = split_cases(cases, 1.0)
        assert len(left) == 10
        assert len(right) == 0

    @classmethod
    def test_quarter_split(cls):
        """25% split."""
        cases = make_cases(20)
        left, right = split_cases(cases, 0.25)
        assert len(left) == 5
        assert len(right) == 15

    @classmethod
    def test_negative_ratio_raises(cls):
        """Negative ratio raises ValueError."""
        cases = make_cases(10)
        with pytest.raises(ValueError):
            split_cases(cases, -0.1)

    @classmethod
    def test_ratio_over_one_raises(cls):
        """Ratio > 1 raises ValueError."""
        cases = make_cases(10)
        with pytest.raises(ValueError):
            split_cases(cases, 1.1)

    @classmethod
    def test_empty_list_splits(cls):
        """Empty list splits to two empty lists."""
        left, right = split_cases([], 0.5)
        assert left == []
        assert right == []


class TestCaseLoader:
    """Test CaseLoader class."""

    @classmethod
    def test_creation(cls):
        """Create CaseLoader from list."""
        cases = make_cases(5)
        loader = CaseLoader(cases)
        assert len(loader) == 5

    @classmethod
    def test_length(cls):
        """__len__ returns correct count."""
        loader = CaseLoader(make_cases(7))
        assert len(loader) == 7

    @classmethod
    def test_iteration(cls):
        """__iter__ yields cases."""
        cases = make_cases(3)
        loader = CaseLoader(cases)
        items = list(loader)
        assert len(items) == 3
        assert items[0].inputs["id"] == 0

    @classmethod
    def test_get_cases_returns_copy(cls):
        """get_cases returns copy."""
        cases = make_cases(3)
        loader = CaseLoader(cases)
        retrieved = loader.get_cases()
        assert len(retrieved) == 3
        assert retrieved is not cases
        assert retrieved == cases

    @classmethod
    def test_empty_loader(cls):
        """Empty loader."""
        loader = CaseLoader([])
        assert len(loader) == 0
        assert list(loader) == []

    @classmethod
    def test_split_method(cls):
        """split method returns two loaders."""
        loader = CaseLoader(make_cases(10))
        left, right = loader.split(0.5, seed=42)
        assert len(left) == 5
        assert len(right) == 5

    @classmethod
    def test_split_preserves_original(cls):
        """split doesn't modify original loader."""
        loader = CaseLoader(make_cases(10))
        loader.split(0.5, seed=0)
        assert len(loader) == 10

    @classmethod
    def test_split_different_seeds(cls):
        """Different seeds produce different splits."""
        loader = CaseLoader(make_cases(10))
        left1, _ = loader.split(0.5, seed=1)
        left2, _ = loader.split(0.5, seed=2)
        ids1 = [c.inputs["id"] for c in left1]
        ids2 = [c.inputs["id"] for c in left2]
        assert ids1 != ids2

    @classmethod
    def test_split_empty_loader(cls):
        """Split empty loader returns empty loaders."""
        loader = CaseLoader([])
        left, right = loader.split(0.5, seed=0)
        assert len(left) == 0
        assert len(right) == 0

    @classmethod
    def test_split_invalid_ratio(cls):
        """Invalid ratio raises ValueError."""
        loader = CaseLoader(make_cases(5))
        with pytest.raises(ValueError):
            loader.split(1.5, seed=0)

    @classmethod
    def test_split_zero_ratio(cls):
        """ratio=0 allowed."""
        loader = CaseLoader(make_cases(5))
        left, right = loader.split(0.0, seed=0)
        assert len(left) == 0
        assert len(right) == 5

    @classmethod
    def test_split_one_ratio(cls):
        """ratio=1 allowed."""
        loader = CaseLoader(make_cases(5))
        left, right = loader.split(1.0, seed=0)
        assert len(left) == 5
        assert len(right) == 0
