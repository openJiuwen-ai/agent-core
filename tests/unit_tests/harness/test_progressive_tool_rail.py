# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.core.foundation.llm.schema.message import SystemMessage
from openjiuwen.core.foundation.tool import ToolCard, ToolExposure, ToolInfo
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.harness.prompts.builder import PromptSection, SystemPromptBuilder
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager
from openjiuwen.harness.rails.progressive_tool_rail import ProgressiveToolRail
from openjiuwen.harness.schema.config import DeepAgentConfig


class _FakeSession:
    def __init__(self, state=None):
        self._state = dict(state or {})

    def get_state(self, key):
        return self._state.get(key)

    def update_state(self, updates):
        self._state.update(updates)

    def get_session_id(self):
        return "progressive-test-session"


class _TestableProgressiveToolRail(ProgressiveToolRail):
    """Test-only helper for seeding cached tool state."""

    def seed_cached_tools(self, *, meta_tool_names, all_tool_infos) -> None:
        self._meta_tool_names = set(meta_tool_names)
        self._cached_all_tool_infos = list(all_tool_infos)


@pytest.mark.asyncio
async def test_before_model_call_updates_builder_and_keeps_preview_messages_intact():
    config = DeepAgentConfig(
        progressive_tool_enabled=True,
        language="cn",
    )
    rail = _TestableProgressiveToolRail(config)
    rail.seed_cached_tools(
        meta_tool_names={"tool_search", "tool_call"},
        all_tool_infos=[
            ToolInfo(name="tool_search", description="Search the tool registry"),
            ToolInfo(name="tool_call", description="Execute a search result"),
            ToolInfo(name="loaded_tool", description="Previously discovered tool"),
            ToolInfo(name="hidden_tool", description="Hidden tool"),
        ],
    )

    builder = SystemPromptBuilder(language="cn")
    builder.add_section(PromptSection(
        name="identity",
        content={"cn": "Base system prompt.", "en": "Base system prompt."},
    ))
    agent = Mock(system_prompt_builder=builder)

    preview_messages = [SystemMessage(content="preview prompt")]
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(
            messages=preview_messages,
            tools=[
                ToolInfo(name="tool_search", description="Search the tool registry"),
                ToolInfo(name="tool_call", description="Execute a search result"),
                ToolInfo(name="loaded_tool", description="Already loaded tool"),
                ToolInfo(name="hidden_tool", description="Hidden tool"),
            ],
        ),
        session=_FakeSession(),
    )

    await rail.before_model_call(ctx)

    prompt = builder.build()
    assert "Base system prompt." in prompt
    assert "## 工具导航" not in prompt
    assert "tool_call" in prompt
    assert preview_messages[0].content == "preview prompt"
    assert [tool.name for tool in ctx.inputs.tools] == ["tool_search", "tool_call"]


@pytest.mark.asyncio
async def test_deferred_catalog_remains_complete_with_window_mutator_manager():
    """The current window mutator renders one attachment, so it needs full snapshots."""
    cards = [ToolCard(id="cron", name="cron_create", description="Create cron",
                      exposure=ToolExposure.DEFERRED)]
    registry = SimpleNamespace(list=lambda: cards)
    manager = PromptAttachmentManager()
    agent = SimpleNamespace(
        system_prompt_builder=SystemPromptBuilder(language="cn"),
        ability_manager=registry,
        prompt_attachment_manager=manager,
    )
    session = _FakeSession()
    rail = ProgressiveToolRail(DeepAgentConfig(progressive_tool_enabled=True, language="cn"))
    ctx = AgentCallbackContext(agent=agent, inputs=ModelCallInputs(tools=[]), session=session)

    await rail.before_model_call(ctx)
    cards.append(ToolCard(id="mail", name="mail_search", description="Search mail",
                          exposure=ToolExposure.DEFERRED))
    await rail.before_model_call(ctx)
    cards.append(ToolCard(id="calendar", name="calendar_query", description="Query calendar",
                          exposure=ToolExposure.DEFERRED))
    await rail.before_model_call(ctx)

    attachments = await manager.collect_for_session(session.get_session_id())
    catalog = next(item.content for item in attachments if item.section == "progressive_deferred_tools")
    assert "cron_create" in catalog
    assert "mail_search" in catalog
    assert "calendar_query" in catalog
