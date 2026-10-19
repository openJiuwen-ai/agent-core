# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression guard: evolution preparation must not block the event loop.

Large trajectories (long sessions, ~20涓?token contexts) used to be
``deepcopy``-ed / serialized synchronously inside the running event loop during
invoke preparation. That starved the asyncio loop for minutes, so the upper
RelayClaw agent WebSocket could not answer its keepalive ping and the request
was torn down with ``jiuwen WebSocket connection closed unexpectedly``
(frontend: 鏅鸿兘浣撹繛鎺ヤ笉绋冲畾 / OA.05000010).

The fix forwards this heavy work through ``asyncio.to_thread``. These tests
pin that contract so a future refactor cannot silently move it back onto the
loop thread.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

import openjiuwen.harness.rails.evolution.evolution_rail as evolution_rail_module
from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.processor import TrajectorySpanProcessor
from openjiuwen.core.single_agent.rail.base import ModelCallInputs
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail


def _assert_worker_thread(seen: list[int]) -> None:
    """Record the executing thread and require it not to be an event-loop thread."""

    seen.append(threading.get_ident())
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()


@pytest.mark.asyncio
async def test_prepare_evolution_input_offloads_deepcopy(monkeypatch):
    loop_thread = threading.get_ident()
    seen_threads: list[int] = []
    real_deepcopy = evolution_rail_module.deepcopy

    def spy(value):
        _assert_worker_thread(seen_threads)
        return real_deepcopy(value)

    monkeypatch.setattr(evolution_rail_module, "deepcopy", spy)

    payload = [{"role": "user", "content": "x" * 16} for _ in range(500)]
    host = SimpleNamespace(_trajectory_to_messages=lambda trajectory, *, fields: payload)

    prepared = await EvolutionRail._prepare_evolution_input(host, None, None)

    assert seen_threads, "deepcopy was never invoked"
    assert all(tid != loop_thread for tid in seen_threads)
    assert len(prepared.messages) == len(payload)


@pytest.mark.asyncio
async def test_prepare_evolution_input_keeps_loop_responsive(monkeypatch):
    """A heartbeat task must keep ticking while the heavy copy runs off-loop."""

    heartbeat_gaps: list[float] = []

    async def heartbeat():
        last = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(0.005)
            now = asyncio.get_running_loop().time()
            heartbeat_gaps.append(now - last)
            last = now

    payload = [{"role": "user", "content": "x" * 64} for _ in range(4000)]
    host = SimpleNamespace(_trajectory_to_messages=lambda trajectory, *, fields: payload)

    beats = asyncio.create_task(heartbeat())
    try:
        await EvolutionRail._prepare_evolution_input(host, None, None)
    finally:
        beats.cancel()

    assert heartbeat_gaps, "heartbeat never ran"
    assert max(heartbeat_gaps) < 0.5


def _make_rail_with_capture(monkeypatch):
    """Build a rail whose contextvar resolves to an active capture."""

    rail = EvolutionRail(trajectory_span_processor=TrajectorySpanProcessor())
    capture = evolution_rail_module._InvokeCapture(
        subscription=object(),
        scope_key=("agent", "session-1", "member-1"),
        session_id="session-1",
        member_id="member-1",
    )
    rail._active_captures[capture.subscription] = capture
    rail._invoke_capture.set(capture)
    window = _minimal_trajectory().to_otlp()
    rail._scope_windows[capture.scope_key] = window
    return rail, capture


def _minimal_trajectory(span_name: str = "tool.call"):
    """Return the smallest Trajectory accepted by from_otlp."""

    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "openjiuwen.trajectory_id",
                                "value": {"stringValue": "traj-test"},
                            }
                        ]
                    },
                    "scopeSpans": [{"spans": [{"name": span_name, "attributes": []}]}],
                }
            ]
        }
    )


def _thread_spy(real, seen_threads):
    def spy(*args, **kwargs):
        _assert_worker_thread(seen_threads)
        return real(*args, **kwargs)

    return spy


@pytest.mark.asyncio
async def test_after_tool_call_drains_and_merges_off_loop(monkeypatch):
    loop_thread = threading.get_ident()
    rail, capture = _make_rail_with_capture(monkeypatch)
    increment = _minimal_trajectory()
    drain_threads: list[int] = []
    merge_threads: list[int] = []

    async def heartbeat():
        last = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(0.005)
            last = asyncio.get_running_loop().time()

    def fake_drain(subscription):
        _assert_worker_thread(drain_threads)
        return increment, ()

    monkeypatch.setattr(rail._trajectory_span_processor, "drain", fake_drain)
    monkeypatch.setattr(
        evolution_rail_module,
        "normalize_otlp",
        _thread_spy(evolution_rail_module.normalize_otlp, merge_threads),
    )
    monkeypatch.setattr(
        evolution_rail_module,
        "_trim_window",
        _thread_spy(evolution_rail_module._trim_window, merge_threads),
    )
    received: list = []

    async def on_after_tool(ctx, trajectory):
        received.append(trajectory)

    monkeypatch.setattr(rail, "_on_after_tool_call", on_after_tool)
    monkeypatch.setattr(rail, "_allow_evolution_trigger", lambda point, ctx: False)

    beats = asyncio.create_task(heartbeat())
    try:
        await rail.after_tool_call(SimpleNamespace(inputs=SimpleNamespace()))
    finally:
        beats.cancel()

    assert drain_threads, "drain was never invoked"
    assert all(tid != loop_thread for tid in drain_threads)
    assert merge_threads, "merge/trim was never invoked"
    assert all(tid != loop_thread for tid in merge_threads)
    assert received and received[0] is not None


@pytest.mark.asyncio
async def test_after_model_call_drains_and_merges_off_loop(monkeypatch):
    loop_thread = threading.get_ident()
    rail, capture = _make_rail_with_capture(monkeypatch)
    increment = _minimal_trajectory(span_name="llm.call")
    drain_threads: list[int] = []
    merge_threads: list[int] = []

    def fake_drain(subscription):
        _assert_worker_thread(drain_threads)
        return increment, ()

    monkeypatch.setattr(rail._trajectory_span_processor, "drain", fake_drain)
    monkeypatch.setattr(rail, "_enrich_latest_llm", lambda traj, response: traj)
    monkeypatch.setattr(
        evolution_rail_module,
        "normalize_otlp",
        _thread_spy(evolution_rail_module.normalize_otlp, merge_threads),
    )
    monkeypatch.setattr(
        evolution_rail_module,
        "_trim_window",
        _thread_spy(evolution_rail_module._trim_window, merge_threads),
    )
    received: list = []

    async def on_after_model(ctx, trajectory):
        received.append(trajectory)

    monkeypatch.setattr(rail, "_on_after_model_call", on_after_model)
    monkeypatch.setattr(rail, "_allow_evolution_trigger", lambda point, ctx: False)

    ctx = SimpleNamespace(inputs=ModelCallInputs())
    await rail.after_model_call(ctx)

    assert drain_threads, "drain was never invoked"
    assert all(tid != loop_thread for tid in drain_threads)
    assert merge_threads, "merge/trim was never invoked"
    assert all(tid != loop_thread for tid in merge_threads)
    assert received and received[0] is not None


async def _async_noop(ctx, trajectory):
    return None
