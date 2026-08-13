# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PermissionInterruptRail 在校验前应从 Host 刷新当前任务 workspace。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, RejectResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.security.mode_controller import PermissionModeController
from openjiuwen.harness.security.models import PermissionConfirmResponse


def _write_call(path: Path) -> ToolCall:
    return ToolCall(
        id="c1",
        type="function",
        name="write_file",
        arguments=json.dumps({"file_path": str(path), "content": "x"}),
    )


@pytest.mark.asyncio
async def test_rail_refreshes_workspace_from_host_before_check(tmp_path: Path) -> None:
    """rail 构造时若绑到 agent 根，校验前应改用 Host 上的任务 workspace。"""
    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    project.mkdir(parents=True)
    current = {"root": agent_ws}
    asked: list[object] = []

    async def _confirm(req):
        asked.append(req)
        return PermissionConfirmResponse(approved=False)

    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "auto"})
    host = ToolPermissionHost(
        resolve_workspace_dir=lambda: Path(current["root"]),
        request_permission_confirmation=_confirm,
    )
    rail = PermissionInterruptRail(config=eff.permissions, host=host)
    current["root"] = project

    decision = await rail.resolve_interrupt(
        ctx=SimpleNamespace(session=None),
        tool_call=_write_call(agent_ws / "leak.txt"),
        user_input=None,
    )

    assert asked, "write to parent of current workspace must ASK"
    assert isinstance(decision, RejectResult)


@pytest.mark.asyncio
async def test_rail_allows_write_inside_refreshed_workspace(tmp_path: Path) -> None:
    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    project.mkdir(parents=True)
    current = {"root": agent_ws}
    asked: list[object] = []

    async def _confirm(req):
        asked.append(req)
        return PermissionConfirmResponse(approved=False)

    ctrl = PermissionModeController()
    eff = ctrl.compose({"enabled": True, "mode": "auto"})
    host = ToolPermissionHost(
        resolve_workspace_dir=lambda: Path(current["root"]),
        request_permission_confirmation=_confirm,
    )
    rail = PermissionInterruptRail(config=eff.permissions, host=host)
    current["root"] = project

    decision = await rail.resolve_interrupt(
        ctx=SimpleNamespace(session=None),
        tool_call=_write_call(project / "ok.txt"),
        user_input=None,
    )

    assert asked == []
    assert isinstance(decision, ApproveResult)
