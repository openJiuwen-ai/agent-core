# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
DeepAgent 工具权限与安全提示示例（仅依赖 openjiuwen）。

两层能力：

1. **SecurityRail（提示层）**  
   ``create_deep_agent`` 默认会挂载
   :class:`openjiuwen.harness.rails.security_rail.SecurityRail`，在模型调用前注入安全相关系统提示。

2. **工具权限护栏（执行层）**  
   ``DeepAgentConfig.permissions`` 且 ``enabled: true`` 时，会挂载
   :class:`openjiuwen.harness.rails.security_rail.tool_security_rail.PermissionInterruptRail`，
   在 ``before_tool_call`` 上判定 **allow / ask / deny**；``ask`` 走 Confirm 中断（需会话侧继续交互）。

宿主注入：:class:`openjiuwen.harness.security.host.ToolPermissionHost`
（``get_permissions_snapshot``、``request_acp_permission``、``persist_allow_rule``、
``resolve_workspace_dir``、``permission_yaml_path`` 等）。YAML 落盘路径以
``permission_yaml_path`` 或 :func:`openjiuwen.harness.security.patterns.set_agent_config_yaml_path_provider`
为准，不依赖环境变量。

运行（在仓库根目录）::

    uv run python examples/permissions/permission_demo.py

无 **API_KEY** 时：仅跑引擎判定与 DeepAgent 挂载检查。  
有 **API_KEY** 时：额外跑「自然语言 → 模型调用 read_file → 命中 ASK 权限」演示（需网络调用模型）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# 允许从任意工作目录直接执行本脚本
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.factory import build_permission_interrupt_rail
from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.rails.security_rail import SecurityRail
from openjiuwen.harness.rails.security_rail import PermissionInterruptRail


def example_permissions_dict() -> dict:
    """最小 ``permissions``（分层策略），read_file 为 ASK 以便演示护栏。"""
    return {
        "enabled": True,
        "schema": "tiered_policy",
        "permission_mode": "normal",
        "tools": {
            "read_file": "ask",
            "write_file": "deny",
        },
        "defaults": {"*": "allow"},
        "rules": [],
        "approval_overrides": [],
    }


def example_permission_host(workspace: Path, config_yaml: Path | None) -> ToolPermissionHost:
    root = workspace.resolve()

    def _workspace_root() -> Path:
        return root

    return ToolPermissionHost(
        resolve_workspace_dir=_workspace_root,
        permission_yaml_path=config_yaml,
    )


def demo_sync_permission_engine(workspace: Path) -> None:
    perms = example_permissions_dict()
    engine = PermissionEngine(perms, workspace_root=workspace.resolve())
    level, rule = engine.evaluate_global_policy_directly(
        "read_file",
        {"path": str(workspace / "notes.txt")},
        channel_id="web",
    )
    print("[Sync] evaluate_global_policy_directly(read_file) ->", level, "| rule:", rule)


async def demo_async_check_permission(workspace: Path) -> None:
    perms = example_permissions_dict()
    engine = PermissionEngine(perms, workspace_root=workspace.resolve())
    result = await engine.check_permission(
        "read_file",
        {"path": str(workspace / "notes.txt")},
        channel_id="web",
    )
    print(
        "[Async] check_permission ->",
        result.permission.value,
        "| matched_rule:",
        result.matched_rule,
        "| needs_approval:",
        result.needs_approval,
    )


async def demo_deep_agent_mounts_rails(workspace: Path) -> None:
    from openjiuwen.core.foundation.llm import init_model

    await Runner.start()
    try:
        api_key = os.getenv("API_KEY", "").strip()
        model_name = os.getenv("MODEL_NAME", "gpt-4.1-mini").strip()
        api_base = os.getenv("API_BASE", "https://api.openai.com/v1").strip()

        if not api_key:
            model = None
            print("[DeepAgent] 未设置 API_KEY，使用 model=None 仅演示配置与初始化。")
        else:
            model = init_model(
                provider="OpenAI",
                model_name=model_name,
                api_key=api_key,
                api_base=api_base,
                verify_ssl=False,
            )

        cfg_yaml = Path(tempfile.gettempdir()) / "openjiuwen_permission_demo_config.yaml"
        host = example_permission_host(workspace, cfg_yaml)

        agent = create_deep_agent(
            model=model,
            card=AgentCard(
                name="permission_demo",
                description="演示工具权限护栏",
            ),
            workspace=str(workspace),
            max_iterations=3,
            language="cn",
            permissions=example_permissions_dict(),
            permission_host=host,
        )

        await agent.ensure_initialized()

        names = [type(r).__name__ for r in agent._registered_rails]
        print("[DeepAgent] 已注册 rails:", names)
        assert "PermissionInterruptRail" in names
        assert any(n == "SecurityRail" for n in names)
    finally:
        await Runner.stop()


def demo_standalone_rail_factory(workspace: Path) -> None:
    rail = build_permission_interrupt_rail(
        permissions=example_permissions_dict(),
        host=example_permission_host(workspace, None),
        workspace_root=workspace.resolve(),
    )
    assert isinstance(rail, PermissionInterruptRail)
    print("[Factory] build_permission_interrupt_rail ->", type(rail).__name__)


async def demo_natural_language_triggers_permission_rail(workspace: Path) -> None:
    """自然语言 → LLM 选择 read_file → ``PermissionInterruptRail`` 在 ASK 上介入。

    需要 ``API_KEY``（及可选 ``MODEL_NAME`` / ``API_BASE``）。若策略为 ASK，运行结果可能包含
    中断/待确认状态，由 Runner 与会话实现决定；本示例只打印返回结构便于观察。
    """
    api_key = os.getenv("API_KEY", "").strip()
    if not api_key:
        print(
            "[NL] 已跳过：设置环境变量 API_KEY 后重跑，即可看到模型调用 read_file 并触发权限护栏。"
        )
        return

    from openjiuwen.core.foundation.llm import init_model

    model_name = os.getenv("MODEL_NAME", "gpt-4.1-mini").strip()
    api_base = os.getenv("API_BASE", "https://api.openai.com/v1").strip()
    model = init_model(
        provider="OpenAI",
        model_name=model_name,
        api_key=api_key,
        api_base=api_base,
        verify_ssl=False,
    )

    cfg_yaml = Path(tempfile.gettempdir()) / "openjiuwen_permission_demo_config.yaml"
    host = example_permission_host(workspace, cfg_yaml)

    await Runner.start()
    try:
        agent = create_deep_agent(
            model=model,
            card=AgentCard(
                name="permission_demo_nl",
                description="通过自然语言读文件以触发权限检查",
            ),
            workspace=str(workspace),
            max_iterations=5,
            language="cn",
            permissions=example_permissions_dict(),
            permission_host=host,
            rails=[SysOperationRail()],
        )
        await agent.ensure_initialized()

        query = (
            "工作区根目录下有一个文件 notes.txt。"
            "请**必须**使用 read_file 工具读取该文件的完整内容，"
            "然后把读到的原文逐字输出，不要省略。"
        )
        query = (
            "在当前演示工作区根目录下有一个文件 notes.txt。请按顺序完成："
            "1）用 list_files 列出工作区根目录的文件名；"
            "2）用 read_file 读取 notes.txt 的完整原文；"
            "3）用 grep 在 notes.txt 中搜索子串 permission。"
            "每一步都必须实际调用对应工具，不要省略。"
        )
        print("[NL] query:", query[:80], "...")
        result = await Runner.run_agent(agent, {"query": query})

        if isinstance(result, dict):
            print("[NL] run_agent 返回 dict，keys:", sorted(result.keys()))
            for key in ("output", "error", "status", "interrupt", "__interaction__"):
                if key in result and result[key] is not None:
                    snippet = result[key]
                    if isinstance(snippet, str) and len(snippet) > 400:
                        snippet = snippet[:400] + "..."
                    print(f"[NL]   {key}:", snippet)
        else:
            print("[NL] run_agent 返回:", type(result).__name__, result)
    finally:
        await Runner.stop()


async def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="ojw-perm-demo-"))
    (workspace / "notes.txt").write_text(
        "permission_demo secret line\n第二行\n",
        encoding="utf-8",
    )
    print("Workspace:", workspace)

    demo_sync_permission_engine(workspace)
    await demo_async_check_permission(workspace)
    demo_standalone_rail_factory(workspace)
    await demo_deep_agent_mounts_rails(workspace)
    await demo_natural_language_triggers_permission_rail(workspace)

    print("\n类说明：")
    print(" - SecurityRail           :", (SecurityRail.__doc__ or "").split("\n")[0])
    print(" - PermissionInterruptRail:", (PermissionInterruptRail.__doc__ or "").split("\n")[0])


if __name__ == "__main__":
    asyncio.run(main())
