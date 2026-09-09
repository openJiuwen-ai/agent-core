# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shell auto-confirm keys must be command-based for all injected shell tools."""

from __future__ import annotations

import json

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail


def _call(name: str, command: str) -> ToolCall:
    return ToolCall(
        id="t1",
        type="function",
        name=name,
        arguments=json.dumps({"command": command}),
    )


def test_powershell_auto_confirm_key_is_command_based_like_bash() -> None:
    rail = PermissionInterruptRail(
        config={
            "enabled": True,
            "tools": {"bash": "ask", "powershell": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
        }
    )
    bash_key = rail._get_auto_confirm_key(_call("bash", "Get-ChildItem"))
    ps_key = rail._get_auto_confirm_key(_call("powershell", "Get-ChildItem"))
    assert bash_key == "bash:Get-ChildItem"
    assert ps_key == "powershell:Get-ChildItem"
    assert ps_key != "powershell"


def test_injected_shell_tool_auto_confirm_key_is_command_based() -> None:
    rail = PermissionInterruptRail(
        config={
            "enabled": True,
            "categories": {"shell": ["run_cmd"]},
            "tools": {"run_cmd": "ask"},
            "defaults": {"*": "allow"},
            "rules": [],
        }
    )
    key = rail._get_auto_confirm_key(_call("run_cmd", "ls"))
    assert key == "run_cmd:ls"
