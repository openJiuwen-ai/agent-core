# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Closable single-consumer cursor for external harness observations."""

from __future__ import annotations

from typing import Protocol, Self, runtime_checkable

from openjiuwen.agent_teams.external.protocol.events import HarnessEvent


@runtime_checkable
class HarnessEventCursor(Protocol):
    """Async observation cursor whose consumer lease can be released early."""

    def __aiter__(self) -> Self:
        """Return this cursor."""
        ...

    async def __anext__(self) -> HarnessEvent:
        """Return the next ordered event or raise ``StopAsyncIteration``."""
        ...

    async def aclose(self) -> None:
        """Idempotently release the observation consumer lease."""
        ...


__all__ = ["HarnessEventCursor"]
