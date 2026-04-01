#!/usr/bin/env python
# coding: utf-8
# pylint: disable=protected-access
"""Tests for BrowserService heartbeat lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.deepagents.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.service import BrowserService
from openjiuwen.core.foundation.tool import McpServerConfig


def _run(coro):
    return asyncio.run(coro)


def _make_service() -> BrowserService:
    mcp_cfg = McpServerConfig(
        server_id="test",
        server_name="test",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": "."},
    )
    return BrowserService(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(max_steps=3, max_failures=1, timeout_s=30, retry_once=False),
    )


# ---------------------------------------------------------------------------
# _start_heartbeat
# ---------------------------------------------------------------------------


def test_start_heartbeat_no_new_task_while_running() -> None:
    async def _test():
        svc = _make_service()

        async def _noop():
            try:
                await asyncio.sleep(9999)
            except asyncio.CancelledError:
                pass

        svc._heartbeat_loop = _noop
        svc._start_heartbeat()
        first_task = svc._heartbeat_task
        svc._start_heartbeat()
        assert svc._heartbeat_task is first_task
        first_task.cancel()
        try:
            await first_task
        except asyncio.CancelledError:
            pass

    _run(_test())


def test_start_heartbeat_replaces_done_task() -> None:
    async def _test():
        svc = _make_service()

        async def _immediate():
            return

        svc._heartbeat_loop = _immediate
        svc._start_heartbeat()
        first_task = svc._heartbeat_task
        await first_task

        async def _noop():
            try:
                await asyncio.sleep(9999)
            except asyncio.CancelledError:
                pass

        svc._heartbeat_loop = _noop
        svc._start_heartbeat()
        assert svc._heartbeat_task is not first_task
        svc._heartbeat_task.cancel()
        try:
            await svc._heartbeat_task
        except asyncio.CancelledError:
            pass

    _run(_test())


# ---------------------------------------------------------------------------
# _check_connection
# ---------------------------------------------------------------------------


def test_check_connection_raises_when_client_not_found() -> None:
    async def _test():
        svc = _make_service()
        with patch(
            "openjiuwen.deepagents.tools.browser_move.playwright_runtime.browser_tools.get_registered_client",
            return_value=None,
        ):
            try:
                await svc._check_connection()
                assert False, "Expected RuntimeError"
            except RuntimeError as exc:
                assert "client" in str(exc).lower() or "not found" in str(exc).lower()

    _run(_test())


def test_check_connection_raises_when_ping_fails() -> None:
    async def _test():
        svc = _make_service()
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=False)
        with patch(
            "openjiuwen.deepagents.tools.browser_move.playwright_runtime.browser_tools.get_registered_client",
            return_value=mock_client,
        ):
            try:
                await svc._check_connection()
                assert False, "Expected RuntimeError"
            except RuntimeError as exc:
                assert "not responding" in str(exc).lower()

    _run(_test())


def test_check_connection_succeeds_when_healthy() -> None:
    async def _test():
        svc = _make_service()
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)
        with patch(
            "openjiuwen.deepagents.tools.browser_move.playwright_runtime.browser_tools.get_registered_client",
            return_value=mock_client,
        ):
            await svc._check_connection()  # must not raise

    _run(_test())


def test_check_connection_raises_when_managed_driver_not_ready() -> None:
    async def _test():
        svc = _make_service()
        svc._managed_driver = MagicMock()
        svc._managed_driver._is_endpoint_ready = MagicMock(return_value=False)
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)
        with patch(
            "openjiuwen.deepagents.tools.browser_move.playwright_runtime.browser_tools.get_registered_client",
            return_value=mock_client,
        ):
            try:
                await svc._check_connection()
                assert False, "Expected RuntimeError"
            except RuntimeError as exc:
                assert "cdp" in str(exc).lower() or "endpoint" in str(exc).lower()

    _run(_test())


# ---------------------------------------------------------------------------
# _heartbeat_loop (real loop body, zero-interval)
# ---------------------------------------------------------------------------


def test_heartbeat_loop_marks_connection_healthy_on_success() -> None:
    async def _test():
        svc = _make_service()
        svc._heartbeat_interval = 0
        checked = asyncio.Event()

        async def mock_check():
            checked.set()

        svc._check_connection = mock_check
        task = asyncio.create_task(svc._heartbeat_loop())
        await asyncio.wait_for(checked.wait(), timeout=2.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert svc._connection_healthy is True
        assert svc._last_heartbeat_ok is not None

    _run(_test())


def test_heartbeat_loop_marks_connection_unhealthy_on_failure() -> None:
    async def _test():
        svc = _make_service()
        svc._heartbeat_interval = 0
        svc._restart = AsyncMock()
        checked = asyncio.Event()

        async def mock_check():
            checked.set()
            raise RuntimeError("connection lost")

        svc._check_connection = mock_check
        task = asyncio.create_task(svc._heartbeat_loop())
        await asyncio.wait_for(checked.wait(), timeout=2.0)
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert svc._connection_healthy is False

    _run(_test())


def test_heartbeat_loop_defers_restart_when_no_inflight_tasks() -> None:
    async def _test():
        svc = _make_service()
        svc._heartbeat_interval = 0
        assert not svc._inflight_tasks
        svc._restart = AsyncMock()
        checked = asyncio.Event()

        async def mock_check():
            checked.set()
            raise RuntimeError("down")

        svc._check_connection = mock_check
        task = asyncio.create_task(svc._heartbeat_loop())
        await asyncio.wait_for(checked.wait(), timeout=2.0)
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        svc._restart.assert_not_awaited()

    _run(_test())


def test_heartbeat_loop_skips_restart_when_inflight_tasks_present() -> None:
    async def _test():
        svc = _make_service()
        svc._heartbeat_interval = 0
        svc._inflight_tasks["session:req"] = {MagicMock()}
        restart_called = asyncio.Event()

        async def fake_restart():
            restart_called.set()

        svc._restart = fake_restart
        checked = asyncio.Event()

        async def mock_check():
            checked.set()
            raise RuntimeError("down")

        svc._check_connection = mock_check
        task = asyncio.create_task(svc._heartbeat_loop())
        await asyncio.wait_for(checked.wait(), timeout=2.0)
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert not restart_called.is_set()

    _run(_test())


# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------


def test_shutdown_cancels_heartbeat_task() -> None:
    async def _test():
        svc = _make_service()

        async def _long():
            await asyncio.sleep(9999)

        svc._heartbeat_task = asyncio.create_task(_long())
        with patch("playwright_runtime.service.Runner") as mock_runner:
            mock_runner.stop = AsyncMock()
            with patch.object(svc, "_stop_managed_driver", AsyncMock()):
                await svc.shutdown()

        assert svc._heartbeat_task.done()

    _run(_test())
