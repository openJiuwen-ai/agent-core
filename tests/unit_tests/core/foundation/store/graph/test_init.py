# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph package exports (__init__.py)."""

from openjiuwen.core.foundation.store import graph as graph_module


class TestExports:
    """Test that package exports are present."""

    @staticmethod
    def test_exports():
        """Import from openjiuwen.core.foundation.store.graph and assert expected names are present."""
        assert hasattr(graph_module, "GraphStore")
        assert hasattr(graph_module, "GraphStoreFactory")
        assert hasattr(graph_module, "GraphConfig")
        assert hasattr(graph_module, "GraphStoreIndexConfig")
        assert hasattr(graph_module, "GraphStoreStorageConfig")
        assert hasattr(graph_module, "ENTITY_COLLECTION")
        assert hasattr(graph_module, "EPISODE_COLLECTION")
        assert hasattr(graph_module, "RELATION_COLLECTION")
        assert hasattr(graph_module, "Entity")
        assert hasattr(graph_module, "Episode")
        assert hasattr(graph_module, "Relation")
