# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the builtin ``prompt.attachment`` event contract."""

from dataclasses import replace

import pytest

from openjiuwen.core.events import Event, EventHandlerOps, ExtensionEventBus
from openjiuwen.core.events.builtins.event_types.prompt_attachment import (
    PROMPT_ATTACHMENT,
    PromptAttachmentInput,
    PromptAttachmentOutput,
)
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, HandlerLevel, SessionContext


async def _built_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


@pytest.mark.asyncio
async def test_prompt_attachment_core_is_registered_and_passes_text_through() -> None:
    bus = await _built_bus()
    event_input = PromptAttachmentInput(
        type="memory",
        text="memory text",
        origin={"kind": "engine"},
        agent_id="agent-1",
        detail={"section": "memory", "metadata": {"provider": "test"}},
    )

    output = await bus.trigger(
        Event(PROMPT_ATTACHMENT, event_input),
        SessionContext("session-1"),
    )

    assert output == PromptAttachmentOutput(text="memory text")
    assert event_input.origin["kind"] == "engine"
    assert event_input.detail["metadata"] == {"provider": "test"}


@pytest.mark.asyncio
async def test_prompt_attachment_handler_can_rewrite_matching_type() -> None:
    seen: list[str] = []

    async def rewrite(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptAttachmentOutput:
        del ops
        event_input = event.event_input
        assert isinstance(event_input, PromptAttachmentInput)
        seen.append(event_input.type)
        return await event_next(
            Event(
                PROMPT_ATTACHMENT,
                replace(event_input, text=f"rewritten: {event_input.text}"),
            )
        )

    bus = ExtensionEventBus()
    bus.register_event_handler(PROMPT_ATTACHMENT, rewrite, {"type": "memory"}, HandlerLevel.USER)
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    output = await bus.trigger(
        Event(
            PROMPT_ATTACHMENT,
            PromptAttachmentInput(type="memory", text="original"),
        )
    )

    assert output == PromptAttachmentOutput(text="rewritten: original")
    assert seen == ["memory"]


@pytest.mark.asyncio
async def test_prompt_attachment_matcher_supports_detail_and_origin_fields() -> None:
    seen: list[str] = []

    async def observe(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptAttachmentOutput:
        del ops
        seen.append(event.event_input.text)
        return await event_next(event)

    bus = ExtensionEventBus()
    bus.register_event_handler(
        PROMPT_ATTACHMENT,
        observe,
        {"origin": {"kind": "plugin"}, "detail": {"source": "plugin.test"}},
    )
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    await bus.trigger(
        Event(
            PROMPT_ATTACHMENT,
            PromptAttachmentInput(
                type="text",
                text="matched",
                origin={"kind": "plugin", "event": "plugin.create"},
                detail={"source": "plugin.test"},
            ),
        )
    )
    await bus.trigger(
        Event(
            PROMPT_ATTACHMENT,
            PromptAttachmentInput(
                type="text",
                text="not matched",
                origin={"kind": "engine"},
                detail={"source": "plugin.test"},
            ),
        )
    )

    assert seen == ["matched"]
