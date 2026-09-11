# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Structural protocols for Code Graph storage backends."""

from __future__ import annotations

from typing import Protocol

from openjiuwen.core.retrieval.code_graph.models import CodeGraphIndex


class IndexStore(Protocol):
    """Disk cache for a built Code Graph index."""

    def load(self, cache_key: str) -> CodeGraphIndex | None:
        """Return a cached index, or ``None`` on miss / version mismatch."""

    def save(self, cache_key: str, index: CodeGraphIndex) -> None:
        """Atomically persist ``index`` under ``cache_key``."""

    def delete(self, cache_key: str) -> None:
        """Remove a cache entry if it exists."""
