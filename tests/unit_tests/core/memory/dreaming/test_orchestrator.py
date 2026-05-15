# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DreamingOrchestrator unit tests."""

from __future__ import annotations

import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from openjiuwen.core.memory.dreaming.orchestrator import (
    DreamingOrchestrator,
)

_MOD = "openjiuwen.core.memory.dreaming.orchestrator"


# ---------------------------------------------------------------------------
# Constructor & health
# ---------------------------------------------------------------------------


class TestDreamingOrchestratorInit(IsolatedAsyncioTestCase):
    def test_init_defaults(self):
        orch = DreamingOrchestrator(
            sweep_fn=AsyncMock(),
            interval_seconds=3600.0,
        )
        self.assertEqual(orch._name, "dreaming")
        self.assertFalse(orch._running)
        self.assertIsNone(orch._task)
        self.assertIsNone(orch._busy_checker)

    def test_init_interval_clamped(self):
        orch_low = DreamingOrchestrator(sweep_fn=AsyncMock(), interval_seconds=10.0)
        self.assertEqual(orch_low._interval, 60.0)

        orch_ok = DreamingOrchestrator(sweep_fn=AsyncMock(), interval_seconds=120.0)
        self.assertEqual(orch_ok._interval, 120.0)

    def test_health_property(self):
        orch = DreamingOrchestrator(sweep_fn=AsyncMock(), interval_seconds=3600.0)
        h = orch.health
        self.assertFalse(h["running"])
        self.assertEqual(h["interval_seconds"], 3600.0)


# ---------------------------------------------------------------------------
# Lifecycle: start / stop
# ---------------------------------------------------------------------------


class TestDreamingOrchestratorLifecycle(IsolatedAsyncioTestCase):
    async def test_start_creates_task_and_sets_running(self):
        orch = DreamingOrchestrator(sweep_fn=AsyncMock(), interval_seconds=60.0)
        await orch.start()
        self.assertTrue(orch._running)
        self.assertIsNotNone(orch._task)
        self.assertIsInstance(orch._task, asyncio.Task)
        self.assertIn("dreaming-loop", orch._task.get_name())
        orch._running = False
        orch._task.cancel()
        try:
            await orch._task
        except asyncio.CancelledError:
            pass

    async def test_start_idempotent(self):
        orch = DreamingOrchestrator(sweep_fn=AsyncMock(), interval_seconds=60.0)
        await orch.start()
        first_task = orch._task
        await orch.start()
        self.assertIs(orch._task, first_task)
        orch._running = False
        orch._task.cancel()
        try:
            await orch._task
        except asyncio.CancelledError:
            pass

    async def test_stop_cancels_task_and_clears_running(self):
        orch = DreamingOrchestrator(sweep_fn=AsyncMock(), interval_seconds=60.0)
        await orch.start()
        await orch.stop()
        self.assertFalse(orch._running)
        self.assertIsNone(orch._task)

    async def test_stop_idempotent(self):
        orch = DreamingOrchestrator(sweep_fn=AsyncMock(), interval_seconds=60.0)
        await orch.start()
        await orch.stop()
        await orch.stop()
        self.assertFalse(orch._running)

    async def test_stop_when_never_started(self):
        orch = DreamingOrchestrator(sweep_fn=AsyncMock(), interval_seconds=60.0)
        await orch.stop()
        self.assertFalse(orch._running)


# ---------------------------------------------------------------------------
# _tick -- busy backoff & error handling
# ---------------------------------------------------------------------------


class TestDreamingOrchestratorTick(IsolatedAsyncioTestCase):
    async def test_tick_sweep_runs_when_no_busy_checker(self):
        sweep = AsyncMock()
        orch = DreamingOrchestrator(sweep_fn=sweep, interval_seconds=60.0)
        orch._running = True
        await orch._tick()
        sweep.assert_awaited_once()

    async def test_tick_skips_sweep_when_busy(self):
        sweep = AsyncMock()
        busy = Mock(return_value=True)
        orch = DreamingOrchestrator(
            sweep_fn=sweep, interval_seconds=60.0, busy_checker=busy,
        )
        orch._running = True
        await orch._tick()
        busy.assert_called_once()
        sweep.assert_not_awaited()

    async def test_tick_runs_sweep_when_not_busy(self):
        sweep = AsyncMock()
        busy = Mock(return_value=False)
        orch = DreamingOrchestrator(
            sweep_fn=sweep, interval_seconds=60.0, busy_checker=busy,
        )
        orch._running = True
        await orch._tick()
        busy.assert_called_once()
        sweep.assert_awaited_once()

    async def test_tick_busy_checker_exception_does_not_block_sweep(self):
        sweep = AsyncMock()
        busy = Mock(side_effect=RuntimeError("checker crash"))
        orch = DreamingOrchestrator(
            sweep_fn=sweep, interval_seconds=60.0, busy_checker=busy,
        )
        orch._running = True
        with self.assertLogs(_MOD, level="WARNING") as log_ctx:
            await orch._tick()
        sweep.assert_awaited_once()
        self.assertTrue(
            any("busy_checker raised exception" in m for m in log_ctx.output),
            "Expected warning log for checker exception",
        )

    async def test_tick_sweep_exception_logged(self):
        sweep = AsyncMock(side_effect=ValueError("sweep failure"))
        orch = DreamingOrchestrator(sweep_fn=sweep, interval_seconds=60.0)
        orch._running = True
        with self.assertLogs(_MOD, level="ERROR") as log_ctx:
            await orch._tick()
        self.assertTrue(
            any("sweep exception" in m for m in log_ctx.output),
            "Expected error log for sweep exception",
        )

    async def test_tick_cancelled_error_propagates(self):
        sweep = AsyncMock(side_effect=asyncio.CancelledError())
        orch = DreamingOrchestrator(sweep_fn=sweep, interval_seconds=60.0)
        orch._running = True
        with self.assertRaises(asyncio.CancelledError):
            await orch._tick()


# ---------------------------------------------------------------------------
# _loop scheduling
# ---------------------------------------------------------------------------


class TestDreamingOrchestratorLoop(IsolatedAsyncioTestCase):
    async def test_loop_respects_cancelled_error(self):
        sweep = AsyncMock()
        orch = DreamingOrchestrator(sweep_fn=sweep, interval_seconds=60.0)
        orch._running = True
        task = asyncio.create_task(orch._loop())
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_loop_exits_when_not_running(self):
        sweep = AsyncMock()
        orch = DreamingOrchestrator(sweep_fn=sweep, interval_seconds=60.0)
        orch._running = True
        orch._interval = 0.01
        with patch.object(asyncio, "sleep", new=AsyncMock()) as mock_sleep:
            task = asyncio.create_task(orch._loop())
            await asyncio.sleep(0.03)
            orch._running = False
            await asyncio.wait_for(task, timeout=2.0)
        sweep.assert_not_awaited()

    async def test_loop_unexpected_exception_is_logged(self):
        sweep = AsyncMock()
        orch = DreamingOrchestrator(sweep_fn=sweep, interval_seconds=60.0)
        orch._running = True
        fake_sleep = AsyncMock(side_effect=ValueError("sleep crash"))
        with patch.object(asyncio, "sleep", new=fake_sleep):
            with self.assertLogs(_MOD, level="ERROR") as log_ctx:
                task = asyncio.create_task(orch._loop())
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except asyncio.TimeoutError:
                    pass
        self.assertTrue(
            any("loop terminated by unexpected error" in m for m in log_ctx.output),
            "Expected error log for loop termination",
        )


# ---------------------------------------------------------------------------
# Integration: multi-tick with interval
# ---------------------------------------------------------------------------


class TestDreamingOrchestratorIntegration(IsolatedAsyncioTestCase):
    async def test_stop_during_initial_sleep_is_cancelled_cleanly(self):
        sweep = AsyncMock()
        orch = DreamingOrchestrator(sweep_fn=sweep, interval_seconds=60.0)
        await orch.start()
        await asyncio.sleep(0.01)
        await orch.stop()
        self.assertFalse(orch._running)
        sweep.assert_not_awaited()

    async def test_custom_name_propagates(self):
        sweep = AsyncMock()
        orch = DreamingOrchestrator(
            sweep_fn=sweep, interval_seconds=60.0, name="code-dreaming",
        )
        self.assertEqual(orch._name, "code-dreaming")
        await orch.start()
        self.assertIn("code-dreaming-loop", orch._task.get_name())
        orch._running = False
        orch._task.cancel()
        try:
            await orch._task
        except asyncio.CancelledError:
            pass
