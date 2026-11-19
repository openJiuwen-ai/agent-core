# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Re-entrancy and depth protection for ``ops`` calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

MAX_OPS_DEPTH = 8


class OpsCallGuard:
    """Track active ops events per asyncio task."""

    _active: ContextVar[tuple[str, ...]] = ContextVar("ops_call_guard", default=())

    @classmethod
    @contextmanager
    def enter(cls, event_type: str) -> Iterator[bool]:
        """Yield ``False`` when the call re-enters itself or exceeds the depth limit."""

        stack = cls._active.get()
        if event_type in stack or len(stack) >= MAX_OPS_DEPTH:
            yield False
            return
        token = cls._active.set(stack + (event_type,))
        try:
            yield True
        finally:
            cls._active.reset(token)
