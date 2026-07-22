#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for SseClient owner-task lifecycle model.

The sse_client / ClientSession async contexts may only be entered AND
exited by the same task (anyio cancel-scope invariant). SseClient now runs
all of connect/disconnect/reconnect on a *dedicated owner task* via a
command queue, so the caller's task is irrelevant. call_tool/list_tools
still run on the caller's task (memory-stream ops are cross-task safe).

These tests stub the actual connect/disconnect logic (_do_connect /
_do_disconnect) to record which task ran them, while exercising the real
command-queue plumbing (_submit / _owner_loop / connect / disconnect /
reconnect).
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient


def _make_client() -> SseClient:
    config = McpServerConfig(
        server_name="test-sse",
        server_path="http://test.local/sse",
    )
    return SseClient(config)


async def _stop(client: SseClient) -> None:
    """Stop the owner task cleanly (if alive) so no task leaks.

    Called at the end of each test. Cancels the owner task directly —
    simpler and more reliable than enqueuing a disconnect, since the
    test's _do_* mocks don't need to cooperate.
    """
    if client._owner_task is None or client._owner_task.done():
        return
    client._owner_task.cancel()
    try:
        await client._owner_task
    except (asyncio.CancelledError, Exception):
        pass


def _install_fake_lifecycle(client: SseClient) -> dict[str, Any]:
    """Replace _do_connect/_do_disconnect with recorders; return state."""
    state: dict[str, Any] = {
        "connect_tasks": [],
        "disconnect_tasks": [],
        "connect_ok": True,
        "disconnect_ok": True,
    }

    async def _fake_connect(*, timeout: float = -1) -> bool:
        state["connect_tasks"].append(asyncio.current_task())
        client._session = object()
        client._is_disconnected = False
        return state["connect_ok"]

    async def _fake_disconnect(*, timeout: float = -1) -> bool:
        state["disconnect_tasks"].append(asyncio.current_task())
        client._session = None
        return state["disconnect_ok"]

    client._do_connect = _fake_connect
    client._do_disconnect = _fake_disconnect
    return state


class TestSseOwnerTaskLifecycle:
    """connect/disconnect/reconnect always run on the owner task."""

    @pytest.mark.asyncio
    async def test_connect_runs_on_owner_task(self) -> None:
        client = _make_client()
        state = _install_fake_lifecycle(client)

        assert await client.connect() is True
        # connect ran on the owner task, not the test's task.
        assert len(state["connect_tasks"]) == 1
        assert state["connect_tasks"][0] is client._owner_task
        assert client._owner_task is not asyncio.current_task()
        await _stop(client)

    @pytest.mark.asyncio
    async def test_disconnect_runs_on_same_task_as_connect(self) -> None:
        """disconnect must run on the SAME task as connect — the owner
        task. This is the core invariant that kills the cross-task
        cancel-scope RuntimeErrors."""
        client = _make_client()
        state = _install_fake_lifecycle(client)
        await client.connect()
        await client.disconnect()

        assert len(state["disconnect_tasks"]) == 1
        # Same task as connect (both the owner task).
        assert state["disconnect_tasks"][0] is state["connect_tasks"][0]

    @pytest.mark.asyncio
    async def test_disconnect_from_other_task_still_uses_owner_task(self) -> None:
        """Calling disconnect() from a different task than the one that
        called connect() must STILL run the teardown on the owner task —
        not the caller's task."""
        client = _make_client()
        state = _install_fake_lifecycle(client)
        await client.connect()
        caller_task = asyncio.current_task()
        assert caller_task is not client._owner_task

        # disconnect from a *different* task.
        async def _from_other() -> bool:
            return await client.disconnect()

        await asyncio.gather(_from_other())

        assert len(state["disconnect_tasks"]) == 1
        # Teardown ran on the owner task, not the other task.
        assert state["disconnect_tasks"][0] is client._owner_task or \
               state["disconnect_tasks"][0] is state["connect_tasks"][0]

    @pytest.mark.asyncio
    async def test_reconnect_runs_disconnect_then_connect_on_owner_task(self) -> None:
        client = _make_client()
        state = _install_fake_lifecycle(client)
        await client.connect()
        await client.reconnect(timeout=-1)

        # reconnect = 1 disconnect + 1 connect, both on owner task.
        assert len(state["disconnect_tasks"]) == 1
        assert len(state["connect_tasks"]) == 2  # initial + reconnect
        owner = state["connect_tasks"][0]
        assert all(t is owner for t in state["connect_tasks"])
        assert state["disconnect_tasks"][0] is owner

    @pytest.mark.asyncio
    async def test_owner_task_exits_after_final_disconnect(self) -> None:
        """A public disconnect() is a final teardown: the owner task must
        exit (not leak) after it completes."""
        client = _make_client()
        _install_fake_lifecycle(client)
        await client.connect()
        owner = client._owner_task
        assert owner is not None

        await client.disconnect()
        # Give the loop a tick to observe _stopping and return.
        await asyncio.sleep(0)
        assert owner.done()

    @pytest.mark.asyncio
    async def test_reconnect_does_not_stop_owner_task(self) -> None:
        """An internal reconnect must NOT stop the owner task — it stays
        alive to serve subsequent reconnects."""
        client = _make_client()
        _install_fake_lifecycle(client)
        await client.connect()
        owner = client._owner_task
        await client.reconnect(timeout=-1)
        await asyncio.sleep(0)
        assert not owner.done()
        await _stop(client)

    @pytest.mark.asyncio
    async def test_reconnect_after_final_disconnect_restarts_owner(self) -> None:
        """After a final disconnect (owner stopped), a new connect restarts
        a fresh owner task — _stopping must be cleared."""
        client = _make_client()
        _install_fake_lifecycle(client)
        await client.connect()
        first_owner = client._owner_task
        await client.disconnect()
        await asyncio.sleep(0)
        assert first_owner.done()

        # New connect after teardown: new owner task, _stopping cleared.
        assert await client.connect() is True
        assert client._owner_task is not first_owner
        assert client._owner_task is not None
        assert client._stopping is False

    @pytest.mark.asyncio
    async def test_connect_failure_propagates_as_false(self) -> None:
        client = _make_client()
        state = _install_fake_lifecycle(client)
        state["connect_ok"] = False
        # connect returns False (not raise); disconnect still runs inside.
        assert await client.connect() is False
        await _stop(client)

    @pytest.mark.asyncio
    async def test_connect_exception_propagates_to_caller(self) -> None:
        client = _make_client()

        async def _boom(*, timeout: float = -1) -> bool:
            raise RuntimeError("connect blew up")

        client._do_connect = _boom
        # _do_connect raises inside owner task → must surface to caller.
        with pytest.raises(RuntimeError, match="connect blew up"):
            await client.connect()
        await _stop(client)

    @pytest.mark.asyncio
    async def test_disconnect_exception_propagates_to_caller(self) -> None:
        client = _make_client()

        async def _boom(*, timeout: float = -1) -> bool:
            raise RuntimeError("disconnect blew up")

        client._do_disconnect = _boom
        with pytest.raises(RuntimeError, match="disconnect blew up"):
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_concurrent_connect_disconnect_serialize_on_owner(self) -> None:
        """The command queue serializes lifecycle ops — two concurrent
        connects run one after another on the owner task, not interleaved."""
        client = _make_client()
        state = _install_fake_lifecycle(client)
        # Slow connect so concurrency is observable.
        runs: list[int] = []

        async def _slow_connect(*, timeout: float = -1) -> bool:
            runs.append(1)
            state["connect_tasks"].append(asyncio.current_task())
            client._is_disconnected = False
            await asyncio.sleep(0.05)
            return True

        client._do_connect = _slow_connect
        results = await asyncio.gather(client.connect(), client.connect())
        assert results == [True, True]
        assert len(state["connect_tasks"]) == 2
        # Both ran on the (same) owner task.
        assert state["connect_tasks"][0] is state["connect_tasks"][1]
        await _stop(client)

    @pytest.mark.asyncio
    async def test_concurrent_reconnects_serialize_and_share_result(self) -> None:
        """Concurrent reconnect() calls must be serialized by the lock so
        only one actual disconnect+connect runs; the others wait for the
        event and reuse the fresh session."""
        client = _make_client()
        state = _install_fake_lifecycle(client)
        await client.connect()

        # Slow down reconnect so concurrency is observable.
        async def _slow_reconnect(*, timeout: float = -1) -> bool:
            state["connect_tasks"].append(asyncio.current_task())
            state["disconnect_tasks"].append(asyncio.current_task())
            client._session = object()
            client._is_disconnected = False
            await asyncio.sleep(0.05)
            return True

        client._do_reconnect = _slow_reconnect

        results = await asyncio.gather(
            client.reconnect(timeout=-1),
            client.reconnect(timeout=-1),
            client.reconnect(timeout=-1),
        )
        assert all(r is True for r in results)
        # Exactly one disconnect + two connects (initial connect + one reconnect).
        assert len(state["disconnect_tasks"]) == 1
        assert len(state["connect_tasks"]) == 2
        await _stop(client)
