# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end chain tests: Tool → registry → Control → Instance."""

from __future__ import annotations

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
    spawn_tool, wait_tool, list_tool = build_subagent_tools(
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
                {"subagent_type": "explore", "task_description": "run chain"},
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
            assert row["status"] == "closed"
            assert row["closed_reason"] == "completed"
            assert "result" not in row

            instance = control._manager.get(subagent_id)
            assert instance._agent.stream_calls == 1

            status_payloads = [
                call.args[0].payload["subagent_updated"]
                for call in session.write_stream.await_args_list
            ]
            assert any(item["status"] == "running" for item in status_payloads)
            assert any(
                item["status"] == "closed" and item["subagent_id"] == subagent_id
                for item in status_payloads
            )
            revisions = [item["revision"] for item in status_payloads if item["subagent_id"] == subagent_id]
            assert revisions == sorted(revisions)
        finally:
            await release_subagent_control(parent, session.get_session_id(), reason="test")

    assert not getattr(parent, "_subagent_controls", {}).get("parent_chain_sess")
