# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Milvus Graph Store

Provide milvus database support for graph-structured vector storage and retrieval capabilities
"""

__all__ = ["MilvusGraphStore", "register_milvus_support"]

from threading import Lock

from openjiuwen.core.foundation.store.graph.result_ranking import register_result_ranker_cls

from ..base import GraphStoreFactory

MILVUS_SUPPORT_REGISTERED: bool = False
_MILVUS_SUPPORT_REGISTER_LOCK = Lock()


def _load_milvus_support():
    try:
        from pymilvus import RRFRanker as rrf_ranker_cls
        from pymilvus import WeightedRanker as weighted_ranker_cls
    except ImportError as exc:
        raise ImportError(
            "Milvus graph store requires optional dependency 'pymilvus'. "
            "Install the Milvus dependencies before using the milvus graph backend."
        ) from exc

    from .milvus_support import MilvusGraphStore as milvus_graph_store_cls

    return milvus_graph_store_cls, weighted_ranker_cls, rrf_ranker_cls


def register_milvus_support():
    """Register Milvus Support, does nothing if registration is already done"""
    global MILVUS_SUPPORT_REGISTERED

    with _MILVUS_SUPPORT_REGISTER_LOCK:
        if not MILVUS_SUPPORT_REGISTERED:
            milvus_graph_store_cls, weighted_ranker_cls, rrf_ranker_cls = _load_milvus_support()
            GraphStoreFactory.register_backend("milvus", milvus_graph_store_cls)
            register_result_ranker_cls(name="milvus", weighted=weighted_ranker_cls, rrf=rrf_ranker_cls)
            MILVUS_SUPPORT_REGISTERED = True


def __getattr__(name: str):
    if name == "MilvusGraphStore":
        milvus_graph_store_cls, _, _ = _load_milvus_support()
        return milvus_graph_store_cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
