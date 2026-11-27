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
- Retryable-error classification: bare ``Not connected`` is not a
      transport-error marker for RuntimeError; lazy reconnect may still
      recover after a prior successful connect.
"""
from __future__ import annotations

import asyncio
import pytest
from typing import Any
from unittest.mock import patch

from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client import sse_client as sse_mod
from openjiuwen.core.foundation.tool.mcp.client.reconnect import (
    is_retryable_transport_error,
    should_attempt_reconnect,
)
from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient
from openjiuwen.core.foundation.tool.mcp.errors import McpTimeoutError


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
        "connects": 0,
    }

    async def _fake_connect(*, timeout: float = -1) -> bool:
        state["connect_tasks"].append(asyncio.current_task())
        state["connects"] += 1
        client._session = object()
        client._is_disconnected = False
        client._lazy_reconnect = True
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

        with pytest.raises(McpTimeoutError):
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
        """Bare ``Not connected`` remains excluded from transport-error markers.

        Lazy recovery is gated by ``should_attempt_reconnect`` +
        ``_lazy_reconnect``, not by flipping this marker to always-true.
        """
        err = RuntimeError("Not connected to SSE server")
        assert is_retryable_transport_error(err) is False

    @pytest.mark.asyncio
    async def test_not_connected_guard_only_retries_after_transport_failure(self) -> None:
        """Never-connected stays closed; post-connect empty session may recover."""
        client = _make_client()
        guard = RuntimeError("Not connected to SSE server")
        assert client._lazy_reconnect is False
        assert should_attempt_reconnect(client, guard) is False

        client._lazy_reconnect = True
        assert should_attempt_reconnect(client, guard) is True

    @pytest.mark.asyncio
    async def test_explicit_disconnect_clears_lazy_reconnect(self) -> None:
        client = _make_client()
        _install_fake_lifecycle(client)
        await client.connect()
        assert client._lazy_reconnect is True

        await client.disconnect()
        assert client._lazy_reconnect is False
        assert should_attempt_reconnect(
            client, RuntimeError("Not connected to SSE server")
        ) is False

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
        """Never-connected call_tool fails fast and does not reconnect."""
        client = _make_client()
        # No connect() called → _session is None, _lazy_reconnect is False.
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
    async def test_call_tool_recovers_after_failed_reconnect_cleared_session(
        self,
    ) -> None:
        """After a transport teardown left ``_session`` empty, lazy reconnect
        rebuilds the transport when the peer is available again."""
        client = _make_client()
        state = _install_fake_lifecycle(client)
        await client.connect()
        assert client._lazy_reconnect is True
        # Simulate: automatic reconnect already ran once, cleared the session,
        # and left the client marked for lazy recovery.
        client._session = None
        client._reconnect_cooldown_until = 0.0

        class _OkSession:
            async def call_tool(self, name: str, arguments: Any = None) -> Any:
                return object()

        async def _recover_connect(*, timeout: float = -1) -> bool:
            state["connects"] += 1
            client._session = _OkSession()
            client._is_disconnected = False
            client._lazy_reconnect = True
            return True

        client._do_connect = _recover_connect
        with patch.object(
            sse_mod, "extract_mcp_tool_result_content", lambda *a, **k: {"ok": True}
        ):
            assert await client.call_tool("t", {}, timeout=0.5) == {"ok": True}
        # Initial connect + recovery connect.
        assert state["connects"] >= 2
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

    @pytest.mark.asyncio
    async def test_invalidate_for_lazy_reconnect_keeps_lazy_flag(self) -> None:
        """Call-timeout detach must clear the session but keep lazy reconnect.

        Public ``disconnect()`` clears ``_lazy_reconnect`` (final teardown).
        Timeout recovery needs the opposite: tear down the zombie transport
        while still allowing the next ``call_tool`` to rebuild.
        """
        client = _make_client()
        state = _install_fake_lifecycle(client)
        await client.connect()
        assert client._session is not None
        assert client._lazy_reconnect is True

        ok = await client.invalidate_for_lazy_reconnect(timeout=0.5)
        assert ok is True
        assert client._session is None
        assert client._lazy_reconnect is True
        assert len(state["disconnect_tasks"]) >= 1
        await _stop(client)

    @pytest.mark.asyncio
    async def test_do_disconnect_aclose_cleans_after_session_nulled(self) -> None:
        """Even if ``_session`` was nulled early, aclose still tears the stack.

        Call-timeout paths used to null ``_session`` then skip manual
        ``__aexit__``; zombie SSE generators stayed open. aclose-based
        teardown must still run when the ref is already gone.
        """
        client = _make_client()
        aclose_calls: list[int] = []

        class _Stack:
            async def aclose(self) -> None:
                aclose_calls.append(1)

        client._exit_stack = _Stack()  # type: ignore[assignment]
        client._session = None  # simulate timeout half-detach
        client._client = object()
        client._read = object()
        client._write = object()

        assert await client._do_disconnect(timeout=1.0) is True
        assert aclose_calls == [1]
        assert client._session is None
        assert client._client is None
        assert client._read is None
        assert client._write is None
        assert client._is_disconnected is True

    @pytest.mark.asyncio
    async def test_disconnect_releases_waiter_for_cancelled_teardown_group(self) -> None:
        """aclose raising a CancelledError group must not hang disconnect/lock.

        anyio teardown often wraps CancelledError in BaseExceptionGroup. The
        group is not an instance of CancelledError, so a naive ``raise`` would
        bypass the disconnect handler and leave ``await fut`` unfinished while
        ``_lifecycle_lock`` is still held.
        """
        client = _make_client()

        class _CancelledTeardown:
            async def aclose(self) -> None:
                raise BaseExceptionGroup("teardown", [asyncio.CancelledError()])

        client._exit_stack = _CancelledTeardown()  # type: ignore[assignment]
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(client.disconnect(), timeout=0.2)
        assert client._session is None
        assert client._is_disconnected is True
        # Lock must be free; a stuck lock would block past this timeout.
        async with asyncio.timeout(0.2):
            async with client._lifecycle_lock:
                pass
        await _stop(client)

    @pytest.mark.asyncio
    async def test_disconnect_preserves_real_error_from_mixed_teardown_group(self) -> None:
        """A mixed teardown group must surface the real Exception to the caller."""
        client = _make_client()

        class _MixedTeardown:
            async def aclose(self) -> None:
                raise BaseExceptionGroup(
                    "teardown",
                    [asyncio.CancelledError(), RuntimeError("real teardown failure")],
                )

        client._exit_stack = _MixedTeardown()  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="real teardown failure"):
            await asyncio.wait_for(client.disconnect(), timeout=0.2)
        assert client._session is None
        assert client._is_disconnected is True
        await _stop(client)

    @pytest.mark.asyncio
    async def test_invalidate_releases_lock_after_cancelled_teardown_group(self) -> None:
        """invalidate_for_lazy_reconnect must release _lifecycle_lock on group cancel."""
        client = _make_client()
        _install_fake_lifecycle(client)
        await client.connect()

        class _CancelledTeardown:
            async def aclose(self) -> None:
                raise BaseExceptionGroup("teardown", [asyncio.CancelledError()])

        # Drive the real _do_disconnect path (not the lifecycle fake).
        client._do_disconnect = SseClient._do_disconnect.__get__(client, SseClient)
        client._exit_stack = _CancelledTeardown()  # type: ignore[assignment]

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(client.invalidate_for_lazy_reconnect(), timeout=0.2)
        # Restore fakes so a second invalidate can succeed; hang here means lock stuck.
        _install_fake_lifecycle(client)
        assert await asyncio.wait_for(
            client.invalidate_for_lazy_reconnect(timeout=0.5), timeout=0.5
        ) is True
        await _stop(client)

    @pytest.mark.asyncio
    async def test_do_disconnect_watchdog_swallows_cancelled_teardown_group(self) -> None:
        """Watchdog cancel re-wrapped as BaseExceptionGroup still returns False."""
        client = _make_client()
        started = asyncio.Event()

        class _GroupOnCancel:
            async def aclose(self) -> None:
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    raise BaseExceptionGroup(
                        "teardown", [asyncio.CancelledError()]
                    )

        client._exit_stack = _GroupOnCancel()  # type: ignore[assignment]
        task = asyncio.create_task(client._do_disconnect(timeout=0.05))
        await started.wait()
        assert await asyncio.wait_for(task, timeout=0.5) is False
        assert client._session is None
        assert client._is_disconnected is True
