# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prompt attachment event integration at the context-history boundary."""

import asyncio
from dataclasses import replace

import pytest

from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.events import Event, EventHandlerOps, ExtensionEventBus
from openjiuwen.core.events.builtins.event_types.prompt_attachment import (
    PROMPT_ATTACHMENT,
    PromptAttachmentInput,
    PromptAttachmentOutput,
)
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, HandlerLevel
from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.prompts.prompt_attachment_manager import (
    PROMPT_ATTACHMENT_HISTORY_METADATA_KEY,
    PromptAttachmentKind,
    PromptAttachmentManager,
)
from openjiuwen.harness.schema.build_context import BuildContext, extension_bus
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec


async def _built_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


def _context() -> SessionModelContext:
    return SessionModelContext(
        "context-1",
        "session-1",
        ContextEngineConfig(),
        history_messages=[UserMessage(content="query")],
        processors=[],
    )


@pytest.mark.asyncio
async def test_attachment_events_run_concurrently_before_history_write_and_preserve_order() -> None:
    started: list[str] = []
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def rewrite(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptAttachmentOutput:
        del ops
        event_input = event.event_input
        assert isinstance(event_input, PromptAttachmentInput)
        assert event_input.agent_id is None
        started.append(str(event_input.detail["section"]))
        if len(started) == 2:
            all_started.set()
        await release.wait()
        return await event_next(
            Event(PROMPT_ATTACHMENT, replace(event_input, text=f"rewritten {event_input.text}"))
        )

    bus = ExtensionEventBus()
    bus.register_event_handler(PROMPT_ATTACHMENT, rewrite, {}, HandlerLevel.USER)
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    manager = PromptAttachmentManager(event_bus=bus)
    await manager.add_section(
        session_id="session-1",
        section="first",
        content="one",
        kind=PromptAttachmentKind.TEXT,
        source="test",
        priority=10,
    )
    await manager.add_section(
        session_id="session-1",
        section="second",
        content="two",
        kind=PromptAttachmentKind.MEMORY,
        source="test",
        priority=20,
    )
    context = _context()

    sync_task = asyncio.create_task(manager.sync_to_context(context, "session-1"))
    await asyncio.wait_for(all_started.wait(), timeout=1)
    assert len(context.get_messages()) == 1

    release.set()
    message = await sync_task

    assert message is not None
    assert started == ["first", "second"]
    assert message.content.index("rewritten one") < message.content.index("rewritten two")
    assert len(context.get_messages()) == 2


@pytest.mark.asyncio
async def test_attachment_event_rewrite_uses_history_and_hashes_original_state() -> None:
    calls: list[str] = []

    async def rewrite(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptAttachmentOutput:
        del ops
        event_input = event.event_input
        assert isinstance(event_input, PromptAttachmentInput)
        calls.append(str(event_input.detail["section"]))
        return await event_next(
            Event(PROMPT_ATTACHMENT, replace(event_input, text=f"extension:{event_input.text}"))
        )

    bus = ExtensionEventBus()
    bus.register_event_handler(PROMPT_ATTACHMENT, rewrite, {})
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    manager = PromptAttachmentManager(event_bus=bus)
    await manager.add_section(
        session_id="session-1",
        section="memory",
        content="original",
        kind=PromptAttachmentKind.MEMORY,
        source="test",
    )
    context = _context()

    snapshot = await manager.sync_to_context(context, "session-1")
    assert snapshot is not None
    assert "extension:original" in snapshot.content
    assert snapshot.metadata[PROMPT_ATTACHMENT_HISTORY_METADATA_KEY] is True

    assert await manager.sync_to_context(context, "session-1") is None
    assert calls == ["memory"]

    await manager.clear_section(session_id="session-1", section="memory")
    removal = await manager.sync_to_context(context, "session-1")
    assert removal is not None
    assert calls == ["memory"]


@pytest.mark.asyncio
async def test_attachment_event_failure_and_omission_are_isolated_per_attachment() -> None:
    calls: list[str] = []

    async def handle(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptAttachmentOutput:
        del ops
        event_input = event.event_input
        assert isinstance(event_input, PromptAttachmentInput)
        section = str(event_input.detail["section"])
        calls.append(section)
        if section == "omit":
            return PromptAttachmentOutput(text=None)
        if section == "fail":
            raise RuntimeError("extension failure")
        return await event_next(event)

    bus = ExtensionEventBus()
    bus.register_event_handler(PROMPT_ATTACHMENT, handle, {})
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    manager = PromptAttachmentManager(event_bus=bus)
    for section, content in (("keep", "kept"), ("fail", "fallback"), ("omit", "hidden")):
        await manager.add_section(
            session_id="session-1",
            section=section,
            content=content,
            kind=PromptAttachmentKind.TEXT,
            source="test",
        )
    context = _context()

    message = await manager.sync_to_context(context, "session-1")

    assert message is not None
    assert "kept" in message.content
    assert "fallback" in message.content
    assert "hidden" not in message.content
    assert calls == ["fail", "keep", "omit"]


@pytest.mark.asyncio
async def test_attachment_event_cancellation_propagates_before_history_write() -> None:
    started = asyncio.Event()
    hold = asyncio.Event()

    async def wait_for_cancel(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptAttachmentOutput:
        del event, ops, event_next
        started.set()
        await hold.wait()
        raise AssertionError("cancelled attachment event resumed unexpectedly")

    bus = ExtensionEventBus()
    bus.register_event_handler(PROMPT_ATTACHMENT, wait_for_cancel, {})
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    manager = PromptAttachmentManager(event_bus=bus)
    await manager.add_section(
        session_id="session-1",
        section="cancelled",
        content="content",
        kind=PromptAttachmentKind.TEXT,
        source="test",
    )
    context = _context()

    sync_task = asyncio.create_task(manager.sync_to_context(context, "session-1"))
    await asyncio.wait_for(started.wait(), timeout=1)
    sync_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await sync_task
    assert len(context.get_messages()) == 1


def test_deep_agent_receives_prompt_attachment_bus_explicitly() -> None:
    bus = ExtensionEventBus()

    agent = DeepAgent(AgentCard(id="agent-1", name="agent-1"), extension_bus=bus)

    assert agent.prompt_attachment_manager.event_bus is bus
    agent.set_extension_bus(None)
    assert agent.prompt_attachment_manager.event_bus is None


def test_build_context_passes_bus_through_deep_agent_spec() -> None:
    bus = ExtensionEventBus()
    context = BuildContext(extension_bus=bus)

    assert extension_bus(context.derive()) is bus
    parts = DeepAgentSpec(enable_sys_operation=False).resolve_parts(context)

    assert parts.extension_bus is bus
    assert "extension_bus" not in DeepAgentSpec(enable_sys_operation=False).model_dump()
