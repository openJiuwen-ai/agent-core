# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Store

Provide graph-structured vector storage and retrieval capabilities
"""

__all__ = ["GraphStore", "GraphStoreFactory"]

from .base import GraphStoreFactory
from .graph_backend import GraphStore
