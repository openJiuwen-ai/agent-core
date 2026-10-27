# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unicode command rules must preserve approval and path guard boundaries."""

from pathlib import Path

import pytest

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.patterns import match_wildcard

_CUSTOMER_COMMANDS = [
    'python3 scripts/build_modify_request.py --original-meet-title "测试会议"',
    ('python3 "/workspace/skills/会议预定/build_meeting_payload.py" --meet-title "AAA" --meet-host-name "张三"'),
]


@pytest.mark.parametrize(
    "command",
    _CUSTOMER_COMMANDS
    + [
        "python3 /workspace/test.py",
        "python3 /workspace/𠀀.py",
    ],
)
def test_python_rule_matches_unicode_paths_and_arguments(command: str) -> None:
    assert match_wildcard(command, "python3 *")


def test_question_mark_matches_one_chinese_character() -> None:
    assert match_wildcard("python3 /workspace/会.py", "python3 /workspace/?.py")
    assert not match_wildcard("python3 /workspace/会议.py", "python3 /workspace/?.py")


@pytest.mark.parametrize(
    "suffix",
    [
        "; id",
        " | id",
        " && id",
        " || id",
        " & id",
        " `id`",
        " $(id)",
        " > /tmp/result",
        " < /tmp/input",
        "\nid",
        "\r",
        "\t",
        "\x00",
        "\u202e",
    ],
)
def test_unicode_wildcard_still_rejects_shell_syntax_and_controls(suffix: str) -> None:
    assert not match_wildcard('python3 "/workspace/会议.py"' + suffix, "python3 *")


def _config() -> dict:
    return {
        "enabled": True,
        "tools": {"bash": "ask"},
        "rules": [
            {
                "id": "shell_allow_python3",
                "tools": ["bash"],
                "pattern": "python3 *",
                "action": "allow",
            }
        ],
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "allow"},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("command", _CUSTOMER_COMMANDS)
async def test_unicode_python_rule_overrides_bash_ask(command: str) -> None:
    engine = PermissionEngine(_config(), workspace_root=Path("/workspace"))
    result = await engine.check_permission(
        "bash",
        {"command": command, "workdir": "/workspace/skills/会议修改"},
    )
    assert result.permission == PermissionLevel.ALLOW
    assert "shell_allow_python3" in result.matched_rule


@pytest.mark.asyncio
async def test_unicode_python_rule_does_not_override_tool_deny() -> None:
    config = _config()
    config["tools"]["bash"] = "deny"
    engine = PermissionEngine(config, workspace_root=Path("/workspace"))
    result = await engine.check_permission("bash", {"command": _CUSTOMER_COMMANDS[1]})
    assert result.permission == PermissionLevel.DENY


@pytest.mark.asyncio
async def test_unicode_python_rule_does_not_override_external_path_ask() -> None:
    engine = PermissionEngine(_config(), workspace_root=Path("/workspace"))
    result = await engine.check_permission("bash", {"command": "python3 /outside/会议.py"})
    assert result.permission == PermissionLevel.ASK
    assert "shell_allow_python3" in result.matched_rule
    assert "file_guard" in result.matched_rule
