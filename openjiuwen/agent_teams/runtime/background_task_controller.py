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
from openjiuwen.harness_protocol.state import HarnessState

# Upper bound on waiting for a cancelled swarmflow task to unwind. Generous:
# unwinding may need to abort live avatar sessions and finish an in-flight
# model call; a hung task must not wedge the team lifecycle forever.
_UNWIND_TIMEOUT_S = 30.0


@dataclass
class SwarmflowRunHandle:
    """Control handles for one live swarmflow run (registered at launch).

    ``inputs`` / ``session_id`` are the resume ticket: pure data, no reference
    to the tool or harness that launched the run. The leader NativeHarness is
    run-cycle scoped (torn down at every round end, rebuilt next cycle) while
    this registry is session scoped, so anything captured from the launching
    cycle would dangle after a pause. The controller relaunches through the
    *current* cycle's launcher instead (see :meth:`set_launcher`).

    ``launcher`` is the one exception to that purity: the SwarmflowTool that
    launched THIS run. With N teammates each tool is a distinct relaunch host,
    so resume prefers it and only falls back to the invoking tool (``via``)
    or the current cycle's launcher when the launching tool's harness is gone
    (leader cross-cycle rebuild) or the handle predates the field. The
    reference is valid only while the handle sits in the active/paused
    registries — deregister drops both — and the handle is never serialized,
    so it does not widen the ticket's data contract.
    """

    task_id: str
    run_id: str  # workflow run_id — registry key for per-run pause/resume/stop
    abort_event: AbortSignal  # engine Runtime.abort_event for THIS run
    backend: Any  # TeamWorkerBackend → abort_sessions()
    native: Any  # leader NativeHarness → async_tool_runtime.cancel
    inputs: dict[str, Any]  # the SAME inputs run_background was launched with
    session_id: str  # session contextvar to restore on relaunch
    launcher: Any = None  # the SwarmflowTool that launched THIS run
    # Member identity of the launching harness (build_context.member_name):
    # the teardown path stops runs by owner before dismantling a member's
    # harness. None for a leader run (or when the identity is unavailable) —
    # legacy handles and test stubs keep working untouched.
    owner_member: str | None = None


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
        self._launcher: Any = None  # the current cycle's SwarmflowTool

    def set_launcher(self, tool: Any) -> None:
        """Register the SwarmflowTool of the current run cycle.

        Every leader NativeHarness build creates a fresh SwarmflowTool (the
        team tool rail is per-harness); the newest one is the only valid
        relaunch host, so each registers itself here on construction.
        """
        self._launcher = tool
        team_logger.info("[bg-ctl] launcher set ctl=%x tool=%x", id(self), id(tool))

    def register(self, handle: SwarmflowRunHandle) -> None:
        """Register a live run's control handles (called at launch)."""
        self._active[handle.run_id] = handle
        team_logger.info(
            "[bg-ctl] register run_id=%s task_id=%s ctl=%x active=%d paused=%d",
            handle.run_id, handle.task_id, id(self), len(self._active), len(self._paused),
        )

    def deregister(self, run_id: str) -> None:
        """Drop a run's handles (called in the launcher's finally; idempotent)."""
        # Only drop the active handle. A paused run lives in _paused awaiting
        # resume; deregister (called from run_background's finally on unwind)
        # must NOT clear it, or resume(run_id) would report not_found and the
        # leader would start a fresh run instead of resuming the paused prefix.
        self._active.pop(run_id, None)
        team_logger.info(
            "[bg-ctl] deregister run_id=%s ctl=%x active=%d paused=%d",
            run_id, id(self), len(self._active), len(self._paused),
        )

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
        runtime = h.native.async_tool_runtime
        try:
            await runtime.cancel(h.task_id)
        except Exception:
            team_logger.debug("[bg-ctl] cancel failed for %s", h.run_id, exc_info=True)
        # ``cancel`` only requests cancellation; the engine writes the
        # pause/seal record and emits the terminal progress event while the
        # task unwinds, asynchronously. Wait for that so a caller that tears the
        # leader harness down right after pause()/stop() does not lose it.
        task = getattr(runtime, "_tasks", {}).get(h.task_id)
        if task is not None and not task.done():
            done, _ = await asyncio.wait({task}, timeout=_UNWIND_TIMEOUT_S)
            if not done:
                team_logger.warning(
                    "[bg-ctl] %s unwind timed out after %ss for %s",
                    reason, _UNWIND_TIMEOUT_S, h.run_id,
                )

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
            team_logger.info(
                "[bg-ctl] pause run_id=%s ctl=%x targets=%s active=%d paused=%d",
                run_id, id(self), sorted(targets), len(self._active), len(self._paused),
            )
            for rid, h in targets.items():
                await self._abort_one(h, "pause")
                self._paused[rid] = h
                self._active.pop(rid, None)
            return bool(targets)

    async def resume(self, run_id: str | None = None, *, via: Any = None) -> bool:
        """Resume paused run(s) — all when ``run_id`` is None, else just that one.

        Relaunches ``run_background`` with the ticket's SAME inputs. The
        relaunch host is, in order: the ticket's own ``launcher`` (the
        SwarmflowTool that launched it) when its harness can still host work;
        the invoking tool (``via`` — the member whose ``action="resume"`` call
        is on the stack, alive by construction); the current cycle's global
        launcher slot. Routing through ``via`` keeps the resumed run's
        narration and completion injection on the member that decided to
        resume instead of on whichever member's tool happened to register
        last. The journal path is unchanged, so the completed prefix is a
        cache hit and only the interrupted call reruns live. No launcher at
        all is a no-op that leaves the ticket parked.
        """
        async with self._lock:
            if run_id is None:
                targets = dict(self._paused)
            else:
                h = self._paused.get(run_id)
                if h is None:
                    return False
                targets = {run_id: h}
            dispatched = False
            for rid, h in targets.items():
                # Route through the run's own launching tool when it can still
                # host work, then the invoking tool, then the current cycle's
                # global launcher (dead/legacy launchers, stubs).
                if h.launcher is not None and self._launcher_alive(h.launcher):
                    launcher = h.launcher
                elif via is not None:
                    launcher = via
                else:
                    launcher = self._launcher
                if launcher is None:
                    team_logger.warning(
                        "[bg-ctl] resume run_id=%s: no launcher registered; ticket kept", rid,
                    )
                    continue
                try:
                    launcher.relaunch(h.inputs, h.session_id)
                except Exception:
                    team_logger.debug("[bg-ctl] relaunch failed for %s", rid, exc_info=True)
                self._paused.pop(rid, None)
                dispatched = True
            return bool(targets) and dispatched

    @staticmethod
    def _launcher_alive(launcher: Any) -> bool:
        """Whether a ticket's launching tool can still host a relaunch.

        A tool is dead when its harness was torn down (leader cross-cycle
        rebuild, member shutdown). Anything without a probe (test stubs, plain
        fakes) counts as alive — resume then routes to it as before.
        """
        harness = getattr(launcher, "_parent_agent", None)
        state = getattr(harness, "state", None)
        if state is None:
            return True
        return state is not HarnessState.TERMINATED

    async def stop(self, run_id: str | None = None) -> bool:
        """Terminal stop of run(s) — dropped, not parked for resume.

        ``run_id=None`` stops every run across both registries: active runs are
        aborted (reason ``stop``, the engine writes a seal record) and dropped;
        paused runs only lose their relaunch closure — their pause record already
        lives in the journal, so a cold-start resume still hits the cache prefix.
        """
        async with self._lock:
            if run_id is None:
                active_targets = dict(self._active)
                paused_targets = dict(self._paused)
            else:
                h = self._active.get(run_id)
                active_targets = {run_id: h} if h is not None else {}
                paused_targets = {run_id: self._paused[run_id]} if run_id in self._paused else {}
            team_logger.info(
                "[bg-ctl] stop run_id=%s ctl=%x active_targets=%s paused_targets=%s",
                run_id, id(self), sorted(active_targets), sorted(paused_targets),
            )
            for rid, h in active_targets.items():
                await self._abort_one(h, "stop")
                self._active.pop(rid, None)   # terminal: NOT into _paused
            for rid in paused_targets:
                self._paused.pop(rid, None)
            return bool(active_targets or paused_targets)

    def is_paused(self, run_id: str | None = None) -> bool:
        """Whether ``run_id`` (any run when None) is currently paused."""
        if run_id is None:
            return bool(self._paused)
        return run_id in self._paused

    async def stop_by_owner(self, member_name: str | None, *, include_paused: bool = False) -> bool:
        """Terminal stop of every run owned by ``member_name``.

        The member-teardown hook: called before a member's harness is
        dismantled so its in-flight runs unwind through the controller's
        three-step abort (terminal event + journal record) instead of dying
        as a bare cancel with no WORKFLOW_STOPPED and a WAL stuck mid-flight.
        Runs with a different owner (other teammates') are untouched; no match
        is a no-op returning ``False``.

        ``owner_member`` is the launching harness's build_context.member_name.
        Paused runs are normally left parked — a team pause tears its run cycle
        down right after parking, and dropping tickets here would break the
        pause→resume flow; ``include_paused=True`` is for permanent teardown
        only (member dispose), where nobody can ever relaunch them.

        Leader runs carry the leader's member name like anyone else (uniform
        fill); the session-level ``stop(None)`` path keeps owning whole-team
        teardown.
        """
        if not member_name:
            return False
        async with self._lock:
            active_targets = {
                rid: h for rid, h in self._active.items() if h.owner_member == member_name
            }
            paused_targets = (
                {rid: h for rid, h in self._paused.items() if h.owner_member == member_name}
                if include_paused
                else {}
            )
            team_logger.info(
                "[bg-ctl] stop_by_owner member=%s ctl=%x active_targets=%s paused_targets=%s",
                member_name, id(self), sorted(active_targets), sorted(paused_targets),
            )
            for rid, h in active_targets.items():
                await self._abort_one(h, "stop")
                self._active.pop(rid, None)  # terminal: NOT into _paused
            for rid in paused_targets:
                self._paused.pop(rid, None)
            return bool(active_targets or paused_targets)


__all__ = ["BackgroundTaskController", "SwarmflowRunHandle"]
