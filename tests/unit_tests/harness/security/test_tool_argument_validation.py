# coding: utf-8

from __future__ import annotations

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.interrupt.interrupt_base import (
    InterruptResult,
    RejectResult,
)
from openjiuwen.harness.rails.security.tool_security_rail import (
    PermissionInterruptRail,
)


def _rail() -> PermissionInterruptRail:
    return PermissionInterruptRail(
        config={
            "enabled": True,
            "defaults": {"*": "allow"},
            "tools": {"bash": "ask"},
        }
    )


def _tool_call(arguments: str, name: str = "bash") -> ToolCall:
    return ToolCall(
        id="call-bash-validation",
        type="function",
        name=name,
        arguments=arguments,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    ["", "   ", "{", "[]", '{"command": ""}', "{}"],
)
async def test_empty_or_invalid_bash_arguments_are_rejected(arguments: str) -> None:
    decision = await _rail().resolve_interrupt(
        ctx=None,
        tool_call=_tool_call(arguments),
        user_input=None,
    )

    assert isinstance(decision, RejectResult)
    assert "INVALID_TOOL_ARGUMENTS" in str(decision.tool_result)


@pytest.mark.asyncio
async def test_valid_bash_arguments_keep_permission_interrupt_behavior() -> None:
    decision = await _rail().resolve_interrupt(
        ctx=None,
        tool_call=_tool_call('{"command": "python test.py"}'),
        user_input=None,
    )

    assert isinstance(decision, InterruptResult)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["exec_command", "mcp_exec_command"])
async def test_command_tool_aliases_validate_arguments(tool_name: str) -> None:
    decision = await _rail().resolve_interrupt(
        ctx=None,
        tool_call=_tool_call("", name=tool_name),
        user_input=None,
    )

    assert isinstance(decision, RejectResult)
    assert "INVALID_TOOL_ARGUMENTS" in str(decision.tool_result)
