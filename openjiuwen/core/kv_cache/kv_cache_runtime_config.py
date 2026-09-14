# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from dataclasses import dataclass

from openjiuwen.core.kv_cache.kv_cache_config import (
    KVC_SESSION_EVICT_TIMEOUT_SECONDS,
    KVC_SESSION_OFFLOAD_PREFETCH_TIMEOUT_SECONDS,
    KVC_TERMINAL_CLEANUP_TIMEOUT_SECONDS,
)


@dataclass(frozen=True, slots=True)
class KVCacheRuntimeConfig:
    """Process-local scheduling budgets for Session-level KVC actions."""

    action_timeout: float = KVC_SESSION_OFFLOAD_PREFETCH_TIMEOUT_SECONDS
    evict_timeout: float = KVC_SESSION_EVICT_TIMEOUT_SECONDS
    close_timeout: float = KVC_TERMINAL_CLEANUP_TIMEOUT_SECONDS
