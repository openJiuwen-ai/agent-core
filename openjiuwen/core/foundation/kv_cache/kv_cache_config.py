# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pydantic import BaseModel, Field, model_validator
from typing_extensions import Self


# KVC management is a best-effort optimization.  Keep every user-visible
# waiting budget here so inference-side integration tuning does not require
# hunting through lifecycle call sites.  These are whole-action budgets (HTTP,
# retries and retry backoff), not per-attempt transport timeouts.
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
    """Runtime KV-cache affinity switches shared by agent and context flows."""

    enable_kv_cache_release: bool = Field(default=False)
    enable_kv_cache_affinity: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_exclusive_modes(self) -> Self:
        if self.enable_kv_cache_release and self.enable_kv_cache_affinity:
            raise ValueError(
                "enable_kv_cache_release and enable_kv_cache_affinity cannot both be True"
            )
        return self
