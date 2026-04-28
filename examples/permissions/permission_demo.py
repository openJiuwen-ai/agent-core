# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
DeepAgent 工具权限与安全提示示例（仅依赖 openjiuwen）。

两层能力：

1. **SecurityRail（提示层）**  
   ``create_deep_agent`` 默认会挂载
   :class:`openjiuwen.harness.rails.security.SecurityRail`，在模型调用前注入安全相关系统提示。

2. **工具权限护栏（执行层）**  
   ``DeepAgentConfig.permissions`` 且 ``enabled: true`` 时，会挂载
   :class:`openjiuwen.harness.rails.security.tool_security_rail.PermissionInterruptRail`，
   在 ``before_tool_call`` 上判定 **allow / ask / deny**；``ask`` 走 Confirm 中断（需会话侧继续交互）。

宿主注入：:class:`openjiuwen.harness.security.host.ToolPermissionHost`
（``get_permissions_snapshot``、``request_permission_confirmation``、``persist_allow_rule``、
``resolve_workspace_dir``、``permission_yaml_path`` 等）。YAML 落盘路径以
``permission_yaml_path`` 为准；未设置则内置 YAML 持久化无法解析路径。不依赖环境变量。

运行（在仓库根目录）::

    uv run python examples/permissions/permission_demo.py

无 **API_KEY** 时：仅跑引擎判定与 DeepAgent 挂载检查。  
有 **API_KEY** 时：额外跑「自然语言 → ``read_file`` → 权限 ASK 中断 → ``InteractiveInput``
批准 → 继续执行直至 ``answer``」完整演示（需网络；模型须能输出原生 ``tool_calls``）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path

# 允许从任意工作目录直接执行本脚本
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.interrupt.response import ToolCallInterruptRequest
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.factory import build_permission_interrupt_rail
from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.rails.security import SecurityRail
from openjiuwen.harness.rails.security import PermissionInterruptRail

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
    )
    print("[Sync] evaluate_global_policy_directly(read_file) ->", level, "| rule:", rule)


async def demo_async_check_permission(workspace: Path) -> None:
    perms = example_permissions_dict()
    engine = PermissionEngine(perms, workspace_root=workspace.resolve())
    result = await engine.check_permission(
        "read_file",
        {"path": str(workspace / "notes.txt")},
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


def _nl_summarize_dict_result(prefix: str, result: dict) -> None:
    """打印 Runner.run_agent 返回 dict 的关键字段（便于演示）。"""
    rt = result.get("result_type")
    print(f"{prefix} result_type={rt!r} keys={sorted(result.keys())}")
    for key in ("output", "error", "status", "interrupt_ids", "state"):
        if key not in result or result[key] is None:
            continue
        val = result[key]
        if key == "state" and isinstance(val, list):
            print(f"{prefix}   {key}: len={len(val)}")
            continue
        snippet = val
        if isinstance(snippet, str) and len(snippet) > 500:
            snippet = snippet[:500] + "..."
        print(f"{prefix}   {key}:", snippet)


async def demo_natural_language_triggers_permission_rail(workspace: Path) -> None:
    """自然语言 → LLM 发起 ``read_file`` → ``PermissionInterruptRail`` 在 ASK 上中断 → 用户批准 → 读完文件。

    需要 ``API_KEY``（及可选 ``MODEL_NAME`` / ``API_BASE``）。必须使用 **同一**
    ``conversation_id`` 做第二轮 ``InteractiveInput`` 恢复，否则会话对不上。

    若首轮直接 ``answer`` 而无 ``interrupt``：常见原因是模型未按 OpenAI 风格输出
    原生 ``tool_calls``（伪 XML / 纯文本描述工具），护栏 ``before_tool_call`` 不会触发。
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
    conversation_id = f"perm_nl_{uuid.uuid4().hex[:12]}"

    await Runner.start()
    try:
        agent = create_deep_agent(
            model=model,
            card=AgentCard(
                name="permission_demo_nl",
                description="通过自然语言读文件以触发权限检查",
            ),
            workspace=str(workspace),
            max_iterations=8,
            language="cn",
            permissions=example_permissions_dict(),
            permission_host=host,
            rails=[SysOperationRail()],
            system_prompt=(
                "你是编程助手。用户要求读文件时，你必须调用 read_file 工具（使用参数 "
                "file_path，相对路径即可，例如 notes.txt），不要用纯文字假装已读取。"
            ),
        )
        await agent.ensure_initialized()

        query = (
            "工作区根目录下有一个文件 notes.txt。"
            "请只调用 read_file 工具读取该文件完整内容，然后把读到的原文逐字写在回复里，不要省略。"
        )
        print("[NL] conversation_id:", conversation_id)
        print("[NL] model:", model_name, "| query:", query[:100], "...")

        inputs_round1 = {"query": query, "conversation_id": conversation_id}
        result1 = await Runner.run_agent(agent, inputs_round1)

        if not isinstance(result1, dict):
            print("[NL] 首轮返回非 dict:", type(result1).__name__, result1)
            return

        _nl_summarize_dict_result("[NL] 首轮", result1)

        if result1.get("result_type") != "interrupt":
            print(
                "[NL] 未出现 interrupt：模型可能未产生可执行的原生 tool_calls，"
                "或首轮已完成读文件（例如策略未命中 ASK）。可换支持 function calling 的模型或调高 max_iterations。"
            )
            return

        interrupt_ids = result1.get("interrupt_ids") or []
        state_list = result1.get("state") or []
        if not interrupt_ids:
            print("[NL] interrupt 但 interrupt_ids 为空，无法演示恢复。")
            return

        tool_call_id = interrupt_ids[0]
        raw = state_list[0].payload.value if state_list and hasattr(state_list[0], "payload") else None
        if isinstance(raw, ToolCallInterruptRequest):
            print(
                "[NL] 中断工具:",
                raw.tool_name,
                "| message 预览:",
                (raw.message or "")[:120].replace("\n", " "),
                "...",
            )
            if raw.tool_name != "read_file":
                print("[NL] 提示：当前中断不是 read_file，本演示仍按 ConfirmPayload 批准一次。")

        interactive = InteractiveInput()
        interactive.update(
            tool_call_id,
            {"approved": True, "feedback": "", "auto_confirm": False},
        )
        inputs_round2 = {"query": interactive, "conversation_id": conversation_id}
        print("[NL] 第二轮：提交 InteractiveInput 批准本次 read_file …")
        result2 = await Runner.run_agent(agent, inputs_round2)

        if not isinstance(result2, dict):
            print("[NL] 次轮返回非 dict:", type(result2).__name__, result2)
            return

        _nl_summarize_dict_result("[NL] 次轮", result2)

        if result2.get("result_type") == "answer":
            out = result2.get("output")
            if isinstance(out, str) and "permission_demo secret" in out:
                print("[NL] ✓ 已读到 notes.txt 中的演示内容（权限护栏 ASK → 批准后执行）。")
            else:
                print("[NL] 次轮为 answer；请检查 output 是否包含预期文件内容。")
        elif result2.get("result_type") == "interrupt":
            print("[NL] 次轮仍为 interrupt：可能还有其它工具待确认，可继续用 InteractiveInput 处理。")
        else:
            print("[NL] 次轮 result_type:", result2.get("result_type"))
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
