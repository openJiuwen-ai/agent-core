# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for result ranking."""

import pytest
from pydantic import ValidationError

from openjiuwen.core.foundation.store.graph.result_ranking import (
    RANKER_CLS,
    BaseRankConfig,
    RRFRankConfig,
    WeightedRankConfig,
    register_result_ranker_cls,
)


class TestRegisterResultRankerCls:
    """Tests for register_result_ranker_cls."""

    @staticmethod
    def test_register_and_retrieve_from_ranker_cls():
        """Register name -> dict(weighted=..., rrf=..., **kwargs); retrieve via RANKER_CLS."""

        def weighted_fn():
            pass

        def rrf_fn():
            pass

        register_result_ranker_cls("test_db", weighted=weighted_fn, rrf=rrf_fn, extra="value")
        assert "test_db" in RANKER_CLS
        assert RANKER_CLS["test_db"]["weighted"] is weighted_fn
        assert RANKER_CLS["test_db"]["rrf"] is rrf_fn
        assert RANKER_CLS["test_db"]["extra"] == "value"
        # Clean up to avoid affecting other tests
        del RANKER_CLS["test_db"]


class TestBaseRankConfig:
    """Tests for BaseRankConfig."""

    @staticmethod
    def test_abstract_cannot_instantiate():
        """Abstract: cannot instantiate (args is abstract)."""
        with pytest.raises(TypeError):
            BaseRankConfig()

    @staticmethod
    def test_name_base_higher_is_better_false():
        """name='base', higher_is_better=False (via concrete subclass)."""

        class ConcreteRankConfig(BaseRankConfig):
            @property
            def args(self):
                return [], {}

        cfg = ConcreteRankConfig()
        assert cfg.name == "base"
        assert cfg.higher_is_better is False

    @staticmethod
    def test_is_active_returns_ones():
        """is_active returns [1, 1, 1]."""

        class ConcreteRankConfig(BaseRankConfig):
            @property
            def args(self):
                return [], {}

        cfg = ConcreteRankConfig()
        assert cfg.is_active == [1, 1, 1]

    @staticmethod
    def test_get_ranker_cls_returns_from_ranker_cls():
        """get_ranker_cls(database) returns from RANKER_CLS or None."""

        class ConcreteRankConfig(BaseRankConfig):
            name: str = "weighted"

            @property
            def args(self):
                return [], {}

        cfg = ConcreteRankConfig()
        # Unknown database returns None from RANKER_CLS.get("nonexistent_db_xyz", {}).get("weighted")
        result = cfg.get_ranker_cls("nonexistent_db_xyz")
        assert result is None or callable(result)


class TestWeightedRankConfig:
    """Tests for WeightedRankConfig."""

    @staticmethod
    def test_defaults():
        """Defaults: dense_name=0.15, dense_content=0.6, sparse_content=0.25."""
        cfg = WeightedRankConfig()
        assert cfg.dense_name == 0.15
        assert cfg.dense_content == 0.6
        assert cfg.sparse_content == 0.25

    @staticmethod
    def test_args_normalized_weights_sum_one():
        """args: normalized weights (sum 1) when sum > 0; else [], {}."""
        cfg = WeightedRankConfig(dense_name=0.2, dense_content=0.2, sparse_content=0.2)
        args_list, args_dict = cfg.args
        assert sum(args_list) == pytest.approx(1.0)
        assert args_dict == {}

    @staticmethod
    def test_args_zero_weights_returns_empty():
        """When all weights 0, args returns [], {}."""
        cfg = WeightedRankConfig(dense_name=0, dense_content=0, sparse_content=0)
        args_list, args_dict = cfg.args
        assert args_list == []
        assert args_dict == {}

    @staticmethod
    def test_bounds_weights_in_zero_one():
        """Bounds: weights in [0, 1]."""
        cfg = WeightedRankConfig(dense_name=0, dense_content=1, sparse_content=0.5)
        assert 0 <= cfg.dense_name <= 1
        assert 0 <= cfg.dense_content <= 1
        assert 0 <= cfg.sparse_content <= 1
        with pytest.raises(ValidationError):
            WeightedRankConfig(dense_name=1.5)


class TestRRFRankConfig:
    """Tests for RRFRankConfig."""

    @staticmethod
    def test_name_rrf_higher_is_better_k_default():
        """name='rrf', higher_is_better=True, k=40, dense_name/content/sparse_content bools."""
        cfg = RRFRankConfig()
        assert cfg.name == "rrf"
        assert cfg.higher_is_better is True
        assert cfg.k == 40
        assert cfg.dense_name is True
        assert cfg.dense_content is True
        assert cfg.sparse_content is True

    @staticmethod
    def test_args_returns_k_and_empty_dict():
        """args returns [k], {}."""
        cfg = RRFRankConfig(k=60)
        args_list, args_dict = cfg.args
        assert args_list == [60]
        assert args_dict == {}

    @staticmethod
    def test_is_active_from_bools():
        """is_active returns list of 0/1 from the three bools."""
        cfg = RRFRankConfig(dense_name=True, dense_content=False, sparse_content=True)
        assert cfg.is_active == [1, 0, 1]
        cfg2 = RRFRankConfig(dense_name=False, dense_content=False, sparse_content=False)
        assert cfg2.is_active == [0, 0, 0]
