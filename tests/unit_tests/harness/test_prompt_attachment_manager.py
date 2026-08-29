# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for DeepAgent prompt attachments."""
from __future__ import annotations

import pytest

from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PROMPT_ATTACHMENT_HISTORY_METADATA_KEY,
    PromptAttachment,
    PromptAttachmentKind,
    PromptAttachmentManager,
    PromptAttachmentUpdate,
)
@pytest.mark.asyncio
async def test_prompt_attachment_manager_collect_render_and_update():
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
    assert rendered.startswith("The following dynamic context is currently active.")
    assert "<prompt-attachment" not in rendered
    assert "runtime rules" in rendered
    assert "memory content" in rendered
    assert "must not appear" not in rendered

    updated = await manager.update_by_id(runtime.id, PromptAttachmentUpdate(content="updated runtime"))
    assert updated.content == "updated runtime"
    assert "updated runtime" in manager.render(await manager.collect_for_session("sess1"))

    assert await manager.remove_by_id(runtime.id, session_id="sess1") is True
    assert await manager.get_by_id(runtime.id, session_id="sess1") is None


@pytest.mark.asyncio
async def test_prompt_attachment_manager_persists_snapshot_then_only_deltas():
    manager = PromptAttachmentManager(language="en")
    runtime = await manager.add_section(
        session_id="sess1",
        section="runtime",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="runtime v1",
    )
    await manager.add_section(
        session_id="sess1",
        section="stable",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="unchanged payload",
    )
    context = SessionModelContext(
        "ctx1",
        "sess1",
        ContextEngineConfig(),
        history_messages=[UserMessage(content="query")],
        processors=[],
    )

    snapshot = await manager.sync_to_context(context, "sess1")

    assert isinstance(snapshot, SystemMessage)
    assert snapshot.metadata[PROMPT_ATTACHMENT_HISTORY_METADATA_KEY] is True
    assert snapshot.content.startswith("The following dynamic context is currently active.")
    assert "<prompt-attachment" not in snapshot.content
    assert "runtime v1" in snapshot.content
    assert "unchanged payload" in snapshot.content
    assert len(context.get_messages()) == 2

    assert await manager.sync_to_context(context, "sess1") is None
    assert len(context.get_messages()) == 2

    restored_manager = PromptAttachmentManager(language="en")
    await restored_manager.add_section(
        session_id="sess1",
        section="runtime",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="runtime v1",
    )
    await restored_manager.add_section(
        session_id="sess1",
        section="stable",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="unchanged payload",
    )
    assert await restored_manager.sync_to_context(context, "sess1") is None
    assert len(context.get_messages()) == 2

    await manager.update_content_by_id(runtime.id, content="runtime v2", session_id="sess1")
    delta = await manager.sync_to_context(context, "sess1")

    assert isinstance(delta, SystemMessage)
    assert delta.content.startswith("The following dynamic context has changed.")
    assert "<prompt-attachment" not in delta.content
    assert "runtime v2" in delta.content
    assert "<system-reminder>" not in delta.content
    assert "unchanged payload" not in delta.content
    assert len(context.get_messages()) == 3
    assert context.get_messages()[0].content == "query"
    assert context.get_messages()[1].content == snapshot.content
    assert context.get_messages()[2].content == delta.content

    await manager.clear_section(session_id="sess1", section="stable")
    removal = await manager.sync_to_context(context, "sess1")

    assert isinstance(removal, SystemMessage)
    assert "no longer active" in removal.content
    assert "- `stable`" in removal.content
    assert "<prompt-attachment" not in removal.content
    assert len(context.get_messages()) == 4
    assert context.get_messages()[-1].content == removal.content


@pytest.mark.asyncio
async def test_prompt_attachment_snapshot_precedes_the_first_user_message():
    manager = PromptAttachmentManager(language="en")
    await manager.add_section(
        session_id="sess1",
        section="runtime",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="initial runtime state",
    )
    context = SessionModelContext(
        "ctx1",
        "sess1",
        ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )

    snapshot = await manager.sync_to_context(context, "sess1")
    user_message = UserMessage(content="query")
    await context.add_messages(user_message)

    assert context.get_messages() == [snapshot, user_message]
    assert isinstance(context.get_messages()[0], SystemMessage)


@pytest.mark.asyncio
async def test_prompt_attachment_snapshot_stays_in_history_order_without_window_mutator():
    manager = PromptAttachmentManager(language="en")
    await manager.add_section(
        session_id="sess1",
        section="runtime",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="initial runtime state",
    )
    context = SessionModelContext(
        "ctx1",
        "sess1",
        ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )

    snapshot = await manager.sync_to_context(context, "sess1")
    await context.add_messages(UserMessage(content="query"))

    window = await context.get_context_window(
        system_messages=[SystemMessage(content="base system")],
    )

    assert window.system_messages == [SystemMessage(content="base system")]
    assert window.context_messages == [snapshot, context.get_messages()[-1]]
    assert window.get_messages() == [
        SystemMessage(content="base system"),
        snapshot,
        context.get_messages()[-1],
    ]


@pytest.mark.asyncio
async def test_prompt_attachment_internal_metadata_does_not_emit_a_delta():
    manager = PromptAttachmentManager(language="en")
    item = await manager.add_section(
        session_id="sess1",
        section="runtime",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="unchanged runtime state",
        priority=10,
        metadata={"revision": 1},
    )
    context = SessionModelContext(
        "ctx1",
        "sess1",
        ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )
    await manager.sync_to_context(context, "sess1")

    await manager.update_metadata_by_id(
        item.id,
        session_id="sess1",
        metadata={"revision": 2},
    )
    await manager.update_by_id(item.id, PromptAttachmentUpdate(priority=20))

    assert await manager.sync_to_context(context, "sess1") is None
    assert len(context.get_messages()) == 1


@pytest.mark.asyncio
async def test_prompt_attachment_manager_keeps_history_attachment_order_in_window():
    manager = PromptAttachmentManager()
    runtime = await manager.add_section(
        session_id="sess1",
        section="runtime",
        kind=PromptAttachmentKind.RUNTIME,
        source="rail.runtime",
        content="runtime context",
    )
    context = SessionModelContext(
        "ctx1",
        "sess1",
        ContextEngineConfig(),
        history_messages=[],
        processors=[],
    )
    snapshot = await manager.sync_to_context(context, "sess1")
    await context.add_messages(
        [
            UserMessage(content="query"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="call-1", type="function", name="read_file", arguments="{}")],
            ),
            ToolMessage(content="file contents", tool_call_id="call-1"),
        ]
    )

    window = await context.get_context_window(
        system_messages=[SystemMessage(content="base system")],
    )

    assert window.system_messages == [SystemMessage(content="base system")]
    assert window.context_messages == context.get_messages()
    assert window.context_messages[0] == snapshot
    assert window.context_messages[-1].content == "file contents"

    await manager.update_content_by_id(runtime.id, content="updated runtime", session_id="sess1")
    delta = await manager.sync_to_context(context, "sess1")
    assert isinstance(delta, SystemMessage)

    window = await context.get_context_window(
        system_messages=[SystemMessage(content="base system")],
    )
    assert window.system_messages == [SystemMessage(content="base system")]
    assert window.context_messages[0].content == snapshot.content
    assert window.context_messages[-2].content == "file contents"
    assert window.context_messages[-1].content == delta.content
    assert window.get_messages()[1].content == snapshot.content
    assert window.get_messages()[-1].content == delta.content


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


def test_prompt_attachment_manager_render_keeps_content_without_internal_wrappers():
    manager = PromptAttachmentManager()
    rendered = manager.render(
        [
            PromptAttachment(
                id='session.sess1.a"<1>',
                section='a"<1>',
                kind="custom<type>",
                source="source&x",
                session_id="sess1",
                content="<raw>&value",
            )
        ]
    )

    assert "id=" not in rendered
    assert "custom<type>" not in rendered
    assert "source=" not in rendered
    assert "<raw>&value" in rendered
    assert "<prompt-attachment" not in rendered


def test_prompt_attachment_plain_text_intro_is_rendered_only_with_attachments():
    manager = PromptAttachmentManager(language="en")
    assert manager.render([]) == ""

    rendered = manager.render(
        [
            PromptAttachment(
                id="session.sess1.runtime",
                section="runtime",
                session_id="sess1",
                content="runtime context",
            )
        ]
    )

    intro_index = rendered.index("The following dynamic context")
    content_index = rendered.index("runtime context")
    assert intro_index < content_index
    assert "<prompt-attachment" not in rendered


@pytest.mark.asyncio
async def test_context_window_mutator_runs_before_window_statistics():
    async def mutator(context, window):
        del context
        messages = list(window.context_messages)
        messages[-1] = UserMessage(content=f"{messages[-1].content}\n\nattached")
        return window.model_copy(update={"context_messages": messages})

    context = SessionModelContext(
        "ctx",
        "sess",
        ContextEngineConfig(),
        history_messages=[UserMessage(content="query")],
        processors=[],
        window_mutators=[mutator],
    )

    window = await context.get_context_window(system_messages=[SystemMessage(content="sys")])

    assert window.get_messages()[-1].content == "query\n\nattached"
    assert window.statistic.total_messages == 2
