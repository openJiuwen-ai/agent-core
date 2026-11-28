# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Opt-in application-level caching for read-only, single-turn text answers."""

from openjiuwen.extensions.semantic_cache.cache import (
    CacheCandidate,
    CacheResult,
    CacheScope,
    CacheStats,
    GeneratedAnswer,
    SemanticResponseCache,
)

__all__ = [
    "CacheCandidate",
    "CacheResult",
    "CacheScope",
    "CacheStats",
    "GeneratedAnswer",
    "SemanticResponseCache",
]
