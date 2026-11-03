# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.state import INTERRUPT_AUTO_CONFIRM_KEY
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.permission_engine.core import PermissionEngine
from openjiuwen.harness.security.permission_engine.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.models import (
    PermissionConfirmResponse,
    PermissionLevel,
)


def _engine(
    workspace: Path,
    protected: Path,
    *,
    enabled: bool = False,
    file_guard_enabled: bool | None = None,
    immutable_control_enabled: bool | None = None,
) -> PermissionEngine:
    config: dict = {"enabled": enabled, "package_builtin_rules": False}
    if file_guard_enabled is not None:
        config["file_guard"] = {"enabled": file_guard_enabled}
    if immutable_control_enabled is not None:
        config["immutable_control_enabled"] = immutable_control_enabled
    return PermissionEngine(
        config,
        workspace_root=workspace,
        immutable_control_roots=[protected],
    )


@pytest.mark.asyncio
async def test_control_plane_write_follows_the_master_switch(tmp_path):
    protected = tmp_path / "control"
    protected.mkdir()
    target = protected / "config.yaml"
    payload = {"file_path": str(target), "content": "permissions:\n  enabled: false\n"}

    disabled = await _engine(
        tmp_path, protected, enabled=False, file_guard_enabled=True,
    ).check_permission("write_file", payload)
    assert disabled.permission == PermissionLevel.ALLOW
    assert disabled.reason == "Permission system is disabled"
    assert disabled.matched_rule != "immutable_control_plane"

    protected_on = await _engine(
        tmp_path, protected, enabled=True, file_guard_enabled=False,
    ).check_permission("write_file", payload)
    assert protected_on.permission == PermissionLevel.DENY
    assert protected_on.matched_rule == "immutable_control_plane"

    host_off = PermissionEngine(
        {
            "enabled": True,
            "package_builtin_rules": False,
            "immutable_control_enabled": False,
            "tools": {"write_file": "allow"},
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "allow", "write": "allow", "exec": "allow"},
            },
        },
        workspace_root=tmp_path,
        immutable_control_roots=[protected],
    )
    host_result = await host_off.check_permission("write_file", payload)
    assert host_result.permission == PermissionLevel.ALLOW
    assert host_result.matched_rule != "immutable_control_plane"


@pytest.mark.asyncio
async def test_protected_read_remains_allowed_in_full_access(tmp_path):
    protected = tmp_path / "control"
    protected.mkdir()
    target = protected / "config.yaml"
    target.write_text("safe", encoding="utf-8")
    engine = _engine(tmp_path, protected, enabled=False)

    result = await engine.check_permission("read_file", {"file_path": str(target)})

    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["powershell", "bash"])
async def test_shell_write_to_protected_root_is_denied(tmp_path, tool_name):
    protected = tmp_path / "control"
    protected.mkdir()
    target = protected / "config.yaml"
    engine = _engine(tmp_path, protected, enabled=True, file_guard_enabled=False)
    command = (
        f'Set-Content -Path "{target}" -Value disabled'
        if tool_name == "powershell"
        else f'echo disabled > "{target}"'
    )

    result = await engine.check_permission(
        tool_name,
        {"command": command, "workdir": str(tmp_path)},
    )

    assert result.permission == PermissionLevel.DENY
    assert result.matched_rule == "immutable_control_plane"


@pytest.mark.asyncio
async def test_shell_write_to_protected_root_is_allowed_when_master_switch_is_off(tmp_path):
    protected = tmp_path / "control"
    protected.mkdir()
    target = protected / "config.yaml"
    engine = _engine(tmp_path, protected, enabled=False, file_guard_enabled=True)

    result = await engine.check_permission(
        "powershell",
        {"command": f'Set-Content -Path "{target}" -Value disabled', "workdir": str(tmp_path)},
    )

    assert result.permission == PermissionLevel.ALLOW
    assert result.reason == "Permission system is disabled"


@pytest.mark.asyncio
async def test_write_outside_protected_root_keeps_normal_full_access_behavior(tmp_path):
    protected = tmp_path / "control"
    protected.mkdir()
    target = tmp_path / "project" / "main.py"
    engine = _engine(tmp_path, protected, enabled=False)

    result = await engine.check_permission(
        "write_file",
        {"file_path": str(target), "content": "print('ok')"},
    )

    assert result.permission == PermissionLevel.ALLOW


@pytest.mark.asyncio
@pytest.mark.parametrize("relative", [
    "package.json",
    "pyproject.toml",
    ".github/workflows/release.yml",
    ".git/hooks/pre-commit",
    ".vscode/tasks.json",
])
async def test_project_execution_config_requires_one_time_approval_when_master_switch_is_on(
    tmp_path, relative,
):
    protected = tmp_path / "control"
    protected.mkdir()
    target = tmp_path / relative
    engine = _engine(tmp_path, protected, enabled=True, file_guard_enabled=False)

    result = await engine.check_permission(
        "write_file",
        {"file_path": str(target), "content": "changed"},
    )

    assert result.permission == PermissionLevel.ASK
    assert result.matched_rule == "immutable_project_execution_config"


@pytest.mark.asyncio
async def test_project_execution_config_is_not_asked_when_master_switch_is_off(tmp_path):
    protected = tmp_path / "control"
    protected.mkdir()
    engine = _engine(tmp_path, protected, enabled=False, file_guard_enabled=True)

    result = await engine.check_permission(
        "write_file",
        {"file_path": str(tmp_path / "package.json"), "content": "changed"},
    )

    assert result.permission == PermissionLevel.ALLOW
    assert result.reason == "Permission system is disabled"
    assert result.matched_rule != "immutable_project_execution_config"


class _Session:
    def __init__(self) -> None:
        self.state: dict = {}

    def get_state(self, key):
        return self.state.get(key)

    def update_state(self, state: dict) -> None:
        self.state.update(state)


def _one_time_approval_rail(tmp_path, *, hosted_confirmation=None):
    persisted: list[str] = []
    config = {"enabled": True, "package_builtin_rules": False, "file_guard": {"enabled": False}}
    host = ToolPermissionHost(
        get_permissions_snapshot=lambda: dict(config),
        persist_allow_rule=lambda _cfg: persisted.append("permanent") or True,
        persist_session_allow_rule=lambda _cfg: persisted.append("session") or True,
        resolve_workspace_dir=lambda: tmp_path,
        request_permission_confirmation=hosted_confirmation,
    )
    return PermissionInterruptRail(config=config, host=host), persisted


@pytest.mark.asyncio
@pytest.mark.parametrize("persist_allow", [False, True])
async def test_project_execution_config_remember_choice_becomes_one_time_on_resume(
    tmp_path, persist_allow,
):
    rail, persisted = _one_time_approval_rail(tmp_path)
    session = _Session()
    ctx = AgentCallbackContext(agent=object(), session=session)
    call = ToolCall(
        id="write-package",
        type="function",
        name="write_file",
        arguments='{"file_path": "%s", "content": "{}"}'
        % str(tmp_path / "package.json").replace("\\", "\\\\"),
    )

    decision = await rail.resolve_interrupt(
        ctx,
        call,
        {"approved": True, "auto_confirm": True, "persist_allow": persist_allow},
    )

    assert isinstance(decision, ApproveResult)
    assert persisted == []
    assert INTERRUPT_AUTO_CONFIRM_KEY not in session.state


@pytest.mark.asyncio
async def test_project_execution_config_hosted_remember_choice_becomes_one_time(tmp_path):
    async def confirm(_request):
        return PermissionConfirmResponse(
            approved=True,
            auto_confirm=True,
            persist_allow=True,
        )

    rail, persisted = _one_time_approval_rail(
        tmp_path,
        hosted_confirmation=confirm,
    )
    session = _Session()
    ctx = AgentCallbackContext(agent=object(), session=session)
    call = ToolCall(
        id="write-package",
        type="function",
        name="write_file",
        arguments='{"file_path": "%s", "content": "{}"}'
        % str(tmp_path / "package.json").replace("\\", "\\\\"),
    )

    decision = await rail.resolve_interrupt(ctx, call, user_input=None)

    assert isinstance(decision, ApproveResult)
    assert persisted == []
    assert INTERRUPT_AUTO_CONFIRM_KEY not in session.state
