#!/usr/bin/env python
# coding: utf-8
"""Reconnect behaviour of the streamable HTTP MCP client."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client import streamable_http_client as shc
from openjiuwen.core.foundation.tool.mcp.client.reconnect import (
    is_retryable_transport_error,
    should_attempt_reconnect,
)
from openjiuwen.core.foundation.tool.mcp.client.streamable_http_client import (
    StreamableHttpClient,
)

SESSION_TERMINATED = McpError(ErrorData(code=32600, message="Session terminated"))
CONNECTION_CLOSED = McpError(ErrorData(code=-32000, message="Connection closed"))
INVALID_PARAMS = McpError(ErrorData(code=-32602, message="Invalid params"))


class _FakeSession:
    def __init__(self, fail_once: Optional[Exception] = None) -> None:
        self._fail_once = fail_once
        self.calls = 0

    async def call_tool(self, name: str, arguments: Any = None) -> Any:
        self.calls += 1
        if self.calls == 1 and self._fail_once is not None:
            raise self._fail_once
        return object()


def _make_client() -> StreamableHttpClient:
    return StreamableHttpClient(
        McpServerConfig(server_name="test-http", server_path="http://test.local/mcp")
    )


async def _stop(client: StreamableHttpClient) -> None:
    if client._owner_task is None or client._owner_task.done():
        return
    client._owner_task.cancel()
    try:
        await client._owner_task
    except (asyncio.CancelledError, Exception):
        pass


def _install_fakes(
    client: StreamableHttpClient,
    *,
    first_session: _FakeSession,
    next_session: Optional[_FakeSession] = None,
) -> dict[str, int]:
    state = {"connects": 0, "disconnects": 0}
    client._session = first_session

    async def _connect(*, timeout: float = -1) -> bool:
        state["connects"] += 1
        client._session = next_session if next_session is not None else _FakeSession()
        client._is_disconnected = False
        client._lazy_reconnect = True
        return True

    async def _disconnect(*, timeout: float = -1) -> bool:
        state["disconnects"] += 1
        client._session = None
        client._is_disconnected = True
        return True

    client._do_connect = _connect
    client._do_disconnect = _disconnect
    return state


@pytest.mark.asyncio
async def test_session_terminated_reconnects_and_retries(monkeypatch: Any) -> None:
    monkeypatch.setattr(shc, "extract_mcp_tool_result_content", lambda *a, **k: {"ok": True})
    client = _make_client()
    healthy = _FakeSession()
    state = _install_fakes(
        client,
        first_session=_FakeSession(fail_once=SESSION_TERMINATED),
        next_session=healthy,
    )

    assert await client.call_tool("t", {}) == {"ok": True}
    assert state["connects"] == 1
    assert healthy.calls == 1
    await _stop(client)


@pytest.mark.asyncio
async def test_business_mcp_error_is_not_retried(monkeypatch: Any) -> None:
    monkeypatch.setattr(shc, "extract_mcp_tool_result_content", lambda *a, **k: {"ok": True})
    client = _make_client()
    state = _install_fakes(
        client,
        first_session=_FakeSession(fail_once=INVALID_PARAMS),
    )

    with pytest.raises(McpError):
        await client.call_tool("t", {})
    assert state["connects"] == 0
    await _stop(client)


@pytest.mark.asyncio
async def test_generation_change_skips_duplicate_reconnect(monkeypatch: Any) -> None:
    """A concurrent caller's successful reconnect must not be torn down again."""
    monkeypatch.setattr(shc, "extract_mcp_tool_result_content", lambda *a, **k: {"ok": True})
    client = _make_client()
    client._generation = 1

    class _Session:
        def __init__(self) -> None:
            self.calls = 0

        async def call_tool(self, name: str, arguments: Any = None) -> Any:
            self.calls += 1
            if self.calls == 1:
                client._generation = 2  # transport rebuilt by another caller
                raise SESSION_TERMINATED
            return object()

    client._session = _Session()
    state = {"connects": 0}

    async def _connect(*, timeout: float = -1) -> bool:
        state["connects"] += 1
        return True

    client._do_connect = _connect
    assert await client.call_tool("t", {}) == {"ok": True}
    assert state["connects"] == 0
    await _stop(client)


@pytest.mark.asyncio
async def test_concurrent_reconnect_runs_once() -> None:
    client = _make_client()
    _install_fakes(client, first_session=_FakeSession())
    assert await client.connect() is True

    state = {"reconnects": 0}

    async def _do_reconnect(*, timeout: float = -1) -> bool:
        state["reconnects"] += 1
        await asyncio.sleep(0.02)
        return True

    client._do_reconnect = _do_reconnect
    results = await asyncio.gather(
        client.reconnect(), client.reconnect(), client.reconnect()
    )
    assert results == [True, True, True]
    assert state["reconnects"] == 1
    await _stop(client)


@pytest.mark.asyncio
async def test_backoff_blocks_repeat_auto_attempts(monkeypatch: Any) -> None:
    """A failed reconnect sets a cooldown so calls don't hammer a dead peer."""
    monkeypatch.setattr(shc, "extract_mcp_tool_result_content", lambda *a, **k: {"ok": True})
    client = _make_client()
    client._session = _FakeSession(fail_once=SESSION_TERMINATED)
    state = {"connects": 0}

    async def _connect(*, timeout: float = -1) -> bool:
        state["connects"] += 1
        return False

    async def _disconnect(*, timeout: float = -1) -> bool:
        client._session = None
        client._is_disconnected = True
        return True

    client._do_connect = _connect
    client._do_disconnect = _disconnect

    with pytest.raises(McpError):
        await client.call_tool("t", {})
    assert state["connects"] == 1
    assert client._reconnect_failure_count == 1
    assert client._reconnect_cooldown_until > 0

    client._lazy_reconnect = True
    with pytest.raises(RuntimeError, match="Not connected"):
        await client.call_tool("t", {})
    assert state["connects"] == 1  # cooldown suppressed the second attempt


@pytest.mark.asyncio
async def test_reconnect_cancellation_is_retried(monkeypatch: Any) -> None:
    """A call cancelled by a reconnect retries on the new session."""
    monkeypatch.setattr(shc, "extract_mcp_tool_result_content", lambda *a, **k: {"ok": True})
    client = _make_client()

    class _CancelledSession:
        async def call_tool(self, name: str, arguments: Any = None) -> Any:
            task = asyncio.current_task()
            assert task is not None
            client._reconnect_cancelled_calls[task] = client._active_calls[task]
            raise asyncio.CancelledError()

    healthy = _FakeSession()
    state = _install_fakes(
        client,
        first_session=_CancelledSession(),  # type: ignore[arg-type]
        next_session=healthy,
    )
    client._lazy_reconnect = True
    client._reconnect_in_progress = True

    assert await client.call_tool("t", {}) == {"ok": True}
    assert state["connects"] == 1
    assert healthy.calls == 1
    await _stop(client)


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_retried() -> None:
    """A caller timeout/cancel must propagate without reconnecting."""
    client = _make_client()
    started = asyncio.Event()

    class _BlockingSession:
        async def call_tool(self, name: str, arguments: Any = None) -> Any:
            started.set()
            await asyncio.Event().wait()

    client._session = _BlockingSession()  # type: ignore[assignment]
    state = {"connects": 0}

    async def _connect(*, timeout: float = -1) -> bool:
        state["connects"] += 1
        return True

    client._do_connect = _connect
    task = asyncio.create_task(client.call_tool("t", {}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state["connects"] == 0
    assert client._active_calls == {}
    await _stop(client)


@pytest.mark.asyncio
async def test_manual_reconnect_bypasses_cooldown() -> None:
    """The explicit ``reconnect()`` API is never rate-limited."""
    client = _make_client()
    client._reconnect_cooldown_until = time.monotonic() + 60
    client._reconnect_failure_count = 3
    state = {"reconnects": 0}

    async def _do_reconnect(*, timeout: float = -1) -> bool:
        state["reconnects"] += 1
        return True

    client._do_reconnect = _do_reconnect
    assert await client.reconnect() is True
    assert state["reconnects"] == 1
    assert client._reconnect_failure_count == 0
    assert client._reconnect_cooldown_until == 0
    await _stop(client)


@pytest.mark.asyncio
async def test_owner_cancellation_releases_queued_submissions() -> None:
    """Commands queued behind a cancelled owner must not hang their callers."""
    client = _make_client()
    started = asyncio.Event()

    async def _blocking_connect(*, timeout: float = -1) -> bool:
        started.set()
        await asyncio.Event().wait()
        return True

    client._do_connect = _blocking_connect
    first = asyncio.create_task(client.connect())
    await started.wait()
    second = asyncio.create_task(client.connect())
    await asyncio.sleep(0)
    client._owner_task.cancel()

    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert client._cmd_queue.empty()
    await _stop(client)


@pytest.mark.asyncio
async def test_owner_cancellation_releases_shared_reconnect_future() -> None:
    """A dead owner must not leave reconnect waiters blocked forever."""
    client = _make_client()
    started = asyncio.Event()

    async def _blocking_connect(*, timeout: float = -1) -> bool:
        started.set()
        await asyncio.Event().wait()
        return True

    client._do_connect = _blocking_connect
    queued_connect = asyncio.create_task(client.connect())
    await started.wait()
    reconnect_owner = asyncio.create_task(client.reconnect())
    await asyncio.sleep(0)
    reconnect_waiter = asyncio.create_task(client.reconnect())
    await asyncio.sleep(0)
    client._owner_task.cancel()

    results = await asyncio.gather(
        queued_connect, reconnect_owner, reconnect_waiter, return_exceptions=True
    )
    assert all(isinstance(result, asyncio.CancelledError) for result in results[:2])
    assert results[2] is False
    assert client._reconnect_future is None
    assert client._reconnect_in_progress is False
    await _stop(client)


def test_no_timeout_uses_connect_deadline() -> None:
    assert StreamableHttpClient._resolve_timeout(-1) == 60.0
    assert StreamableHttpClient._resolve_timeout(2.5) == 2.5


@pytest.mark.asyncio
async def test_cancelled_reconnect_clears_state_only_after_owner_finishes() -> None:
    """A caller timeout must not let a second reconnect race the owner task."""
    client = _make_client()
    client._session = _FakeSession()

    async def _disconnect(*, timeout: float = -1) -> bool:
        await asyncio.sleep(0.1)
        client._session = None
        return True

    async def _connect(*, timeout: float = -1) -> bool:
        return True

    client._do_disconnect = _disconnect
    client._do_connect = _connect

    task = asyncio.create_task(client.reconnect(timeout=1))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client._reconnect_in_progress is True

    await asyncio.sleep(0.2)
    assert client._reconnect_in_progress is False
    assert client._reconnect_future is None
    await _stop(client)


@pytest.mark.asyncio
async def test_lifecycle_can_be_repeated() -> None:
    client = _make_client()
    _install_fakes(client, first_session=_FakeSession())
    for _ in range(2):
        assert await client.connect() is True
        assert await client.disconnect() is True
        assert client._session is None
        assert client._owner_task is None


@pytest.mark.asyncio
async def test_disconnect_resets_state_when_teardown_fails() -> None:
    client = _make_client()
    client._session = object()

    class _BadStack:
        async def aclose(self) -> None:
            raise RuntimeError("teardown failed")

    client._exit_stack = _BadStack()  # type: ignore[assignment]
    assert await client._do_disconnect(timeout=1) is False
    assert client._session is None
    assert client._read is None
    assert client._write is None
    assert client._is_disconnected is True


@pytest.mark.asyncio
async def test_disconnect_propagates_external_cancellation() -> None:
    """Only the disconnect watchdog may suppress the owner task cancellation."""
    client = _make_client()
    started = asyncio.Event()

    class _BlockingStack:
        async def aclose(self) -> None:
            started.set()
            await asyncio.Event().wait()

    client._exit_stack = _BlockingStack()  # type: ignore[assignment]
    task = asyncio.create_task(client._do_disconnect(timeout=10))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client._session is None
    assert client._is_disconnected is True


@pytest.mark.asyncio
async def test_disconnect_watchdog_cancels_stalled_teardown() -> None:
    """The watchdog remains the only cancellation source that is contained."""
    client = _make_client()

    class _BlockingStack:
        async def aclose(self) -> None:
            await asyncio.Event().wait()

    client._exit_stack = _BlockingStack()  # type: ignore[assignment]
    task = asyncio.create_task(client._do_disconnect(timeout=0.01))
    assert await task is False
    assert client._session is None
    assert client._is_disconnected is True


@pytest.mark.asyncio
async def test_not_connected_guard_only_retries_after_transport_failure() -> None:
    client = _make_client()
    guard = RuntimeError("Not connected to streamable-http server")

    assert should_attempt_reconnect(client, guard) is False
    client._lazy_reconnect = True
    assert should_attempt_reconnect(client, guard) is True


def test_only_transport_and_session_errors_are_retryable() -> None:
    assert is_retryable_transport_error(SESSION_TERMINATED) is True
    assert is_retryable_transport_error(CONNECTION_CLOSED) is True
    assert is_retryable_transport_error(
        McpError(ErrorData(code=32600, message="Session not found"))
    ) is True
    assert is_retryable_transport_error(INVALID_PARAMS) is False
    assert StreamableHttpClient._reconnect_decorator_applied is True
