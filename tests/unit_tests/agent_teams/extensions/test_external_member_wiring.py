# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""External CLI members: the system prompt runs through the build entry."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.extensions.events import (
    TEAM_BUILD_POLICY,
    register_team_event_types,
)
from openjiuwen.agent_teams.extensions.models import TeamPolicyBuildOutput
from openjiuwen.agent_teams.prompts import build_team_member_system_prompt
from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.rails.team_context import TeamHandleKey
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext
from openjiuwen.agent_teams.spawn.external_cli_spawn import _build_member_system_prompt
from openjiuwen.core.events import Event, ExtensionEventBus, HandlerLevel
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE
from openjiuwen.core.single_agent.prompts.builder import PromptSection


async def _ready_bus() -> ExtensionEventBus:
    """Create a host-initialized bus with the team events registered."""
    bus = ExtensionEventBus()
    register_team_event_types(bus)
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


def _stub_spec(bus: ExtensionEventBus | None) -> Any:
    """Spec stand-in carrying what the prompt builder reads."""
    extras = {TeamHandleKey.EXTENSION_BUS: bus} if bus is not None else {}
    return SimpleNamespace(
        build_context=SimpleNamespace(extras=extras),
        team_name="research-team",
        lifecycle="temporary",
        teammate_mode="build_mode",
        dispatch_mode="autonomous",
        enable_member_messaging=False,
        max_subtask_depth=0,
        team_mode="default",
        predefined_members=[],
        expose_human_agents_to_teammates=False,
    )


def _member_ctx() -> TeamRuntimeContext:
    return TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="researcher",
        cli_agent="claude",
    )


@pytest.mark.asyncio
async def test_external_prompt_runs_through_the_build_chain() -> None:
    """A wired spec routes the CLI member's sections through the chain."""

    async def append_section(event, _ops, next_handler):
        chain_output = await next_handler(event)
        assert isinstance(chain_output, TeamPolicyBuildOutput)
        extra = PromptSection(name="business_extra", content={"cn": "business norm body"}, priority=50)
        return TeamPolicyBuildOutput(sections=(*chain_output.sections, extra))

    bus = await _ready_bus()
    bus.register_event_handler(
        TEAM_BUILD_POLICY,
        append_section,
        match_pattern={"role": "teammate"},
        handler_level=HandlerLevel.USER,
    )
    prompt = await _build_member_system_prompt(
        _stub_spec(bus),
        _member_ctx(),
        "researcher",
        hitt_enabled=False,
        session_id="session-1",
    )
    assert prompt is not None
    # The handler's section reached the CLI member's system prompt.
    assert "business norm body" in prompt


@pytest.mark.asyncio
async def test_external_prompt_without_bus_renders_default() -> None:
    """An unwired spec keeps the local default assembly, unchanged."""
    prompt = await _build_member_system_prompt(
        _stub_spec(None),
        _member_ctx(),
        "researcher",
        hitt_enabled=False,
        session_id="session-1",
    )
    assert prompt is not None
    assert "business norm body" not in prompt
    # The default teammate policy is in the prompt (external variant), and
    # the MCP namespace wrapper is applied for a non-codex CLI member.
    assert "business norm body" not in prompt
    assert "<team-policy" in prompt


@pytest.mark.asyncio
async def test_member_prompt_renders_prebuilt_sections() -> None:
    """build_team_member_system_prompt renders given sections as-is."""
    custom = PromptSection(name="custom_rule", content={"cn": "custom rule body"}, priority=50)
    prompt = build_team_member_system_prompt(
        role=TeamRole.TEAMMATE,
        member_name="researcher",
        language="cn",
        loader=load_template,
        sections=(custom,),
    )
    assert "custom rule body" in prompt


@pytest.mark.asyncio
async def test_variant_rides_on_the_build_input() -> None:
    """The external variant selects the external teammate template."""
    from openjiuwen.agent_teams.extensions.models import TeamPolicyBuildInput, TeamPolicyContext
    from openjiuwen.agent_teams.extensions.policy import build_default_policy_sections
    from openjiuwen.agent_teams.prompts.sections import TeamSectionName

    build_input = TeamPolicyBuildInput(
        team_name="research-team",
        role=TeamRole.TEAMMATE,
        member_name="researcher",
        workspace_prompt_variant="external",
    )
    sections = build_default_policy_sections(build_input, TeamPolicyContext(language="cn"))
    role = next(s for s in sections if s.name == TeamSectionName.ROLE)
    native = build_default_policy_sections(
        TeamPolicyBuildInput(team_name="research-team", role=TeamRole.TEAMMATE, member_name="researcher"),
        TeamPolicyContext(language="cn"),
    )
    native_role = next(s for s in native if s.name == TeamSectionName.ROLE)
    assert role.content != native_role.content
