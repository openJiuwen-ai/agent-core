# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Contract tests for stable progressive tool discovery and execution.

The model sees a stable top-level tool surface consisting of direct tools plus
the fixed ``tool_search`` and ``tool_call`` wrappers. Search returns complete
JSON Schemas, but result tools are never dynamically added to that surface.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.foundation.tool import ToolCard, ToolExposure, ToolInfo
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ModelCallInputs,
    ToolCallInputs,
)
from openjiuwen.harness.prompts.builder import PromptSection, SystemPromptBuilder
from openjiuwen.harness.rails.progressive_tool_rail import ProgressiveToolRail
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.tools.tool_discovery.tool_call import ToolCallTool
from openjiuwen.harness.tools.tool_discovery.tool_search import ToolSearchTool


FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "when": {
            "type": "string",
            "description": "When the reminder should run",
        },
        "text": {
            "type": "string",
            "description": "Reminder text",
        },
    },
    "required": ["when", "text"],
    "additionalProperties": False,
}


class _FakeSession:
    def __init__(self, state: dict[str, Any] | None = None):
        self._state = dict(state or {})

    def get_state(self, key):
        return self._state.get(key)

    def update_state(self, updates):
        self._state.update(updates)


class _CapturingAbilityManager:
    """Small ability-manager double that records rail registration."""

    def __init__(self):
        self.registered: dict[str, tuple[Any, Any]] = {}
        self.cards: list[Any] = []
        self.registry_revision = 0
        self.executed: list[Any] = []

    def add_ability(self, card, tool):
        self.registered[card.name] = (card, tool)
        self.cards.append(card)
        self.registry_revision += 1
        return SimpleNamespace(added=True)

    def get(self, name):
        item = self.registered.get(name)
        if item:
            return item[0]
        return next((card for card in self.cards if card.name == name), None)

    async def execute(self, ctx, tool_call, session, parallel_tool_calls=False):
        self.executed.append(tool_call)
        return [
            (
                {"jobs": []},
                ToolMessage(
                    content="{\"jobs\": []}",
                    tool_call_id=tool_call.id,
                ),
            )
        ]

    def remove_ability(self, name):
        self.registered.pop(name, None)

    def list(self):
        return list(self.cards)


class _TestableProgressiveToolRail(ProgressiveToolRail):
    def seed_cached_tools(self, *, all_tool_infos):
        self._cached_all_tool_infos = list(all_tool_infos)
        for tool in all_tool_infos:
            self._agent_manager.cards.append(
                ToolCard(
                    id=tool.name,
                    name=tool.name,
                    description=tool.description,
                    input_params=tool.parameters,
                    exposure=ToolExposure.DEFERRED,
                )
            )
        self._agent_manager.registry_revision += len(all_tool_infos)


def _agent(ability_manager):
    return SimpleNamespace(
        card=SimpleNamespace(id="test-agent"),
        ability_manager=ability_manager,
    )


def _rail_and_agent():
    rail = _TestableProgressiveToolRail(
        DeepAgentConfig(progressive_tool_enabled=True, language="cn")
    )
    manager = _CapturingAbilityManager()
    agent = _agent(manager)
    rail._agent_manager = manager
    rail.init(agent)
    return rail, agent, manager


def test_init_registers_fixed_search_and_call_meta_tools():
    """Both meta tools are registered once and remain model-visible."""

    rail, _agent_instance, manager = _rail_and_agent()

    assert list(manager.registered) == ["tool_search", "tool_call"]
    assert rail._meta_tool_names == {"tool_search", "tool_call"}

    card, _tool = manager.registered["tool_search"]
    assert set(card.input_params["properties"]) == {"query", "limit"}
    assert "query" in card.input_params["required"]
    assert card.input_params["properties"]["limit"]["default"] == 5

    call_card, call_tool = manager.registered["tool_call"]
    assert isinstance(call_tool, ToolCallTool)
    assert set(call_card.input_params["properties"]) == {"name", "args"}
    assert call_card.input_params["required"] == ["name", "args"]
    assert call_card.exposure is ToolExposure.DIRECT


@pytest.mark.asyncio
async def test_tool_search_returns_complete_schema_in_results():
    """A search result contains the exact callable schema, not a summary."""

    rail, _agent_instance, manager = _rail_and_agent()
    rail.seed_cached_tools(
        all_tool_infos=[
            ToolInfo(
                name="cron_create_job",
                description="Create a calendar reminder",
                parameters=FULL_SCHEMA,
            ),
            ToolInfo(
                name="slack_send_message",
                description="Send a Slack message",
                parameters={"type": "object", "properties": {}},
            ),
        ]
    )

    search_tool = manager.registered["tool_search"][1]
    output = await search_tool.invoke(
        {"query": "calendar", "limit": 5},
        session=_FakeSession(),
    )

    assert output.success is True
    assert output.data == {
        "query": "calendar",
        "results": [
            {
                "name": "cron_create_job",
                "description": "Create a calendar reminder",
                "parameters": FULL_SCHEMA,
            }
        ],
        "count": 1,
    }


@pytest.mark.asyncio
async def test_tool_search_allows_model_limit_and_clamps_it():
    """The model may choose a limit, bounded by the server safety range."""

    received_limits: list[int] = []

    async def search_tools(query, limit, session):
        received_limits.append(limit)
        return [{"name": str(index)} for index in range(30)]

    search_tool = ToolSearchTool(search_tools=search_tools, result_limit=25)
    output = await search_tool.invoke(
        {"query": "calendar", "limit": 1},
        session=_FakeSession(),
    )

    assert output.success is True
    assert received_limits == [1]
    assert output.data["count"] == 1


@pytest.mark.asyncio
async def test_tool_search_uses_configured_default_when_model_omits_limit():
    received_limits: list[int] = []

    async def search_tools(query, limit, session):
        received_limits.append(limit)
        return [{"name": str(index)} for index in range(30)]

    search_tool = ToolSearchTool(search_tools=search_tools, result_limit=25)
    output = await search_tool.invoke(
        {"query": "calendar"},
        session=_FakeSession(),
    )

    assert output.success is True
    assert received_limits == [20]
    assert output.data["count"] == 20


@pytest.mark.asyncio
async def test_before_model_call_keeps_fixed_meta_tools_and_direct_tools_only():
    """Search results do not dynamically expand model-visible tools."""

    rail, _agent_instance, _manager = _rail_and_agent()
    rail.seed_cached_tools(
        all_tool_infos=[
            ToolInfo(name="cron_create_job", description="Create a reminder"),
            ToolInfo(name="hidden_tool", description="Not visible yet"),
        ]
    )

    builder = SystemPromptBuilder(language="cn")
    builder.add_section(
        PromptSection(
            name="identity",
            content={"cn": "Base system prompt.", "en": "Base system prompt."},
        )
    )
    ctx = AgentCallbackContext(
        agent=SimpleNamespace(
            system_prompt_builder=builder,
            ability_manager=_manager,
        ),
        inputs=ModelCallInputs(
            tools=[
                ToolInfo(name="tool_search", description="Search the tool registry"),
                ToolInfo(name="tool_call", description="Execute a search result"),
                ToolInfo(name="cron_create_job", description="Create a reminder"),
                ToolInfo(name="hidden_tool", description="Not visible yet"),
            ]
        ),
        session=_FakeSession(),
    )

    await rail.before_model_call(ctx)

    assert [tool.name for tool in ctx.inputs.tools] == ["tool_search", "tool_call"]


@pytest.mark.asyncio
async def test_search_result_requires_tool_call_wrapper_and_unknown_name_is_rejected():
    """A searched result runs through the fixed wrapper, not a direct call."""

    rail, agent, manager = _rail_and_agent()
    rail.seed_cached_tools(
        all_tool_infos=[
            ToolInfo(
                name="cron_create_job",
                description="Create a calendar reminder",
                parameters=FULL_SCHEMA,
            )
        ]
    )
    session = _FakeSession()
    search_tool = manager.registered["tool_search"][1]

    search_output = await search_tool.invoke(
        {"query": "calendar", "limit": 1}, session=session
    )
    assert search_output.success is True

    call_tool = manager.registered["tool_call"][1]
    wrapper_ctx = AgentCallbackContext(
        agent=agent,
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id="wrapper-call"),
            tool_name="tool_call",
            tool_args={
                "name": "cron_create_job",
                "args": {"when": "tomorrow", "text": "call mom"},
            },
        ),
        session=session,
    )
    wrapper_output = await call_tool.invoke(
        wrapper_ctx.inputs.tool_args,
        session=session,
        _tool_callback_context=wrapper_ctx,
    )
    assert wrapper_output.success is True
    assert wrapper_output.data == {
        "name": "cron_create_job",
        "result": {"jobs": []},
    }
    assert manager.executed[-1].name == "cron_create_job"
    assert manager.executed[-1].arguments == '{"when": "tomorrow", "text": "call mom"}'

    direct_ctx = AgentCallbackContext(
        agent=agent,
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id="direct-call"),
            tool_name="cron_create_job",
            tool_args={"when": "tomorrow", "text": "call mom"},
        ),
        session=session,
    )
    await rail.before_tool_call(direct_ctx)
    assert direct_ctx.extra.get("_skip_tool") is True

    rejected_ctx = AgentCallbackContext(
        agent=agent,
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id="unknown-call"),
            tool_name="unknown_tool",
            tool_args={},
        ),
        session=session,
    )
    await rail.before_tool_call(rejected_ctx)

    assert rejected_ctx.extra.get("_skip_tool") is True
    assert isinstance(rejected_ctx.inputs.tool_msg, ToolMessage)
    assert rejected_ctx.inputs.tool_msg.tool_call_id == "unknown-call"


@pytest.mark.asyncio
async def test_tool_call_rejects_a_name_that_was_not_searched():
    rail, agent, manager = _rail_and_agent()
    rail.seed_cached_tools(
        all_tool_infos=[
            ToolInfo(
                name="cron_create_job",
                description="Create a calendar reminder",
                parameters=FULL_SCHEMA,
            )
        ]
    )
    session = _FakeSession()
    call_tool = manager.registered["tool_call"][1]
    wrapper_ctx = AgentCallbackContext(
        agent=agent,
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id="wrapper-call"),
            tool_name="tool_call",
            tool_args={"name": "cron_create_job", "args": {}},
        ),
        session=session,
    )

    output = await call_tool.invoke(
        wrapper_ctx.inputs.tool_args,
        session=session,
        _tool_callback_context=wrapper_ctx,
    )

    assert output.success is False
    assert "must be returned by tool_search" in (output.error or "")
    assert manager.executed == []
