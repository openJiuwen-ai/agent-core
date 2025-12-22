#!/usr/bin/env python
# coding: utf-8
"""
Super ReAct Agent Example
Demonstrates how to use the SuperReActAgent with custom context management
"""

import asyncio
import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# Ensure both repo root and `examples/` are importable
CURRENT_DIR = os.path.dirname(__file__)
REPO_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", ".."))
EXAMPLES_DIR = os.path.abspath(os.path.join(REPO_ROOT, "examples"))

for path in [REPO_ROOT, EXAMPLES_DIR]:
    if path not in sys.path:
        sys.path.insert(0, path)

from examples.super_agent import (
    SuperReActAgent
)
from examples.super_agent import (
    SuperAgentFactory
)
from examples.super_agent import (
    get_main_agent_system_prompt,
    get_browsing_agent_system_prompt
)
from openjiuwen.core.foundation.llm import ModelConfig
from openjiuwen.core.foundation.llm import BaseModelInfo
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard

from openjiuwen.core.protocols.mcp import McpServerConfig
from openjiuwen.core.runner import Runner
from mcp import StdioServerParameters

# Environment configuration
API_BASE = os.getenv("API_BASE", "https://openrouter.ai/api/v1")
API_KEY = os.getenv("API_KEY", "your_api_key_here")
MODEL_NAME = os.getenv("MODEL_NAME", "anthropic/claude-3.5-sonnet")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "openrouter")
os.environ.setdefault("LLM_SSL_VERIFY", "false")

# ===== GAIA dataset file path =====
GAIA_DATASET_FILE_PATH = "examples/super_agent/data/test.jsonl"
GAIA_DATASET = [json.loads(line) for line in open(GAIA_DATASET_FILE_PATH)]
# ===== MCP 工具组与实际 MCP server 的映射 =====

# 每个“tool-*”代表一组 MCP 工具（某个 MCP server 上的所有 tools）
MCP_TOOL_GROUPS = {
    # 主 / 子 single_agent 共用
    "tool-autobrowser": {
        "server_name": "browser-use-server",
        "client_type": "sse",
        "params": "http://127.0.0.1:8930/sse",
    },
    "tool-transcribe": {
        "server_name": "audio-mcp-server",
        "client_type": "sse",
        "params": "http://127.0.0.1:8933/sse",
    },
    "tool-reasoning": {
        "server_name": "reasoning-mcp-server",
        "client_type": "sse",
        "params": "http://127.0.0.1:8934/sse",
    },
    "tool-reading": {
        "server_name": "reading-mcp-server",
        "client_type": "sse",
        "params": "http://127.0.0.1:8935/sse",
    },
    "tool-searching": {
        "server_name": "searching-mcp-server",
        "client_type": "sse",
        "params": "http://127.0.0.1:8936/sse",
    },
    "tool-vqa": {
        "server_name": "vision-mcp-server",
        "client_type": "sse",
        "params": "http://127.0.0.1:8932/sse",
    },
    "tool-code": {
        "server_name": "e2b-python-interpreter",
        "client_type": "sse",
        "params": "http://127.0.0.1:8931/sse",
    },
}


async def build_mcp_tool_groups(agent: SuperReActAgent) -> dict[str, list[LocalFunction]]:
    """
    使用某个 SuperReActAgent 实例，把上面 MCP_TOOL_GROUPS 里的所有 server都注册成 LocalFunction 工具，并按 tool-group 名字归类返回。

    返回：
        {
          "tool-autobrowser": [LocalFunction(...), ...],  # browser-use-server.* MCP 工具
          "tool-transcribe": [...],
          ...
        }
    """
    tool_groups: dict[str, list[LocalFunction]] = {}

    for group_name, cfg in MCP_TOOL_GROUPS.items():
        tools = await agent.create_mcp_tools(
            server_name=cfg["server_name"],
            client_type=cfg["client_type"],
            params=cfg["params"],
        )
        tool_groups[group_name] = tools

    return tool_groups

def create_model_config():
    """Create model configuration for SuperReActAgent"""
    model_info = BaseModelInfo(
        api_key=API_KEY,
        api_base=API_BASE,
        model=MODEL_NAME,
        timeout=60  # Increased timeout for API calls
    )

    return ModelConfig(
        model_provider=MODEL_PROVIDER,
        model_info=model_info
    )


def create_math_tools():
    """Create basic math tools"""
    # Addition tool
    add_tool = LocalFunction(
        card=ToolCard(
            name="add",
            description="Add two numbers together",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"description": "First number", "type": "integer"},
                    "b": {"description": "Second number", "type": "integer"},
                },
                "required": ["a", "b"],
            },
        ),
        func=lambda a, b: a + b,
    )

    # Multiplication tool
    multiply_tool = LocalFunction(
        card=ToolCard(
            name="multiply",
            description="Multiply two numbers together",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"description": "First number", "type": "integer"},
                    "b": {"description": "Second number", "type": "integer"},
                },
                "required": ["a", "b"],
            },
        ),
        func=lambda a, b: a * b,
    )

    # Subtraction tool
    subtract_tool = LocalFunction(
        card=ToolCard(
            name="subtract",
            description="Subtract two numbers",
            parameters={
                "type": "object",
                "properties": {
                    "a": {"description": "First number", "type": "integer"},
                    "b": {"description": "Second number to subtract", "type": "integer"},
                },
                "required": ["a", "b"],
            },
        ),
        func=lambda a, b: a - b,
    )

    return [add_tool, multiply_tool, subtract_tool]


def create_date_tool():
    """Create a tool to get current date"""
    def get_current_date():
        """Get current date in YYYY-MM-DD format"""
        current_datetime = datetime.now()
        return current_datetime.strftime("%Y-%m-%d")

    date_tool = LocalFunction(
        card=ToolCard(
            name="get_current_date",
            description="Get the current date in YYYY-MM-DD format",
        ),
        func=get_current_date,
    )

    return date_tool

# ========= MCP 工具封装成 LocalFunction 的通用方法 =========

def _make_mcp_call_coroutine(server_name: str, tool_name: str):
    """
    为某个 MCP 工具生成一个 coroutine 函数：
    - 入参是工具的参数（**kwargs）
    - 内部通过 Runner.run_tool 调用真正的 MCP 工具
    """
    async def _wrapper(**kwargs):
        tool_id = f"{server_name}.{tool_name}"  # 例如：browser-use-server.browser_navigate
        tool = Runner.resource_mgr.get_tool(tool_id)
        result = await tool.invoke(kwargs)

        # Test 里约定：如果返回 dict 且有 "result" 字段，就用它
        if isinstance(result, dict) and "result" in result:
            return result["result"]
        return result

    return _wrapper


async def _register_mcp_server_as_local_tools(
    server_name: str,
    client_type: str,
    params,
):
    """
    注册一个 MCP server（SSE / stdio / playwright），并把该 server 上所有 tools
    映射成 LocalFunction，返回 List[LocalFunction]，可以直接传给 SuperReActAgent.

    :param server_name: MCP server 名字，如 "browser-use-server"
    :param client_type: "sse" / "stdio" / "playwright"
    :param params: ToolServerConfig.params，对应：
                   - sse: "http://127.0.0.1:8930/sse"
                   - stdio: StdioServerParameters(...)
                   - playwright: url 或 StdioServerParameters
    """

    # 1. 注册 MCP server
    server_cfg = McpServerConfig(
        server_name=server_name,
        params=params,
        client_type=client_type,
    )
    ok_list = await Runner.resource_mgr.add_tool_servers([server_cfg])
    if not ok_list or not ok_list[0].is_ok():
        raise RuntimeError(f"Failed to add MCP server: {server_name}")

    # 2. 用 Runner.list_tools 拿到工具列表（McpToolInfo）
    tool_infos = await Runner.resource_mgr.get_mcp_tool_infos(server_name=server_name)

    local_tools = []

    for info in tool_infos:
        schema = getattr(info, "schema", {}) or {}

        # 4. 为每个 tool 生成自己独立的 coroutine wrapper
        async_func = _make_mcp_call_coroutine(server_name, info.name)

        #  LocalFunction 支持 func 是 async 函数?
        mcp_local_tool = LocalFunction(
            card=ToolCard(
                name=info.name,
                description=getattr(info, "description", "") or f"MCP tool {info.name} from {server_name}",
                parameters=schema,
            ),
            func=async_func,  # 传入的是 async 函数
        )

        local_tools.append(mcp_local_tool)

    return local_tools


# 便捷包装
async def create_sse_mcp_tools(server_name: str, sse_url: str):
    """
    将一个 SSE MCP server 上的所有工具注册为 LocalFunction
    """
    return await _register_mcp_server_as_local_tools(
        server_name=server_name,
        client_type="sse",
        params=sse_url,
    )

async def create_stdio_mcp_tools(server_name: str, command: str, args: list[str]):
    """
    将一个 stdio MCP server 上的所有工具注册为 LocalFunction
    """
    params = StdioServerParameters(command=command, args=args)
    return await _register_mcp_server_as_local_tools(
        server_name=server_name,
        client_type="stdio",
        params=params,
    )

async def example_mcp_main_and_sub_agents(queries: list | None = None):
    """
    Example: 主 Agent + browsing 子 Agent，使用不同 MCP 工具集
    当 queries 传入多个问题时，会在同一次 Runner 生命周期中依次运行，
    每次运行前都会清空上下文，避免串话。
    main_agent:
      tools: [tool-vqa, tool-reading, tool-code, tool-reasoning, tool-transcribe, tool-autobrowser]
    sub_agents:
      single_agent-browsing:
        tools: [tool-searching, tool-vqa, tool-reading, tool-code, tool-autobrowser]
    """
    assert queries is not None, "query is required"
    assert len(queries) > 0, "query is required"
    assert isinstance(queries, list), "queries must be a list"


    print("\n" + "=" * 60)
    print("Example: Super ReAct Agent Test Run")
    print("=" * 60)

    # 启动 Runner
    await Runner.start()

    # 构造 main_agent 配置
    main_agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="super_react_main_mcp",
        agent_version="1.0",
        description="Main MCP single_agent with multiple tool groups",
        model=create_model_config(),
        prompt_template=[
            {
                "role": "system",
                "content": get_main_agent_system_prompt(datetime.now()),
            }
        ],
        max_iteration=20,              
        max_tool_calls_per_turn=8,  # ISSUE: 
        enable_o3_hints=True,
        enable_o3_final_answer=True,
    )

    # 构造子 single_agent 配置
    browsing_agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="single_agent-browsing",
        agent_version="1.0",
        description="Browsing specialist sub-single_agent using searching + browser + vqa + reading + code",
        model=create_model_config(),
        prompt_template=[
            {
                "role": "system",
                "content": get_browsing_agent_system_prompt(datetime.now()),
            }
        ],
        max_iteration=20,             
        max_tool_calls_per_turn=8,
        enable_o3_hints=True,
        enable_o3_final_answer=True,
    )

    # 实例化 main_agent 和 browsing_agent （MCP tools 在后续部分添加）
    main_agent = SuperReActAgent(
        agent_config=main_agent_config,
        tools=None,
        workflows=None,
        
    )

    browsing_agent = SuperReActAgent(
        agent_config=browsing_agent_config,
        tools=None,
        workflows=None,
        
    )

    # 用 main_agent 构建出所有 MCP 工具组（只调用一次）
    tool_groups = await build_mcp_tool_groups(main_agent)
    # tool_groups 形如：
    # {
    #   "tool-autobrowser": [LocalFunction(...), ...],
    #   "tool-transcribe": [...],
    #   "tool-reasoning":  [...],
    #   "tool-reading":    [...],
    #   "tool-searching":  [...],
    #   "tool-vqa":        [...],
    #   "tool-code":       [...],
    # }

    # 把工具分配到 main_agent / browsing_agent

    MAIN_AGENT_TOOL_GROUPS = [
        "tool-vqa",
        "tool-reading",
        "tool-code",
        "tool-reasoning",
        "tool-transcribe",
        "tool-autobrowser",
    ]

    BROWSING_AGENT_TOOL_GROUPS = [
        "tool-searching",
        "tool-vqa",
        "tool-reading",
        "tool-code",
        "tool-autobrowser",
    ]

    # 给 main_agent 添加工具
    for group in MAIN_AGENT_TOOL_GROUPS:
        if group in tool_groups:
            main_agent.add_tools(tool_groups[group])
        else:
            print(f"[WARN] tool group '{group}' not found in MCP_TOOL_GROUPS")

    # 给 browsing_agent 添加工具
    for group in BROWSING_AGENT_TOOL_GROUPS:
        if group in tool_groups:
            browsing_agent.add_tools(tool_groups[group])
        else:
            print(f"[WARN] tool group '{group}' not found in MCP_TOOL_GROUPS")

    # 把 browsing_agent 作为子 single_agent 注册到 main_agent 中
    print("Registering sub-single_agent 'single_agent-browsing' as a tool on main_agent...")
    main_agent.register_sub_agent("single_agent-browsing", browsing_agent)
    print(f"Sub-agents registered on main_agent: {list(main_agent._sub_agents.keys())}")

    results: list[dict] = []

    for idx, query in enumerate(queries, start=1):
        # if not query or not isinstance(query, str):
        #     print(f"[WARN] Skipping invalid query at index {idx - 1}")
        #     continue
        usr_question, usr_file = query
        # 在新问题前清空上下文，确保不会串话
        main_agent._context_manager.clear()
        browsing_agent._context_manager.clear()

        print(f"\nMain query #{idx}:\n{query}\n")

        result = await main_agent.invoke({"query": usr_question, "file_path": usr_file})
        results.append({"query": query, "result": result})

        print(f"Result type: {result.get('result_type', 'unknown')}")
        print(f"Output:\n{result.get('output', 'No output')}")

        main_history = main_agent._context_manager.get_history()
        browsing_history = browsing_agent._context_manager.get_history()
        print(f"\nMain single_agent context messages: {len(main_history)}")
        print(f"Browsing sub-single_agent context messages: {len(browsing_history)}")

    # 资源清理（MCP server & Runner）
    # 如果想显式移除所有 server，可以逐个 remove
    for server_name in {cfg["server_name"] for cfg in MCP_TOOL_GROUPS.values()}:
        try:
            await Runner.resource_mgr.remove_mcp_server(server_name)
        except RuntimeError as e:
            if "cancel scope" in str(e):
                print(f"Ignoring SSE shutdown error for {server_name}: {e}")
            else:
                raise

    try:
        await Runner.stop()
    except RuntimeError as e:
        if "cancel scope" in str(e):
            print("Ignore MCP SSE shutdown RuntimeError during Runner.stop:", e)
        else:
            raise

    return results

async def main():
    """Main function to run all examples"""
    print("\n" + "=" * 70)
    print("Super ReAct Agent Test Run")
    print("=" * 70)
    print("This test run runs a main single_agent and a browsing sub-single_agent with different MCP tool groups.")
    print("The intention is to run whatever questions you want to test on the GAIA dataset.")
    print("\nMake sure your API configuration is correct before running.")
    try:
        gaia_queries = []
        for entry in GAIA_DATASET:
            task, file = entry.get("task_question"), entry.get("file_path")
            gaia_queries.append((task, file))
    
        if not gaia_queries:
            print("[WARN] No valid questions found in GAIA dataset, falling back to default sample query.")
        results = await example_mcp_main_and_sub_agents(gaia_queries)

        for item in results:
            print("\n" + "=" * 70)
            print(f"Query: {item.get('query', 'Unknown query')}")
            print(f"Result: {item.get('result')}")
        print("\n" + "=" * 70)
        print("All queries completed successfully!")
        print("=" * 70)

    except Exception as e:
        print(f"\n[ERROR] Error occurred: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
