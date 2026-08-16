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
    task_id: str
    run_id: str          # NEW
    abort_event: AbortSignal
    backend: Any
    native: Any
    relaunch: Callable[[], None]


class BackgroundTaskController:
    def __init__(self) -> None:
        self._active: dict[str, SwarmflowRunHandle] = {}   # keyed by run_id
        self._paused: dict[str, SwarmflowRunHandle] = {}  # keyed by run_id
        self._lock = asyncio.Lock()

    def register(self, handle: SwarmflowRunHandle) -> None:
        self._active[handle.run_id] = handle

    def deregister(self, run_id: str) -> None:
        self._active.pop(run_id, None)
        self._paused.pop(run_id, None)

    async def _abort_one(self, h: SwarmflowRunHandle, reason: str) -> None:
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
        if run_id is None:
            return bool(self._paused)
        return run_id in self._paused


__all__ = ["BackgroundTaskController", "SwarmflowRunHandle"]
