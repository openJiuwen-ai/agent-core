# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Date format arguments must match without bypassing command approvals."""

import pytest

from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.patterns import match_wildcard
from openjiuwen.harness.security.skill_authorization import compose_skill_permissions

_DATE_COMMAND = 'date "+%Y-%m-%d %H:%M:%S"'


def _skill_config() -> dict:
    return compose_skill_permissions(
        {"enabled": True, "tools": {"bash": "ask"}, "file_guard": {"enabled": False}},
        {
            "rules": [
                {"id": "allow_date", "pattern": "date *", "action": "allow"},
                {"id": "deny_rm", "pattern": "rm *", "action": "deny"},
            ]
        },
    )


@pytest.mark.parametrize("command", ["date +X", "date %Y", "date +%s", _DATE_COMMAND])
def test_wildcard_matches_formatted_date(command: str) -> None:
    assert match_wildcard(command, "date *")


def test_question_mark_matches_date_format_character() -> None:
    assert match_wildcard("date +%", "date +?")
    assert not match_wildcard("date +%Y", "date +?")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["bash", "mcp_exec_command", "create_terminal"])
async def test_skill_date_rule_overrides_tool_approval(tool: str) -> None:
    config = _skill_config()
    config["tools"][tool] = "ask"
    result = await PermissionEngine(config).check_permission(tool, {"command": _DATE_COMMAND})
    assert result.permission == PermissionLevel.ALLOW
    assert "rules[allow_date]" in result.matched_rule


@pytest.mark.parametrize(
    "suffix",
    ["; id", " && id", " | id", " `id`", " $(id)", " > /tmp/date.txt", " < /tmp/date.txt", "\nid"],
)
@pytest.mark.asyncio
async def test_date_rule_rejects_shell_syntax(suffix: str) -> None:
    command = _DATE_COMMAND + suffix
    assert not match_wildcard(command, "date *")
    result = await PermissionEngine(_skill_config()).check_permission("bash", {"command": command})
    assert result.permission != PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_skill_rm_deny_matches_format_characters_in_path() -> None:
    result = await PermissionEngine(_skill_config()).check_permission("bash", {"command": "rm -f /tmp/date+%Y.txt"})
    assert result.permission == PermissionLevel.DENY
    assert "rules[deny_rm]" in result.matched_rule
