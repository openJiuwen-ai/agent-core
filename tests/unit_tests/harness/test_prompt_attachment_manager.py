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
    PromptAttachmentUpdate,
)
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.prompt_attachments import (
    build_prompt_attachments_section,
)


@pytest.mark.asyncio
async def test_prompt_attachment_manager_collect_render_inject_and_update():
    manager = PromptAttachmentManager()

    runtime = await manager.add_section(
        session_id="sess1",
        section="runtime",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="runtime rules",
        priority=10,
    )
    await manager.add_section(
        session_id="sess1",
        section="memory",
        kind=PromptAttachmentKind.MEMORY,
        source="rail.memory",
        content="memory content",
        priority=20,
    )
    await manager.add_section(
        session_id="sess2",
        section="runtime",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="must not appear",
    )

    collected = await manager.collect_for_session("sess1")
    assert [item.id for item in collected] == ["session.sess1.runtime", "session.sess1.memory"]

    rendered = manager.render(collected)
    assert rendered.startswith("<system-reminder>")
    assert "runtime rules" in rendered
    assert "memory content" in rendered
    assert "must not appear" not in rendered

    original = [SystemMessage(content="sys"), UserMessage(content="query")]
    injected = manager.inject_messages(original, rendered)
    assert original[-1].content == "query"
    assert injected[-1].content.startswith("query\n\n<system-reminder>")

    updated = await manager.update_by_id(runtime.id, PromptAttachmentUpdate(content="updated runtime"))
    assert updated.content == "updated runtime"
    assert "updated runtime" in manager.render(await manager.collect_for_session("sess1"))

    assert await manager.remove_by_id(runtime.id, session_id="sess1") is True
    assert await manager.get_by_id(runtime.id, session_id="sess1") is None


def test_prompt_attachment_manager_render_truncates_large_content():
    manager = PromptAttachmentManager()
    rendered = manager.render(
        [
            PromptAttachment(
                id="session.sess1.large",
                section="large",
                session_id="sess1",
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
async def test_prompt_attachment_manager_section_is_unique_inside_session():
    manager = PromptAttachmentManager()

    first = await manager.add_section(
        session_id="sess1",
        section="runtime",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="v1",
    )
    second = await manager.add_section(
        session_id="sess1",
        section="runtime",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="v2",
    )

    assert second.id == first.id
    items = await manager.collect_for_session("sess1")
    assert [item.content for item in items] == ["v2"]


@pytest.mark.asyncio
async def test_prompt_attachment_manager_filter_and_clear_interfaces():
    manager = PromptAttachmentManager()
    await manager.add_section(
        session_id="sess1",
        section="runtime",
        kind="custom_note",
        source="rail.runtime",
        content="one",
    )
    await manager.add_section(
        session_id="sess1",
        section="memory",
        kind="custom_note",
        source="rail.memory",
        content="two",
    )

    found = await manager.list_by_filter(session_id="sess1", kind="custom_note", source="rail.runtime")
    assert [item.section for item in found] == ["runtime"]

    assert await manager.clear_section(session_id="sess1", section="runtime") == 1
    assert [item.section for item in await manager.collect_for_session("sess1")] == ["memory"]
    assert await manager.remove_by_filter(session_id="sess1", source="rail.memory") == 1
    assert await manager.collect_for_session("sess1") == []


@pytest.mark.asyncio
async def test_prompt_attachment_context_writer_generates_stable_ids_and_adds_prompt_section():
    class FakeSession:
        def get_session_id(self):
            return "sess/1"

    class FakePromptSection:
        name = "runtime"
        priority = 30

        def render(self, language):
            return f"runtime {language}"

    class FakeContext:
        session = FakeSession()
        inputs = {}
        extra = {}

    manager = PromptAttachmentManager()
    writer = manager.bind_context(FakeContext())

    item = await writer.add_section(
        section="request context",
        kind=PromptAttachmentKind.RUNTIME,
        source="product.request_context",
        content="manual",
    )
    assert item.id == "session.sess_1.request_context"

    prompt_item = await writer.add_from_prompt_section(
        FakePromptSection(),
        kind=PromptAttachmentKind.RUNTIME,
        source="product.runtime",
        language="en",
    )
    assert prompt_item is not None
    assert prompt_item.id == "session.sess_1.runtime"
    assert prompt_item.content == "runtime en"


@pytest.mark.asyncio
async def test_prompt_attachment_manager_sorts_by_priority_source_section():
    manager = PromptAttachmentManager()
    await manager.add_section(
        session_id="sess1",
        section="z",
        kind=PromptAttachmentKind.TEXT,
        source="b.source",
        content="z",
        priority=10,
    )
    await manager.add_section(
        session_id="sess1",
        section="a",
        kind=PromptAttachmentKind.TEXT,
        source="b.source",
        content="a",
        priority=10,
    )
    await manager.add_section(
        session_id="sess1",
        section="m",
        kind=PromptAttachmentKind.TEXT,
        source="a.source",
        content="m",
        priority=10,
    )
    await manager.add_section(
        session_id="sess1",
        section="last",
        kind=PromptAttachmentKind.TEXT,
        source="a.source",
        content="last",
        priority=20,
    )

    collected = await manager.collect_for_session("sess1")
    assert [item.section for item in collected] == ["m", "a", "z", "last"]


@pytest.mark.asyncio
async def test_prompt_attachment_manager_convenience_update_interfaces():
    manager = PromptAttachmentManager()
    item = await manager.add_section(
        session_id="sess1",
        section="session_text",
        kind=PromptAttachmentKind.TEXT,
        source="manual",
        content="before",
        metadata={"old": "value"},
    )

    content_updated = await manager.update_content_by_id(item.id, content="after", session_id="sess1")
    assert content_updated.content == "after"

    metadata_updated = await manager.update_metadata_by_id(
        item.id,
        metadata={"new": "value"},
        session_id="sess1",
    )
    assert metadata_updated.metadata["old"] == "value"
    assert metadata_updated.metadata["new"] == "value"

    metadata_replaced = await manager.update_metadata_by_id(
        item.id,
        metadata={"only": "value"},
        session_id="sess1",
        merge=False,
    )
    assert metadata_replaced.metadata == {"only": "value", "section": "session_text", "source": "manual"}


def test_prompt_attachment_manager_render_escapes_xml():
    manager = PromptAttachmentManager()
    rendered = manager.render([
        PromptAttachment(
            id='session.sess1.a"<1>',
            section='a"<1>',
            kind="custom<type>",
            source="source&x",
            session_id="sess1",
            content="<raw>&value",
        )
    ])

    assert 'id=' not in rendered
    assert 'type="custom&lt;type&gt;"' in rendered
    assert 'source=' not in rendered
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

    window = await context.get_context_window(system_messages=[SystemMessage(content="sys")])

    assert window.get_messages()[-1].content == "query\n\nattached"
    assert released_messages[-1].content == "query\n\nattached"
    assert window.statistic.total_messages == 2
