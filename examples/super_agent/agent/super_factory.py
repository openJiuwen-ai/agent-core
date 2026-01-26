#!/usr/bin/env python
# coding: utf-8
# SPDX-FileCopyrightText: 2025 MiromindAI
# SPDX-License-Identifier: Apache-2.0
"""
Factory functions for creating Super agents
"""

from typing import List, Dict, Optional

from examples.super_agent.agent.super_config import (
    SuperAgentConfig,
    SuperAgentFactory
)
from examples.super_agent.agent.super_react_agent import (
    SuperReActAgent
)
from openjiuwen.core.single_agent import PluginSchema, WorkflowSchema
from openjiuwen.core.foundation.llm import ModelConfig
from openjiuwen.core.foundation.llm import BaseModelInfo as ModelInfo
from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.workflow import Workflow


def create_super_main_agent(
    agent_id: str = "super_main",
    agent_version: str = "1.0",
    description: str = "Super Main Agent",
    model_name: str = "anthropic/claude-3.5-sonnet",
    api_key: str = "",
    api_base: str = "https://openrouter.ai/api/v1",
    system_prompt: str = "",
    max_iteration: int = 20,
    max_tool_calls_per_turn: int = 5,
    tools: List[Tool] = None,
    workflows: List[Workflow] = None,
    sub_agent_configs: Dict[str, SuperAgentConfig] = None,
    enable_o3_hints: bool = False,
    enable_o3_final_answer: bool = False,
    o3_api_key: Optional[str] = None,
    task_guidance: str = ""
) -> SuperReActAgent:
    """
    Create a Super main single_agent

    Args:
        agent_id: Agent ID
        agent_version: Agent version
        description: Agent description
        model_name: LLM model name
        api_key: OpenRouter API key
        api_base: OpenRouter API base URL
        system_prompt: System prompt
        max_iteration: Max iterations for ReAct loop
        max_tool_calls_per_turn: Max tool calls per turn
        tools: List of tools
        workflows: List of workflows
        sub_agent_configs: Sub-single_agent configurations
        enable_o3_hints: Enable O3 hints extraction
        enable_o3_final_answer: Enable O3 final answer extraction
        o3_api_key: OpenAI API key for O3
        task_guidance: Additional guidance for task execution

    Returns:
        SuperReActAgent instance
    """

    # Create model config
    model_info = ModelInfo(
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
        timeout=600
    )

    model_config = ModelConfig(
        model_provider="openrouter",
        model_info=model_info
    )

    # Create prompt template
    prompt_template = [
        {"role": "system", "content": system_prompt}
    ] if system_prompt else []

    # Create tool and workflow schemas
    tool_names = [tool.card.name for tool in (tools or [])]

    plugin_schemas = []
    for tool in (tools or []):
        plugin_schemas.append(PluginSchema(
            id=tool.card.name,
            name=tool.card.name,
            description=getattr(tool, 'description', ''),
            inputs={}
        ))

    workflow_schemas = []
    for workflow in (workflows or []):
        card = workflow.card
        workflow_schemas.append(WorkflowSchema(
            id=card.id,
            name=card.name,
            version=card.version,
            description=card.description
        ))

    # Create main single_agent config
    agent_config = SuperAgentFactory.create_main_agent_config(
        agent_id=agent_id,
        agent_version=agent_version,
        description=description,
        model=model_config,
        prompt_template=prompt_template,
        workflows=workflow_schemas,
        plugins=plugin_schemas,
        tools=tool_names,
        max_iteration=max_iteration,
        max_tool_calls_per_turn=max_tool_calls_per_turn,
        enable_o3_hints=enable_o3_hints,
        enable_o3_final_answer=enable_o3_final_answer,
        o3_api_key=o3_api_key,
        task_guidance=task_guidance
    )

    # Add sub-single_agent configs
    if sub_agent_configs:
        agent_config.sub_agent_configs = sub_agent_configs

    # Create single_agent instance
    main_agent = SuperReActAgent(
        agent_config=agent_config,
        workflows=workflows,
        tools=tools
    )

    return main_agent


def create_super_sub_agent(
    agent_id: str,
    agent_version: str = "1.0",
    description: str = "",
    model_name: str = "anthropic/claude-3.5-sonnet",
    api_key: str = "",
    api_base: str = "https://openrouter.ai/api/v1",
    system_prompt: str = "",
    max_iteration: int = 10,
    max_tool_calls_per_turn: int = 3,
    tools: List[Tool] = None,
    workflows: List[Workflow] = None
) -> SuperReActAgent:
    """
    Create a Super sub-single_agent

    Args:
        agent_id: Agent ID (also used as single_agent type)
        agent_version: Agent version
        description: Agent description
        model_name: LLM model name
        api_key: OpenRouter API key
        api_base: OpenRouter API base URL
        system_prompt: System prompt
        max_iteration: Max iterations for ReAct loop
        max_tool_calls_per_turn: Max tool calls per turn
        tools: List of tools
        workflows: List of workflows

    Returns:
        SuperReActAgent instance
    """

    # Create model config
    model_info = ModelInfo(
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
        timeout=600
    )

    model_config = ModelConfig(
        model_provider="openrouter",
        model_info=model_info
    )

    # Create prompt template
    prompt_template = [
        {"role": "system", "content": system_prompt}
    ] if system_prompt else []

    # Create tool and workflow schemas
    tool_names = [tool.card.name for tool in (tools or [])]

    plugin_schemas = []
    for tool in (tools or []):
        plugin_schemas.append(PluginSchema(
            id=tool.card.name,
            name=tool.card.name,
            description=getattr(tool, 'description', ''),
            inputs={}
        ))

    workflow_schemas = []
    for workflow in (workflows or []):
        workflow_schemas.append(WorkflowSchema(
            id=workflow.card.id,
            name=workflow.card.name,
            version=workflow.card.version,
            description=workflow.card.description
        ))

    # Create sub-single_agent config
    agent_config = SuperAgentFactory.create_sub_agent_config(
        agent_id=agent_id,
        agent_version=agent_version,
        description=description,
        model=model_config,
        prompt_template=prompt_template,
        workflows=workflow_schemas,
        plugins=plugin_schemas,
        tools=tool_names,
        max_iteration=max_iteration,
        max_tool_calls_per_turn=max_tool_calls_per_turn
    )

    # Create single_agent instance
    sub_agent = SuperReActAgent(
        agent_config=agent_config,
        workflows=workflows,
        tools=tools
    )

    return sub_agent


def create_agent_system_with_sub_agents(
    main_agent_params: Dict,
    sub_agent_configs: Dict[str, Dict]
) -> SuperReActAgent:
    """
    Create a complete single_agent system with main single_agent and sub-agents

    Args:
        main_agent_params: Parameters for main single_agent (same as create_super_main_agent)
        sub_agent_configs: Dict mapping agent_name -> sub-single_agent parameters

    Returns:
        Main single_agent with sub-agents registered

    Example:
        main_agent = create_agent_system_with_sub_agents(
            main_agent_params={
                "agent_id": "main",
                "api_key": "...",
                "system_prompt": "...",
                "tools": main_tools,
                "max_iteration": 20
            },
            sub_agent_configs={
                "single_agent-browser": {
                    "agent_id": "single_agent-browser",
                    "description": "Browser single_agent for web search",
                    "api_key": "...",
                    "system_prompt": "...",
                    "tools": browser_tools,
                    "max_iteration": 10
                },
                "single_agent-coder": {
                    "agent_id": "single_agent-coder",
                    "description": "Coding single_agent",
                    "api_key": "...",
                    "system_prompt": "...",
                    "tools": coder_tools,
                    "max_iteration": 10
                }
            }
        )
    """

    # Create sub-agents first
    sub_agents = {}
    for agent_name, sub_params in sub_agent_configs.items():
        sub_agent = create_super_sub_agent(**sub_params)
        sub_agents[agent_name] = sub_agent

    # Create main single_agent
    main_agent = create_super_main_agent(**main_agent_params)

    # Register sub-agents with main single_agent
    for agent_name, sub_agent in sub_agents.items():
        main_agent.register_sub_agent(agent_name, sub_agent)

    return main_agent
