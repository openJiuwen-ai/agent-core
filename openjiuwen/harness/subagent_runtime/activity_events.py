# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Subagent activity payloads and parent-session stream emission."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.models import SubagentActivity

SUBAGENT_ACTIVITY_EVENT_TYPE = "subagent_activity"
_MAX_CONSECUTIVE_FAILURES = 3


def build_activity_payload(activity: SubagentActivity) -> dict[str, Any]:
    """Build the external subagent activity payload."""
    return activity.to_dict()


async def emit_subagent_activity(
    session: Session,
    *,
    projection: dict[str, Any],
) -> None:
    """Write one subagent activity update to the parent session stream."""
    await session.write_stream(
        OutputSchema(
            type=SUBAGENT_ACTIVITY_EVENT_TYPE,
            index=0,
            payload={"subagent_activity": projection},
        )
    )


class ActivityEmitter:
    """Bounded queue + background drain for subagent activity events."""

    def __init__(
        self,
        session: Session,
        *,
        config: SubagentRuntimeConfig,
    ) -> None:
        self._session = session
        self._config = config
        self._queue: asyncio.Queue[SubagentActivity] = asyncio.Queue(
            maxsize=config.activity_queue_size,
        )
        self._drain_task: asyncio.Task[None] | None = None
        self._disabled = False
        self._consecutive_failures = 0
        self.dropped = 0

    @property
    def disabled(self) -> bool:
        return self._disabled

    def start(self) -> None:
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop())

    def offer(self, activity: SubagentActivity) -> None:
        if self._disabled:
            return
        try:
            self._queue.put_nowait(activity)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(activity)
            except asyncio.QueueFull:
                self.dropped += 1

    async def close(self) -> None:
        if self._drain_task is None:
            return
        self._drain_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._drain_task
        self._drain_task = None

    async def _drain_loop(self) -> None:
        while True:
            activity = await self._queue.get()
            try:
                await emit_subagent_activity(
                    self._session,
                    projection=build_activity_payload(activity),
                )
                self._consecutive_failures = 0
            except Exception as exc:
                self._consecutive_failures += 1
                if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    self._disabled = True
                    logger.warning(
                        "[subagent_activity] emitter disabled after %s failures: %s",
                        self._consecutive_failures,
                        exc,
                    )
                    return


__all__ = [
    "SUBAGENT_ACTIVITY_EVENT_TYPE",
    "ActivityEmitter",
    "build_activity_payload",
    "emit_subagent_activity",
]
