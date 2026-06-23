# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Background task controller: external pause/resume for leader background work.

Threaded through ``Runner.run_agent_team_streaming`` and attached to the leader
harness, this is the embedder-held control surface for long-running background
tools (today: the leader's swarmflow run). A single object instead of a growing
set of Runner facade methods, so new controls / callbacks extend the object, not
the SDK surface.

The controller is a registry + control plane: each live swarmflow run registers
a :class:`SwarmflowRunHandle` at launch (carrying the engine abort signal, the
worker backend, the owning harness, and a relaunch closure) and deregisters on
completion. ``pause`` / ``resume`` operate on the registered handles.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from openjiuwen.core.common.logging import team_logger


@dataclass
class SwarmflowRunHandle:
    """Control handles for one live swarmflow run (registered at launch)."""

    task_id: str
    abort_event: asyncio.Event  # engine Runtime.abort_event for THIS run
    backend: Any  # TeamWorkerBackend → abort_sessions()
    native: Any  # leader NativeHarness → async_tool_runtime.cancel
    relaunch: Callable[[], None]  # re-launch run_background with the SAME inputs


class BackgroundTaskController:
    """Unified pause/resume control surface threaded through streaming.

    Lifecycle-neutral: created by the embedder, attached to the leader harness,
    and self-populated by ``SwarmflowTool`` as runs launch. Pausing / resuming
    with no matching run is a no-op (returns ``False``).
    """

    def __init__(self) -> None:
        self._active: dict[str, SwarmflowRunHandle] = {}
        self._paused: dict[str, Callable[[], None]] = {}  # task_id → relaunch
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Registration seam (SwarmflowTool self-registers at launch)
    # ------------------------------------------------------------------

    def register(self, handle: SwarmflowRunHandle) -> None:
        """Register a live run's control handles (called at launch)."""
        self._active[handle.task_id] = handle

    def deregister(self, task_id: str) -> None:
        """Drop a run's handles (called in the launcher's finally; idempotent)."""
        self._active.pop(task_id, None)

    # ------------------------------------------------------------------
    # Control surface (embedder)
    # ------------------------------------------------------------------

    async def pause(self) -> bool:
        """Pause every active background run. Returns ``False`` when none active.

        Three steps per run, in this order (correctness-critical):

        1. set the engine ``abort_event`` — queued ``agent()`` / session turns are
           gated, and an in-flight call reaching the pre-journal guard does NOT
           persist to the WAL;
        2. abort live avatar sessions — their supervisor is a separate asyncio
           task the top-level cancel cannot reach, so abort them here in the
           controller coroutine where it runs to completion (else the supervisor
           leaks);
        3. cancel the top-level swarmflow task — stops the in-flight ``run_once``
           worker (not abortable) and unwinds the engine; the WAL is preserved
           (``finalize`` is skipped on the cancel path) for resume.
        """
        async with self._lock:
            if not self._active:
                return False
            for task_id, handle in list(self._active.items()):
                handle.abort_event.set()
                try:
                    await handle.backend.abort_sessions()
                except Exception:  # noqa: BLE001 - best effort; cancel still stops the run
                    team_logger.debug("[bg-ctl] abort_sessions failed for %s", task_id, exc_info=True)
                try:
                    await handle.native.async_tool_runtime.cancel(task_id)
                except Exception:  # noqa: BLE001 - cancel is best-effort
                    team_logger.debug("[bg-ctl] cancel failed for %s", task_id, exc_info=True)
                self._paused[task_id] = handle.relaunch
                self._active.pop(task_id, None)
            return True

    async def resume(self) -> bool:
        """Resume every paused run by relaunching it. Returns ``False`` when none.

        The relaunch closure re-invokes ``run_background`` with the SAME inputs;
        the journal path is unchanged, so the completed prefix is a cache hit and
        only the interrupted call reruns live.
        """
        async with self._lock:
            if not self._paused:
                return False
            for task_id, relaunch in list(self._paused.items()):
                try:
                    relaunch()
                except Exception:  # noqa: BLE001 - a failed relaunch must not strand the rest
                    team_logger.debug("[bg-ctl] relaunch failed for %s", task_id, exc_info=True)
                self._paused.pop(task_id, None)
            return True

    def is_paused(self) -> bool:
        """Whether any run is currently paused (awaiting resume)."""
        return bool(self._paused)


__all__ = ["BackgroundTaskController", "SwarmflowRunHandle"]
