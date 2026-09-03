# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build KVC lineage and Runtime inheritance for single-agent child Sessions."""

from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.kv_cache.kv_cache_metadata import (
    KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV,
    resolve_session_lineage,
)


def build_child_session_kwargs(agent: Any, parent_session: Any) -> dict:
    """Return no Session mutation when the child Agent has affinity disabled."""
    config_fn = getattr(agent, "config", None)
    config = config_fn() if callable(config_fn) else getattr(agent, "_config", None)
    kv_config = getattr(config, "kv_cache_affinity_config", None)
    if getattr(kv_config, "enable_kv_cache_affinity", False) is not True:
        return {}
    child_envs = dict(parent_session.get_envs() or {})
    runtime_session_id = parent_session.get_session_id()
    try:
        parent_cache_id, _ = resolve_session_lineage(parent_session)
    except Exception as exc:
        logger.warning(
            "KVC child lineage resolution failed; using runtime session: session_id=%s error=%s",
            runtime_session_id,
            exc,
        )
        parent_cache_id = runtime_session_id
    child_envs[KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV] = parent_cache_id or runtime_session_id
    return {
        "envs": child_envs,
        "parent_session_id": parent_cache_id or runtime_session_id,
        "kv_cache_runtime": parent_session.get_kv_cache_runtime(),
    }


__all__ = ["build_child_session_kwargs"]
