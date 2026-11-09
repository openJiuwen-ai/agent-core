# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""删除计数目录兜底：严格证明缺失时按注册工具的真实执行目录计数。

`rm -rf` 等复合命令拿不到 ``LocalDeleteExecution`` 证明，此前计数会退回
workspace 根目录，相对路径解析失败导致 count=0、审批静默放行。
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.core.sys_operation import OperationMode, SandboxGatewayConfig, SysOperation, SysOperationCard
from openjiuwen.core.sys_operation.config import PreDeployLauncherConfig
from openjiuwen.core.sys_operation.cwd import set_cwd
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult, InterruptResult
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.permission_engine.core import PermissionEngine
from openjiuwen.harness.security.permission_engine.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.models import PermissionLevel
from openjiuwen.harness.tools.shell.bash import BashTool


def _config(threshold: int = 3) -> dict:
    return {
        "enabled": True,
        "package_builtin_rules": False,
        "tools": {"bash": "allow"},
        "soft_delete": {"enabled": True, "batch_delete_threshold": threshold},
    }


def _sandbox_operation() -> SysOperation:
    return SysOperation(SysOperationCard(
        id="sandbox-count", mode=OperationMode.SANDBOX,
        gateway_config=SandboxGatewayConfig(launcher_config=PreDeployLauncherConfig(
            sandbox_type="jiuwenbox", base_url="http://localhost:8321",
        )),
    ))


def _sandbox_bash() -> BashTool:
    return BashTool(_sandbox_operation())


class _SubBashTool(BashTool):
    """仓库里确实存在 BashTool 子类注册进 ability_manager（如 rsi 的 RSIBashTool）。"""


def _context(tool, command: str, workdir: str | None = None, name: str = "bash"):
    args = {"command": command, "shell_type": "bash"}
    if workdir is not None:
        args["workdir"] = workdir
    call = ToolCall(id="count-cwd", type="function", name=name, arguments=json.dumps(args))
    ctx = SimpleNamespace(
        agent=SimpleNamespace(ability_manager=SimpleNamespace(_tool_instances={name: tool})),
        inputs=ToolCallInputs(tool_call=call, tool_name=name, tool_args=call.arguments), session=None, extra={},
    )
    return ctx, call, args


def _seed(target, count: int = 4):
    target.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (target / f"f{i}").write_bytes(b"x")
    return target


@pytest.mark.asyncio
async def test_engine_counts_with_delete_count_cwd_without_execution_proof(tmp_path) -> None:
    _seed(tmp_path / "demo")
    # workspace_root 故意给成父目录，复现「根目录 ≠ 执行目录」时的 count=0。
    engine = PermissionEngine(config=_config(), workspace_root=tmp_path.parent)
    args = {"command": "rm -rf demo", "shell_type": "bash"}

    without = await engine.check_permission("bash", args)
    assert "del_guard" not in (without.matched_rule or "")

    with_cwd = await engine.check_permission("bash", args, delete_count_cwd=str(tmp_path))
    assert with_cwd.permission == PermissionLevel.ASK
    assert "del_guard:batch_delete" in (with_cwd.matched_rule or "")
    assert "删除 4 个文件" in (with_cwd.reason or "")


@pytest.mark.asyncio
async def test_rail_counts_with_registered_tool_workdir_when_proof_missing(tmp_path) -> None:
    _seed(tmp_path / "demo")
    rail = PermissionInterruptRail(config=_config())
    ctx, call, _ = _context(_sandbox_bash(), "rm -rf demo && echo done", workdir=str(tmp_path))
    result = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(result, InterruptResult)
    assert "删除 4 个文件" in result.request.metadata["ask_reason"]


@pytest.mark.asyncio
async def test_bash_tool_subclass_counts_with_registered_tool_workdir(tmp_path) -> None:
    _seed(tmp_path / "demo")
    rail = PermissionInterruptRail(config=_config())
    # type() 精确判断会漏掉子类工具，isinstance 才能让它们参与计数目录推断。
    ctx, call, _ = _context(_SubBashTool(_sandbox_operation()), "rm -rf demo && echo done", workdir=str(tmp_path))
    result = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(result, InterruptResult)
    assert "删除 4 个文件" in result.request.metadata["ask_reason"]


@pytest.mark.asyncio
async def test_rail_counts_with_context_cwd_when_no_workdir(tmp_path) -> None:
    _seed(tmp_path / "demo")
    set_cwd(str(tmp_path))
    rail = PermissionInterruptRail(config=_config())
    ctx, call, _ = _context(_sandbox_bash(), "rm -rf demo && echo done")
    result = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(result, InterruptResult)
    assert "删除 4 个文件" in result.request.metadata["ask_reason"]


@pytest.mark.asyncio
async def test_host_cwd_hook_precedes_native_inference(tmp_path) -> None:
    _seed(tmp_path / "demo")

    def hook(_ctx, _call, _args):
        return str(tmp_path)

    rail = PermissionInterruptRail(config=_config(), host=ToolPermissionHost(resolve_shell_cwd=hook))
    # 无 workdir：原生推断退回进程 cwd（数到 0），hook 提供真实目录后应触发审批。
    ctx, call, _ = _context(_sandbox_bash(), "rm -rf demo")
    result = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(result, InterruptResult)
    assert "删除 4 个文件" in result.request.metadata["ask_reason"]


@pytest.mark.asyncio
async def test_non_shell_tool_never_consults_cwd_hook(tmp_path) -> None:
    hook = Mock(return_value=str(tmp_path))
    rail = PermissionInterruptRail(
        config={"enabled": True, "package_builtin_rules": False, "tools": {"read_file": "allow"}},
        host=ToolPermissionHost(resolve_shell_cwd=hook),
    )
    ctx, call, _ = _context(SimpleNamespace(), "rm -rf demo", name="read_file")
    result = await rail.resolve_interrupt(ctx, call, None)
    assert isinstance(result, ApproveResult)
    hook.assert_not_called()
