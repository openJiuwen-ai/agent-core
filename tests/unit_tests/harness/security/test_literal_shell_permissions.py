# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Literal arguments must not authorize extra commands or evade path restrictions."""

from copy import deepcopy
from pathlib import Path

import pytest

from openjiuwen.harness.security import literal_shell
from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.models import PermissionLevel
from openjiuwen.harness.security.skill_authorization import compose_skill_permissions


def config():
    return compose_skill_permissions(
        {"enabled": True, "tools": {"bash": "ask"}, "file_guard": {"enabled": False}},
        {"rules": [
            {"id": "python", "pattern": "python3 *", "action": "allow"},
            {"id": "cd", "pattern": "cd *", "action": "allow"},
        ]},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [
    '''python3 "skills/会议预定/probe.py" --json '{"headers":{"Content-Type":"application/json;charset=UTF-8"},"data":"<BFF_EVENT>ok</BFF_EVENT>"}' ''',
    'cd "./skills/会议预定" && python3 probe.py --mode=test',
    "python3 probe.py --stdin <<'EOF'\n{\"value\":\"a;b<c>\"}\nEOF",
    'python3 probe.py --stdin <<"PAYLOAD"\n{"value":"$(literal text)"}\nPAYLOAD',
])
async def test_verified_literal_skill_commands_are_allowed(command):
    result = await PermissionEngine(config()).check_permission("bash", {"command": command})
    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
@pytest.mark.parametrize("delimiter", ["'EOF'", '"EOF"'])
async def test_literal_heredoc_after_leading_cd_is_allowed(delimiter):
    command = (
        'cd "./skills/会议预定" && python3 payload_demo.py --mode modify --tool-response-stdin '
        f'<<{delimiter}\n{{"txBody":{{"descInfo":"交易成功"}}}}\nEOF'
    )
    result = await PermissionEngine(config()).check_permission("bash", {"command": command})
    assert result.is_allowed
    assert "rules[cd]" in result.matched_rule
    assert "rules[python]" in result.matched_rule


@pytest.mark.parametrize("command", [
    "cd ./ || python3 probe.py <<'EOF'\n{}\nEOF",
    "cd ./ && python3 - <<'EOF'\nprint(1)\nEOF",
    "cd ./ && python3 -c 'print(1)' <<'EOF'\n{}\nEOF",
    "cd ./ && python3 probe.py <<EOF\n$(id)\nEOF",
    "cd ./ && python3 probe.py <<'EOF'\n{}\nEOF\ntouch marker",
    "cd ./ && python3 probe.py > marker <<'EOF'\n{}\nEOF",
    "cd ./ && python3 probe.py <<'EOF'\n{}\nEOF\n| cat",
    "cd ./ && cd ../ && python3 probe.py <<'EOF'\n{}\nEOF",
    "echo prefix && python3 probe.py <<'EOF'\n{}\nEOF",
    'cd "${DEST}" && python3 probe.py <<\'EOF\'\n{}\nEOF',
])
def test_cd_heredoc_does_not_accept_other_shell_structures(command):
    assert literal_shell.parse_literal_shell(command) is None


@pytest.mark.asyncio
async def test_cd_heredoc_preserves_denies_and_requires_both_command_rules(tmp_path):
    command = "cd ./ && python3 ../outside.py <<'EOF'\n{}\nEOF"
    for denied in ("cd", "python"):
        policy = config()
        next(rule for rule in policy["rules"] if rule["id"] == denied)["action"] = "deny"
        assert (await PermissionEngine(policy).check_permission("bash", {"command": command})).is_denied
    for allowed in ("cd", "python"):
        policy = config()
        policy["rules"] = [rule for rule in policy["rules"] if rule["id"] == allowed]
        assert not (await PermissionEngine(policy).check_permission("bash", {"command": command})).is_allowed
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = config()
    policy["file_guard"] = {"enabled": True, "defaults": {"exec": "deny"}, "workspace": {"exec": "allow"}}
    assert (await PermissionEngine(policy, workspace_root=workspace).check_permission(
        "bash", {"command": command, "workdir": str(workspace)},
    )).is_denied


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "123", "-1", "202610190845", "202610190945", "20071276990"])
async def test_literal_numeric_arguments_are_allowed(value):
    result = await PermissionEngine(config()).check_permission("bash", {
        "command": f"python3 probe.py --value {value}",
    })
    assert result.is_allowed


@pytest.mark.asyncio
async def test_customer_meeting_command_with_unquoted_integers():
    command = (
        'cd "/home/app/.jiuwenswarm/workspace_d300780a449651504880c57ae13b0390/'
        'agent/jiuwenclaw_workspace/skills/permverify" && python3 payload_demo.py '
        '--meet-title "某艺勇预定的会议" --meet-bgn-time 202610190845 --meet-end-time 202610190945 '
        '--meet-host-name "某艺勇" --require-password true --teller-no 20071276990'
    )
    base = {
        "enabled": True, "tools": {"bash": "ask"}, "file_guard": {"enabled": False},
        "rules": [
            {"id": "rule_cd", "tools": ["bash"], "pattern": r"re:^cd\s+.*$", "action": "allow"},
            {"id": "rule_python3", "tools": ["bash"], "pattern": r"re:^python3\s+.*$", "action": "allow"},
        ],
    }
    overlay = {"rules": [
        {"id": "allow_cd", "pattern": "cd *", "action": "allow"},
        {"id": "allow_python3", "pattern": "python3 *", "action": "allow"},
    ]}
    result = await PermissionEngine(compose_skill_permissions(base, overlay)).check_permission(
        "bash", {"command": command},
    )
    assert result.is_allowed
    for rule_id in ("allow_cd", "rule_cd", "allow_python3", "rule_python3"):
        assert f"rules[{rule_id}]" in result.matched_rule


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [
    'python3 probe.py && touch marker',
    'python3 probe.py; touch marker',
    'python3 probe.py | cat',
    'python3 probe.py > marker',
    'python3 probe.py < data.json',
    'python3 probe.py &',
    'python3 probe.py;',
    'python3 probe.py $(id)',
    'python3 probe.py "${VALUE}"',
    'python3 probe.py [ab].txt',
    'python3 probe.py x{a,b}.txt',
    'python3 -c "print(1)"',
    "python3 - <<'EOF'\nprint(1)\nEOF",
    "python3 probe.py <<EOF\n$(id)\nEOF",
    "python3 probe.py <<'EOF'\n{}\nEOF\ntouch marker",
    "python3 probe.py <<'EOF' > marker\n{}\nEOF",
    "cd - && python3 probe.py",
    "cd -P && python3 probe.py",
    "cd ./ && cd ../ && python3 probe.py",
    "/bin/cd ./ && python3 probe.py",
    "cd directory_from_cdpath && python3 probe.py",
    "python3 probe.py --value 123 && touch marker",
    "python3 probe.py --value 123 > marker",
    "python3 probe.py --value 123 $(id)",
    "python3 probe.py --value 123; touch marker",
])
async def test_unsupported_syntax_does_not_gain_automatic_permission(command):
    result = await PermissionEngine(config()).check_permission("bash", {"command": command})
    assert result.permission != PermissionLevel.ALLOW


@pytest.mark.asyncio
async def test_tool_deny_and_parameter_deny_remain_effective():
    command = '''python3 probe.py --json '{"id":1}' '''
    policy = config()
    policy["tools"]["bash"] = "deny"
    assert (await PermissionEngine(policy).check_permission("bash", {"command": command})).is_denied
    policy["tools"]["bash"] = "allow"
    policy["rules"] = [{"id": "deny_python", "tools": ["bash"], "pattern": "python3 *", "action": "deny"}]
    assert (await PermissionEngine(policy).check_permission("bash", {"command": command})).is_denied


@pytest.mark.asyncio
async def test_baseline_allow_is_not_evidence_for_a_chain():
    policy = config()
    policy["tools"]["bash"] = "allow"
    policy["rules"] = []
    assert (await PermissionEngine(policy).check_permission("bash", {
        "command": "cd ./ && python3 probe.py",
    })).needs_approval


@pytest.mark.asyncio
async def test_unanchored_regex_cannot_authorize_a_chain_by_argument_text():
    policy = config()
    policy["rules"] = [{"id": "argument_hit", "tools": ["bash"], "pattern": "re:python3", "action": "allow"}]
    assert (await PermissionEngine(policy).check_permission("bash", {
        "command": "echo python3 && echo python3",
    })).needs_approval


@pytest.mark.asyncio
async def test_native_and_legacy_guard_check_scripts_after_cd(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text('print("not executed")\n')
    for guard in [
        {"enabled": True, "defaults": {"read": "ask", "write": "ask", "exec": "deny"},
         "workspace": {"read": "allow", "write": "allow", "exec": "allow"}},
        {"enabled": True},
    ]:
        policy = config()
        policy["file_guard"] = guard
        if "defaults" not in guard:
            policy["external_directory"] = {"*": "deny"}
        engine = PermissionEngine(policy, workspace_root=workspace)
        result = await engine.check_permission("bash", {
            "command": "cd ./ && python3 ../outside.py", "workdir": str(workspace),
        })
        assert result.is_denied, result


@pytest.mark.asyncio
async def test_parser_unavailable_keeps_conservative_behavior(monkeypatch):
    monkeypatch.setattr(literal_shell, "_get_tree_sitter_bash_parser", lambda: None)
    result = await PermissionEngine(config()).check_permission("bash", {"command": "cd ./ && python3 probe.py"})
    assert result.needs_approval


@pytest.mark.asyncio
@pytest.mark.parametrize("shell_type", ["cmd", "powershell"])
async def test_other_shell_dialects_do_not_receive_bash_allow_evidence(shell_type):
    result = await PermissionEngine(config()).check_permission("bash", {
        "command": "cd ./ && python3 probe.py", "shell_type": shell_type,
    })
    assert result.needs_approval


@pytest.mark.asyncio
async def test_each_remembered_subcommand_is_checked_without_rule_label_parsing():
    policy = deepcopy(config())
    policy["approval_overrides"] = policy.pop("rules")
    result = await PermissionEngine(policy).check_permission("bash", {"command": "cd ./ && python3 probe.py"})
    assert result.is_allowed


@pytest.mark.asyncio
async def test_full_text_ask_cannot_hide_a_subcommand_deny():
    policy = config()
    policy["rules"] += [
        {"tools": ["bash"], "pattern": "re:^cd .*", "action": "ask"},
        {"tools": ["bash"], "pattern": "python3 *", "action": "deny"},
    ]
    result = await PermissionEngine(policy).check_permission("bash", {"command": "cd ./ && python3 probe.py"})
    assert result.is_denied


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, 1, "invalid"])
async def test_invalid_optional_policy_sections_remain_conservative(value):
    policy = config()
    policy.update({"rules": value, "approval_overrides": value, "defaults": value})
    result = await PermissionEngine(policy).check_permission("bash", {"command": "cd ./ && python3 probe.py"})
    assert result.needs_approval


@pytest.mark.parametrize("command", [
    'python3.12 -c "print(1)"', 'nodejs -e "console.log(1)"', 'dash -c "echo ok"',
])
def test_interpreter_aliases_do_not_gain_literal_evidence(command):
    assert literal_shell.parse_literal_shell(command) is None


@pytest.mark.asyncio
async def test_symlinked_script_after_cd_still_obeys_exec_guard(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text('print("not executed")\n')
    (workspace / "probe.py").symlink_to(outside)
    policy = config()
    policy["file_guard"] = {"enabled": True, "defaults": {"exec": "deny"}, "workspace": {"exec": "allow"}}
    result = await PermissionEngine(policy, workspace_root=workspace).check_permission("bash", {
        "command": "cd ./ && python3 probe.py", "workdir": str(workspace),
    })
    assert result.is_denied


@pytest.mark.asyncio
async def test_legacy_path_checks_are_supplemented_not_replaced(tmp_path):
    policy = config()
    policy["file_guard"] = {"enabled": True}
    policy["external_directory"] = {"*": "deny"}
    result = await PermissionEngine(policy, workspace_root=tmp_path / "workspace").check_permission("bash", {
        "command": f"cd ./ && python3 probe.py --out {tmp_path / 'outside.json'}",
    })
    assert result.is_denied
