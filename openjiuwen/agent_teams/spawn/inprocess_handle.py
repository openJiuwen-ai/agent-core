# coding: utf-8
"""In-process spawn handle that mirrors SpawnedProcessHandle interface."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class InProcessSpawnHandle:
    """Lightweight handle wrapping an asyncio.Task instead of a subprocess.

    Implements the same surface used by TeamAgent (_spawned_handles):
    is_alive, is_healthy, shutdown, force_kill, start/stop_health_check.
    """

    process_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    on_unhealthy: Optional[Callable[[], Any]] = field(default=None, repr=False)
    _shutdown_requested: bool = field(default=False, repr=False)

    # ------------------------------------------------------------------
    # Properties compatible with SpawnedProcessHandle
    # ------------------------------------------------------------------

    @property
    def is_alive(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def is_healthy(self) -> bool:
        return self.is_alive and not self._shutdown_requested

    # ------------------------------------------------------------------
    # Health-check stubs (no-op for in-process tasks)
    # ------------------------------------------------------------------

    async def start_health_check(self, interval: float | None = None) -> None:
        """No-op: in-process tasks do not need IPC health checks."""

    async def stop_health_check(self) -> None:
        """No-op."""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def shutdown(self, timeout: float | None = None) -> bool:
        """Cancel the task and wait for completion.

        Returns True if the task finished within *timeout* seconds.
        """
        self._shutdown_requested = True
        if self._task is None or self._task.done():
            return True
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout or 10.0)
        return self._task.done()

    async def force_kill(self) -> None:
        """Immediately cancel the task."""
        self._shutdown_requested = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def wait_for_completion(self) -> int:
        """Block until the wrapped task finishes. Returns 0 on success."""
        if self._task is None:
            return -1
        try:
            await self._task
            return 0
        except (asyncio.CancelledError, Exception):
            return -1
