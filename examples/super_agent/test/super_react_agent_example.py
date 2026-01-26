#!/usr/bin/env python
# coding: utf-8
"""
Super ReAct Agent Example
Demonstrates how to use the SuperReActAgent with custom context management
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
from openjiuwen.core.foundation.llm import ModelConfig
from openjiuwen.core.foundation.llm import BaseModelInfo
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard


# Environment configuration
API_BASE = os.getenv("API_BASE", "https://openrouter.ai/api/v1")
API_KEY = os.getenv("API_KEY", "your_api_key_here")
MODEL_NAME = os.getenv("MODEL_NAME", "anthropic/claude-3.5-sonnet")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "openrouter")
os.environ.setdefault("LLM_SSL_VERIFY", "false")


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
        await example_with_date_tool()
        await example_multi_step_problem()
        await example_context_management()
        await example_streaming()
        await example_token_usage()
        await example_sub_agent_delegation()

        print("\n" + "=" * 70)
        print("All examples completed successfully!")
        print("=" * 70)

    except Exception as e:
        print(f"\n[ERROR] Error occurred: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
