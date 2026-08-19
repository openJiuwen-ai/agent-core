# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end chain tests: Tool → registry → Control → Instance."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.session.agent import Session
from openjiuwen.harness.subagent_runtime.models import SubagentStatusKind
from openjiuwen.harness.tools.subagent._control_registry import (
    get_subagent_control,
    release_subagent_control,
)
from openjiuwen.harness.tools.subagent.subagent_tools import build_subagent_tools
from tests.unit_tests.harness.subagent_runtime.test_control import (
    ControlParentAgent,
    _patch_create_session,
)
from tests.unit_tests.harness.subagent_runtime.test_instance import MockAgent


@pytest.mark.asyncio
async def test_spawn_wait_list_tool_chain_uses_real_control() -> None:
    """Tool.invoke → get_subagent_control → Control → Instance worker (no control mock)."""
    parent = ControlParentAgent(
        mock_agent=MockAgent(output="chain output", delay_s=0.05),
    )
    parent.card = SimpleNamespace(id="parent")
    session = Session(session_id="parent_chain_sess")
    session.write_stream = AsyncMock()
    spawn_tool, wait_tool, list_tool, *_rest = build_subagent_tools(
        parent,
        language="cn",
        available_agents="- explore: explorer",
    )

    with _patch_create_session(), patch(
        "openjiuwen.harness.subagent_runtime.control.WAIT_TIMEOUT_MS_MIN",
        100,
    ):
        try:
            spawn_result = await spawn_tool.invoke(
                {
                    "subagent_type": "explore",
                    "task_description": "run chain",
                    "display_name": "Explorer",
                    "role": "run integration chain",
                },
                session=session,
            )
            assert spawn_result.success is True
            subagent_id = spawn_result.data["subagent_id"]
            assert spawn_result.data["sub_session_id"] == subagent_id
            assert spawn_result.data["task_id"]
            assert spawn_result.data["status"] == SubagentStatusKind.PENDING_INIT.value
            assert "output" not in spawn_result.data

            control = get_subagent_control(parent, session)
            assert control is get_subagent_control(parent, session)

            wait_result = await wait_tool.invoke(
                {"subagent_ids": [subagent_id], "timeout_ms": 5_000},
                session=session,
            )
            assert wait_result.success is True
            assert wait_result.data["timed_out"] is False
            assert wait_result.data["statuses"][subagent_id] == SubagentStatusKind.COMPLETED.value
            assert wait_result.data["results"][subagent_id] == "chain output"

            list_result = await list_tool.invoke({}, session=session)
            assert list_result.success is True
            assert list_result.data["capacity"]["used"] == 1
            assert len(list_result.data["subagents"]) == 1
            row = list_result.data["subagents"][0]
            assert row["subagent_id"] == subagent_id
            assert row["status"] == "idle"
            assert row["turn_outcome"] == "completed"
            assert row["can_send_input"] is True
            assert "result" not in row

            instance = control._manager.get(subagent_id)
            assert instance._agent.stream_calls == 1

            status_payloads = [
                call.args[0].payload["subagent_updated"]
                for call in session.write_stream.await_args_list
                if "subagent_updated" in call.args[0].payload
            ]
            assert any(item["status"] == "running" for item in status_payloads)
            assert any(
                item["status"] == "idle" and item["subagent_id"] == subagent_id
                for item in status_payloads
            )
            revisions = [item["revision"] for item in status_payloads if item["subagent_id"] == subagent_id]
            assert revisions == sorted(revisions)
        finally:
            await release_subagent_control(parent, session.get_session_id(), reason="test")

    assert not getattr(parent, "_subagent_controls", {}).get("parent_chain_sess")


@pytest.mark.asyncio
async def test_six_tool_lifecycle_chain_uses_real_control() -> None:
    parent = ControlParentAgent(
        mock_agent=MockAgent(output="turn-1", delay_s=0.05),
    )
    parent.card = SimpleNamespace(id="parent")
    session = Session(session_id="parent_chain_lifecycle")
    session.write_stream = AsyncMock()
    tools = build_subagent_tools(parent, language="cn")
    spawn_tool, wait_tool, _list_tool, send_input_tool, close_tool, resume_tool = tools

    with _patch_create_session(), patch(
        "openjiuwen.harness.subagent_runtime.control.WAIT_TIMEOUT_MS_MIN",
        100,
    ), patch(
        "openjiuwen.harness.subagent_runtime.control.CheckpointerFactory.get_checkpointer",
    ) as get_checkpointer:
        checkpointer = AsyncMock()
        checkpointer.session_exists = AsyncMock(return_value=True)
        get_checkpointer.return_value = checkpointer

        try:
            spawn_result = await spawn_tool.invoke(
                {
                    "subagent_type": "explore",
                    "task_description": "first turn",
                    "display_name": "Explorer",
                    "role": "first lifecycle turn",
                },
                session=session,
            )
            subagent_id = spawn_result.data["subagent_id"]

            wait_result = await wait_tool.invoke(
                {"subagent_ids": [subagent_id], "timeout_ms": 5_000},
                session=session,
            )
            assert wait_result.data["results"][subagent_id] == "turn-1"

            parent.mock_agent.output = "turn-2"
            instance = get_subagent_control(parent, session)._manager.get(subagent_id)
            instance._agent.output = "turn-2"
            send_result = await send_input_tool.invoke(
                {"subagent_id": subagent_id, "query": "second turn"},
                session=session,
            )
            assert send_result.data["task_id"]
            await asyncio.sleep(0.1)

            wait_result = await wait_tool.invoke(
                {"subagent_ids": [subagent_id], "timeout_ms": 5_000},
                session=session,
            )
            assert wait_result.data["results"][subagent_id] == "turn-2"

            close_result = await close_tool.invoke(
                {"subagent_id": subagent_id},
                session=session,
            )
            assert close_result.data["previous_status"] == SubagentStatusKind.COMPLETED.value

            resume_result = await resume_tool.invoke(
                {"subagent_id": subagent_id},
                session=session,
            )
            assert resume_result.data["status"] == "running"
            assert resume_result.data["restored"] is True

            parent.mock_agent.output = "turn-3"
            instance = get_subagent_control(parent, session)._manager.get(subagent_id)
            instance._agent.output = "turn-3"
            await send_input_tool.invoke(
                {"subagent_id": subagent_id, "query": "third turn"},
                session=session,
            )
            await asyncio.sleep(0.1)
            wait_result = await wait_tool.invoke(
                {"subagent_ids": [subagent_id], "timeout_ms": 5_000},
                session=session,
            )
            assert wait_result.data["results"][subagent_id] == "turn-3"

            control = get_subagent_control(parent, session)
            assert control._manager.get(subagent_id)._agent.stream_calls == 1
        finally:
            await release_subagent_control(parent, session.get_session_id(), reason="test")
