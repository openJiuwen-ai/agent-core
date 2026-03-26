# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentRail, AgentCallbackContext
from openjiuwen.deepagents.rails import ConfirmInterruptRail

API_BASE = os.getenv("API_BASE", "")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen3-coder-flash")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "OpenAI")


@dataclass
class NestedAgentConfig:
    agent_id: str
    agent_name: str
    system_prompt: str
    tools: List[Tool] = field(default_factory=list)
    sub_agent_cards: List[AgentCard] = field(default_factory=list)
    rail_tool_names: List[str] = field(default_factory=list)


@dataclass
class AgentWithToolsConfig:
    tools: List[Tool]
    session_id_prefix: str = "test"
    system_prompt: str = "You are an assistant."
    rail_tool_names: List[str] = field(default_factory=list)
    trace_tool_names: List[str] = field(default_factory=list)
    close_stream_on_post_run: bool = True
    sub_agents: List[ReActAgent] = field(default_factory=list)

__all__ = [
    "ReadTool",
    "WriteTool",
    "ActionTool",
    "FailingTool",
    "TraceRail",
    "create_agent_with_tools",
    "create_simple_agent",
    "create_sub_agent",
    "create_nested_agents",
    "create_nested_agent",
    "NestedAgentConfig",
    "AgentWithToolsConfig",
    "assert_interrupt_result",
    "assert_answer_result",
    "get_tool_name_from_state",
    "get_filepath_from_state",
    "confirm_interrupt",
    "reject_interrupt",
    "API_KEY",
    "API_BASE",
    "MODEL_NAME",
    "MODEL_PROVIDER",
]


async def create_nested_agent(config: NestedAgentConfig) -> ReActAgent:
    """Create nested agent with tools and sub-agents"""
    agent = ReActAgent(card=AgentCard(id=config.agent_id, name=config.agent_name))
    agent_config = ReActAgentConfig()
    agent_config.configure_model_client(
        provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        model_name=MODEL_NAME,
    )
    agent_config.configure_prompt_template([
        {"role": "system", "content": config.system_prompt}
    ])
    agent.configure(agent_config)

    if config.tools:
        for tool in config.tools:
            Runner.resource_mgr.add_tool(tool)
            agent.ability_manager.add(tool.card)

    if config.sub_agent_cards:
        for card in config.sub_agent_cards:
            agent.ability_manager.add(card)

    if config.rail_tool_names:
        rail = ConfirmInterruptRail(tool_names=config.rail_tool_names)
        await agent.register_rail(rail)

    return agent


class ReadTool(Tool):
    """Generic read tool"""

    def __init__(self, tool_id: str = None):
        self._tool_id = tool_id or f"read_{uuid.uuid4().hex[:8]}"
        super().__init__(
            ToolCard(
                id=self._tool_id,
                name="read",
                description="Read file content",
                input_params={
                    "type": "object",
                    "properties": {
                        "filepath": {"description": "File path", "type": "string"},
                    },
                    "required": ["filepath"],
                },
            )
        )
        self.invoke_count = 0
        self.invoke_log: List[Dict[str, Any]] = []

    async def invoke(self, inputs, session=None, **kwargs):
        self.invoke_count += 1
        filepath = inputs.get("filepath", "")
        result = {"success": True, "content": f"Content of file {filepath}", "invoke_count": self.invoke_count}
        self.invoke_log.append({"filepath": filepath, "result": result})
        return result

    async def stream(self, inputs, **kwargs):
        result = await self.invoke(inputs, **kwargs)
        yield result


class WriteTool(Tool):
    """Generic write tool"""

    def __init__(self, tool_id: str = None):
        self._tool_id = tool_id or f"write_{uuid.uuid4().hex[:8]}"
        super().__init__(
            ToolCard(
                id=self._tool_id,
                name="write",
                description="Write file content",
                input_params={
                    "type": "object",
                    "properties": {
                        "filepath": {"description": "File path", "type": "string"},
                        "content": {"description": "Content", "type": "string"},
                    },
                    "required": ["filepath", "content"],
                },
            )
        )
        self.invoke_count = 0
        self.invoke_log: List[Dict[str, Any]] = []

    async def invoke(self, inputs, session=None, **kwargs):
        self.invoke_count += 1
        filepath = inputs.get("filepath", "")
        content = inputs.get("content", "")
        result = {"success": True, "message": f"Written to {filepath}", "invoke_count": self.invoke_count}
        self.invoke_log.append({"filepath": filepath, "content": content, "result": result})
        return result

    async def stream(self, inputs, **kwargs):
        result = await self.invoke(inputs, **kwargs)
        yield result


class ActionTool(Tool):
    """Generic action tool"""

    def __init__(self, name: str = "action", tool_id: str = None):
        self._name = name
        self._tool_id = tool_id or f"{name}_{uuid.uuid4().hex[:8]}"
        super().__init__(
            ToolCard(
                id=self._tool_id,
                name=name,
                description=f"Execute {name} operation",
                input_params={
                    "type": "object",
                    "properties": {
                        "action": {"description": "Operation", "type": "string"},
                    },
                    "required": ["action"],
                },
            )
        )
        self.invoke_count = 0
        self.invoke_log: List[Dict[str, Any]] = []

    async def invoke(self, inputs, session=None, **kwargs):
        self.invoke_count += 1
        action = inputs.get("action", "")
        result = {"success": True, "data": f"Execute {self._name}: {action}", "invoke_count": self.invoke_count}
        self.invoke_log.append({"action": action, "result": result})
        return result

    async def stream(self, inputs, **kwargs):
        result = await self.invoke(inputs, **kwargs)
        yield result


class FailingTool(Tool):
    """Tool that will fail"""

    def __init__(self):
        super().__init__(
            ToolCard(
                name="failing_tool",
                description="Tool that will fail",
                input_params={
                    "type": "object",
                    "properties": {
                        "should_fail": {"description": "Whether should fail", "type": "boolean"},
                    },
                    "required": ["should_fail"],
                },
            )
        )
        self.fail_count = 0

    async def invoke(self, inputs, session=None, **kwargs):
        should_fail = inputs.get("should_fail", False)
        self.fail_count += 1
        if should_fail:
            raise RuntimeError(f"Tool execution failed ({self.fail_count}th call)")
        return {"success": True, "data": f"Successfully executed ({self.fail_count}th call)"}

    async def stream(self, inputs, **kwargs):
        result = await self.invoke(inputs, **kwargs)
        yield result


class TraceRail(AgentRail):
    """Rail for verifying tool call flows
    """

    def __init__(self, tool_names: Optional[List[str]] = None):
        super().__init__()
        self.tool_names = tool_names
        self.tool_execution_records: List[Dict[str, Any]] = []
        self.tool_invoke_count: Dict[str, int] = {}

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = ctx.inputs.tool_name if hasattr(ctx.inputs, 'tool_name') else ""
        tool_result = ctx.inputs.tool_result if hasattr(ctx.inputs, 'tool_result') else None

        self.tool_execution_records.append({
            "tool_name": tool_name,
            "result": tool_result
        })

        if tool_name not in self.tool_invoke_count:
            self.tool_invoke_count[tool_name] = 0
        if tool_result is not None:
            self.tool_invoke_count[tool_name] += 1

    def get_execution_count(self, tool_name: str) -> int:
        return self.tool_invoke_count.get(tool_name, 0)

    def reset(self):
        self.tool_execution_records.clear()
        self.tool_invoke_count.clear()


async def create_agent_with_tools(
        config: AgentWithToolsConfig,
) -> tuple[ReActAgent, Any, Optional[TraceRail]]:
    """Create Agent with tools and optional sub agents
    
    Args:
        config: AgentWithToolsConfig dataclass containing all parameters
    
    Returns:
        (agent, session, trace_rail) tuple
    """
    agent = ReActAgent(card=AgentCard(id=f"{config.session_id_prefix}_agent"))
    agent_config = ReActAgentConfig()
    agent_config.configure_model_client(
        provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        model_name=MODEL_NAME,
    )
    agent_config.configure_prompt_template([
        {"role": "system", "content": config.system_prompt}
    ])
    agent.configure(agent_config)

    for tool in config.tools:
        Runner.resource_mgr.add_tool(tool)
        agent.ability_manager.add(tool.card)

    # Register sub agents as tools
    if config.sub_agents:
        for sub_agent in config.sub_agents:
            # Add sub agent to resource_mgr (使用无参数lambda)
            Runner.resource_mgr.add_agent(sub_agent.card, agent=lambda: sub_agent)
            # Add sub agent card to ability_manager so main agent can call it
            agent.ability_manager.add(sub_agent.card)

    trace_rail = None
    if config.trace_tool_names:
        trace_rail = TraceRail(tool_names=config.trace_tool_names)
        await agent.register_rail(trace_rail)

    if config.rail_tool_names:
        rail = ConfirmInterruptRail(tool_names=config.rail_tool_names)
        await agent.register_rail(rail)

    session = create_agent_session(
        session_id=f"{config.session_id_prefix}_{uuid.uuid4().hex}",
        card=AgentCard(id=f"{config.session_id_prefix}_agent"),
        close_stream_on_post_run=config.close_stream_on_post_run,
    )
    return agent, session, trace_rail


async def create_simple_agent(
        session_id_prefix: str = "test",
        system_prompt: str = "You are an assistant. When the user requests to execute operations, call the read tool.",
        rail_tool_names: Optional[List[str]] = None,
        with_write_tool: bool = False,
        close_stream_on_post_run: bool = True,
) -> tuple[ReActAgent, Any, ReadTool, Optional[WriteTool], Optional[TraceRail]]:
    """Create simple Agent (for most interrupt tests)
    
    Create read tool by default, optionally create write tool.
    """
    read_tool = ReadTool()
    tools: List[Tool] = [read_tool]
    write_tool = None

    if with_write_tool:
        write_tool = WriteTool()
        tools.append(write_tool)

    agent, session, trace_rail = await create_agent_with_tools(
        AgentWithToolsConfig(
            tools=tools,
            session_id_prefix=session_id_prefix,
            system_prompt=system_prompt,
            rail_tool_names=rail_tool_names or ["read"],
            trace_tool_names=[t.card.name for t in tools],
            close_stream_on_post_run=close_stream_on_post_run,
        )
    )

    return agent, session, read_tool, write_tool, trace_rail


async def create_sub_agent(
        agent_id: str,
        agent_name: str,
        system_prompt: str,
        tools: Optional[List[Tool]] = None,
) -> ReActAgent:
    """Create a sub agent that can be used as a tool by parent agent
    
    Args:
        agent_id: Unique agent ID
        agent_name: Agent name (used as tool name)
        system_prompt: System prompt for the agent
        tools: Optional list of tools for the sub agent
    
    Returns:
        Configured ReActAgent instance
    """
    card = AgentCard(
        id=agent_id,
        name=agent_name,
        description=f"Sub agent: {agent_name}",
    )
    agent = ReActAgent(card=card)
    config = ReActAgentConfig()
    config.configure_model_client(
        provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        model_name=MODEL_NAME,
    )
    config.configure_prompt_template([
        {"role": "system", "content": system_prompt}
    ])
    agent.configure(config)

    # Add tools to sub agent if provided
    if tools:
        for tool in tools:
            Runner.resource_mgr.add_tool(tool)
            agent.ability_manager.add(tool.card)

    return agent


def assert_interrupt_result(result: dict, expected_count: int = 1):
    """Verify interrupt result"""
    assert isinstance(result, dict)
    assert result.get("result_type") == "interrupt"
    interrupt_ids = result.get("interrupt_ids", [])
    state_list = result.get("state", [])
    assert len(interrupt_ids) == expected_count, f"Expected {expected_count} interrupts, actual {len(interrupt_ids)}"
    assert len(state_list) == expected_count
    return interrupt_ids, state_list


def assert_answer_result(result: dict):
    """Verify answer result"""
    assert isinstance(result, dict)
    assert result.get("result_type") == "answer"


def get_tool_name_from_state(state_item) -> str:
    """Get tool name from state item"""
    payload = state_item.payload.value if hasattr(state_item, 'payload') else None
    return payload.tool_name if payload and hasattr(payload, 'tool_name') else ""


def get_filepath_from_state(state_item) -> str:
    """Get filepath from state item tool_args"""
    payload = state_item.payload.value if hasattr(state_item, 'payload') else None
    if not payload or not hasattr(payload, 'tool_args'):
        return ""
    try:
        tool_args = json.loads(payload.tool_args) if isinstance(payload.tool_args, str) else payload.tool_args
        return tool_args.get("filepath", "") if isinstance(tool_args, dict) else ""
    except (json.JSONDecodeError, TypeError):
        return ""


def confirm_interrupt(tool_call_id: str, auto_confirm: bool = False) -> "InteractiveInput":
    """Create InteractiveInput to confirm an interrupt"""
    from openjiuwen.core.session import InteractiveInput
    interactive_input = InteractiveInput()
    interactive_input.update(tool_call_id, {
        "approved": True,
        "feedback": "Confirm",
        "auto_confirm": auto_confirm
    })
    return interactive_input


def reject_interrupt(tool_call_id: str, feedback: str = "Reject") -> "InteractiveInput":
    """Create InteractiveInput to reject an interrupt"""
    from openjiuwen.core.session import InteractiveInput
    interactive_input = InteractiveInput()
    interactive_input.update(tool_call_id, {"approved": False, "feedback": feedback})
    return interactive_input


async def create_nested_agents(
        layer_configs: List[Dict[str, Any]],
        rail_tool_names: Optional[List[str]] = None,
) -> tuple[ReActAgent, List[Tool], Optional[TraceRail]]:
    """Create nested agents hierarchy

    Args:
        layer_configs: List of dicts with keys:
            - agent_id: str
            - agent_name: str
            - system_prompt: str
            - tools: Optional[List[Tool]]
            - sub_agent_cards: Optional[List[AgentCard]] - cards of sub-agents to call
        rail_tool_names: Tool names for ConfirmInterruptRail interception

    Returns:
        (main_agent, tools, trace_rail) tuple
    """
    trace_rail = TraceRail()
    all_tools = []

    for i, config in enumerate(layer_configs):
        tools = config.get("tools", [])
        all_tools.extend(tools)

    read_tool = ReadTool()
    all_tools.append(read_tool)

    for config in layer_configs:
        for tool in config.get("tools", []):
            Runner.resource_mgr.add_tool(tool)
        if config.get("tools"):
            pass

    agents = []
    for i, config in enumerate(layer_configs):
        is_leaf = (i == len(layer_configs) - 1)
        agent_tools = [read_tool] if is_leaf else []
        for tool in config.get("tools", []):
            if tool not in agent_tools:
                agent_tools.append(tool)

        agent = ReActAgent(card=AgentCard(
            id=config["agent_id"],
            name=config.get("agent_name", config["agent_id"])
        ))
        config_obj = ReActAgentConfig()
        config_obj.configure_model_client(
            provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            model_name=MODEL_NAME,
        )
        config_obj.configure_prompt_template([
            {"role": "system", "content": config["system_prompt"]}
        ])
        agent.configure(config_obj)

        for tool in agent_tools:
            Runner.resource_mgr.add_tool(tool)
            agent.ability_manager.add(tool.card)

        if rail_tool_names:
            rail = ConfirmInterruptRail(tool_names=rail_tool_names)
            await agent.register_rail(rail)

        await agent.register_rail(trace_rail)

        agents.append(agent)

        card = AgentCard(
            id=config["agent_id"],
            name=config.get("agent_name", config["agent_id"]),
            description=f"Agent: {config['agent_id']}",
            input_params={
                "type": "object",
                "properties": {
                    "query": {"description": "Task description", "type": "string"},
                },
                "required": ["query"],
            },
        )
        Runner.resource_mgr.add_agent(card, agent=lambda a=agent: a)

        if i > 0:
            parent_config = layer_configs[i - 1]
            parent_agent = agents[i - 1]
            parent_agent.ability_manager.add(card)

    main_agent = agents[0]
    return main_agent, all_tools, trace_rail
