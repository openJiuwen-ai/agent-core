# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Background task controller: external pause/resume/stop for leader background work.

Threaded through ``Runner.run_agent_team_streaming`` and attached to the leader
harness, this is the embedder-held control surface for long-running background
tools (today: the leader's swarmflow run). A single object instead of a growing
set of Runner facade methods, so new controls / callbacks extend the object, not
the SDK surface.

The controller is a registry + control plane: each live swarmflow run registers
a :class:`SwarmflowRunHandle` at launch (carrying the engine abort signal, the
worker backend, the owning harness, and a relaunch closure) and deregisters on
completion. Handles are keyed by ``run_id`` (not ``task_id``) so a leader can
address one specific run; ``pause`` / ``resume`` / ``stop`` operate per-run, with
``pause(None)`` / ``resume(None)`` preserving the full-collection behaviour, and
``stop`` being terminal (the run is dropped, not parked for resume).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from openjiuwen.agent_teams.workflow.engine.runtime import AbortSignal
from openjiuwen.core.common.logging import team_logger


@dataclass
class SwarmflowRunHandle:
    """Control handles for one live swarmflow run (registered at launch)."""

    task_id: str
    run_id: str  # workflow run_id — registry key for per-run pause/resume/stop
    abort_event: AbortSignal  # engine Runtime.abort_event for THIS run
    backend: Any  # TeamWorkerBackend → abort_sessions()
    native: Any  # leader NativeHarness → async_tool_runtime.cancel
    relaunch: Callable[[], None]  # re-launch run_background with the SAME inputs


class BackgroundTaskController:
    """Unified pause/resume/stop control surface threaded through streaming.

    Lifecycle-neutral: created by the embedder, attached to the leader harness,
    and self-populated by ``SwarmflowTool`` as runs launch. Control with no
    matching run is a no-op (returns ``False``).
    """

    def __init__(self) -> None:
        self._active: dict[str, SwarmflowRunHandle] = {}   # keyed by run_id
        self._paused: dict[str, SwarmflowRunHandle] = {}  # keyed by run_id
        self._lock = asyncio.Lock()

    def register(self, handle: SwarmflowRunHandle) -> None:
        """Register a live run's control handles (called at launch)."""
        self._active[handle.run_id] = handle

    def deregister(self, run_id: str) -> None:
        """Drop a run's handles (called in the launcher's finally; idempotent)."""
        # Only drop the active handle. A paused run lives in _paused awaiting
        # resume; deregister (called from run_background's finally on unwind)
        # must NOT clear it, or resume(run_id) would report not_found and the
        # leader would start a fresh run instead of resuming the paused prefix.
        self._active.pop(run_id, None)

    async def _abort_one(self, h: SwarmflowRunHandle, reason: str) -> None:
        """Abort one run in three steps, in this order (correctness-critical).

        1. set the engine ``abort_event`` — queued ``agent()`` / session turns
           are gated, and an in-flight call reaching the pre-journal guard does
           NOT persist to the WAL;
        2. abort live avatar sessions — their supervisor is a separate asyncio
           task the top-level cancel cannot reach, so abort them here where the
           coroutine runs to completion (else the supervisor leaks);
        3. cancel the top-level swarmflow task — stops the in-flight
           ``run_once`` worker (not abortable) and unwinds the engine; the WAL
           is preserved for resume.
        """
        h.abort_event.set(reason)
        try:
            await h.backend.abort_sessions()
        except Exception:
            team_logger.debug("[bg-ctl] abort_sessions failed for %s", h.run_id, exc_info=True)
        try:
            await h.native.async_tool_runtime.cancel(h.task_id)
        except Exception:
            team_logger.debug("[bg-ctl] cancel failed for %s", h.run_id, exc_info=True)

    async def pause(self, run_id: str | None = None) -> bool:
        """Pause active run(s) — all when ``run_id`` is None, else just that one."""
        async with self._lock:
            if run_id is None:
                targets = dict(self._active)
            else:
                h = self._active.get(run_id)
                if h is None:
                    return False
                targets = {run_id: h}
            for rid, h in targets.items():
                await self._abort_one(h, "pause")
                self._paused[rid] = h
                self._active.pop(rid, None)
            return bool(targets)

    async def resume(self, run_id: str | None = None) -> bool:
        """Resume paused run(s) — all when ``run_id`` is None, else just that one.

        The relaunch closure re-invokes ``run_background`` with the SAME inputs;
        the journal path is unchanged, so the completed prefix is a cache hit and
        only the interrupted call reruns live.
        """
        async with self._lock:
            if run_id is None:
                targets = dict(self._paused)
            else:
                h = self._paused.get(run_id)
                if h is None:
                    return False
                targets = {run_id: h}
            for rid, h in targets.items():
                try:
                    h.relaunch()
                except Exception:
                    team_logger.debug("[bg-ctl] relaunch failed for %s", rid, exc_info=True)
                self._paused.pop(rid, None)
            return bool(targets)

    async def stop(self, run_id: str) -> bool:
        """Terminal stop of one run — dropped, not parked for resume."""
        async with self._lock:
            h = self._active.get(run_id)
            if h is not None:
                await self._abort_one(h, "stop")
                self._active.pop(run_id, None)   # terminal: NOT into _paused
                return True
            if run_id in self._paused:
                # Already aborted at pause time; just drop the relaunch
                # closure so a later resume(run_id) cannot relaunch.
                self._paused.pop(run_id, None)
                return True
            return False

    def is_paused(self, run_id: str | None = None) -> bool:
        """Whether ``run_id`` (any run when None) is currently paused."""
        if run_id is None:
            return bool(self._paused)
        return run_id in self._paused


__all__ = ["BackgroundTaskController", "SwarmflowRunHandle"]
