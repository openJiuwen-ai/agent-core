# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.harness.subagent_runtime.config import WAIT_TIMEOUT_MS_MAX
from openjiuwen.harness.subagent_runtime.models import SubagentStatus, WaitResult
from openjiuwen.harness.tools.subagent import subagent_tools as subagent_tools_mod
from openjiuwen.harness.tools.subagent.subagent_tools import build_subagent_tools


def _wait_tool():
    tools = build_subagent_tools(SimpleNamespace())
    return next(tool for tool in tools if tool.card.name == "subagent_wait")


def test_wait_card_exempts_resilience_timeout() -> None:
    wait_tool = _wait_tool()
    assert wait_tool.card.properties["resilience"]["timeout_s"] is None
    assert AbilityManager._resolve_call_timeout(wait_tool.card) is None


@pytest.mark.asyncio
async def test_wait_tool_returns_timed_out_above_turn_default() -> None:
    wait_tool = _wait_tool()

    class _Control:
        async def wait(self, ids, timeout_ms=0):
            assert timeout_ms == WAIT_TIMEOUT_MS_MAX
            _ = ids
            return WaitResult(
                statuses={"sa-1": SubagentStatus.running()},
                results={},
                output_files={},
                timed_out=True,
            )

        async def emit_status_update(self, sid, session=None):
            _ = sid, session

    with patch.object(subagent_tools_mod, "get_subagent_control", return_value=_Control()):
        output = await wait_tool.invoke(
            {"subagent_ids": ["sa-1"], "timeout_ms": WAIT_TIMEOUT_MS_MAX},
            session=object(),
        )
    assert output.success is True
    assert output.data["timed_out"] is True
    assert output.data["statuses"]["sa-1"] == "running"
