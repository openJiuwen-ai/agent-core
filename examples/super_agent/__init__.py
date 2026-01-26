#!/usr/bin/env python
# coding: utf-8
"""
Super Agent Package
Enhanced ReAct Agent with custom context management
"""

from examples.super_agent.agent.super_config import (
    AgentConstraints,
    SuperAgentConfig
)

from examples.super_agent.agent.super_factory import (
    SuperAgentFactory
)

from examples.super_agent.agent.super_react_agent import (
    SuperReActAgent
)
from examples.super_agent.agent.context_manager import (
    ContextManager
)
from examples.super_agent.agent.o3_handler import (
    O3Handler
)
from examples.super_agent.agent.tool_call_handler import (
    ToolCallHandler
)
from examples.super_agent.agent.prompt_templates import (
    get_summary_prompt,
    get_o3_hints_prompt,
    get_o3_answer_type_prompt,
    get_o3_final_answer_prompt,
    get_task_instruction_prompt,
    get_main_agent_system_prompt,
    get_browsing_agent_system_prompt,
    generate_mcp_system_prompt,
    process_input
)

from examples.super_agent.llm.openrouter_llm import (
    OpenRouterLLM,
    OpenRouterConfig,
    ContextLimitError
)

__all__ = [
    # Config
    "SuperAgentConfig",
    "SuperAgentFactory",
    "AgentConstraints",
    # Agent
    "SuperReActAgent",
    # Managers and Handlers
    "ContextManager",
    "O3Handler",
    "ToolCallHandler",
    # Prompt templates
    "get_summary_prompt",
    "get_o3_hints_prompt",
    "get_o3_answer_type_prompt",
    "get_o3_final_answer_prompt",
    "get_task_instruction_prompt",
    "get_main_agent_system_prompt",
    "get_browsing_agent_system_prompt",
    "generate_mcp_system_prompt",
    "process_input",
    # LLM
    "OpenRouterLLM",
    "OpenRouterConfig",
    "ContextLimitError",
]

