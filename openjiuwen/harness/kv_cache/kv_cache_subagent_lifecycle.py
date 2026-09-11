# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""KVC lifecycle policy for DeepAgent subagents."""

import hashlib
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.kv_cache.kv_cache_metadata import resolve_session_lineage
from openjiuwen.core.session.agent import Session, create_agent_session


def affinity_enabled(deep_agent: Any) -> bool:
    """Return without inspecting model/binding state when affinity is disabled."""
    deep_config = getattr(deep_agent, "deep_config", None)
    kv_config = getattr(deep_config, "kv_cache_affinity_config", None)
    return getattr(kv_config, "enable_kv_cache_affinity", False) is True


def is_sticky_subagent_type(subagent_type: str) -> bool:
    # Browser process/profile reuse is owned by BrowserServiceRegistry. Keeping
    # its model session sticky would leak the previous query's tools/PageState
    # into an unrelated TaskTool call.
    return str(subagent_type or "").strip() == "verification_agent"


def resolve_subagent_parent_cache_id(parent_session: Any) -> str:
    """Return the provider-facing parent identity without changing OFF behavior."""
    runtime_session_id = str(parent_session.get_session_id() or "").strip()
    try:
        cache_id, _ = resolve_session_lineage(parent_session)
    except Exception as exc:
        logger.warning(
            "[HarnessKVC] parent lineage resolution failed; using runtime session: "
            "session_id=%s error=%s",
            runtime_session_id,
            exc,
        )
        return runtime_session_id
    return str(cache_id or runtime_session_id).strip() or runtime_session_id


def scope_sub_session_id(
        sub_session_id: str,
        *,
        runtime_parent_session_id: str,
        parent_cache_id: str,
) -> str:
    """Disambiguate Team-member children while keeping runtime ids path-safe."""
    if not parent_cache_id or parent_cache_id == runtime_parent_session_id:
        return sub_session_id
    digest = hashlib.sha256(parent_cache_id.encode("utf-8")).hexdigest()[:12]
    return f"{sub_session_id}_scope_{digest}"


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


def create_subagent_session(
        parent_session: Session,
        *,
        sub_session_id: str,
        parent_cache_id: str,
        card: Any = None,
) -> Session:
    """Create a child Session that shares its parent's application KVC runtime."""
    return create_agent_session(
        session_id=sub_session_id,
        card=card,
        parent_session_id=parent_cache_id,
        kv_cache_runtime=parent_session.get_kv_cache_runtime(),
    )


async def prepare_subagent(session: Session, *, subagent_type: str) -> None:
    if is_sticky_subagent_type(subagent_type):
        await session.prepare_kvc()


async def finish_subagent(
        session: Session,
        *,
        subagent_type: str,
        succeeded: bool,
) -> None:
    """Offload resumable successful workers; evict terminal/failed workers."""
    if succeeded and is_sticky_subagent_type(subagent_type):
        await session.suspend_kvc()
        return
    await session.release_kvc()


async def evict_subagent(session: Session) -> None:
    await session.release_kvc()
