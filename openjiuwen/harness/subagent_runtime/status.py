# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-process status publish/subscribe channel for one subagent instance."""

from __future__ import annotations

import asyncio

from openjiuwen.harness.subagent_runtime.models import SubagentStatus


class StatusChannel:
    """Sender half of a watch-style status channel."""

    def __init__(self, initial: SubagentStatus | None = None) -> None:
        self._status = initial or SubagentStatus.pending_init()
        self._version = 0
        self._closed = False
        self._condition = asyncio.Condition()

    def current(self) -> SubagentStatus:
        return self._status

    def version(self) -> int:
        return self._version

    def subscribe(self) -> StatusReceiver:
        return StatusReceiver(self)

    async def set(self, status: SubagentStatus) -> None:
        async with self._condition:
            self._status = status
            self._version += 1
            self._condition.notify_all()

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    async def wait_for_version_change(self, seen: int) -> tuple[int, bool]:
        async with self._condition:
            await self._condition.wait_for(
                lambda: self._version != seen or self._closed
            )
            return self._version, self._version != seen


class StatusReceiver:
    """Receiver half; each subscriber tracks its own seen version."""

    def __init__(self, channel: StatusChannel) -> None:
        self._channel = channel
        self._seen = channel.version()

    def current(self) -> SubagentStatus:
        return self._channel.current()

    async def changed(self) -> bool:
        """Return False when the channel closed without a version advance."""
        version, changed = await self._channel.wait_for_version_change(self._seen)
        self._seen = version
        return changed

    async def wait_for_final(self) -> SubagentStatus:
        status = self.current()
        while not status.is_final():
            if not await self.changed():
                return self.current()
            status = self.current()
        return status
