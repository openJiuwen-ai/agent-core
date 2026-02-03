#!/usr/bin/env python
# coding: utf-8
"""
Super Agent Configuration
Enhanced ReAct Agent Config with advanced features
"""

from typing import List, Dict, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.single_agent.legacy.schema import PluginSchema, WorkflowSchema
from openjiuwen.core.application.llm_agent import (
    ReActAgentConfig,
    ConstrainConfig
)
from openjiuwen.core.foundation.llm import (
    ModelConfig
)


class AgentConstraints(BaseModel):
    """Agent execution constraints"""
    max_iteration: int = Field(default=10, description="Maximum iterations for ReAct loop")
    max_tool_calls_per_turn: int = Field(default=5, description="Maximum tool calls per turn")
    reserved_max_chat_rounds: int = Field(default=20, description="Reserved max chat rounds for context")

    def to_constrain_config(self) -> ConstrainConfig:
        """Convert to ConstrainConfig for compatibility with ReActAgentConfig"""
        return ConstrainConfig(
            max_iteration=self.max_iteration,
            reserved_max_chat_rounds=self.reserved_max_chat_rounds
        )


class SuperAgentConfig(ReActAgentConfig):
    """
    Enhanced ReAct Agent Config with advanced features:
    - O3 hints and final answer extraction
    - Context limit handling
    - Sub-single_agent support
    """

    # Agent type (main or sub-single_agent name)
    agent_type: str = Field(default="main", description="Agent type: main or sub-single_agent name")

    # O3 integration
    enable_o3_hints: bool = Field(default=False, description="Enable O3 hints extraction")
    enable_o3_final_answer: bool = Field(default=False, description="Enable O3 final answer extraction")
    o3_api_key: Optional[str] = Field(default=None, description="OpenAI API key for O3")

    # Context management
    enable_context_limit_retry: bool = Field(default=True, description="Enable context limit retry with message removal")

    # Tool result keeping
    keep_tool_result: int = Field(default=-1, description="Number of tool results to keep in history (-1 = keep all)")

    # Tool call constraints
    max_tool_calls_per_turn: int = Field(default=5, description="Maximum tool calls per turn")

    # Sub-single_agent configuration (for main single_agent only)
    sub_agent_configs: Dict[str, "SuperAgentConfig"] = Field(
        default_factory=dict,
        description="Sub-single_agent configurations keyed by single_agent name"
    )

    # Guidance text for summary generation
    task_guidance: str = Field(
        default="",
        description="Additional guidance for task execution and summary generation"
    )


class SuperAgentFactory:
    """Factory for creating Super agents"""

    @staticmethod
    def create_main_agent_config(
        agent_id: str,
        agent_version: str,
        description: str,
        model: ModelConfig,
        prompt_template: List[Dict],
        workflows: List[WorkflowSchema] = None,
        plugins: List[PluginSchema] = None,
        tools: List[str] = None,
        max_iteration: int = 20,
        max_tool_calls_per_turn: int = 5,
        enable_o3_hints: bool = False,
        enable_o3_final_answer: bool = False,
        o3_api_key: Optional[str] = None,
        task_guidance: str = ""
    ) -> SuperAgentConfig:
        """Create main single_agent configuration"""

        constraints = AgentConstraints(
            max_iteration=max_iteration,
            max_tool_calls_per_turn=max_tool_calls_per_turn
        )

        return SuperAgentConfig(
            id=agent_id,
            version=agent_version,
            description=description,
            model=model,
            prompt_template=prompt_template,
            workflows=workflows or [],
            plugins=plugins or [],
            tools=tools or [],
            constrain=constraints.to_constrain_config(),  # Convert to ConstrainConfig
            max_tool_calls_per_turn=max_tool_calls_per_turn,
            agent_type="main",
            enable_o3_hints=enable_o3_hints,
            enable_o3_final_answer=enable_o3_final_answer,
            o3_api_key=o3_api_key,
            task_guidance=task_guidance
        )

    @staticmethod
    def create_sub_agent_config(
        agent_id: str,
        agent_version: str,
        description: str,
        model: ModelConfig,
        prompt_template: List[Dict],
        workflows: List[WorkflowSchema] = None,
        plugins: List[PluginSchema] = None,
        tools: List[str] = None,
        max_iteration: int = 10,
        max_tool_calls_per_turn: int = 3
    ) -> SuperAgentConfig:
        """Create sub-single_agent configuration"""

        constraints = AgentConstraints(
            max_iteration=max_iteration,
            max_tool_calls_per_turn=max_tool_calls_per_turn
        )

        return SuperAgentConfig(
            id=agent_id,
            version=agent_version,
            description=description,
            model=model,
            prompt_template=prompt_template,
            workflows=workflows or [],
            plugins=plugins or [],
            tools=tools or [],
            constrain=constraints.to_constrain_config(),  # Convert to ConstrainConfig
            max_tool_calls_per_turn=max_tool_calls_per_turn,
            agent_type=agent_id,  # Use agent_id as agent_type for sub-agents
            enable_o3_hints=False,  # Sub-agents don't use O3
            enable_o3_final_answer=False
        )
