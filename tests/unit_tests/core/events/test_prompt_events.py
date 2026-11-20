# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Contract tests for the builtin ``prompt.section`` and ``prompt.compose`` events."""

from __future__ import annotations

from dataclasses import replace

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events import Event, ExtensionEventBus
from openjiuwen.core.events.builtins.event_types.prompt_compose import (
    PROMPT_COMPOSE,
    PromptComposeInput,
    PromptComposeOutput,
    PromptOutputStyle,
    PromptSectionSpec,
)
from openjiuwen.core.events.builtins.event_types.prompt_section import (
    PROMPT_SECTION,
    PromptSectionInput,
    PromptSectionOutput,
)
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, HandlerLevel, SessionContext


async def _build_bus() -> ExtensionEventBus:
    bus = ExtensionEventBus()
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


def _compose_input() -> PromptComposeInput:
    return PromptComposeInput(
        model="mock-model",
        prompt_model=None,
        surfaces=(),
        tools=("read_file",),
        output_style=PromptOutputStyle(name="default", is_keeping_coding_instructions=True),
        traits=(),
        detail={"language": "en"},
    )


@pytest.mark.asyncio
async def test_prompt_section_core_passes_text_and_user_handler_can_rewrite() -> None:
    bus = await _build_bus()

    async def rewrite(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptSectionOutput:
        del ops
        output = await event_next(event)
        assert isinstance(output, PromptSectionOutput)
        if event.event_input.name == "identity":
            return replace(output, text="rewritten identity")
        return output

    bus.register_event_handler(PROMPT_SECTION, rewrite, {"name": "identity"}, HandlerLevel.USER)

    output = await bus.trigger(
        Event(
            PROMPT_SECTION,
            PromptSectionInput(name="identity", text="original identity", detail={"priority": 10}),
        )
    )

    assert output == PromptSectionOutput(text="rewritten identity")


@pytest.mark.asyncio
async def test_prompt_section_rejects_unknown_match_fields() -> None:
    bus = await _build_bus()

    async def passthrough(event: Event, ops: EventHandlerOps | None, event_next: EventNext) -> object:
        del ops
        return await event_next(event)

    bus.register_event_handler(PROMPT_SECTION, passthrough, {"category": "system_prompt"})

    with pytest.raises(BaseError):
        await bus.trigger(
            Event(PROMPT_SECTION, PromptSectionInput(name="identity", text="text", detail={}))
        )


@pytest.mark.asyncio
async def test_prompt_section_handler_can_omit_a_section() -> None:
    bus = await _build_bus()

    async def omit(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptSectionOutput:
        del event, ops, event_next
        return PromptSectionOutput(text=None)

    bus.register_event_handler(PROMPT_SECTION, omit, {}, HandlerLevel.USER)
    output = await bus.trigger(
        Event(PROMPT_SECTION, PromptSectionInput(name="optional", text="text", detail={}))
    )

    assert output == PromptSectionOutput(text=None)


@pytest.mark.asyncio
async def test_prompt_compose_output_validator_rejects_duplicate_section_ids() -> None:
    bus = await _build_bus()

    async def duplicate(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptComposeOutput:
        del event, ops, event_next
        spec = PromptSectionSpec(
            id="identity",
            text="identity",
            priority=10,
            category="system_prompt",
            carrier="system_message",
            detail={},
        )
        return PromptComposeOutput(sections=(spec, spec))

    bus.register_event_handler(PROMPT_COMPOSE, duplicate, {}, HandlerLevel.USER)

    with pytest.raises(BaseError):
        await bus.trigger(Event(PROMPT_COMPOSE, _compose_input()))


@pytest.mark.asyncio
async def test_prompt_compose_core_uses_bound_agent_and_triggers_sections_in_order() -> None:
    from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.harness.prompts import PromptSection, SystemPromptBuilder
    from openjiuwen.harness.schema.config import DeepAgentConfig

    session = SessionContext("prompt-compose-core-session")
    agent = DeepAgent(AgentCard(id="prompt-agent", name="prompt-agent", description="prompt agent"))
    agent.configure(DeepAgentConfig(enable_task_loop=False))
    builder = SystemPromptBuilder(language="en")
    builder.add_section(PromptSection("first", {"en": "first"}, priority=20, category="system_prompt"))
    builder.add_section(PromptSection("second", {"en": "second"}, priority=10, category="memory"))
    agent.system_prompt_builder = builder
    get_agent_runtime().bind(session.session_id, agent)
    bus = await _build_bus()
    seen: list[str] = []

    async def observe_section(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptSectionOutput:
        del ops
        seen.append(event.event_input.name)
        return await event_next(event)

    bus.register_event_handler(PROMPT_SECTION, observe_section, {}, HandlerLevel.USER)
    try:
        output = await bus.trigger(Event(PROMPT_COMPOSE, _compose_input()), session)
    finally:
        get_agent_runtime().unbind(session.session_id, agent)

    assert isinstance(output, PromptComposeOutput)
    assert seen == ["second", "first"]
    assert [section.id for section in output.sections] == ["second", "first"]
    assert [section.text for section in output.sections] == ["second", "first"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_ids"),
    [
        ("full", ["identity", "safety", "extra"]),
        ("minimal", ["identity", "safety"]),
        ("none", ["identity"]),
    ],
)
async def test_prompt_compose_core_preserves_builder_prompt_mode(mode: str, expected_ids: list[str]) -> None:
    from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.harness.prompts import PromptMode, PromptSection, SystemPromptBuilder
    from openjiuwen.harness.schema.config import DeepAgentConfig

    session = SessionContext(f"prompt-compose-mode-{mode}")
    agent = DeepAgent(AgentCard(id=f"prompt-agent-{mode}", name=f"prompt-agent-{mode}", description="prompt agent"))
    agent.configure(DeepAgentConfig(enable_task_loop=False))
    builder = SystemPromptBuilder(language="en", mode=PromptMode(mode))
    for name, priority in (("identity", 10), ("safety", 20), ("extra", 30)):
        builder.add_section(PromptSection(name, {"en": name}, priority=priority))
    agent.system_prompt_builder = builder
    get_agent_runtime().bind(session.session_id, agent)
    bus = await _build_bus()
    try:
        output = await bus.trigger(Event(PROMPT_COMPOSE, _compose_input()), session)
    finally:
        get_agent_runtime().unbind(session.session_id, agent)

    assert [section.id for section in output.sections] == expected_ids
