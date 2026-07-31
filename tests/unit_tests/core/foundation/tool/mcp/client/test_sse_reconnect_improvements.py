#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for SseClient reconnect behavior improvements.

- Concurrent reconnect result/exception propagation (waiters see the
      real result/exception of the in-flight reconnect).
- Session call timeout handling (hung session call should raise
      asyncio.TimeoutError within the configured bound).
- Race between public disconnect() and concurrent reconnect() should
      not interleave into an inconsistent half-torn state.
- Retryable-error classification: the "not connected" guard must not be
      treated as a retryable transport error, and marker matching is
      case-insensitive.
"""
from __future__ import annotations

import asyncio
import pytest
from typing import Any

from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.reconnect import is_retryable_transport_error
from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient


def _make_client() -> SseClient:
    config = McpServerConfig(
        server_name="test-sse",
        server_path="http://test.local/sse",
    )
    return SseClient(config)


def _install_fake_lifecycle(client: SseClient) -> dict[str, Any]:
    """Replace _do_connect/_do_disconnect with minimal fakes; return state."""
    state: dict[str, Any] = {
        "connect_tasks": [],
        "disconnect_tasks": [],
    }

    async def _fake_connect(*, timeout: float = -1) -> bool:
        state["connect_tasks"].append(asyncio.current_task())
        client._session = object()
        client._is_disconnected = False
        return True

    async def _fake_disconnect(*, timeout: float = -1) -> bool:
        state["disconnect_tasks"].append(asyncio.current_task())
        client._session = None
        return True

    client._do_connect = _fake_connect
    client._do_disconnect = _fake_disconnect
    return state


async def _stop(client: SseClient) -> None:
    """Stop the owner task cleanly (if alive) so no task leaks."""
    if client._owner_task is None or client._owner_task.done():
        return
    client._owner_task.cancel()
    try:
        await client._owner_task
    except (asyncio.CancelledError, Exception):
        pass


class TestSseReconnectR1R2R6:
    @pytest.mark.asyncio
    async def test_concurrent_reconnect_shares_failure(self) -> None:
        """All concurrent reconnect() callers see the same False result
        when the in-flight reconnect fails."""
        client = _make_client()
        _install_fake_lifecycle(client)
        await client.connect()

        async def _fail_reconnect(*, timeout: float = -1) -> bool:
            await asyncio.sleep(0.02)
            return False

        client._do_reconnect = _fail_reconnect

        results = await asyncio.gather(
            client.reconnect(timeout=-1),
            client.reconnect(timeout=-1),
            client.reconnect(timeout=-1),
        )
        assert results == [False, False, False]
        await _stop(client)

    @pytest.mark.asyncio
    async def test_concurrent_reconnect_shares_exception(self) -> None:
        """All concurrent reconnect() callers see the same exception."""
        client = _make_client()
        _install_fake_lifecycle(client)
        await client.connect()

        async def _boom_reconnect(*, timeout: float = -1) -> bool:
            await asyncio.sleep(0.01)
            raise RuntimeError("reconnect blew up")

        client._do_reconnect = _boom_reconnect

        results = await asyncio.gather(
            client.reconnect(timeout=-1),
            client.reconnect(timeout=-1),
            client.reconnect(timeout=-1),
            return_exceptions=True,
        )
        assert all(isinstance(e, RuntimeError) and str(e) == "reconnect blew up" for e in results)
        await _stop(client)

    @pytest.mark.asyncio
    async def test_call_tool_times_out_on_hung_session(self) -> None:
        """A hung session call must raise asyncio.TimeoutError directly
        from the inner wait_for, not hang the caller. Because "timeout" is
        intentionally NOT a retryable marker, the decorator lets the
        TimeoutError propagate without attempting a reconnect.
        """
        client = _make_client()

        class _FakeSession:
            async def call_tool(self, *a, **kw):
                await asyncio.sleep(10)

        client._session = _FakeSession()

        with pytest.raises(asyncio.TimeoutError):
            # No outer safety net — R2 must bound the call itself.
            await client.call_tool("t", {}, timeout=0.05)
        await _stop(client)

    @pytest.mark.asyncio
    async def test_concurrent_disconnect_reconnect_no_interleave(self) -> None:
        """Concurrent public disconnect() and reconnect() should not
        interleave into a half-torn state. The invariant asserted here is
        either session is down (disconnect wins) or owner is alive
        (reconnect wins)."""
        client = _make_client()
        _install_fake_lifecycle(client)
        await client.connect()

        async def slow_disconnect(*, timeout: float = -1) -> bool:
            await asyncio.sleep(0.05)
            client._session = None
            return True

        client._do_disconnect = slow_disconnect

        await asyncio.gather(
            client.disconnect(),
            client.reconnect(timeout=-1),
            return_exceptions=True,
        )

        assert client._session is None or (client._owner_task is not None and not client._owner_task.done())
        await _stop(client)

    @pytest.mark.asyncio
    async def test_disconnect_during_inflight_reconnect_does_not_orphan(self) -> None:
        """If a public disconnect() arrives while a reconnect is
        in-flight, no caller should hang forever on an orphaned future.
        Every reconnect() awaiter resolves (result or exception) and the
        owner ends in a consistent state."""
        client = _make_client()
        _install_fake_lifecycle(client)
        await client.connect()

        started = asyncio.Event()

        async def slow_reconnect(*, timeout: float = -1) -> bool:
            started.set()
            await asyncio.sleep(0.05)
            client._session = object()
            client._is_disconnected = False
            return True

        client._do_reconnect = slow_reconnect

        async def _reconnect_caller() -> Any:
            return await client.reconnect(timeout=-1)

        # Kick a reconnect, let it enter the slow path, then fire disconnect.
        rc = asyncio.create_task(_reconnect_caller())
        await started.wait()
        await asyncio.gather(client.disconnect(), rc, return_exceptions=True)

        # No reconnect caller left pending; owner consistent.
        assert rc.done()
        assert client._owner_task is None or client._owner_task.done()
        await _stop(client)

    @pytest.mark.asyncio
    async def test_not_connected_guard_not_treated_as_retryable(self) -> None:
        """The ``RuntimeError("Not connected to SSE server")`` guard raised
        when ``_session`` is None must NOT be treated as a retryable transport
        error. Otherwise a call made while not connected triggers a wasteful
        disconnect+connect instead of failing fast. The decorator lets the
        RuntimeError propagate without reconnecting."""
        err = RuntimeError("Not connected to SSE server")
        assert is_retryable_transport_error(err) is False

    @pytest.mark.asyncio
    async def test_retryable_markers_match_case_insensitively(self) -> None:
        """Marker matching is case-insensitive on both the exception class name
        and message text, regardless of how the marker pattern is cased at the
        definition site."""
        # Message with mixed case still matches a "broken pipe" marker.
        assert is_retryable_transport_error(RuntimeError("Broken PIPE on socket")) is True
        # A class whose name lowercases to contain a marker substring matches.
        class _EndOfStream(Exception):
            pass
        assert is_retryable_transport_error(_EndOfStream()) is True

    @pytest.mark.asyncio
    async def test_not_connected_marker_skipped_only_for_runtime_error(self) -> None:
        """The "not connected" marker is skipped for RuntimeError (our guard),
        but still applies to other exception types whose message contains it
        (e.g. an anyio-style resource error surfaced as a plain Exception)."""
        assert is_retryable_transport_error(RuntimeError("not connected")) is False
        assert is_retryable_transport_error(Exception("not connected")) is True

    @pytest.mark.asyncio
    async def test_retryable_by_type_even_without_matching_marker(self) -> None:
        """Retryable transport errors are also detected by their type (anyio
        ClosedResourceError / BrokenResourceError), not only by message markers.
        Guards the _RETRYABLE_TYPES path which once regressed to dead code
        after the marker refactor removed its definition."""
        from anyio import BrokenResourceError, ClosedResourceError

        assert is_retryable_transport_error(ClosedResourceError()) is True
        assert is_retryable_transport_error(BrokenResourceError()) is True

    @pytest.mark.asyncio
    async def test_call_tool_when_not_connected_does_not_reconnect(self) -> None:
        """Calling call_tool with no active session raises the guard error
        directly and does NOT invoke the (mocked) reconnect path."""
        client = _make_client()
        # No connect() called → _session is None → call_tool hits the guard.
        reconnect_calls: list[int] = []
        original_reconnect = client.reconnect

        async def _spy_reconnect(*, timeout: float = -1) -> bool:
            reconnect_calls.append(1)
            return await original_reconnect(timeout=timeout)

        client.reconnect = _spy_reconnect  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="Not connected to SSE server"):
            await client.call_tool("t", {}, timeout=0.05)
        assert reconnect_calls == []
        await _stop(client)

    @pytest.mark.asyncio
    async def test_owner_loop_survives_caller_cancel_mid_command(self) -> None:
        """If the caller cancels its await while a command is executing
        on the owner task, the owner loop must stay alive and serve the next
        command. The in-flight command completes on the owner task (its
        result is dropped since the caller's future is cancelled)."""
        client = _make_client()
        _install_fake_lifecycle(client)

        release = asyncio.Event()

        async def _blocking_connect(*, timeout: float = -1) -> bool:
            await release.wait()  # blocks on owner task until released
            client._session = object()
            client._is_disconnected = False
            return True

        client._do_connect = _blocking_connect

        # Caller times out and cancels its wait on the future.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client.connect(), timeout=0.02)
        # The command is still running on the owner; release it.
        release.set()
        await asyncio.sleep(0)

        # Owner loop survived → a new connect submits and succeeds.
        assert await client.connect() is True
        assert client._owner_task is not None and not client._owner_task.done()
        await _stop(client)
