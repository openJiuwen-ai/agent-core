# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Subagent instance lifecycle management for one parent session."""

from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen.core.foundation.kv_cache import KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.harness.kv_cache.kv_cache_hooks import affinity_enabled
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.errors import (
    build_subagent_runtime_error,
    raise_subagent_not_found,
)
from openjiuwen.harness.subagent_runtime.instance import SubagentInstance


async def _close_session_quietly(session: Any) -> None:
    close_stream = getattr(session, "close_stream", None)
    if not callable(close_stream):
        return
    try:
        result = close_stream()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        return


class SubagentSessionManager:
    """Create, index, and tear down subagent instances for one parent session."""

    def __init__(
        self,
        parent_agent: Any,
        config: SubagentRuntimeConfig,
        running_semaphore: asyncio.Semaphore,
    ) -> None:
        self._parent_agent = parent_agent
        self._config = config
        self._running_semaphore = running_semaphore
        self._instances: dict[str, SubagentInstance] = {}

    async def create(
        self,
        *,
        subagent_type: str,
        subagent_id: str,
        parent_session_id: str,
        display_name: str,
        role: str,
        browser_capabilities: list[str] | None = None,
    ) -> SubagentInstance:
        subagent = self._parent_agent.create_subagent(
            subagent_type,
            subagent_id,
            browser_capabilities,
        )

        envs: dict[str, Any] = {}
        if affinity_enabled(self._parent_agent):
            envs[KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV] = parent_session_id

        card = subagent.card

        def session_factory() -> Any:
            return create_agent_session(
                session_id=subagent_id,
                card=card,
                envs=envs,
            )

        try:
            instance = SubagentInstance(
                subagent_id=subagent_id,
                subagent_type=subagent_type,
                display_name=display_name,
                role=role,
                parent_session_id=parent_session_id,
                agent=subagent,
                session_factory=session_factory,
                running_semaphore=self._running_semaphore,
            )
            await instance.start_worker()
        except Exception:
            raise

        self._instances[subagent_id] = instance
        return instance

    def find(self, subagent_id: str) -> SubagentInstance | None:
        return self._instances.get(subagent_id)

    def get(self, subagent_id: str) -> SubagentInstance:
        instance = self.find(subagent_id)
        if instance is None:
            raise_subagent_not_found(subagent_id)
        return instance

    async def remove(
        self,
        subagent_id: str,
        *,
        reason: str = "manual",
    ) -> SubagentInstance | None:
        instance = self._instances.pop(subagent_id, None)
        if instance is None:
            return None
        await instance.shutdown(reason)
        return instance

    def list_ids(self) -> list[str]:
        return list(self._instances.keys())

    async def persist(self, subagent_id: str) -> None:
        _ = subagent_id

    async def restore(self, subagent_id: str) -> SubagentInstance:
        raise build_subagent_runtime_error(
            f"restore not supported: subagent_id={subagent_id}",
        ) from None
