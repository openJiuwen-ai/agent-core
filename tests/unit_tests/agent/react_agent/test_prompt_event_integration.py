# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Loop integration tests for final prompt composition events."""

from __future__ import annotations

from dataclasses import replace
import uuid

import pytest

from openjiuwen.core.events import Event
from openjiuwen.core.events.builtins.event_types.prompt_compose import PROMPT_COMPOSE
from openjiuwen.core.events.builtins.event_types.prompt_section import (
    PROMPT_SECTION,
    PromptSectionOutput,
)
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import HandlerLevel
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import PromptSection
from tests.unit_tests.core.events.conftest import (
    build_extension_bus,
    default_agent_options,
    fake_model,
    harness_runner,
    patch_mock_llm,
)
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response


@pytest.mark.asyncio
async def test_prompt_section_rewrite_reaches_the_final_model_request(tmp_path) -> None:
    bus = await build_extension_bus()
    seen: list[str] = []

    async def rewrite_identity(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptSectionOutput:
        del ops
        seen.append(event.event_input.name)
        output = await event_next(event)
        assert isinstance(output, PromptSectionOutput)
        if event.event_input.name == "identity":
            return replace(output, text="rewritten by prompt.section")
        return output

    bus.register_event_handler(PROMPT_SECTION, rewrite_identity, {}, HandlerLevel.USER)
    mock_llm = MockLLMModel()
    mock_llm.set_responses([create_text_response("done")])

    async with harness_runner(bus=bus):
        agent = create_deep_agent(
            model=fake_model(),
            card=AgentCard(id=f"prompt_section_{uuid.uuid4().hex[:8]}", name="PromptSectionAgent"),
            system_prompt="original identity",
            max_iterations=2,
            **default_agent_options(tmp_path),
        )
        with patch_mock_llm(mock_llm):
            await Runner.run_agent(
                agent=agent,
                inputs={"query": "compose prompt", "conversation_id": f"prompt_{uuid.uuid4().hex[:8]}"},
            )

    assert seen and seen[0] == "identity"
    assert mock_llm.call_history
    system_message = mock_llm.call_history[0][0]
    assert "rewritten by prompt.section" in system_message.content


@pytest.mark.asyncio
async def test_prompt_compose_output_order_is_used_by_the_final_renderer(tmp_path) -> None:
    bus = await build_extension_bus()
    expected_content: list[str] = []

    async def reverse_sections(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del ops
        output = await event_next(event)
        assert hasattr(output, "sections")
        reversed_sections = tuple(reversed(output.sections))
        expected_content.extend(section.text for section in reversed_sections if section.text)
        return replace(output, sections=reversed_sections)

    bus.register_event_handler(PROMPT_COMPOSE, reverse_sections, {}, HandlerLevel.USER)
    mock_llm = MockLLMModel()
    mock_llm.set_responses([create_text_response("done")])

    async with harness_runner(bus=bus):
        agent = create_deep_agent(
            model=fake_model(),
            card=AgentCard(id=f"prompt_compose_{uuid.uuid4().hex[:8]}", name="PromptComposeAgent"),
            system_prompt="identity section",
            max_iterations=2,
            **default_agent_options(tmp_path),
        )
        assert agent.system_prompt_builder is not None
        agent.system_prompt_builder.add_section(
            PromptSection(name="extra", content={"cn": "extra section", "en": "extra section"}, priority=20)
        )
        with patch_mock_llm(mock_llm):
            await Runner.run_agent(
                agent=agent,
                inputs={"query": "compose order", "conversation_id": f"prompt_{uuid.uuid4().hex[:8]}"},
            )

    assert mock_llm.call_history
    system_message = mock_llm.call_history[0][0]
    assert system_message.content == "\n\n".join(expected_content)
