# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph database configuration."""

import pytest
from pydantic import ValidationError

from openjiuwen.core.foundation.store.graph.database_config import (
    BM25Config,
    GraphStoreIndexConfig,
    GraphStoreStorageConfig,
)
from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusAUTO


class TestBM25Config:
    """Tests for BM25Config."""

    @staticmethod
    def test_default_values():
        """Default values: bm25_b=0.75, bm25_k1=1.2."""
        cfg = BM25Config()
        assert cfg.bm25_b == 0.75
        assert cfg.bm25_k1 == 1.2

    @staticmethod
    def test_bm25_b_in_range():
        """Validation: bm25_b in [0, 1]."""
        assert BM25Config(bm25_b=0).bm25_b == 0
        assert BM25Config(bm25_b=1).bm25_b == 1

    @staticmethod
    def test_bm25_b_out_of_range_raises():
        """bm25_b > 1 raises ValidationError."""
        with pytest.raises(ValidationError):
            BM25Config(bm25_b=1.5)

    @staticmethod
    def test_bm25_k1_ge_zero():
        """Validation: bm25_k1 >= 0."""
        assert BM25Config(bm25_k1=0).bm25_k1 == 0

    @staticmethod
    def test_bm25_k1_negative_raises():
        """bm25_k1 < 0 raises ValidationError."""
        with pytest.raises(ValidationError):
            BM25Config(bm25_k1=-0.1)


class TestGraphStoreIndexConfig:
    """Tests for GraphStoreIndexConfig."""

    @staticmethod
    def test_required_fields():
        """Required fields: index_type, distance_metric."""
        cfg = GraphStoreIndexConfig(
            index_type=MilvusAUTO(),
            distance_metric="cosine",
        )
        assert cfg.distance_metric == "cosine"
        assert cfg.index_type is not None

    @staticmethod
    def test_defaults():
        """Defaults: extra_configs={}, bm25_config is BM25Config instance."""
        cfg = GraphStoreIndexConfig(
            index_type=MilvusAUTO(),
            distance_metric="euclidean",
        )
        assert cfg.extra_configs == {}
        assert isinstance(cfg.bm25_config, BM25Config)

    @staticmethod
    def test_distance_metric_cosine():
        """distance_metric accepts 'cosine'."""
        cfg = GraphStoreIndexConfig(index_type=MilvusAUTO(), distance_metric="cosine")
        assert cfg.distance_metric == "cosine"

    @staticmethod
    def test_distance_metric_euclidean():
        """distance_metric accepts 'euclidean'."""
        cfg = GraphStoreIndexConfig(index_type=MilvusAUTO(), distance_metric="euclidean")
        assert cfg.distance_metric == "euclidean"

    @staticmethod
    def test_distance_metric_dot():
        """distance_metric accepts 'dot'."""
        cfg = GraphStoreIndexConfig(index_type=MilvusAUTO(), distance_metric="dot")
        assert cfg.distance_metric == "dot"

    @staticmethod
    def test_distance_metric_invalid_raises():
        """distance_metric other than cosine/euclidean/dot raises ValidationError."""
        with pytest.raises(ValidationError):
            GraphStoreIndexConfig(
                index_type=MilvusAUTO(),
                distance_metric="l2",
            )


class TestGraphStoreStorageConfig:
    """Tests for GraphStoreStorageConfig."""

    @staticmethod
    def test_default_values():
        """Default values for uuid, name, content, language, user_id, entities, relations, episodes, obj_type."""
        cfg = GraphStoreStorageConfig()
        assert cfg.uuid == 32
        assert cfg.name == 500
        assert cfg.content == 65535
        assert cfg.language == 10
        assert cfg.user_id == 32
        assert cfg.entities == 4096
        assert cfg.relations == 4096
        assert cfg.episodes == 4096
        assert cfg.obj_type == 20

    @staticmethod
    def test_varchar_field_out_of_range_raises():
        """Varchar limit: value <= 1 or > 65535 raises ValidationError."""
        with pytest.raises(ValidationError):
            GraphStoreStorageConfig(uuid=1)  # gt=1, so 1 is invalid
        with pytest.raises(ValidationError):
            GraphStoreStorageConfig(uuid=0)
        with pytest.raises(ValidationError):
            GraphStoreStorageConfig(name=65536)

    @staticmethod
    def test_array_limit_out_of_range_raises():
        """Array limit: value <= 1 or > 4096 raises ValidationError."""
        with pytest.raises(ValidationError):
            GraphStoreStorageConfig(entities=1)  # gt=1
        with pytest.raises(ValidationError):
            GraphStoreStorageConfig(relations=5000)  # le=4096
