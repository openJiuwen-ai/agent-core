# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.foundation.store.kv.db_based_kv_store import DbBasedKVStore
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore

# Vector store exports
from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore,
    VectorSearchResult,
    CollectionSchema,
    FieldSchema,
    VectorDataType,
)


def create_vector_store(store_type: str, **kwargs) -> BaseVectorStore | None:
    if store_type == "chroma":
        from openjiuwen.core.foundation.store.vector.chroma_vector_store import ChromaVectorStore
        return ChromaVectorStore(**kwargs)
    elif store_type == "milvus":
        from openjiuwen.core.foundation.store.vector.milvus_vector_store import MilvusVectorStore
        return MilvusVectorStore(**kwargs)
    else:
        return None


__all__ = [
    "BaseDbStore",
    "BaseKVStore",
    "BaseVectorStore",
    'DbBasedKVStore',
    'InMemoryKVStore',
    'DefaultDbStore',
    "VectorSearchResult",
    "CollectionSchema",
    "FieldSchema",
    "VectorDataType",
    "create_vector_store",
]
