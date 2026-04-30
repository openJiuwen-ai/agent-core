# -*- coding: utf-8 -*-
"""

本示例演示如何在 DeepAgent 中集成 LspRail 和 SysOperationRail（以 pyright 为例）：

Demo 1-8  — LSP 代码导航：
  1. goToDefinition        — 跳转到函数/类定义
  2. findReferences        — 查找符号的所有引用位置
  3. documentSymbol        — 列出当前文件中的所有符号
  4. workspaceSymbol       — 在整个工作区中搜索符号
  5. goToImplementation    — 跳转到接口/抽象类的实现
  6. prepareCallHierarchy  — 准备调用层次结构
  7. incomingCalls         — 查找调用当前符号的调用者
  8. outgoingCalls         — 查找当前符号调用的下游符号
  9. before_model_call 诊断注入 — after_tool_call 触发 LSP → before_model_call 注入诊断 → Agent 自动修复

  Demo 9（LspRail.before_model_call）：展示完整流水线：
  Agent 编辑文件 → after_tool_call 触发 LSP 重新分析 → before_model_call
  将 pyright 诊断作为 UserMessage 自动注入 LLM 上下文 → Agent 感知错误并
  逐步修复，直到诊断队列清空，全程无需显式调用任何诊断工具。

运行前提：
    pyright 已安装 （npm install -g pyright）

"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(Path(__file__).parent / "sample_code"))

from openjiuwen.core.foundation.llm import init_model
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.rails.lsp_rail import LspRail
from openjiuwen.harness.lsp import shutdown_lsp, InitializeOptions

from sample_code.diagnostic_params import (
    safe_print,
    Psep,
    Pline,
    wait_for_diagnostics,
    print_diagnostics,
)

# ============================================================
# 配置
# ============================================================
_API_KEY = os.getenv(
    "API_KEY",
    "your api key here",
)
_MODEL_NAME = os.getenv("MODEL_NAME", "your model here")
_API_BASE = os.getenv("API_BASE", "your api base here")

# 被分析的示例代码目录（含 pyproject.toml，LSP 需要）
SAMPLE_CODE = Path(__file__).parent / "sample_code"


# ============================================================
# Demo 查询定义（1-8 导航，9 before_model_call 诊断注入）
# ============================================================
DEMO_QUERIES = [
    {
        "id": 1,
        "name": "goToDefinition",
        "description": "跳转到函数定义",
        "query": (
            "请分析当前工作区中的 main.py 文件，"
            "找到第 25 行调用的 create_sample_model 函数定义位置，"
            "然后阅读该定义的源代码内容。"
            "最后总结：create_sample_model 函数的作用是什么？"
        ),
    },
    {
        "id": 2,
        "name": "findReferences",
        "description": "查找符号的所有引用位置",
        "query": (
            "请在当前工作区中使用 LSP 的 findReferences 功能，"
            "查找所有使用 DataModel 类的地方，"
            "包括定义处。列出每个引用位置的文件名和行号。"
        ),
    },
    {
        "id": 3,
        "name": "documentSymbol",
        "description": "列出当前文件中的所有符号",
        "query": (
            "请使用 LSP 的 documentSymbol 功能，"
            "列出当前工作区中 models.py 文件的所有符号（类、函数、枚举等），"
            "包括它们的类型和定义行号。"
        ),
    },
    {
        "id": 4,
        "name": "workspaceSymbol",
        "description": "在整个工作区中搜索符号",
        "query": (
            "请使用 LSP 的 workspaceSymbol 功能，"
            "在整个工作区中搜索名称包含 'model' 的符号（如类、函数、变量），"
            "列出每个符号的名称、类型和定义位置。"
        ),
    },
    {
        "id": 5,
        "name": "goToImplementation",
        "description": "跳转到接口/抽象类的实现",
        "query": (
            "请使用 LSP 的 goToImplementation 功能，"
            "在 models.py 中找到 DataModel 类的定义位置，"
            "查看该类的所有方法签名和属性定义。"
        ),
    },
    {
        "id": 6,
        "name": "prepareCallHierarchy",
        "description": "准备调用层次结构",
        "query": (
            "请使用 LSP 的 prepareCallHierarchy 功能，"
            "分析 main.py 中 create_sample_model 函数的调用关系："
            "哪些位置调用了它？它又调用了哪些其他函数？"
        ),
    },
    {
        "id": 7,
        "name": "incomingCalls",
        "description": "查找调用当前符号的调用者",
        "query": (
            "请使用 LSP 的 incomingCalls 功能，"
            "分析 main.py 中 create_sample_model 函数的调用者，"
            "列出所有调用该函数的位置（文件名和行号）。"
        ),
    },
    {
        "id": 8,
        "name": "outgoingCalls",
        "description": "查找当前符号调用的下游符号",
        "query": (
            "请使用 LSP 的 outgoingCalls 功能，"
            "分析 main.py 中 create_sample_model 函数调用了哪些下游函数或类，"
            "列出每个被调用的符号及其定义位置。"
        ),
    },
    {
        "id": 9,
        "name": "before_model_call 诊断注入",
        "description": (
            "after_tool_call 触发 LSP 重新分析 → before_model_call 将诊断自动注入 LLM 上下文 → Agent 感知并修复所有错误"
        ),
        "query": (
            f"检查文件 {str((Path(__file__).parent / 'sample_code' / 'test.py').resolve())} 。\n"
            f"请按以下步骤修复，直到文件不再含有任何错误：\n"
            f"1. 先用 read_file 读取该文件，了解当前内容。\n"
            f"2. 根据内容和上下文中的诊断信息，用 edit_file 修复一处错误。\n"
            f"3. 每次 edit_file 后，重新用 read_file 读取文件，再进行下一次编辑。\n"
        ),
    },
]


# ============================================================
# 主入口
# ============================================================

async def main():
    Psep()
    safe_print("DeepAgent + LspRail + SysOperationRail 完整示例 — 9 种演示")
    safe_print("  Demo 1-8  — LSP 代码导航操作")
    safe_print("  Demo 9    — after_tool_call → LspRail.before_model_call → 诊断注入 → Agent 自动修复")
    Psep()
    safe_print(f"被分析代码: {SAMPLE_CODE}")

    if not SAMPLE_CODE.exists():
        safe_print(f"错误: 示例代码目录不存在: {SAMPLE_CODE}")
        return

    demo9_file = SAMPLE_CODE / "test.py"

    await Runner.start()

    model = init_model(
        provider="OpenAI",
        model_name=_MODEL_NAME,
        api_key=_API_KEY,
        api_base=_API_BASE,
        verify_ssl=False,
    )

    # SysOperationRail 提供 read_file / edit_file / write_file（Demo 9 需要）
    # LspRail 提供 LSP 导航工具（Demo 1-8）以及 after_tool_call / before_model_call 钩子（Demo 9）
    agent = create_deep_agent(
        model=model,
        card=AgentCard(
            name="lsp_demo",
            description="具备 LSP 代码导航和文件编辑自动诊断能力的 AI 编程助手",
        ),
        workspace=str(SAMPLE_CODE),
        rails=[
            SysOperationRail(),
            LspRail(options=InitializeOptions(cwd=str(SAMPLE_CODE)), verbose=True),
        ],
        max_iterations=10,
        language="cn",
    )

    try:
        for demo in DEMO_QUERIES:
            Psep()
            safe_print(f"演示 {demo['id']}：{demo['name']} — {demo['description']}")
            Psep()

            if demo["id"] == 9:
                safe_print(f"[准备] Demo 9 使用现有文件: {demo9_file.resolve()}")
                safe_print("[说明] LspRail.after_tool_call 将在每次 edit_file 后触发 LSP 分析，")
                safe_print("[说明] LspRail.before_model_call 将在每次 LLM 调用前把诊断注入上下文。")
                Pline()

            safe_print(f"\n[用户指令]\n{demo['query']}\n")
            Pline()

            try:
                result = await Runner.run_agent(agent, {"query": demo["query"]})
                output = result.get("output", "") if isinstance(result, dict) else str(result)
                safe_print(f"[Agent 输出]\n{output}")
            except Exception as e:
                safe_print(f"[错误] {e}")
                import traceback
                traceback.print_exc()

            # Demo 9 results: verify diagnostics cleared after agent auto-fixed all errors
            if demo["id"] == 9:
                Pline()
                safe_print("等待 pyright 最终诊断（确认 Agent 已修复所有错误）", end="", flush=True)
                remaining_diags = await wait_for_diagnostics(timeout=20.0, initial_delay=2.0)
                if not remaining_diags:
                    safe_print("[Demo 9] Agent 已修复所有错误 — 诊断队列为空")
                    safe_print("说明：LspRail.before_model_call 在每轮 LLM 调用前将 pyright 诊断")
                    safe_print("      自动注入上下文，Agent 无需显式调用任何诊断工具即完成修复。")
                else:
                    print_diagnostics("before_model_call 注入后残留诊断", remaining_diags)

        Psep()
        safe_print("PASSED")
        Psep()
    finally:
        await shutdown_lsp()
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
