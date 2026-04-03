# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Database connector module for VectorNode persistence.

Provides connectors that mirror the :class:`JSONFileConnector` interface
but persist data to a real database:

- :class:`MilvusConnector` – Milvus vector database (≥ 2.3, default port
  19530).  Stores embeddings as ``FLOAT_VECTOR`` columns and supports fast
  ANN search via HNSW / IVF indexes.  Requires ``pymilvus``.
"""

from .milvus_connector import MilvusConnector

__all__ = ["MilvusConnector"]
