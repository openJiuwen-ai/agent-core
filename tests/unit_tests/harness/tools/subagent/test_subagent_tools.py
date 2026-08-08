# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for runtime subagent tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.subagent_runtime.models import (
    SpawnResult,
    SubagentStatus,
    SubagentStatusKind,
    WaitResult,
)
from openjiuwen.harness.tools.subagent.subagent_tools import (
    SubagentListTool,
    SubagentSpawnTool,
    SubagentWaitTool,
)


def _parent() -> SimpleNamespace:
    return SimpleNamespace(card=SimpleNamespace(id="parent"))


def _spawn_tool(parent: SimpleNamespace | None = None) -> SubagentSpawnTool:
    return SubagentSpawnTool(
        ToolCard(id="subagent_spawn", name="subagent_spawn", description="spawn"),
        parent or _parent(),
    )


def _wait_tool(parent: SimpleNamespace | None = None) -> SubagentWaitTool:
    return SubagentWaitTool(
        ToolCard(id="subagent_wait", name="subagent_wait", description="wait"),
        parent or _parent(),
    )


@pytest.mark.asyncio
async def test_subagent_spawn_delegates_to_control() -> None:
    parent = _parent()
    tool = _spawn_tool(parent)
    control = SimpleNamespace(
        spawn=AsyncMock(
            return_value=SpawnResult(
                subagent_id="parent_sub_explore",
                task_id="task1",
                status=SubagentStatus.pending_init(),
            ),
        ),
    )
    session = Session(session_id="parent_sess")

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        result = await tool.invoke(
            {"subagent_type": "explore_agent", "task_description": "hello"},
            session=session,
        )

    control.spawn.assert_awaited_once_with(
        "explore_agent",
        "hello",
        browser_capabilities=None,
    )
    assert result.success is True
    assert result.data["subagent_id"] == "parent_sub_explore"
    assert result.data["sub_session_id"] == "parent_sub_explore"
    assert result.data["status"] == SubagentStatusKind.PENDING_INIT.value
    assert "output" not in result.data


@pytest.mark.asyncio
async def test_subagent_spawn_requires_subagent_type_and_task_description() -> None:
    tool = _spawn_tool()
    session = Session(session_id="parent_sess")
    control = SimpleNamespace(spawn=AsyncMock())

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        with pytest.raises(Exception, match="subagent_type"):
            await tool.invoke({"task_description": "hello"}, session=session)
        with pytest.raises(Exception, match="task_description"):
            await tool.invoke({"subagent_type": "explore_agent"}, session=session)

    control.spawn.assert_not_called()


@pytest.mark.asyncio
async def test_subagent_spawn_browser_capabilities_validation() -> None:
    tool = _spawn_tool()
    session = Session(session_id="parent_sess")
    control = SimpleNamespace(
        spawn=AsyncMock(
            return_value=SpawnResult(
                subagent_id="parent_sub_explore",
                task_id="task1",
                status=SubagentStatus.pending_init(),
            ),
        ),
    )

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        await tool.invoke(
            {
                "subagent_type": "explore_agent",
                "task_description": "hello",
                "browser_capabilities": ["navigate"],
            },
            session=session,
        )
        control.spawn.assert_awaited_once_with(
            "explore_agent",
            "hello",
            browser_capabilities=None,
        )

        control.spawn.reset_mock()
        await tool.invoke(
            {
                "subagent_type": "browser_agent",
                "task_description": "browse",
                "browser_capabilities": ["navigate"],
            },
            session=session,
        )
        control.spawn.assert_awaited_once_with(
            "browser_agent",
            "browse",
            browser_capabilities=["navigate"],
        )

        with pytest.raises(Exception, match="browser_capabilities"):
            await tool.invoke(
                {
                    "subagent_type": "browser_agent",
                    "task_description": "browse",
                    "browser_capabilities": [1, 2],
                },
                session=session,
            )


@pytest.mark.asyncio
async def test_subagent_spawn_requires_session_kwarg() -> None:
    tool = _spawn_tool()
    with pytest.raises(Exception, match="valid session"):
        await tool.invoke(
            {"subagent_type": "explore_agent", "task_description": "hello"},
        )


@pytest.mark.asyncio
async def test_subagent_wait_returns_statuses_and_results() -> None:
    parent = _parent()
    tool = _wait_tool(parent)
    control = SimpleNamespace(
        wait=AsyncMock(
            return_value=WaitResult(
                statuses={"sub1": SubagentStatus.completed("answer")},
                results={"sub1": "answer"},
                timed_out=False,
            ),
        ),
    )
    session = Session(session_id="parent_sess")

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        result = await tool.invoke(
            {"subagent_ids": ["sub1"], "timeout_ms": 30_000},
            session=session,
        )

    assert result.data["results"] == {"sub1": "answer"}
    assert result.data["statuses"] == {"sub1": SubagentStatusKind.COMPLETED.value}
    assert result.data["timed_out"] is False


@pytest.mark.asyncio
async def test_subagent_wait_rejects_empty_subagent_ids() -> None:
    tool = _wait_tool()
    session = Session(session_id="parent_sess")
    control = SimpleNamespace(wait=AsyncMock())

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        with pytest.raises(Exception, match="non-empty list"):
            await tool.invoke({"subagent_ids": []}, session=session)

    control.wait.assert_not_called()


@pytest.mark.asyncio
async def test_subagent_list_returns_capacity_and_rows() -> None:
    parent = _parent()
    tool = SubagentListTool(
        ToolCard(id="subagent_list", name="subagent_list", description="list"),
        parent,
    )
    control = SimpleNamespace(
        capacity=lambda: {"used": 1, "max": 10},
        describe_live=lambda: [{"subagent_id": "sub1", "status": "completed"}],
    )
    session = Session(session_id="parent_sess")

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        result = await tool.invoke({}, session=session)

    assert result.data["capacity"] == {"used": 1, "max": 10}
    assert result.data["subagents"][0]["subagent_id"] == "sub1"
