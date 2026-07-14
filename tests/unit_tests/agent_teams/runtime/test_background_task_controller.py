# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""BackgroundTaskController: external pause/resume orchestration.

Verifies the control surface deterministically (no harness, no engine): pause
runs its three ordered steps — set the engine abort_event, abort sessions, then
cancel the top-level task — and parks the run for resume; resume relaunches it and
clears the parked set; both are no-ops with nothing registered.
"""
from __future__ import annotations

import asyncio

from openjiuwen.agent_teams.runtime.background_task_controller import (
    BackgroundTaskController,
    SwarmflowRunHandle,
)


class _StubBackend:
    """Records the abort_sessions call into a shared order sequence."""

    def __init__(self, seq: list) -> None:
        self._seq = seq

    async def abort_sessions(self) -> None:
        self._seq.append("abort_sessions")


class _StubRuntime:
    def __init__(self, seq: list) -> None:
        self._seq = seq
        self.cancelled: list[str] = []

    async def cancel(self, task_id: str) -> bool:
        self._seq.append(f"cancel:{task_id}")
        self.cancelled.append(task_id)
        return True


class _StubNative:
    def __init__(self, seq: list) -> None:
        self.async_tool_runtime = _StubRuntime(seq)


def _make_handle(task_id: str, seq: list, relaunched: list):
    """Build a SwarmflowRunHandle wired to recording stubs."""
    ev = asyncio.Event()
    native = _StubNative(seq)
    handle = SwarmflowRunHandle(
        task_id=task_id,
        abort_event=ev,
        backend=_StubBackend(seq),
        native=native,
        relaunch=lambda: relaunched.append(task_id),
    )
    return handle, ev, native


def test_pause_runs_three_steps_in_order_and_parks_for_resume():
    """pause(): set abort_event → abort_sessions → cancel task; then parked."""
    seq: list = []
    relaunched: list = []
    ctl = BackgroundTaskController()
    handle, ev, native = _make_handle("w1", seq, relaunched)
    ctl.register(handle)

    ok = asyncio.run(ctl.pause())

    assert ok is True
    assert ev.is_set()  # step 1: engine abort signal raised
    # steps 2 and 3 ran in order — sessions aborted BEFORE the top-level cancel.
    assert seq == ["abort_sessions", "cancel:w1"]
    assert native.async_tool_runtime.cancelled == ["w1"]
    assert ctl.is_paused() is True
    assert relaunched == []  # resume not yet called


def test_resume_relaunches_and_clears_paused():
    """resume(): relaunch every parked run with its remembered closure."""
    seq: list = []
    relaunched: list = []
    ctl = BackgroundTaskController()
    handle, _ev, _native = _make_handle("w1", seq, relaunched)
    ctl.register(handle)

    async def scenario() -> bool:
        await ctl.pause()
        return await ctl.resume()

    resumed = asyncio.run(scenario())

    assert resumed is True
    assert relaunched == ["w1"]
    assert ctl.is_paused() is False


def test_pause_and_resume_are_noops_when_nothing_registered():
    """No active / no parked run → both return False without error."""
    ctl = BackgroundTaskController()
    assert asyncio.run(ctl.pause()) is False
    assert asyncio.run(ctl.resume()) is False
