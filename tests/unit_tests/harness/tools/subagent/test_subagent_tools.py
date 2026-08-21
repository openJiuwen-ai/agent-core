# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for runtime subagent tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.harness.subagent_runtime.config import WAIT_TIMEOUT_MS_DEFAULT
from openjiuwen.harness.subagent_runtime.models import (
    ResumeResult,
    SpawnResult,
    SubagentStatus,
    SubagentStatusKind,
    WaitResult,
)
from openjiuwen.harness.tools.subagent.subagent_tools import (
    SubagentCloseTool,
    SubagentListTool,
    SubagentResumeTool,
    SubagentSendInputTool,
    SubagentSpawnTool,
    SubagentWaitTool,
    build_subagent_tools,
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


def test_build_subagent_tools_wait_declares_call_timeout() -> None:
    tools = build_subagent_tools(_parent(), language="cn")
    wait_tool = next(tool for tool in tools if tool.card.name == "subagent_wait")
    expected_timeout_s = WAIT_TIMEOUT_MS_DEFAULT / 1000.0
    assert wait_tool.card.properties["resilience"]["timeout_s"] == expected_timeout_s
    assert AbilityManager._resolve_call_timeout(wait_tool.card) == expected_timeout_s


def _control_mock(**kwargs) -> SimpleNamespace:
    defaults = {
        "spawn": AsyncMock(),
        "wait": AsyncMock(),
        "emit_status_update": AsyncMock(),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_subagent_spawn_delegates_to_control() -> None:
    parent = _parent()
    tool = _spawn_tool(parent)
    control = _control_mock(
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
            {
                "subagent_type": "explore_agent",
                "task_description": "hello",
                "display_name": "Explorer",
                "role": "researcher",
            },
            session=session,
        )

    control.spawn.assert_awaited_once_with(
        "explore_agent",
        "hello",
        display_name="Explorer",
        role="researcher",
        browser_capabilities=None,
    )
    control.emit_status_update.assert_awaited_once_with(
        "parent_sub_explore",
        session=session,
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
            await tool.invoke(
                {
                    "task_description": "hello",
                    "display_name": "Explorer",
                    "role": "researcher",
                },
                session=session,
            )
        with pytest.raises(Exception, match="task_description"):
            await tool.invoke(
                {
                    "subagent_type": "explore_agent",
                    "display_name": "Explorer",
                    "role": "researcher",
                },
                session=session,
            )
        with pytest.raises(Exception, match="display_name"):
            await tool.invoke(
                {
                    "subagent_type": "explore_agent",
                    "task_description": "hello",
                    "role": "researcher",
                },
                session=session,
            )
        with pytest.raises(Exception, match="role"):
            await tool.invoke(
                {
                    "subagent_type": "explore_agent",
                    "task_description": "hello",
                    "display_name": "Explorer",
                },
                session=session,
            )

    control.spawn.assert_not_called()


@pytest.mark.asyncio
async def test_subagent_spawn_browser_capabilities_validation() -> None:
    tool = _spawn_tool()
    session = Session(session_id="parent_sess")
    control = _control_mock(
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
                "display_name": "Explorer",
                "role": "researcher",
                "browser_capabilities": ["navigate"],
            },
            session=session,
        )
        control.spawn.assert_awaited_once_with(
            "explore_agent",
            "hello",
            display_name="Explorer",
            role="researcher",
            browser_capabilities=None,
        )

        control.spawn.reset_mock()
        await tool.invoke(
            {
                "subagent_type": "browser_agent",
                "task_description": "browse",
                "display_name": "Browser",
                "role": "web research",
                "browser_capabilities": ["navigate"],
            },
            session=session,
        )
        control.spawn.assert_awaited_once_with(
            "browser_agent",
            "browse",
            display_name="Browser",
            role="web research",
            browser_capabilities=["navigate"],
        )

        with pytest.raises(Exception, match="browser_capabilities"):
            await tool.invoke(
                {
                    "subagent_type": "browser_agent",
                    "task_description": "browse",
                    "display_name": "Browser",
                    "role": "web research",
                    "browser_capabilities": [1, 2],
                },
                session=session,
            )


@pytest.mark.asyncio
async def test_subagent_spawn_requires_session_kwarg() -> None:
    tool = _spawn_tool()
    with pytest.raises(Exception, match="valid session"):
        await tool.invoke(
            {
                "subagent_type": "explore_agent",
                "task_description": "hello",
                "display_name": "Explorer",
                "role": "researcher",
            },
        )


@pytest.mark.asyncio
async def test_subagent_wait_returns_statuses_and_results() -> None:
    parent = _parent()
    tool = _wait_tool(parent)
    control = _control_mock(
        wait=AsyncMock(
            return_value=WaitResult(
                statuses={"sub1": SubagentStatus.completed("answer")},
                results={"sub1": "answer"},
                output_files={"sub1": "/tmp/sub1/output.md"},
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
    assert result.data["output_files"] == {"sub1": "/tmp/sub1/output.md"}
    assert result.data["statuses"] == {"sub1": SubagentStatusKind.COMPLETED.value}
    assert result.data["timed_out"] is False
    control.emit_status_update.assert_awaited_once_with("sub1", session=session)


@pytest.mark.asyncio
async def test_subagent_wait_accepts_subagent_id_alias() -> None:
    tool = _wait_tool()
    session = Session(session_id="parent_sess")
    control = _control_mock(
        wait=AsyncMock(
            return_value=WaitResult(
                statuses={"sub1": SubagentStatus.completed("answer")},
                results={"sub1": "answer"},
                output_files={},
                timed_out=False,
            ),
        ),
    )

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        result = await tool.invoke({"subagent_id": "sub1"}, session=session)

    control.wait.assert_awaited_once_with(["sub1"], timeout_ms=WAIT_TIMEOUT_MS_DEFAULT)
    assert result.success is True


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
        describe_live=lambda: [{"subagent_id": "sub1", "status": "closed"}],
    )
    session = Session(session_id="parent_sess")

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        result = await tool.invoke({}, session=session)

    assert result.data["capacity"] == {"used": 1, "max": 10}
    assert result.data["subagents"][0]["subagent_id"] == "sub1"


@pytest.mark.asyncio
async def test_subagent_send_input_delegates_to_control() -> None:
    parent = _parent()
    tool = SubagentSendInputTool(
        ToolCard(id="subagent_send_input", name="subagent_send_input", description="send"),
        parent,
    )
    control = _control_mock(
        send_input=AsyncMock(return_value="task-2"),
        get_status=lambda _sid: SubagentStatus.running(),
    )
    session = Session(session_id="parent_sess")

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        result = await tool.invoke(
            {"subagent_id": "sub1", "query": "continue", "interrupt": True},
            session=session,
        )

    control.send_input.assert_awaited_once_with("sub1", "continue", interrupt=True)
    control.emit_status_update.assert_awaited_once_with("sub1", session=session)
    assert result.data == {
        "subagent_id": "sub1",
        "task_id": "task-2",
        "status": SubagentStatusKind.RUNNING.value,
    }


@pytest.mark.asyncio
async def test_subagent_send_input_validates_inputs() -> None:
    tool = SubagentSendInputTool(
        ToolCard(id="subagent_send_input", name="subagent_send_input", description="send"),
        _parent(),
    )
    session = Session(session_id="parent_sess")
    control = _control_mock(send_input=AsyncMock())

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        with pytest.raises(Exception, match="subagent_id"):
            await tool.invoke({"query": "hello"}, session=session)
        with pytest.raises(Exception, match="query"):
            await tool.invoke({"subagent_id": "sub1"}, session=session)
        with pytest.raises(Exception, match="interrupt"):
            await tool.invoke(
                {"subagent_id": "sub1", "query": "hello", "interrupt": "yes"},
                session=session,
            )

    control.send_input.assert_not_called()


@pytest.mark.asyncio
async def test_subagent_close_delegates_to_control() -> None:
    parent = _parent()
    tool = SubagentCloseTool(
        ToolCard(id="subagent_close", name="subagent_close", description="close"),
        parent,
    )
    control = _control_mock(
        close=AsyncMock(return_value=SubagentStatus.completed("done")),
    )
    session = Session(session_id="parent_sess")

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        result = await tool.invoke({"subagent_id": "sub1"}, session=session)

    control.close.assert_awaited_once_with("sub1", reason="manual")
    control.emit_status_update.assert_awaited_once_with("sub1", session=session)
    assert result.data["previous_status"] == SubagentStatusKind.COMPLETED.value


@pytest.mark.asyncio
async def test_subagent_resume_delegates_to_control() -> None:
    parent = _parent()
    tool = SubagentResumeTool(
        ToolCard(id="subagent_resume", name="subagent_resume", description="resume"),
        parent,
    )
    control = _control_mock(
        resume=AsyncMock(
            return_value=ResumeResult(
                status=SubagentStatus.pending_init(),
                restored=True,
            ),
        ),
    )
    session = Session(session_id="parent_sess")

    with patch(
        "openjiuwen.harness.tools.subagent.subagent_tools.get_subagent_control",
        return_value=control,
    ):
        result = await tool.invoke({"subagent_id": "sub1"}, session=session)

    control.resume.assert_awaited_once_with("sub1")
    control.emit_status_update.assert_awaited_once_with("sub1", session=session)
    assert result.data["status"] == "running"
    assert result.data["restored"] is True
