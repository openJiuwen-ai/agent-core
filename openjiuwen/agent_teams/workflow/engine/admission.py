# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Engine-side agent admission protocol and default semaphore implementation."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class AgentAdmission(Protocol):
    """Protocol for acquiring a concurrent ``agent()`` slot."""

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """Block until a slot is available, then release on exit."""
        ...


class SemaphoreAdmission:
    """Default single-layer admission — equivalent to legacy ``Runtime.sem``."""

    def __init__(self, cap: int) -> None:
        self._sem = asyncio.Semaphore(max(1, cap))

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        async with self._sem:
            yield


__all__ = ["AgentAdmission", "SemaphoreAdmission"]
