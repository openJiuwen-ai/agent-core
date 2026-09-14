# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pydantic import BaseModel, Field


# KVC management is a best-effort optimization. These are whole-action
# budgets (HTTP, retries and retry backoff), not transport timeouts.
KVC_RANGE_ACTION_TIMEOUT_SECONDS = 1.5
KVC_SESSION_OFFLOAD_PREFETCH_TIMEOUT_SECONDS = 2.0
KVC_SESSION_EVICT_TIMEOUT_SECONDS = 3.0
KVC_TERMINAL_CLEANUP_TIMEOUT_SECONDS = 5.0
KVC_MANAGEMENT_MAX_ATTEMPTS = 1


def resolve_kvc_action_timeout(
        action: str,
        target: str,
        timeout: float | None = None,
) -> float:
    """Resolve one explicit whole-action KVC timeout."""
    if timeout is not None:
        return float(timeout)
    if target in {"messages", "tools"}:
        return KVC_RANGE_ACTION_TIMEOUT_SECONDS
    if action == "evict":
        return KVC_SESSION_EVICT_TIMEOUT_SECONDS
    return KVC_SESSION_OFFLOAD_PREFETCH_TIMEOUT_SECONDS


class KVCacheAffinityConfig(BaseModel):
    """Enable the unified ``agent_hint`` KV-cache affinity protocol."""

    enable_kv_cache_affinity: bool = Field(default=False)
