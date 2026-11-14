# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PermissionInterruptRail surfaces permission warnings in tool output.

Destructive-command detection lives in agent_shield's
``DestructiveWarningComponent``; the shell tools keep no separate rule table.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from agent_shield.tool_permission import get_permission_manager

from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.tools.base_tool import ToolOutput

_KEY = "test-rail-warnings"
_CONFIG = {
    "enabled": True,
    "permission_mode": "normal",
    "tools": {"bash": "allow"},
    "rules": [],
    "approval_overrides": [],
}


@pytest.fixture(autouse=True)
def _clean_key():
    yield
    manager = get_permission_manager()
    if manager.has_config(_KEY):
        manager.remove_config(_KEY)


def _call(command: str) -> ToolCall:
    return ToolCall(
        id="c-warn",
        type="function",
        name="bash",
        arguments=json.dumps({"command": command}),
    )


@pytest.mark.asyncio
async def test_rail_prepends_component_warning_to_tool_output() -> None:
    rail = PermissionInterruptRail(config=_CONFIG, permission_key=_KEY)
    call = _call("git commit --amend")
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        inputs=ToolCallInputs(
            tool_call=call,
            tool_name="bash",
            tool_args={"command": "git commit --amend"},
        ),
        extra={},
    )

    decision = await rail.resolve_interrupt(ctx, call, None, {})
    assert isinstance(decision, ApproveResult)

    ctx.inputs.tool_msg = ToolMessage(content="done", tool_call_id=call.id)
    ctx.inputs.tool_result = ToolOutput(success=True, data={"content": "done"})
    await rail.after_tool_call(ctx)

    assert "rewrite" in ctx.inputs.tool_msg.content.lower()
    assert ctx.inputs.tool_result.data["content"].startswith("May rewrite the last commit")
