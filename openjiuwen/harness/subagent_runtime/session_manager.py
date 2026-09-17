# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Subagent instance lifecycle management for one parent session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.kv_cache.kv_cache_metadata import KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.harness.execution_subject import current_execution_subject
from openjiuwen.harness.kv_cache import kv_cache_subagent_lifecycle
from openjiuwen.harness.kv_cache.kv_cache_subagent_lifecycle import affinity_enabled
from openjiuwen.harness.subagent_runtime.activity import ActivityProjector
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.errors import (
    raise_subagent_not_found,
)
from openjiuwen.harness.subagent_runtime.instance import SubagentInstance
from openjiuwen.harness.subagent_runtime.models import (
    SubagentActivity,
    SubagentMessage,
    SubagentStatus,
    UserInputOp,
)
from openjiuwen.harness.subagent_runtime.transcript import TranscriptProjector


async def _close_session_quietly(session: Any) -> None:
    close_stream = getattr(session, "close_stream", None)
    if not callable(close_stream):
        return
    try:
        result = close_stream()
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:
        logger.debug(
            "Failed to close subagent session stream quietly: %s",
            exc,
            exc_info=True,
        )


class SubagentSessionManager:
    """Create, index, and tear down subagent instances for one parent session."""

    def __init__(
        self,
        parent_agent: Any,
        config: SubagentRuntimeConfig,
        running_semaphore: asyncio.Semaphore,
        *,
        parent_session: Any | None = None,
        status_change_handler: Callable[[str, SubagentStatus], Awaitable[None]] | None = None,
        activity_handler: Callable[[SubagentActivity], None] | None = None,
        transcript_handler: Callable[[SubagentMessage], Awaitable[None]] | None = None,
    ) -> None:
        self._parent_agent = parent_agent
        self._config = config
        self._running_semaphore = running_semaphore
        self._parent_session = parent_session
        self._status_change_handler = status_change_handler
        self._activity_handler = activity_handler
        self._transcript_handler = transcript_handler
        self._instances: dict[str, SubagentInstance] = {}
        self._projectors: dict[str, ActivityProjector] = {}
        self._transcript_projectors: dict[str, TranscriptProjector] = {}

    def _build_turn_hooks(
        self,
        subagent_type: str,
        subagent_id: str,
        parent_session_id: str,
    ) -> tuple[
        Callable[[Any], Awaitable[None]] | None,
        Callable[[Any, bool], Awaitable[None]] | None,
    ]:
        parent = self._parent_agent
        if not affinity_enabled(parent):
            return None, None

        async def on_turn_start(session: Any) -> None:
            await kv_cache_subagent_lifecycle.prepare_subagent(
                session,
                subagent_type=subagent_type,
            )

        async def on_turn_finished(session: Any, succeeded: bool) -> None:
            await kv_cache_subagent_lifecycle.finish_subagent(
                session,
                subagent_type=subagent_type,
                succeeded=succeeded,
            )

        return on_turn_start, on_turn_finished

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
        parent_subject = current_execution_subject()
        parent_subject_id = parent_subject.subject_id if parent_subject is not None else "main"

        def session_factory() -> Any:
            return create_agent_session(
                session_id=subagent_id,
                card=card,
                envs=envs,
                parent_session_id=parent_session_id,
                kv_cache_runtime=(
                    self._parent_session.get_kv_cache_runtime()
                    if self._parent_session is not None
                    else None
                ),
            )

        on_turn_start, on_turn_finished = self._build_turn_hooks(
            subagent_type,
            subagent_id,
            parent_session_id,
        )

        async def on_status_changed(status: SubagentStatus) -> None:
            if self._status_change_handler is not None:
                await self._status_change_handler(subagent_id, status)

        projector = ActivityProjector(subagent_id=subagent_id, config=self._config)
        self._projectors[subagent_id] = projector
        transcript_projector = TranscriptProjector(
            subagent_id=subagent_id,
            parent_session_id=parent_session_id,
        )
        self._transcript_projectors[subagent_id] = transcript_projector
        instance_holder: dict[str, SubagentInstance] = {}

        async def on_turn_stream_start(op: UserInputOp) -> None:
            if self._transcript_handler is None:
                return
            message = transcript_projector.begin_turn(op.task_id, op.query)
            await self._transcript_handler(message)

        async def on_turn_stream_end(op: UserInputOp, aggregator: Any) -> None:
            if self._activity_handler is not None:
                for activity in projector.flush_pending(op.task_id):
                    self._activity_handler(activity)
            if self._transcript_handler is None:
                return
            message = transcript_projector.end_turn(op.task_id, aggregator)
            await self._transcript_handler(message)

        async def on_chunk(chunk: Any) -> None:
            instance = instance_holder.get("instance")
            if instance is None:
                return
            task_id = instance.current_task_id or ""
            if self._activity_handler is not None:
                for activity in projector.project(chunk, task_id=task_id):
                    self._activity_handler(activity)
            if self._transcript_handler is not None:
                message = transcript_projector.project(chunk, task_id=task_id)
                if message is not None:
                    await self._transcript_handler(message)

        instance = SubagentInstance(
            subagent_id=subagent_id,
            subagent_type=subagent_type,
            display_name=display_name,
            role=role,
            parent_session_id=parent_session_id,
            parent_subject_id=parent_subject_id,
            agent=subagent,
            session_factory=session_factory,
            running_semaphore=self._running_semaphore,
            turn_timeout_s=self._config.turn_timeout_s,
            include_parent_session_id=affinity_enabled(self._parent_agent),
            on_turn_start=on_turn_start,
            on_turn_finished=on_turn_finished,
            on_status_changed=on_status_changed,
            on_chunk=on_chunk if (self._activity_handler is not None or self._transcript_handler is not None) else None,
            on_turn_stream_start=on_turn_stream_start if self._transcript_handler else None,
            on_turn_stream_end=on_turn_stream_end
            if (self._activity_handler is not None or self._transcript_handler is not None)
            else None,
        )
        instance_holder["instance"] = instance
        await instance.start_worker()

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
        self._projectors.pop(subagent_id, None)
        self._transcript_projectors.pop(subagent_id, None)
        await instance.shutdown(reason)
        return instance

    def list_ids(self) -> list[str]:
        return list(self._instances.keys())

    async def restore(
        self,
        *,
        subagent_type: str,
        subagent_id: str,
        parent_session_id: str,
        display_name: str,
        role: str,
        browser_capabilities: list[str] | None = None,
    ) -> SubagentInstance:
        """Rebuild a subagent instance; conversation history is restored in session.pre_run()."""
        existing = self.find(subagent_id)
        if existing is not None and not existing.is_closed():
            return existing

        checkpointer = CheckpointerFactory.get_checkpointer()
        if not await checkpointer.session_exists(subagent_id):
            raise_subagent_not_found(subagent_id)

        return await self.create(
            subagent_type=subagent_type,
            subagent_id=subagent_id,
            parent_session_id=parent_session_id,
            display_name=display_name,
            role=role,
            browser_capabilities=browser_capabilities,
        )
