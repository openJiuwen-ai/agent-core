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


def _engine(workspace: Path, protected: Path, *, enabled: bool = False) -> PermissionEngine:
    return PermissionEngine(
        {"enabled": enabled, "package_builtin_rules": False},
        workspace_root=workspace,
        immutable_control_roots=[protected],
    )


@pytest.mark.asyncio
async def test_protected_write_is_denied_even_when_permissions_are_disabled(tmp_path):
    protected = tmp_path / "control"
    protected.mkdir()
    target = protected / "config.yaml"
    engine = _engine(tmp_path, protected, enabled=False)

    result = await engine.check_permission(
        "write_file",
        {"file_path": str(target), "content": "permissions:\n  enabled: false\n"},
    )

    assert result.permission == PermissionLevel.DENY
    assert result.matched_rule == "immutable_control_plane"


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
    engine = _engine(tmp_path, protected, enabled=False)
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
async def test_project_execution_config_requires_one_time_approval_in_full_access(tmp_path, relative):
    protected = tmp_path / "control"
    protected.mkdir()
    target = tmp_path / relative
    engine = _engine(tmp_path, protected, enabled=False)

    result = await engine.check_permission(
        "write_file",
        {"file_path": str(target), "content": "changed"},
    )

    assert result.permission == PermissionLevel.ASK
    assert result.matched_rule == "immutable_project_execution_config"


class _Session:
    def __init__(self) -> None:
        self.state: dict = {}

    def get_state(self, key):
        return self.state.get(key)

    def update_state(self, state: dict) -> None:
        self.state.update(state)


def _one_time_approval_rail(tmp_path, *, hosted_confirmation=None):
    persisted: list[str] = []
    config = {"enabled": False, "package_builtin_rules": False}
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
