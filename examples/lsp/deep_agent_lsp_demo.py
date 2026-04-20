# -*- coding: utf-8 -*-
"""

本示例演示如何在 DeepAgent 中集成 LspRail（以pyright为例），通过 LSP 协议实现：
1. goToDefinition        — 跳转到函数/类定义
2. findReferences       — 查找符号的所有引用位置
3. documentSymbol       — 列出当前文件中的所有符号
4. workspaceSymbol      — 在整个工作区中搜索符号
5. goToImplementation   — 跳转到接口/抽象类的实现
6. prepareCallHierarchy — 准备调用层次结构
7. incomingCalls        — 查找调用当前符号的调用者
8. outgoingCalls       — 查找当前符号调用的下游符号

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

from openjiuwen.core.foundation.llm import init_model
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.lsp_rail import LspRail
from openjiuwen.harness.lsp import shutdown_lsp, InitializeOptions

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

P = lambda msg: print(msg, flush=True)
Psep = lambda: print("=" * 70, flush=True)

# ============================================================
# 8 个查询定义
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
]


async def main():
    P("=" * 70)
    P("DeepAgent + LspRail 完整示例 — 8 种 LSP 操作演示")
    P("=" * 70)
    P(f"被分析代码: {SAMPLE_CODE}")

    if not SAMPLE_CODE.exists():
        P(f"错误: 示例代码目录不存在: {SAMPLE_CODE}")
        return

    await Runner.start()

    model = init_model(
        provider="OpenAI",
        model_name=_MODEL_NAME,
        api_key=_API_KEY,
        api_base=_API_BASE,
        verify_ssl=False,
    )
    card = AgentCard(
        name="lsp_demo",
        description="具备 LSP 代码导航能力的 AI 编程助手",
    )
    agent = create_deep_agent(
        model=model,
        card=card,
        workspace=str(SAMPLE_CODE),
        rails=[LspRail(options=InitializeOptions(cwd=str(SAMPLE_CODE)))],
        max_iterations=10,
        language="cn",
    )

    try:
        for demo in DEMO_QUERIES:
            Psep()
            P(f"演示 {demo['id']}：{demo['name']} — {demo['description']}")
            Psep()
            P(f"\n[用户指令]\n{demo['query']}\n")
            P("-" * 70)

            try:
                result = await Runner.run_agent(agent, {"query": demo["query"]})
                output = result.get("output", "") if isinstance(result, dict) else str(result)
                print(f"[Agent 输出]\n{output}")
            except Exception as e:
                P(f"[错误] {e}")
                import traceback
                traceback.print_exc()

        Psep()
        P("PASSED")
        Psep()
    finally:
        await shutdown_lsp()
        await Runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
