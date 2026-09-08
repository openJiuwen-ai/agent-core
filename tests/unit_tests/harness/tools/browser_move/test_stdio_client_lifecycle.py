#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lifecycle tests for the browser-specific MCP stdio client."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Any

import mcp
import mcp.client.stdio as mcp_stdio
import pytest

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.clients import stdio_client as stdio_client_module
from openjiuwen.harness.tools.browser_move.clients.stdio_client import BrowserMoveStdioClient


def _make_client(*, timeout_s: float | None = None) -> BrowserMoveStdioClient:
    params: dict[str, Any] = {"command": "unused-test-command", "args": []}
    if timeout_s is not None:
        params["timeout_s"] = timeout_s
    return BrowserMoveStdioClient(
        McpServerConfig(
            server_name="test-browser-stdio",
            server_path="stdio://test-browser-stdio",
            client_type="stdio",
            params=params,
        )
    )


class _HangingTransport(AbstractAsyncContextManager):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def __aenter__(self) -> Any:
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


def _install_hanging_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> _HangingTransport:
    transport = _HangingTransport()
    monkeypatch.setattr(mcp_stdio, "stdio_client", lambda _params: transport)
    return transport


async def _cancel_owner(client: BrowserMoveStdioClient) -> None:
    """Best-effort test cleanup for behavior that fails before the fix."""
    owner_task = client._owner_task or client._leaked_owner_task
    if owner_task is None or owner_task.done():
        return
    owner_task.cancel()
    try:
        await owner_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_configured_timeout_bounds_context_enter_and_cleans_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client(timeout_s=0.01)
    transport = _install_hanging_transport(monkeypatch)

    try:
        result = await asyncio.wait_for(client.connect(), timeout=0.25)

        assert result is False
        assert transport.cancelled.is_set()
        assert client._owner_task is None
        assert client._session is None
    finally:
        await _cancel_owner(client)


@pytest.mark.asyncio
async def test_explicit_timeout_remains_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client(timeout_s=60.0)
    owner_started = asyncio.Event()

    async def _wait_for_close() -> None:
        try:
            owner_started.set()
            await client._owner_close.wait()
        finally:
            client._owner_task = None

    monkeypatch.setattr(client, "_run_owner", _wait_for_close)

    result = await asyncio.wait_for(client.connect(timeout=0.01), timeout=0.25)

    assert owner_started.is_set()
    assert result is False
    assert client._owner_task is None


@pytest.mark.asyncio
async def test_no_timeout_without_config_stays_pending_until_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
    transport = _install_hanging_transport(monkeypatch)
    connect_task = asyncio.create_task(client.connect())

    try:
        await asyncio.wait_for(transport.entered.wait(), timeout=0.25)
        await asyncio.sleep(0.02)
        assert not connect_task.done()

        connect_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await connect_task

        assert transport.cancelled.is_set()
        assert client._owner_task is None
    finally:
        if not connect_task.done():
            connect_task.cancel()
        await _cancel_owner(client)


@pytest.mark.asyncio
async def test_caller_cancellation_waits_for_owner_cleanup_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client(timeout_s=60.0)
    transport = _install_hanging_transport(monkeypatch)
    connect_task = asyncio.create_task(client.connect())

    try:
        await asyncio.wait_for(transport.entered.wait(), timeout=0.25)
        connect_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await connect_task

        assert transport.cancelled.is_set()
        assert client._owner_task is None
        assert client._session is None
    finally:
        if not connect_task.done():
            connect_task.cancel()
        await _cancel_owner(client)


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_interrupt_owner_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client(timeout_s=60.0)
    owner_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def _pending_owner() -> None:
        owner_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            client._owner_task = None

    async def _blocked_force_close() -> None:
        cleanup_started.set()
        try:
            await release_cleanup.wait()
        except asyncio.CancelledError:
            cleanup_cancelled.set()
            raise

    monkeypatch.setattr(client, "_run_owner", _pending_owner)
    monkeypatch.setattr(client, "_force_close", _blocked_force_close)
    connect_task = asyncio.create_task(client.connect())

    try:
        await asyncio.wait_for(owner_started.wait(), timeout=0.25)
        connect_task.cancel()
        await asyncio.wait_for(cleanup_started.wait(), timeout=0.25)

        connect_task.cancel()
        await asyncio.sleep(0)
        connect_task.cancel()
        await asyncio.sleep(0)

        assert not connect_task.done()
        assert not cleanup_cancelled.is_set()

        release_cleanup.set()
        done, _ = await asyncio.wait({connect_task}, timeout=0.25)
        assert connect_task in done
        with pytest.raises(asyncio.CancelledError):
            connect_task.result()
    finally:
        release_cleanup.set()
        await _cancel_owner(client)


@pytest.mark.asyncio
async def test_force_close_keeps_owner_state_when_cancellation_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client(timeout_s=0.01)
    owner_started = asyncio.Event()
    release_owner = asyncio.Event()
    session = object()
    transport = object()
    read = object()
    write = object()
    exit_stack = client._exit_stack

    async def _stubborn_owner() -> None:
        owner_started.set()
        try:
            while not release_owner.is_set():
                try:
                    await release_owner.wait()
                except asyncio.CancelledError:
                    continue
        finally:
            client._owner_task = None
            client._session = None
            client._client = None
            client._read = None
            client._write = None

    client._session = session
    client._client = transport
    client._read = read
    client._write = write
    owner_task = asyncio.create_task(_stubborn_owner())
    client._owner_task = owner_task
    monkeypatch.setattr(
        stdio_client_module,
        "_OWNER_CANCEL_WAIT_S",
        0.01,
        raising=False,
    )

    force_close_task = asyncio.create_task(client._force_close())
    try:
        await asyncio.wait_for(owner_started.wait(), timeout=0.25)
        await asyncio.sleep(0.05)

        assert force_close_task.done()
        assert client._owner_task is owner_task
        assert client._leaked_owner_task is owner_task
        assert client._session is session
        assert client._client is transport
        assert client._read is read
        assert client._write is write
        assert client._exit_stack is exit_stack
        assert await client.connect() is False
        assert client._owner_task is owner_task
    finally:
        release_owner.set()
        if not force_close_task.done():
            try:
                await asyncio.wait_for(force_close_task, timeout=0.25)
            except (asyncio.CancelledError, Exception):
                pass
        await asyncio.wait_for(owner_task, timeout=0.25)


class _RecordingContext(AbstractAsyncContextManager):
    def __init__(self, value: Any, events: list[tuple[str, asyncio.Task | None]]) -> None:
        self._value = value
        self._events = events

    async def __aenter__(self) -> Any:
        self._events.append(("enter", asyncio.current_task()))
        return self._value

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        self._events.append(("exit", asyncio.current_task()))


class _Session:
    async def initialize(self) -> None:
        return None


@pytest.mark.asyncio
async def test_successful_context_lifecycle_stays_in_one_owner_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client(timeout_s=0.25)
    caller_task = asyncio.current_task()
    transport_events: list[tuple[str, asyncio.Task | None]] = []
    session_events: list[tuple[str, asyncio.Task | None]] = []
    session = _Session()

    monkeypatch.setattr(
        mcp_stdio,
        "stdio_client",
        lambda _params: _RecordingContext((object(), object()), transport_events),
    )
    monkeypatch.setattr(
        mcp,
        "ClientSession",
        lambda *_args, **_kwargs: _RecordingContext(session, session_events),
    )

    assert await client.connect() is True
    assert await client.disconnect() is True

    lifecycle_tasks = [task for _, task in transport_events + session_events]
    assert [event for event, _ in transport_events] == ["enter", "exit"]
    assert [event for event, _ in session_events] == ["enter", "exit"]
    assert len(set(lifecycle_tasks)) == 1
    assert lifecycle_tasks[0] is not caller_task
    assert client._owner_task is None


class _HangingSession:
    def __init__(self) -> None:
        self.initialize_cancelled = asyncio.Event()

    async def initialize(self) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.initialize_cancelled.set()
            raise


@pytest.mark.asyncio
async def test_initialize_timeout_returns_false_and_cleans_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client(timeout_s=0.01)
    session = _HangingSession()

    monkeypatch.setattr(
        mcp_stdio,
        "stdio_client",
        lambda _params: _RecordingContext((object(), object()), []),
    )
    monkeypatch.setattr(
        mcp,
        "ClientSession",
        lambda *_args, **_kwargs: _RecordingContext(session, []),
    )

    assert await asyncio.wait_for(client.connect(), timeout=0.25) is False
    assert session.initialize_cancelled.is_set()
    assert client._owner_task is None
    assert client._session is None
