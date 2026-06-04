# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Side-channel input transport into a running external CLI agent.

The team delivers inbound text to a spawned CLI member by writing to the
CLI's input channel. :class:`Injector` is the abstraction; ``StdinPipeInjector``
is the first (Unix-first) backend, writing newline-framed text to the
subprocess stdin pipe. PTY / Windows backends can implement the same
Protocol later without touching the runtime.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from openjiuwen.core.common.logging import team_logger


@runtime_checkable
class Injector(Protocol):
    """Writes a unit of text into a running external agent's input channel."""

    async def write(self, text: str) -> None:
        """Deliver one text unit to the agent (newline-framed by the backend)."""
        ...

    async def aclose(self) -> None:
        """Release the input channel. Must be idempotent."""
        ...


class StdinPipeInjector:
    """Inject text by writing newline-framed lines to a subprocess stdin pipe.

    Works only for CLI agents that read stdin continuously (e.g. an
    interactive / streaming-input mode). One-shot CLIs that read stdin once
    will not observe later writes — those degrade to turn-boundary delivery.
    """

    def __init__(self, stdin: asyncio.StreamWriter):
        """Bind to an open subprocess stdin stream."""
        self._stdin = stdin
        self._closed = False

    async def write(self, text: str) -> None:
        """Write ``text`` plus a trailing newline and flush the pipe."""
        if self._closed:
            team_logger.debug("StdinPipeInjector.write after close; dropping")
            return
        self._stdin.write((text + "\n").encode("utf-8"))
        await self._stdin.drain()

    async def aclose(self) -> None:
        """Close the stdin pipe. Idempotent."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._stdin.can_write_eof():
                self._stdin.write_eof()
            self._stdin.close()
        except (RuntimeError, OSError) as exc:
            team_logger.debug("StdinPipeInjector close failed: {}", exc)


__all__ = ["Injector", "StdinPipeInjector"]
