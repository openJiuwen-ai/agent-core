# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
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


class _AttachmentSession(_FakeSession):
    def __init__(self, session_id="session-1", state=None):
        super().__init__(state=state)
        self.session_id = session_id

    def get_session_id(self):
        return self.session_id


class _ToolRegistry:
    def __init__(self, cards):
        self.cards = list(cards)

    def list(self):
        return list(self.cards)


def _deferred_card(name, description):
    return ToolCard(
        id=name,
        name=name,
        description=description,
        input_params={"type": "object", "properties": {}},
        exposure=ToolExposure.DEFERRED,
    )


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
    assert "# 渐进式工具使用规则" in prompt
    assert "固定的 `tool_call`" in prompt
    assert preview_messages[0].content == "preview prompt"
    assert [tool.name for tool in ctx.inputs.tools] == ["tool_search", "tool_call"]


@pytest.mark.asyncio
async def test_deferred_catalog_uses_initial_snapshot_then_incremental_attachments():
    config = DeepAgentConfig(progressive_tool_enabled=True, language="cn")
    rail = ProgressiveToolRail(config)
    registry = _ToolRegistry(
        [
            _deferred_card("cron_create", "创建定时任务"),
            _deferred_card("calendar_query", "查询日历事件"),
        ]
    )
    attachment_manager = PromptAttachmentManager(language="cn")
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(
        PromptSection(
            name="identity",
            content={"cn": "Base system prompt.", "en": "Base system prompt."},
        )
    )
    agent = SimpleNamespace(
        system_prompt_builder=builder,
        ability_manager=registry,
        prompt_attachment_manager=attachment_manager,
    )
    session = _AttachmentSession()
    context = SessionModelContext(
        "ctx-1",
        session.session_id,
        ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(tools=[]),
        session=session,
        context=context,
    )

    await rail.before_model_call(ctx)
    await attachment_manager.add_section(
        session_id=session.session_id,
        section="runtime",
        kind="runtime",
        source="test.runtime",
        content="runtime state",
    )
    assert await attachment_manager.sync_to_context(context, session.session_id) is not None
    initial_prompt = builder.build()
    assert "cron_create" in initial_prompt
    assert "calendar_query" in initial_prompt
    assert "session 初始目录" in initial_prompt

    await rail.before_model_call(ctx)
    assert await attachment_manager.sync_to_context(context, session.session_id) is None

    registry.cards.append(_deferred_card("mail_search", "搜索邮件"))
    await rail.before_model_call(ctx)
    delta = await attachment_manager.sync_to_context(context, session.session_id)

    assert delta is not None
    assert "mail_search" in delta.content
    assert "cron_create" not in delta.content
    assert "calendar_query" not in delta.content
    assert "mail_search" not in builder.build()
    assert len(context.get_messages()) == 2


@pytest.mark.asyncio
async def test_deferred_catalog_delta_reports_updates_and_removals():
    config = DeepAgentConfig(progressive_tool_enabled=True, language="cn")
    rail = ProgressiveToolRail(config)
    original = _deferred_card("cron_create", "创建定时任务")
    removed = _deferred_card("calendar_query", "查询日历事件")
    registry = _ToolRegistry([original, removed])
    manager = PromptAttachmentManager(language="cn")
    builder = SystemPromptBuilder(language="cn")
    agent = SimpleNamespace(
        system_prompt_builder=builder,
        ability_manager=registry,
        prompt_attachment_manager=manager,
    )
    session = _AttachmentSession()
    context = SessionModelContext(
        "ctx-2",
        session.session_id,
        ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(tools=[]),
        session=session,
        context=context,
    )

    await rail.before_model_call(ctx)
    await manager.add_section(
        session_id=session.session_id,
        section="runtime",
        kind="runtime",
        source="test.runtime",
        content="runtime state",
    )
    await manager.sync_to_context(context, session.session_id)

    original.description = "创建和更新定时任务"
    registry.cards = [original]
    await rail.before_model_call(ctx)
    delta = await manager.sync_to_context(context, session.session_id)

    assert delta is not None
    assert "创建和更新定时任务" in delta.content
    assert "calendar_query" in delta.content
    assert "修改" in delta.content
    assert "删除" in delta.content
    assert "当前不可用" in delta.content


@pytest.mark.asyncio
async def test_deferred_catalog_does_not_repeat_after_session_state_reset():
    """The materialized attachment remains the diff baseline after a reload."""

    config = DeepAgentConfig(progressive_tool_enabled=True, language="cn")
    registry = _ToolRegistry([_deferred_card("cron_create", "创建定时任务")])
    manager = PromptAttachmentManager(language="cn")
    builder = SystemPromptBuilder(language="cn")
    agent = SimpleNamespace(
        system_prompt_builder=builder,
        ability_manager=registry,
        prompt_attachment_manager=manager,
    )
    session = _AttachmentSession()
    context = SessionModelContext(
        "ctx-state-reset",
        session.session_id,
        ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(tools=[]),
        session=session,
        context=context,
    )

    # Establish the initial static directory and an attachment-history
    # snapshot so later changes are emitted as deltas.
    rail = ProgressiveToolRail(config)
    await rail.before_model_call(ctx)
    await manager.add_section(
        session_id=session.session_id,
        section="runtime",
        kind="runtime",
        source="test.runtime",
        content="runtime state",
    )
    await manager.sync_to_context(context, session.session_id)

    registry.cards.append(_deferred_card("mail_search", "搜索邮件"))
    await rail.before_model_call(ctx)
    added = await manager.sync_to_context(context, session.session_id)
    assert added is not None
    assert "mail_search" in added.content

    # A hot reload can replace the rail/session state while retaining the
    # attachment manager.  The same catalog must not produce a new message.
    session._state.clear()
    reloaded_rail = ProgressiveToolRail(config)
    await reloaded_rail.before_model_call(ctx)
    assert await manager.sync_to_context(context, session.session_id) is None

    # The same guarantee applies to a repeated removal, which is the pattern
    # visible in the model log (catalog versions 3, 4, and 5).
    registry.cards = [registry.cards[0]]
    await reloaded_rail.before_model_call(ctx)
    removed = await manager.sync_to_context(context, session.session_id)
    assert removed is not None
    assert "mail_search" in removed.content

    session._state.clear()
    final_rail = ProgressiveToolRail(config)
    await final_rail.before_model_call(ctx)
    assert await manager.sync_to_context(context, session.session_id) is None


@pytest.mark.asyncio
async def test_deferred_catalog_recreates_full_snapshot_when_history_has_no_baseline():
    config = DeepAgentConfig(progressive_tool_enabled=True, language="cn")
    rail = ProgressiveToolRail(config)
    registry = _ToolRegistry([_deferred_card("cron_create", "创建定时任务")])
    manager = PromptAttachmentManager(language="cn")
    builder = SystemPromptBuilder(language="cn")
    agent = SimpleNamespace(
        system_prompt_builder=builder,
        ability_manager=registry,
        prompt_attachment_manager=manager,
    )
    session = _AttachmentSession()
    first_context = SessionModelContext(
        "ctx-3",
        session.session_id,
        ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )
    first_ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(tools=[]),
        session=session,
        context=first_context,
    )
    await rail.before_model_call(first_ctx)
    await manager.sync_to_context(first_context, session.session_id)

    second_context = SessionModelContext(
        "ctx-4",
        session.session_id,
        ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )
    second_ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(tools=[]),
        session=session,
        context=second_context,
    )
    await rail.before_model_call(second_ctx)
    recovered = await manager.sync_to_context(second_context, session.session_id)

    assert recovered is not None
    assert "目录版本" in recovered.content
    assert "目录版本：1" in recovered.content
    assert "cron_create" in recovered.content
    assert "新增" not in recovered.content

    # Rebuilding the full snapshot because a context has no baseline does
    # not mean that the deferred directory itself changed.
    third_context = SessionModelContext(
        "ctx-5",
        session.session_id,
        ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )
    third_ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(tools=[]),
        session=session,
        context=third_context,
    )
    await rail.before_model_call(third_ctx)
    recovered_again = await manager.sync_to_context(third_context, session.session_id)
    assert recovered_again is not None
    assert "目录版本：1" in recovered_again.content
