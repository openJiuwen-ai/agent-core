# -*- coding: UTF-8 -*-
"""Tests for ToolMgr.add_tool_server idempotency under shared server_id.

When multiple agents share the same DeepAgentSpec instance (e.g. team
members spawning concurrently from a single ``agents['teammate']``
template), they end up calling ``add_tool_server`` with the same
``McpServerConfig`` server_id. Without dedup the second caller crashes
with "already exist tool" because the tool ids collide. These tests
pin down both the duplicate-call path and the concurrent-call path.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.tool import McpServerConfig, McpToolCard
from openjiuwen.core.runner.resources_manager.tool_manager import ToolMgr


def _make_server_config(server_id: str = "srv-1", server_name: str = "demo") -> McpServerConfig:
    return McpServerConfig(
        server_id=server_id,
        server_name=server_name,
        server_path="",
        client_type="stdio",
        params={"command": "python", "args": ["-m", "demo"]},
    )


def _make_tool_card(name: str, server_id: str, server_name: str) -> McpToolCard:
    return McpToolCard(
        id=f"{server_id}.{server_name}.{name}",
        name=name,
        description=f"{name} tool",
        input_params={"type": "object", "properties": {}},
        server_name=server_name,
        server_id=server_id,
    )


@pytest.mark.asyncio
async def test_add_tool_server_returns_cached_cards_when_server_id_already_registered() -> None:
    """A second add for the same server_id must reuse the existing client and tools."""
    mgr = ToolMgr()
    cfg = _make_server_config()

    fake_client = MagicMock()
    fake_client.connect = AsyncMock(return_value=True)
    fake_client.list_tools = AsyncMock(
        return_value=[
            _make_tool_card("alpha", cfg.server_id, cfg.server_name),
            _make_tool_card("beta", cfg.server_id, cfg.server_name),
        ]
    )
    fake_client.disconnect = AsyncMock(return_value=True)

    with patch.object(ToolMgr, "_create_client", staticmethod(lambda c: fake_client)):
        first = await mgr.add_tool_server(cfg)
        second = await mgr.add_tool_server(cfg)

    assert {c.name for c in first} == {"alpha", "beta"}
    assert {c.name for c in second} == {"alpha", "beta"}
    # Client must be created and connected exactly once across both calls.
    assert fake_client.connect.await_count == 1
    assert fake_client.list_tools.await_count == 1


@pytest.mark.asyncio
async def test_add_tool_server_serializes_concurrent_calls_for_same_server_id() -> None:
    """Two concurrent ``add_tool_server`` calls must not both hit the registration path."""
    mgr = ToolMgr()
    cfg = _make_server_config(server_id="race-srv")

    connect_started = asyncio.Event()
    release_connect = asyncio.Event()

    async def slow_connect() -> bool:
        connect_started.set()
        await release_connect.wait()
        return True

    fake_client = MagicMock()
    fake_client.connect = AsyncMock(side_effect=slow_connect)
    fake_client.list_tools = AsyncMock(return_value=[_make_tool_card("gamma", cfg.server_id, cfg.server_name)])

    with patch.object(ToolMgr, "_create_client", staticmethod(lambda c: fake_client)):
        first_task = asyncio.create_task(mgr.add_tool_server(cfg))
        # Wait until the first call is inside connect; only then schedule the second.
        await connect_started.wait()
        second_task = asyncio.create_task(mgr.add_tool_server(cfg))
        # Give the second task a chance to reach the lock; it must be blocked.
        await asyncio.sleep(0)
        assert not second_task.done()
        release_connect.set()

        first_cards = await first_task
        second_cards = await second_task

    assert {c.name for c in first_cards} == {"gamma"}
    assert {c.name for c in second_cards} == {"gamma"}
    # The slow connect path must run only once even under contention.
    assert fake_client.connect.await_count == 1
    assert fake_client.list_tools.await_count == 1


@pytest.mark.asyncio
async def test_add_tool_server_registers_distinct_server_ids_independently() -> None:
    """Different server_ids must keep using independent locks and registrations."""
    mgr = ToolMgr()
    cfg_a = _make_server_config(server_id="srv-a", server_name="alpha-srv")
    cfg_b = _make_server_config(server_id="srv-b", server_name="beta-srv")

    fake_client_a = MagicMock()
    fake_client_a.connect = AsyncMock(return_value=True)
    fake_client_a.list_tools = AsyncMock(return_value=[_make_tool_card("x", cfg_a.server_id, cfg_a.server_name)])

    fake_client_b = MagicMock()
    fake_client_b.connect = AsyncMock(return_value=True)
    fake_client_b.list_tools = AsyncMock(return_value=[_make_tool_card("y", cfg_b.server_id, cfg_b.server_name)])

    def fake_create(config: McpServerConfig) -> MagicMock:
        return fake_client_a if config.server_id == "srv-a" else fake_client_b

    with patch.object(ToolMgr, "_create_client", staticmethod(fake_create)):
        cards_a = await mgr.add_tool_server(cfg_a)
        cards_b = await mgr.add_tool_server(cfg_b)

    assert [c.name for c in cards_a] == ["x"]
    assert [c.name for c in cards_b] == ["y"]
    assert fake_client_a.connect.await_count == 1
    assert fake_client_b.connect.await_count == 1
