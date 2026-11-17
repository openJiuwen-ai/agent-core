# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Replay a deliverable misdirected into an immutable candidate package."""

import json
from types import SimpleNamespace

import pytest

from openjiuwen.rsi.harness_rsi.evaluator.case_backend import (
    _single_harness_rails,
    _single_harness_system_prompt,
)
from openjiuwen.rsi.harness_rsi.evaluator.harness_input_rail import HarnessInputRail


def _context(name, args):
    return SimpleNamespace(inputs=SimpleNamespace(
        tool_name=name, tool_args=args, tool_call=None,
        tool_result=None, tool_msg=None,
    ), extra={})


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["write_file", "edit_file"])
@pytest.mark.parametrize("serialized", [False, True])
async def test_plugin_write_is_rejected_but_workspace_write_allowed(tmp_path, name, serialized):
    plugin = tmp_path / "candidate"
    workspace = tmp_path / "workspace"
    rail = HarnessInputRail(plugin, workspace)
    args = {"file_path": str(plugin / "diagnosis_recovery_plan.md"), "content": "answer"}
    denied = _context(name, json.dumps(args) if serialized else args)
    await rail.before_tool_call(denied)
    assert denied.extra["_skip_tool"] is True
    assert str(workspace) in denied.inputs.tool_msg.content
    assert "HARNESS_INPUT_READ_ONLY" in denied.inputs.tool_result

    allowed = _context(name, {**args, "file_path": str(workspace / "answer.md")})
    await rail.before_tool_call(allowed)
    assert "_skip_tool" not in allowed.extra


@pytest.mark.asyncio
async def test_native_skill_reads_and_workspace_shell_are_unchanged(tmp_path):
    plugin = tmp_path / "candidate"
    rail = HarnessInputRail(plugin, tmp_path / "workspace")
    for name, args in [
        ("read_file", {"file_path": str(plugin / "skills" / "SKILL.md")}),
        ("skill", {"name": "diagnosis_planning_coverage"}),
        ("bash", {"command": "echo answer > answer.md"}),
    ]:
        ctx = _context(name, args)
        await rail.before_tool_call(ctx)
        assert "_skip_tool" not in ctx.extra


@pytest.mark.asyncio
async def test_shell_redirection_cannot_write_plugin(tmp_path):
    plugin = tmp_path / "candidate"
    rail = HarnessInputRail(plugin, tmp_path / "workspace")
    ctx = _context("bash", {"command": f'echo answer > "{(plugin / "answer.md").as_posix()}"'})
    await rail.before_tool_call(ctx)
    assert ctx.extra["_skip_tool"] is True


def test_boundary_is_bound_for_local_and_container_evaluations(tmp_path):
    for workspace, shell_only in [(str(tmp_path / "workspace"), False), ("/testbed", True)]:
        rails = _single_harness_rails(
            None, harness_path=tmp_path / "candidate", workspace=workspace, shell_only=shell_only,
        )
        boundary = [rail for rail in rails if isinstance(rail, HarnessInputRail)]
        assert len(boundary) == 1
        assert boundary[0].workspace == workspace
        prompt = _single_harness_system_prompt("solver", workspace=workspace)
        assert f"`{workspace}`" in prompt
        assert "read-only capability sources" in prompt
