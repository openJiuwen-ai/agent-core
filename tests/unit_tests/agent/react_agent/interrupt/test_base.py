# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentRail, AgentCallbackContext
from openjiuwen.harness.rails.interrupt.confirm_rail import ConfirmInterruptRail

os.environ.setdefault("LLM_SSL_VERIFY", "false")


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


class ReadTool(Tool):
    """Generic read tool for testing"""

    def __init__(self):
        super().__init__(
            ToolCard(
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

    async def invoke(self, inputs, session=None, **kwargs):
        self.invoke_count += 1
        filepath = inputs.get("filepath", "")
        return {"success": True, "content": f"Content of file {filepath}", "invoke_count": self.invoke_count}

    async def stream(self, inputs, **kwargs):
        result = await self.invoke(inputs, **kwargs)
        yield result


class WriteTool(Tool):
    """Generic write tool for testing"""

    def __init__(self):
        super().__init__(
            ToolCard(
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

    async def invoke(self, inputs, session=None, **kwargs):
        self.invoke_count += 1
        filepath = inputs.get("filepath", "")
        content = inputs.get("content", "")
        return {"success": True, "message": f"Written to {filepath}", "invoke_count": self.invoke_count}

    async def stream(self, inputs, **kwargs):
        result = await self.invoke(inputs, **kwargs)
        yield result


class ActionTool(Tool):
    """Generic action tool for testing"""

    def __init__(self, name: str = "action"):
        self._name = name
        super().__init__(
            ToolCard(
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

    async def invoke(self, inputs, session=None, **kwargs):
        self.invoke_count += 1
        action = inputs.get("action", "")
        return {"success": True, "data": f"Execute {self._name}: {action}", "invoke_count": self.invoke_count}

    async def stream(self, inputs, **kwargs):
        result = await self.invoke(inputs, **kwargs)
        yield result


class TraceRail(AgentRail):
    """Rail for verifying tool call flows"""

    def __init__(self, tool_names: Optional[List[str]] = None):
        super().__init__()
        self.tool_names = tool_names
        self.tool_invoke_count: Dict[str, int] = {}

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = ctx.inputs.tool_name if hasattr(ctx.inputs, 'tool_name') else ""
        if tool_name not in self.tool_invoke_count:
            self.tool_invoke_count[tool_name] = 0
        if hasattr(ctx.inputs, 'tool_result') and ctx.inputs.tool_result is not None:
            self.tool_invoke_count[tool_name] += 1

    def get_execution_count(self, tool_name: str) -> int:
        return self.tool_invoke_count.get(tool_name, 0)


async def create_agent_with_tools(
    config: AgentWithToolsConfig,
) -> tuple[ReActAgent, Any, Optional[TraceRail]]:
    """Create Agent with tools"""
    agent = ReActAgent(card=AgentCard(id=f"{config.session_id_prefix}_agent"))
    agent_config = ReActAgentConfig()
    agent_config.configure_model_client(
        provider="OpenAI",
        api_key="sk-fake",
        api_base="https://api.openai.com/v1",
        model_name="gpt-3.5-turbo",
        verify_ssl=False,
    )
    agent_config.configure_prompt_template([
        {"role": "system", "content": config.system_prompt}
    ])
    agent.configure(agent_config)

    for tool in config.tools:
        Runner.resource_mgr.add_tool(tool)
        agent.ability_manager.add(tool.card)

    trace_rail = None
    if config.trace_tool_names:
        trace_rail = TraceRail(tool_names=config.trace_tool_names)
        await agent.register_rail(trace_rail)

    if config.rail_tool_names:
        rail = ConfirmInterruptRail(tool_names=config.rail_tool_names)
        await agent.register_rail(rail)

    session = create_agent_session(
        session_id=f"{config.session_id_prefix}_test",
        card=AgentCard(id=f"{config.session_id_prefix}_agent"),
    )
    return agent, session, trace_rail


async def create_nested_agent(config: NestedAgentConfig) -> ReActAgent:
    """Create nested agent with tools and sub-agents"""
    agent = ReActAgent(card=AgentCard(id=config.agent_id, name=config.agent_name))
    agent_config = ReActAgentConfig()
    agent_config.configure_model_client(
        provider="OpenAI",
        api_key="sk-fake",
        api_base="https://api.openai.com/v1",
        model_name="gpt-3.5-turbo",
        verify_ssl=False,
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


async def create_simple_agent(
    session_id_prefix: str = "test",
    system_prompt: str = "You are an assistant. When the user requests to execute operations, call the read tool.",
    rail_tool_names: Optional[List[str]] = None,
    with_write_tool: bool = False,
) -> tuple[ReActAgent, Any, ReadTool, Optional[WriteTool], Optional[TraceRail]]:
    """Create simple Agent with read tool and optionally write tool"""
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
        )
    )
    
    return agent, session, read_tool, write_tool, trace_rail


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


def confirm_interrupt(tool_call_id: str, auto_confirm: bool = False) -> InteractiveInput:
    """Create InteractiveInput to confirm an interrupt"""
    interactive_input = InteractiveInput()
    interactive_input.update(tool_call_id, {
        "approved": True,
        "feedback": "Confirm",
        "auto_confirm": auto_confirm
    })
    return interactive_input


def reject_interrupt(tool_call_id: str, feedback: str = "Reject") -> InteractiveInput:
    """Create InteractiveInput to reject an interrupt"""
    interactive_input = InteractiveInput()
    interactive_input.update(tool_call_id, {"approved": False, "feedback": feedback})
    return interactive_input
