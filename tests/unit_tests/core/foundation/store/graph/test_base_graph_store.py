# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for graph store protocol (base_graph_store.py)."""

from unittest.mock import MagicMock

from openjiuwen.core.foundation.store.graph.base_graph_store import GraphStore


class TestGraphStoreProtocol:
    """Protocol compliance and isinstance check."""

    @staticmethod
    def test_mock_implementing_protocol_is_instance_of_graph_store():
        """A minimal implementation with required members is recognized by isinstance(..., GraphStore)."""
        mock_store = MagicMock(spec=GraphStore)
        assert isinstance(mock_store, GraphStore)

    @staticmethod
    def test_protocol_has_required_members():
        """Protocol defines config, semophore, embedder, from_config, rebuild, refresh, add_data, etc."""
        required = [
            "config",
            "semophore",
            "embedder",
            "from_config",
            "rebuild",
            "refresh",
            "add_data",
            "add_entity",
            "add_relation",
            "add_episode",
            "is_empty",
            "query",
            "delete",
            "search",
            "attach_embedder",
            "close",
        ]
        for attr in required:
            assert hasattr(GraphStore, attr)
