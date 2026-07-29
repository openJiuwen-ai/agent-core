# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Thin KVC policy hooks for DeepAgent subagent lifecycles."""

from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.kv_cache import (
    dispatch_session_kv_cache_signal,
    evict_session_kv_cache,
)


def affinity_enabled(deep_agent: Any) -> bool:
    """Return without inspecting model/binding state when affinity is disabled."""
    deep_config = getattr(deep_agent, "deep_config", None)
    kv_config = getattr(deep_config, "kv_cache_affinity_config", None)
    return getattr(kv_config, "enable_kv_cache_affinity", False) is True


def is_sticky_subagent_type(subagent_type: str) -> bool:
    return str(subagent_type or "").strip() in ("browser_agent", "verification_agent")


def resolve_sub_session_id(
        *,
        task_id: str,
        parent_session_id: str,
        metadata: dict,
) -> str:
    sub_session_id = metadata.get("sub_session_id")
    if sub_session_id:
        return str(sub_session_id)
    safe_task_id = str(task_id or "").strip() or "unknown"
    return f"{parent_session_id}_sub_{safe_task_id}"


def get_model(deep_agent: Any) -> Any | None:
    deep_config = getattr(deep_agent, "deep_config", None)
    return getattr(deep_config, "model", None) if deep_config is not None else None


def prefetch_sticky_subagent(
        deep_agent: Any,
        *,
        subagent_type: str,
        sub_session_id: str,
        parent_session_id: str,
) -> None:
    if not affinity_enabled(deep_agent) or not is_sticky_subagent_type(subagent_type):
        return
    try:
        dispatch_session_kv_cache_signal(
            get_model(deep_agent),
            "prefetch",
            session_id=sub_session_id,
            parent_session_id=parent_session_id,
            enabled=True,
        )
    except Exception as exc:
        logger.warning(
            "[HarnessKVC] subagent prefetch failed: sub_session=%s parent_session=%s error=%s",
            sub_session_id,
            parent_session_id,
            exc,
        )


async def finish_subagent(
        deep_agent: Any,
        *,
        subagent_type: str,
        sub_session_id: str,
        parent_session_id: str,
        succeeded: bool,
) -> None:
    """Offload resumable successful workers; evict terminal/failed workers."""
    if not affinity_enabled(deep_agent):
        return
    model = get_model(deep_agent)
    try:
        if succeeded and is_sticky_subagent_type(subagent_type):
            dispatch_session_kv_cache_signal(
                model,
                "offload",
                session_id=sub_session_id,
                parent_session_id=parent_session_id,
                enabled=True,
            )
            return
        await evict_session_kv_cache(
            model,
            session_id=sub_session_id,
            parent_session_id=parent_session_id,
            enabled=True,
        )
    except Exception as exc:
        logger.warning(
            "[HarnessKVC] subagent cleanup failed: sub_session=%s parent_session=%s error=%s",
            sub_session_id,
            parent_session_id,
            exc,
        )


async def evict_subagent(
        deep_agent: Any,
        *,
        sub_session_id: str,
        parent_session_id: str,
) -> None:
    if not affinity_enabled(deep_agent):
        return
    try:
        await evict_session_kv_cache(
            get_model(deep_agent),
            session_id=sub_session_id,
            parent_session_id=parent_session_id,
            enabled=True,
        )
    except Exception as exc:
        logger.warning(
            "[HarnessKVC] subagent evict failed: sub_session=%s parent_session=%s error=%s",
            sub_session_id,
            parent_session_id,
            exc,
        )
