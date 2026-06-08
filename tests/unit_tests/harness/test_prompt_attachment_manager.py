# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for DeepAgent prompt attachments."""
from __future__ import annotations

import pytest

from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
from openjiuwen.harness.prompts.builder import SystemPromptBuilder
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PromptAttachment,
    PromptAttachmentKind,
    PromptAttachmentManager,
    PromptAttachmentScope,
    PromptAttachmentUpdate,
)
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.prompt_attachments import (
    build_prompt_attachments_section,
)


@pytest.mark.asyncio
async def test_prompt_attachment_manager_collect_render_inject():
    manager = PromptAttachmentManager()

    await manager.add(PromptAttachment(
        id="session_rules",
        scope=PromptAttachmentScope.SESSION,
        kind=PromptAttachmentKind.RUNTIME,
        session_id="sess1",
        content="session rules",
        priority=0,
    ))
    await manager.add(PromptAttachment(
        id="session_summary",
        scope=PromptAttachmentScope.SESSION,
        kind=PromptAttachmentKind.TEXT,
        session_id="sess1",
        content="session content",
        priority=1,
    ))
    await manager.add(PromptAttachment(
        id="turn_diag",
        scope=PromptAttachmentScope.TURN,
        kind=PromptAttachmentKind.DIAGNOSTIC,
        session_id="sess1",
        invoke_turn_id="turn1",
        content="turn content",
        priority=2,
    ))
    await manager.add(PromptAttachment(
        id="other_turn",
        scope=PromptAttachmentScope.TURN,
        kind=PromptAttachmentKind.TEXT,
        session_id="sess2",
        invoke_turn_id="turn2",
        content="must not appear",
    ))

    collected = await manager.collect_for_turn("sess1", "turn1")
    assert [item.id for item in collected] == [
        "session_rules",
        "session_summary",
        "turn_diag",
    ]

    rendered = manager.render(collected)
    assert rendered.startswith("<system-reminder>")
    assert "session rules" in rendered
    assert "session content" in rendered
    assert "turn content" in rendered
    assert "must not appear" not in rendered

    original = [SystemMessage(content="sys"), UserMessage(content="query")]
    injected = manager.inject_messages(original, rendered)
    assert original[-1].content == "query"
    assert injected[-1].content.startswith("query\n\n<system-reminder>")

    updated = await manager.update_by_id(
        "turn_diag",
        PromptAttachmentUpdate(content="updated turn content"),
    )
    assert updated.content == "updated turn content"
    assert "updated turn content" in manager.render(
        await manager.collect_for_turn("sess1", "turn1")
    )

    assert await manager.remove_by_id("turn_diag", session_id="sess1") is True
    assert await manager.get_by_id("turn_diag", session_id="sess1") is None
    assert "updated turn content" not in manager.render(
        await manager.collect_for_turn("sess1", "turn1")
    )


@pytest.mark.asyncio
async def test_prompt_attachment_manager_render_truncates_large_content():
    manager = PromptAttachmentManager()
    rendered = manager.render(
        [
            PromptAttachment(
                id="large",
                scope=PromptAttachmentScope.TURN,
                session_id="sess1",
                invoke_turn_id="turn1",
                content="x" * 20,
            )
        ],
        max_prompt_attachment_chars=5,
        max_rendered_chars=0,
    )

    assert "xxxxx" in rendered
    assert "x" * 20 not in rendered
    assert "[Prompt attachment truncated:" in rendered


@pytest.mark.asyncio
async def test_prompt_attachment_manager_filter_interfaces():
    manager = PromptAttachmentManager()
    await manager.add(PromptAttachment(
        id="a1",
        scope=PromptAttachmentScope.TURN,
        kind="custom_note",
        source="rail.context",
        session_id="sess1",
        invoke_turn_id="turn1",
        content="one",
    ))
    await manager.add(PromptAttachment(
        id="a2",
        scope=PromptAttachmentScope.TURN,
        kind="custom_note",
        source="rail.context",
        session_id="sess1",
        invoke_turn_id="turn2",
        content="two",
    ))

    found = await manager.list_by_filter(
        session_id="sess1",
        invoke_turn_id="turn1",
        scope=PromptAttachmentScope.TURN,
        kind="custom_note",
        source="rail.context",
    )
    assert [item.id for item in found] == ["a1"]

    updated = await manager.update_by_filter(
        PromptAttachmentUpdate(content="updated"),
        session_id="sess1",
        invoke_turn_id="turn1",
        scope=PromptAttachmentScope.TURN,
        source="rail.context",
    )
    assert [item.content for item in updated] == ["updated"]

    assert await manager.remove_by_filter(
        session_id="sess1",
        invoke_turn_id="turn1",
        scope=PromptAttachmentScope.TURN,
        source="rail.context",
    ) == 1
    assert [item.id for item in await manager.collect_for_turn("sess1", "turn2")] == ["a2"]


@pytest.mark.asyncio
async def test_prompt_attachment_manager_convenience_writer_and_clear_turn():
    manager = PromptAttachmentManager()
    await manager.add_text(
        content="turn text",
        scope=PromptAttachmentScope.TURN,
        session_id="sess1",
        invoke_turn_id="turn1",
        prompt_attachment_id="turn_text",
        source="manual",
    )
    await manager.upsert_text(
        content="session text",
        scope=PromptAttachmentScope.SESSION,
        session_id="sess1",
        prompt_attachment_id="session_text",
        source="manual",
    )

    collected = await manager.collect_for_turn("sess1", "turn1")
    assert [item.id for item in collected] == ["session_text", "turn_text"]

    assert await manager.clear_turn("sess1", "turn1") == 1
    collected_after = await manager.collect_for_turn("sess1", "turn1")
    assert [item.id for item in collected_after] == ["session_text"]


@pytest.mark.asyncio
async def test_prompt_attachment_context_writer_generates_stable_ids_and_upserts():
    class FakeSession:
        def get_session_id(self):
            return "sess/1"

    class FakeContext:
        session = FakeSession()
        inputs = {"_invoke_turn_id": "turn/1"}
        extra = {}

    manager = PromptAttachmentManager()
    writer = manager.for_context(FakeContext())

    session_item = await writer.upsert_section(
        section="runtime",
        scope=PromptAttachmentScope.SESSION,
        kind=PromptAttachmentKind.RUNTIME,
        source="product.runtime",
        content="session v1",
    )
    assert session_item.id == "session.sess_1.runtime"
    assert session_item.metadata["section"] == "runtime"

    updated = await writer.upsert_section(
        section="runtime",
        scope=PromptAttachmentScope.SESSION,
        kind=PromptAttachmentKind.RUNTIME,
        source="product.runtime",
        content="session v2",
    )
    assert updated.id == session_item.id

    turn_item = await writer.upsert_section(
        section="request_context",
        scope=PromptAttachmentScope.TURN,
        kind=PromptAttachmentKind.RUNTIME,
        source="product.request_context",
        content="turn only",
    )
    assert turn_item.id == "turn.sess_1.turn_1.request_context"

    collected = await manager.collect_for_turn("sess/1", "turn/1")
    assert [item.id for item in collected] == [
        "session.sess_1.runtime",
        "turn.sess_1.turn_1.request_context",
    ]
    assert [item.content for item in collected] == ["session v2", "turn only"]


@pytest.mark.asyncio
async def test_prompt_attachment_context_writer_rejects_turn_without_invoke_turn_id():
    class FakeSession:
        def get_session_id(self):
            return "sess1"

    class FakeContext:
        session = FakeSession()
        inputs = {}
        extra = {}

    writer = PromptAttachmentManager().for_context(FakeContext())

    with pytest.raises(ValueError, match="invoke_turn_id"):
        await writer.upsert_section(
            section="request_context",
            scope=PromptAttachmentScope.TURN,
            kind=PromptAttachmentKind.RUNTIME,
            source="product.request_context",
            content="turn only",
        )


@pytest.mark.asyncio
async def test_prompt_attachment_manager_sorts_scope_before_priority():
    manager = PromptAttachmentManager()
    await manager.add(PromptAttachment(
        id="turn_high_priority",
        scope=PromptAttachmentScope.TURN,
        kind=PromptAttachmentKind.RUNTIME,
        priority=0,
        session_id="sess1",
        invoke_turn_id="turn1",
        content="turn",
    ))
    await manager.add(PromptAttachment(
        id="session_low_priority",
        scope=PromptAttachmentScope.SESSION,
        kind=PromptAttachmentKind.RUNTIME,
        priority=100,
        session_id="sess1",
        content="session",
    ))

    collected = await manager.collect_for_turn("sess1", "turn1")
    assert [item.id for item in collected] == ["session_low_priority", "turn_high_priority"]


@pytest.mark.asyncio
async def test_prompt_attachment_manager_convenience_update_interfaces():
    manager = PromptAttachmentManager()
    await manager.add_text(
        content="before",
        scope=PromptAttachmentScope.SESSION,
        session_id="sess1",
        prompt_attachment_id="session_text",
        metadata={"old": "value"},
    )

    content_updated = await manager.update_content_by_id(
        "session_text",
        content="after",
        session_id="sess1",
    )
    assert content_updated.content == "after"

    metadata_updated = await manager.update_metadata_by_id(
        "session_text",
        metadata={"new": "value"},
        session_id="sess1",
    )
    assert metadata_updated.metadata == {"old": "value", "new": "value"}

    metadata_replaced = await manager.update_metadata_by_id(
        "session_text",
        metadata={"only": "value"},
        session_id="sess1",
        merge=False,
    )
    assert metadata_replaced.metadata == {"only": "value"}


def test_prompt_attachment_manager_render_escapes_xml():
    manager = PromptAttachmentManager()
    rendered = manager.render([
        PromptAttachment(
            id='a"<1>',
            scope=PromptAttachmentScope.TURN,
            kind="custom<type>",
            source="source&x",
            session_id="sess1",
            invoke_turn_id="turn1",
            content="<raw>&value",
        )
    ])

    assert 'id="a&quot;&lt;1&gt;"' in rendered
    assert 'type="custom&lt;type&gt;"' in rendered
    assert "source&amp;x" in rendered
    assert "&lt;raw&gt;&amp;value" in rendered


def test_prompt_attachment_manager_injects_into_type_text_content_blocks():
    manager = PromptAttachmentManager()
    original = [
        UserMessage(content=[
            {"type": "text", "text": "query"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        ])
    ]

    injected = manager.inject_messages(original, "<system-reminder>attached</system-reminder>")

    assert original[-1].content[0]["text"] == "query"
    assert injected[-1].content[0]["type"] == "text"
    assert injected[-1].content[0]["text"] == "query\n\n<system-reminder>attached</system-reminder>"
    assert all(block.get("kind") != "text" for block in injected[-1].content if isinstance(block, dict))


def test_prompt_attachment_manager_appends_type_text_block_for_image_only_content():
    manager = PromptAttachmentManager()
    original = [
        UserMessage(content=[
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        ])
    ]

    injected = manager.inject_messages(original, "<system-reminder>attached</system-reminder>")

    assert injected[-1].content[-1] == {
        "type": "text",
        "text": "<system-reminder>attached</system-reminder>",
    }


def test_prompt_attachments_section_explains_system_reminder_tags():
    builder = SystemPromptBuilder(language="en")
    builder.add_section(build_prompt_attachments_section())

    section = builder.get_section(SectionName.PROMPT_ATTACHMENTS)
    assert section is not None
    prompt = builder.build()
    assert "<system-reminder>" in prompt
    assert "<prompt-attachment>" in prompt
    assert "bear no direct relation" in prompt


@pytest.mark.asyncio
async def test_context_window_mutator_runs_before_kv_release():
    released_messages = []

    async def mutator(context, window):
        del context
        messages = list(window.context_messages)
        messages[-1] = UserMessage(content=f"{messages[-1].content}\n\nattached")
        return window.model_copy(update={"context_messages": messages})

    class FakeKVCacheManager:
        async def release(self, window, **kwargs):
            del kwargs
            released_messages.extend(window.get_messages())

    context = SessionModelContext(
        "ctx",
        "sess",
        ContextEngineConfig(),
        history_messages=[UserMessage(content="query")],
        processors=[],
        window_mutators=[mutator],
    )
    context._kv_cache_manager = FakeKVCacheManager()

    window = await context.get_context_window(
        system_messages=[SystemMessage(content="sys")]
    )

    assert window.get_messages()[-1].content == "query\n\nattached"
    assert released_messages[-1].content == "query\n\nattached"
    assert window.statistic.total_messages == 2
