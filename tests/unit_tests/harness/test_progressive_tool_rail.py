# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import Mock

import pytest

from openjiuwen.core.foundation.llm.schema.message import SystemMessage
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.harness.prompts.builder import PromptSection, SystemPromptBuilder
from openjiuwen.harness.rails.progressive_tool_rail import ProgressiveToolRail
from openjiuwen.harness.schema.config import DeepAgentConfig


class _FakeSession:
    def __init__(self, state=None):
        self._state = dict(state or {})

    def get_state(self, key):
        return self._state.get(key)

    def update_state(self, updates):
        self._state.update(updates)


class _TestableProgressiveToolRail(ProgressiveToolRail):
    """Test-only helper for seeding cached tool state."""

    def seed_cached_tools(self, *, meta_tool_names, all_tool_infos) -> None:
        self._meta_tool_names = set(meta_tool_names)
        self._cached_all_tool_infos = list(all_tool_infos)


@pytest.mark.asyncio
async def test_before_model_call_updates_builder_and_keeps_preview_messages_intact():
    config = DeepAgentConfig(
        progressive_tool_enabled=True,
        progressive_tool_always_visible_tools=["always_tool"],
        language="cn",
    )
    rail = _TestableProgressiveToolRail(config)
    rail.seed_cached_tools(
        meta_tool_names={"search_tools", "load_tools"},
        all_tool_infos=[
            ToolInfo(name="always_tool", description="Always visible tool"),
            ToolInfo(name="loaded_tool", description="Already loaded tool"),
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
                ToolInfo(name="search_tools", description="Search tool registry"),
                ToolInfo(name="always_tool", description="Always visible tool"),
                ToolInfo(name="loaded_tool", description="Already loaded tool"),
                ToolInfo(name="hidden_tool", description="Hidden tool"),
            ],
        ),
        session=_FakeSession(
            {"__progressive_visible_tool_names__": ["loaded_tool"]}
        ),
    )

    await rail.before_model_call(ctx)

    prompt = builder.build()
    assert "Base system prompt." in prompt
    assert "## 工具导航" in prompt
    assert "## 渐进式工具使用规则" in prompt
    assert preview_messages[0].content == "preview prompt"
    assert [tool.name for tool in ctx.inputs.tools] == [
        "search_tools",
        "always_tool",
        "loaded_tool",
    ]
