# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for Milvus graph store package __init__ and registration."""

from openjiuwen.core.foundation.store.graph.base import GraphStoreFactory
from openjiuwen.core.foundation.store.graph.result_ranking import RANKER_CLS


class TestRegisterMilvusSupport:
    """Tests for register_milvus_support()."""

    @staticmethod
    def test_milvus_backend_registered_in_factory():
        """After import, 'milvus' backend is registered in GraphStoreFactory."""
        assert "milvus" in GraphStoreFactory.class_map
        from openjiuwen.core.foundation.store.graph.milvus.milvus_support import MilvusGraphStore

        assert GraphStoreFactory.class_map["milvus"] is MilvusGraphStore

    @staticmethod
    def test_milvus_ranker_cls_registered():
        """After import, 'milvus' has weighted and rrf in RANKER_CLS."""
        assert "milvus" in RANKER_CLS
        assert "weighted" in RANKER_CLS["milvus"]
        assert "rrf" in RANKER_CLS["milvus"]
        assert RANKER_CLS["milvus"]["weighted"] is not None
        assert RANKER_CLS["milvus"]["rrf"] is not None

    @staticmethod
    def test_register_is_idempotent():
        """Calling register_milvus_support again does not double-register."""
        from openjiuwen.core.foundation.store.graph.milvus import (
            register_milvus_support,
        )
        from openjiuwen.core.foundation.store.graph.milvus.milvus_support import MilvusGraphStore

        register_milvus_support()
        assert GraphStoreFactory.class_map["milvus"] is MilvusGraphStore
        # Second call should be no-op (MILVUS_SUPPORT_REGISTERED is True)
        register_milvus_support()
        assert GraphStoreFactory.class_map["milvus"] is MilvusGraphStore
