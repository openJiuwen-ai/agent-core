# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Contract tests for the tool-search-only progressive disclosure flow.

These tests deliberately describe the new contract before its implementation:
the model sees one top-level ``tool_search`` function, search returns complete
JSON Schemas, and a result can be called directly in the following turn.
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

    def add_ability(self, card, tool):
        self.registered[card.name] = (card, tool)
        self.cards.append(card)
        self.registry_revision += 1
        return SimpleNamespace(added=True)

    def get(self, name):
        item = self.registered.get(name)
        return item[0] if item else None

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


def test_init_registers_only_tool_search():
    """The progressive rail exposes one meta-tool, not search/load pairs."""

    rail, _agent_instance, manager = _rail_and_agent()

    assert list(manager.registered) == ["tool_search"]
    assert rail._meta_tool_names == {"tool_search"}

    card, _tool = manager.registered["tool_search"]
    assert set(card.input_params["properties"]) == {"query", "limit"}
    assert "query" in card.input_params["required"]
    assert card.input_params["properties"]["limit"]["default"] == 5


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
async def test_before_model_call_keeps_only_tool_search_in_top_level_tools():
    """Search results/history do not dynamically expand model-visible tools."""

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
                ToolInfo(name="cron_create_job", description="Create a reminder"),
                ToolInfo(name="hidden_tool", description="Not visible yet"),
            ]
        ),
        session=_FakeSession(),
    )

    await rail.before_model_call(ctx)

    assert [tool.name for tool in ctx.inputs.tools] == ["tool_search"]


@pytest.mark.asyncio
async def test_search_result_can_be_called_directly_but_unknown_tool_is_rejected():
    """Search authorizes its result for the next direct call; unknown names do not."""

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

    allowed_ctx = AgentCallbackContext(
        agent=agent,
        inputs=ToolCallInputs(
            tool_name="cron_create_job",
            tool_args={"when": "tomorrow", "text": "call mom"},
        ),
        session=session,
    )
    await rail.before_tool_call(allowed_ctx)
    assert allowed_ctx.extra.get("_skip_tool") is not True

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
