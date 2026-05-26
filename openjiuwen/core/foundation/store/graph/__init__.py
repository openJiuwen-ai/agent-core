# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Store

Provide graph-structured vector storage and retrieval capabilities
"""

__all__ = [
    # Graph store & factory
    "GraphStore",
    "GraphStoreFactory",
    # Graph store configurations
    "GraphConfig",
    "GraphStoreIndexConfig",
    "GraphStoreStorageConfig",
    # Constants
    "ENTITY_COLLECTION",
    "EPISODE_COLLECTION",
    "RELATION_COLLECTION",
    # Graph object definitions
    "Entity",
    "Episode",
    "Relation",
]

from .base import GraphStoreFactory
from .base_graph_store import GraphStore
from .config import GraphConfig, GraphStoreIndexConfig, GraphStoreStorageConfig
from .constants import ENTITY_COLLECTION, EPISODE_COLLECTION, RELATION_COLLECTION
from .graph_object import Entity, Episode, Relation

try:
    from .milvus import register_milvus_support

    register_milvus_support()
except ImportError:
    # Milvus is optional; it is registered eagerly only when its dependency is available.
    pass
