# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph store constants."""

from openjiuwen.core.foundation.store.graph.constants import (
    ARRAY_LIMIT,
    DEFAULT_WORKER_NUM,
    ENTITY_COLLECTION,
    EPISODE_COLLECTION,
    RELATION_COLLECTION,
    VARCHAR_LIMIT,
)


class TestConstantsValues:
    """Test collection name constants."""

    @staticmethod
    def test_entity_collection_value():
        """Assert ENTITY_COLLECTION has expected string value."""
        assert ENTITY_COLLECTION == "ENTITY_COLLECTION"

    @staticmethod
    def test_relation_collection_value():
        """Assert RELATION_COLLECTION has expected string value."""
        assert RELATION_COLLECTION == "RELATION_COLLECTION"

    @staticmethod
    def test_episode_collection_value():
        """Assert EPISODE_COLLECTION has expected string value."""
        assert EPISODE_COLLECTION == "EPISODE_COLLECTION"


class TestVarcharLimit:
    """Test VARCHAR_LIMIT constant."""

    @staticmethod
    def test_varchar_limit_is_dict():
        """Assert VARCHAR_LIMIT is a dict with gt=1, le=65535."""
        assert isinstance(VARCHAR_LIMIT, dict)
        assert VARCHAR_LIMIT["gt"] == 1
        assert VARCHAR_LIMIT["le"] == 65535


class TestArrayLimit:
    """Test ARRAY_LIMIT constant."""

    @staticmethod
    def test_array_limit_is_dict():
        """Assert ARRAY_LIMIT is a dict with gt=1, le=4096."""
        assert isinstance(ARRAY_LIMIT, dict)
        assert ARRAY_LIMIT["gt"] == 1
        assert ARRAY_LIMIT["le"] == 4096


class TestDefaultWorkerNum:
    """Test DEFAULT_WORKER_NUM constant."""

    @staticmethod
    def test_default_worker_num_value():
        """Assert DEFAULT_WORKER_NUM == 10."""
        assert DEFAULT_WORKER_NUM == 10
