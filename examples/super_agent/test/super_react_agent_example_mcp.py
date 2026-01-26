#!/usr/bin/env python
# coding: utf-8
"""
Super ReAct Agent Example
Demons
trates how to use the SuperReActAgent with custom context management
"""

import asyncio
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

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner
from mcp import StdioServerParameters

# Environment configuration
API_BASE = os.getenv("API_BASE", "https://openrouter.ai/api/v1")
API_KEY = os.getenv("API_KEY", "your_api_key_here")
MODEL_NAME = os.getenv("MODEL_NAME", "anthropic/claude-3.5-sonnet")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "openrouter")
os.environ.setdefault("LLM_SSL_VERIFY", "false")


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

async def example_basic_calculation():
    """Example 1: Basic calculation using SuperReActAgent"""
    print("\n" + "=" * 60)
    print("Example 1: Basic Calculation with SuperReActAgent")
    print("=" * 60)

    # Create single_agent configuration using SuperAgentFactory
    agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="super_react_agent_calc",
        agent_version="1.0",
        description="Math calculator single_agent with super features",
        model=create_model_config(),
        prompt_template=[
            {"role": "system", "content": "You are a helpful math assistant. Use the available tools to perform calculations accurately."}
        ],
        max_iteration=15,
        max_tool_calls_per_turn=5,
        enable_o3_hints=False,
        enable_o3_final_answer=False
    )

    # Create SuperReActAgent
    agent = SuperReActAgent(
        agent_config=agent_config,
        tools=create_math_tools(),
        workflows=None,
        
    )

    # Run single_agent (no Runner needed - direct invoke)
    result = await agent.invoke({
        "query": "Calculate 15 + 27, then multiply the result by 3"
    })

    print(f"Query: Calculate 15 + 27, then multiply the result by 3")
    print(f"Result type: {result.get('result_type', 'unknown')}")
    print(f"Output: {result.get('output', 'No output')}")

    # Check context manager state
    history = agent._context_manager.get_history()
    print(f"\nContext: {len(history)} messages in history")


async def example_with_date_tool():
    """Example 2: Using date tool"""
    print("\n" + "=" * 60)
    print("Example 2: Date Query with SuperReActAgent")
    print("=" * 60)

    # Create single_agent configuration
    agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="super_react_agent_date",
        agent_version="1.0",
        description="Agent with date capabilities",
        model=create_model_config(),
        prompt_template=[
            {"role": "system", "content": "You are a helpful assistant that can provide date information."}
        ],
        max_iteration=10,
        max_tool_calls_per_turn=3,
        enable_o3_hints=False,
        enable_o3_final_answer=False
    )

    # Create single_agent
    agent = SuperReActAgent(
        agent_config=agent_config,
        tools=[create_date_tool()],
        workflows=None,
        
    )

    # Run single_agent
    result = await agent.invoke({
        "query": "What is today's date?"
    })

    print(f"Query: What is today's date?")
    print(f"Result type: {result.get('result_type', 'unknown')}")
    print(f"Output: {result.get('output', 'No output')}")


async def example_multi_step_problem():
    """Example 3: Multi-step problem solving"""
    print("\n" + "=" * 60)
    print("Example 3: Multi-Step Problem Solving")
    print("=" * 60)

    # Create single_agent configuration
    agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="super_react_agent_multi",
        agent_version="1.0",
        description="Agent for complex problem solving",
        model=create_model_config(),
        prompt_template=[
            {"role": "system", "content": "You are a problem-solving assistant. Break down complex problems into steps and use tools to solve them efficiently."}
        ],
        max_iteration=20,
        max_tool_calls_per_turn=6,
        enable_o3_hints=False,
        enable_o3_final_answer=False
    )

    # Create single_agent with all tools
    all_tools = create_math_tools() + [create_date_tool()]
    agent = SuperReActAgent(
        agent_config=agent_config,
        tools=all_tools,
        workflows=None,
        
    )

    # Run single_agent with a complex query
    result = await agent.invoke({
        "query": "I have 100 dollars. I spend 35 dollars, then I multiply what's left by 2. How much money do I have now?"
    })

    print(f"Query: I have 100 dollars. I spend 35 dollars, then I multiply what's left by 2. How much money do I have now?")
    print(f"Result type: {result.get('result_type', 'unknown')}")
    print(f"Output: {result.get('output', 'No output')}")


async def example_context_management():
    """Example 4: Demonstrating context management"""
    print("\n" + "=" * 60)
    print("Example 4: Context Management")
    print("=" * 60)

    # Create single_agent configuration
    agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="super_react_agent_context",
        agent_version="1.0",
        description="Agent demonstrating context management",
        model=create_model_config(),
        prompt_template=[
            {"role": "system", "content": "You are a helpful assistant with memory of previous conversations."}
        ],
        max_iteration=10,
        max_tool_calls_per_turn=3,
        enable_o3_hints=False,
        enable_o3_final_answer=False
    )

    # Create single_agent
    agent = SuperReActAgent(
        agent_config=agent_config,
        tools=create_math_tools(),
        workflows=None,
        
    )

    # Clear context to start fresh
    agent._context_manager.clear()

    # First conversation turn
    print("\nTurn 1:")
    result1 = await agent.invoke({
        "query": "Calculate 10 + 5 and remember this result"
    })
    print(f"Output: {result1.get('output', 'No output')}")

    # Second conversation turn - tests context memory
    print("\nTurn 2:")
    result2 = await agent.invoke({
        "query": "What was the result from my previous calculation?"
    })
    print(f"Output: {result2.get('output', 'No output')}")

    # Check context state
    history = agent._context_manager.get_history()
    print(f"\nTotal messages in context: {len(history)}")
    print(f"Message roles: {[msg['role'] for msg in history]}")


async def example_streaming():
    """Example 5: Streaming responses"""
    print("\n" + "=" * 60)
    print("Example 5: Streaming Responses")
    print("=" * 60)

    # Create single_agent configuration
    agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="super_react_agent_stream",
        agent_version="1.0",
        description="Agent with streaming support",
        model=create_model_config(),
        prompt_template=[
            {"role": "system", "content": "You are a helpful math assistant."}
        ],
        max_iteration=10,
        max_tool_calls_per_turn=3,
        enable_o3_hints=False,
        enable_o3_final_answer=False
    )

    # Create single_agent
    agent = SuperReActAgent(
        agent_config=agent_config,
        tools=create_math_tools(),
        workflows=None,
        
    )

    # Stream single_agent responses
    print("Query: Calculate 25 + 15")
    print("Streaming chunks:")
    async for chunk in agent.stream({
        "query": "Calculate 25 + 15"
    }):
        print(f"  Chunk: {chunk}")


async def example_token_usage():
    """Example 6: Token usage tracking"""
    print("\n" + "=" * 60)
    print("Example 6: Token Usage Tracking")
    print("=" * 60)

    # Create single_agent configuration
    agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="super_react_agent_tokens",
        agent_version="1.0",
        description="Agent for token usage demo",
        model=create_model_config(),
        prompt_template=[
            {"role": "system", "content": "You are a helpful assistant."}
        ],
        max_iteration=10,
        max_tool_calls_per_turn=3,
        enable_o3_hints=False,
        enable_o3_final_answer=False
    )

    # Create single_agent
    agent = SuperReActAgent(
        agent_config=agent_config,
        tools=create_math_tools(),
        workflows=None,
        
    )

    # Run a simple calculation
    result = await agent.invoke({
        "query": "What is 50 + 25?"
    })

    print(f"Output: {result.get('output', 'No output')}")

    # Get token usage summary
    llm = agent._get_llm()
    summary_lines, log_string = llm.format_token_usage_summary()

    print("\nToken Usage Summary:")
    for line in summary_lines:
        print(f"  {line}")

    if log_string:
        print("\nDetailed Log:")
        print(log_string)


async def example_sub_agent_delegation():
    """Example 7: Sub-single_agent registration and task delegation"""
    print("\n" + "=" * 60)
    print("Example 7: Sub-Agent Registration and Task Delegation")
    print("=" * 60)

    # Create sub-single_agent configuration (specialized math single_agent)
    sub_agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="single_agent-math-specialist",
        agent_version="1.0",
        description="Specialized math single_agent for complex calculations",
        model=create_model_config(),
        prompt_template=[
            {"role": "system", "content": "You are a specialized math single_agent. Perform calculations accurately using the available tools and provide detailed results."}
        ],
        max_iteration=10,
        max_tool_calls_per_turn=5,
        enable_o3_hints=False,
        enable_o3_final_answer=False
    )

    # Create sub-single_agent with math tools
    sub_agent = SuperReActAgent(
        agent_config=sub_agent_config,
        tools=create_math_tools(),
        workflows=None,
    )

    # Create main single_agent configuration (orchestrator)
    main_agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="super_react_orchestrator",
        agent_version="1.0",
        description="Main orchestrator single_agent that delegates tasks to specialized sub-agents",
        model=create_model_config(),
        prompt_template=[
            {"role": "system", "content": "You are an orchestrator single_agent. When asked to perform mathematical calculations, delegate to the single_agent-math-specialist sub-single_agent. Provide the sub-single_agent with a clear task description."}
        ],
        max_iteration=15,
        max_tool_calls_per_turn=3,
        enable_o3_hints=False,
        enable_o3_final_answer=False
    )

    # Create main single_agent (no direct math tools - will use sub-single_agent)
    main_agent = SuperReActAgent(
        agent_config=main_agent_config,
        tools=[create_date_tool()],  # Only has date tool, not math
        workflows=None,
    )

    # Register sub-single_agent as a tool
    print("Registering sub-single_agent 'single_agent-math-specialist' as a tool...")
    main_agent.register_sub_agent("single_agent-math-specialist", sub_agent)

    # Verify sub-single_agent is registered
    print(f"Sub-agents registered: {list(main_agent._sub_agents.keys())}")

    # Run main single_agent with a task that requires delegation
    print("\nQuery: Calculate (25 + 15) * 3 using the math specialist")
    result = await main_agent.invoke({
        "query": "Calculate (25 + 15) * 3. Use the math specialist sub-single_agent for this calculation."
    })

    print(f"Result type: {result.get('result_type', 'unknown')}")
    print(f"Output: {result.get('output', 'No output')}")

    # Check main single_agent's context
    main_history = main_agent._context_manager.get_history()
    print(f"\nMain single_agent context: {len(main_history)} messages")

    # Check sub-single_agent's context
    sub_history = sub_agent._context_manager.get_history()
    print(f"Sub-single_agent context: {len(sub_history)} messages")

async def example_mcp_integration():
    """Example 8: MCP 工具集成到 SuperReActAgent"""
    print("\n" + "=" * 60)
    print("Example 8: MCP Tools via SuperReActAgent")
    print("=" * 60)

    # ------ 1. 启动 Runner ------
    await Runner.start()

    # ------ 2. 构造一个带 MCP 能力的 SuperReActAgent ------

    agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id="super_react_agent_mcp",
        agent_version="1.0",
        description="Super single_agent with MCP tool capabilities",
        model=create_model_config(),
        prompt_template=[
            {
                "role": "system",
                "content": (
                    "You are a web-browsing and checking assistant.\n"
                    "You have access to MCP tools such as browser_navigate, browser_click, "
                    "browser_extract_text, doubter, checker, etc. "
                    "Use these tools when you need to browse or verify actions."
                ),
            }
        ],
        max_iteration=15,
        max_tool_calls_per_turn=8,
        enable_o3_hints=False,
        enable_o3_final_answer=False,
    )

    agent = SuperReActAgent(agent_config)
    
    # ------ 3. 注册 MCP servers 并获取 LocalFunction 工具 ------

    # 3.1 SSE 模式的 browser-use-server
    tool_autobrowser = await agent.create_mcp_tools(
        server_name="browser-use-server",
        client_type="sse",
        params="http://127.0.0.1:8930/sse",
    )
    tool_transcribe = await agent.create_mcp_tools(
        server_name="audio-mcp-server",
        client_type="sse",
        params="http://127.0.0.1:8933/sse",
    )
    tool_reasoning = await agent.create_mcp_tools(
        server_name="reasoning-mcp-server",
        client_type="sse",
        params="http://127.0.0.1:8934/sse",
    )
    tool_reading = await agent.create_mcp_tools(
        server_name="reading-mcp-server",
        client_type="sse",
        params="http://127.0.0.1:8935/sse",
    )
    tool_searching = await agent.create_mcp_tools(
        server_name="searching-mcp-server",
        client_type="sse",
        params="http://127.0.0.1:8936/sse",
    )
    tool_vqa = await agent.create_mcp_tools(
        server_name="vision-mcp-server",
        client_type="sse",
        params="http://127.0.0.1:8932/sse",
    )
    tool_code = await agent.create_mcp_tools(
        server_name="e2b-python-interpreter",
        client_type="sse",
        params="http://127.0.0.1:8931/sse",
    )
    

    # # 3.2 stdio 模式的 doubter-mcp-server
    # doubter_tools = await single_agent.create_mcp_tools(
    #     server_name="doubter-mcp-server",
    #     client_type="stdio",
    #     params=StdioServerParameters(
    #         command=sys.executable,
    #         args=["-m", "mcp_entrypoint"], 
    #     ),
    # )

    agent.add_tools(tool_autobrowser) 

    # ------ 4. 发起一个会触发 MCP 调用的 query ------
    query = (
        "Please navigate to https://example.com using the browser_navigate MCP tool, "
        "then tell me the page title. If needed, you can also use browser_extract_text."
    )

    print(f"\nQuery: {query}\n")

    result = await agent.invoke({"query": query})

    print(f"Result type: {result.get('result_type', 'unknown')}")
    print(f"Output: {result.get('output', 'No output')}")

    # ------ 5.清理 MCP server 资源 ------
    await Runner.resource_mgr.remove_mcp_server("browser-use-server")
    # await tool_mgr.remove_tool_server("doubter-mcp-server")

    try:
        await Runner.stop()
    except RuntimeError as e:
        if "cancel scope" in str(e):
            print("Ignore MCP SSE shutdown RuntimeError during Runner.stop: %s", e)
        else:
            raise


async def example_mcp_main_and_sub_agents():
    """
    Example 9: 主 Agent + browsing 子 Agent，使用不同 MCP 工具集
    main_agent:
      tools: [tool-vqa, tool-reading, tool-code, tool-reasoning, tool-transcribe, tool-autobrowser]
    sub_agents:
      single_agent-browsing:
        tools: [tool-searching, tool-vqa, tool-reading, tool-code, tool-autobrowser]
    """
    print("\n" + "=" * 60)
    print("Example 9: MCP Main Agent + Browsing Sub-Agent")
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
        max_tool_calls_per_turn=8,
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
    print("--------- main single_agent system prompt ---------")
    print(get_main_agent_system_prompt(datetime.now()))
    print("--------- browsing single_agent system prompt ---------")
    print(get_browsing_agent_system_prompt(datetime.now()))

    # 实例化 main_agent 和 browsing_agent（暂时都不加 MCP 工具）
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

    query = (
    "A porterhouse by any other name is centered around a letter. What does Three Dog Night think about the first natural number that starts with that letter? Give the first line from the lyrics that references it."
    )
    print(f"\nMain query:\n{query}\n")

    result = await main_agent.invoke({"query": query})
    print(f"Result type: {result.get('result_type', 'unknown')}")
    print(f"Output:\n{result.get('output', 'No output')}")

    # 9.查看两边的上下文长度
    main_history = main_agent._context_manager.get_history()
    browsing_history = browsing_agent._context_manager.get_history()
    print(f"\nMain single_agent context messages: {len(main_history)}")
    print(f"Browsing sub-single_agent context messages: {len(browsing_history)}")

    # 10. 资源清理（MCP server & Runner）
    # 如果想显式移除所有 server，可以逐个 remove
    for server_name in {
        cfg["server_name"] for cfg in MCP_TOOL_GROUPS.values()
    }:
        await Runner.resource_mgr.remove_mcp_server(server_name)

    try:
        await Runner.stop()
    except RuntimeError as e:
        if "cancel scope" in str(e):
            print("Ignore MCP SSE shutdown RuntimeError during Runner.stop:", e)
        else:
            raise

async def main():
    """Main function to run all examples"""
    print("\n" + "=" * 70)
    print("Super ReAct Agent Examples")
    print("=" * 70)
    print("\nThese examples demonstrate the SuperReActAgent features:")
    print("1. Basic calculation with multiple tool calls")
    print("2. Using date tools")
    print("3. Multi-step problem solving")
    print("4. Context management and conversation memory")
    print("5. Streaming responses")
    print("6. Token usage tracking")
    print("7. Sub-single_agent registration and task delegation")
    print("\nKey Features:")
    print("- Custom context management (no ContextEngine dependency)")
    print("- Direct invoke (no Runner required)")
    print("- Enhanced tool handling")
    print("- Context limit handling")
    print("- Sub-single_agent registration as tools")
    print("\nMake sure your API configuration is correct before running.")
    try:
        # Run examples
        await example_basic_calculation()
        # await example_with_date_tool()
        # await example_multi_step_problem()
        # await example_context_management()
        # await example_streaming()
        # await example_token_usage()
        # await example_sub_agent_delegation()
        # await example_mcp_integration()
        # await example_mcp_main_and_sub_agents()
        
        print("\n" + "=" * 70)
        print("All examples completed successfully!")
        print("=" * 70)

    except Exception as e:
        print(f"\n[ERROR] Error occurred: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
