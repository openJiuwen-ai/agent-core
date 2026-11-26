# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent Loop integration tests for final prompt composition events."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import uuid

import pytest

from openjiuwen.agent_teams.extensions.events import register_team_event_types
from openjiuwen.agent_teams.harness.native_harness import NativeHarness
from openjiuwen.agent_teams.harness.team_harness import TeamHarness
from openjiuwen.agent_teams.rails.elements import TEAM_POLICY
from openjiuwen.agent_teams.rails.team_context import TeamHandleKey
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.events import Event, ExtensionEventBus
from openjiuwen.core.events.builtins.event_types.prompt_attachment import (
    PROMPT_ATTACHMENT,
    PromptAttachmentOutput,
)
from openjiuwen.core.events.builtins.event_types.prompt_compose import PROMPT_COMPOSE
from openjiuwen.core.events.builtins.event_types.prompt_section import (
    PROMPT_SECTION,
    PromptSectionOutput,
)
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, HandlerLevel
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent_team import Session as TeamSession
from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentKind
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec, ModelSpec, RailSpec
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


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True], ids=["run_once", "streaming"])
async def test_team_members_route_prompt_events_to_the_current_agent(tmp_path, streaming: bool) -> None:
    bus = ExtensionEventBus()
    register_team_event_types(bus)
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    team_session = TeamSession()
    session_id = team_session.get_session_id()
    runtime = get_agent_runtime()
    members: dict[str, NativeHarness] = {}
    compose_calls: list[tuple[str, str | None]] = []
    section_calls: list[tuple[str, str | None, str]] = []
    attachment_calls: list[tuple[str, str | None, str | None]] = []

    async def observe_compose(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        assert ops is not None and ops.session_context is not None
        compose_calls.append((ops.session_context.session_id, ops.session_context.agent_id))
        # A session-only lookup would now return the other member's builder.
        for agent_id, native in members.items():
            if agent_id != ops.session_context.agent_id:
                runtime.bind(session_id, native)
        return await event_next(event)

    async def rewrite_section(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptSectionOutput:
        assert ops is not None and ops.session_context is not None
        section_calls.append((ops.session_context.session_id, ops.session_context.agent_id, event.event_input.name))
        output = await event_next(event)
        assert isinstance(output, PromptSectionOutput)
        return replace(output, text=f"section:{ops.session_context.agent_id}:{output.text}")

    async def rewrite_attachment(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> PromptAttachmentOutput:
        assert ops is not None and ops.session_context is not None
        attachment_calls.append(
            (ops.session_context.session_id, ops.session_context.agent_id, event.event_input.agent_id)
        )
        output = await event_next(event)
        assert isinstance(output, PromptAttachmentOutput)
        return replace(output, text=f"attachment:{ops.session_context.agent_id}:{output.text}")

    bus.register_event_handler(PROMPT_COMPOSE, observe_compose, {}, HandlerLevel.USER)
    bus.register_event_handler(PROMPT_SECTION, rewrite_section, {"name": "team_bootstrap"}, HandlerLevel.USER)
    bus.register_event_handler(PROMPT_SECTION, rewrite_section, {"name": "team_role"}, HandlerLevel.USER)
    bus.register_event_handler(PROMPT_ATTACHMENT, rewrite_attachment, {"section": "member-notes"}, HandlerLevel.USER)

    roles = (TeamRole.LEADER, TeamRole.TEAMMATE)
    async with harness_runner(bus=bus):
        model = fake_model()
        harnesses = {}
        requests = {}
        try:
            for role in roles:
                harness = TeamHarness.build(
                    agent_spec=DeepAgentSpec(
                        card=AgentCard(id=role.value, name=role.value),
                        model=ModelSpec(
                            model_client_config=model.model_client_config,
                            model_request_config=model.model_config,
                        ),
                        system_prompt=f"{role.value} identity",
                        cwd=str(tmp_path),
                        language="en",
                        enable_task_loop=streaming,
                        enable_sys_operation=False,
                        enable_read_image_multimodal=False,
                        rails=[RailSpec(type=TEAM_POLICY)],
                    ),
                    role=role,
                    member_name=role.value,
                    build_context=BuildContext(
                        role=role.value,
                        member_name=role.value,
                        language="en",
                        extension_bus=bus,
                        extras={TeamHandleKey.EXTENSION_BUS: bus},
                    ),
                )
                harnesses[role] = harness
                native = harness._native
                assert native is not None
                members[native.card.id] = native
                await native.prompt_attachment_manager.add_section(
                    session_id=session_id,
                    section="member-notes",
                    content=f"{role.value} notes",
                    kind=PromptAttachmentKind.TEXT,
                    source="test",
                )
                if streaming:
                    await harness.start(team_session=team_session)
                    assert runtime.get(session_id, agent_id=native.card.id) is native
            for role, harness in harnesses.items():
                mock_llm = MockLLMModel()
                mock_llm.set_responses([create_text_response("done")])
                with patch_mock_llm(mock_llm):
                    if streaming:
                        await _run_team_streaming_round(harness)
                    else:
                        await harness.run_once("check the member prompt", team_session=team_session)
                requests[role] = mock_llm.call_history[0]
            if streaming:
                await harnesses[TeamRole.LEADER].stop()
                assert runtime.get(session_id, agent_id=TeamRole.LEADER.value) is None
                assert runtime.get(session_id, agent_id=TeamRole.TEAMMATE.value) is members[TeamRole.TEAMMATE.value]
                await harnesses[TeamRole.TEAMMATE].stop()
                assert runtime.get(session_id, agent_id=TeamRole.TEAMMATE.value) is None
        finally:
            for harness in harnesses.values():
                await harness.dispose()
            for native in members.values():
                runtime.unbind(session_id, native)

    assert compose_calls == [(session_id, role.value) for role in roles]
    assert section_calls == [(session_id, "leader", "team_bootstrap"), (session_id, "teammate", "team_role")]
    assert attachment_calls == [(session_id, role.value, role.value) for role in roles]
    role_intros = {
        TeamRole.LEADER: "You are the TeamLeader",
        TeamRole.TEAMMATE: "You are Teammate",
    }
    for role, other in zip(roles, reversed(roles)):
        messages = requests[role]
        assert f"{role.value} identity" in messages[0].content
        assert f"{other.value} identity" not in messages[0].content
        assert f"section:{role.value}:" in messages[0].content
        assert role_intros[role] in messages[0].content
        assert role_intros[other] not in messages[0].content
        assert any(f"attachment:{role.value}:{role.value} notes" in msg.content for msg in messages)
        assert all(f"{other.value} notes" not in msg.content for msg in messages)


async def _run_team_streaming_round(harness: TeamHarness) -> None:
    finished = asyncio.Event()
    outcomes: list[str] = []

    async def on_round(kind: str) -> None:
        if kind in {"finished", "failed"}:
            outcomes.append(kind)
            finished.set()

    await harness.subscribe(on_round=on_round)
    await harness.send("check the member prompt")
    await asyncio.wait_for(finished.wait(), timeout=10)
    assert outcomes == ["finished"]
