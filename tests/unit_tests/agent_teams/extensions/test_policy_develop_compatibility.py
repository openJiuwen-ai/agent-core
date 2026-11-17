# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression coverage for the policy-builder move across release and develop."""

from inspect import signature
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.extensions import policy
from openjiuwen.agent_teams.extensions.events import build_team_policy_sections, register_team_event_types
from openjiuwen.agent_teams.extensions.models import TeamPolicyBuildInput, TeamPolicyContext
from openjiuwen.agent_teams.extensions.ops import current_policy_context
from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.rails.team_policy_rail import TeamPolicyRail
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TeamContextTracker
from openjiuwen.agent_teams.tools.tool_team import BuildTeamTool
from openjiuwen.core.events import Event, ExtensionEventBus
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.extensions.ops_build_handlers import team as team_ops


def _section(name):
    return PromptSection(name=name, content={"cn": name}, priority=11)


@pytest.mark.asyncio
async def test_real_policy_chain_keeps_capabilities_and_excludes_prefix(monkeypatch):
    """The moved CORE sees all four options, and disclosure excludes every prefix."""
    seen = {}

    def prefix(*, bootstrap_mode, bound_group_name, **kwargs):
        seen["prefix"] = (bootstrap_mode, bound_group_name)
        return [_section(name) for name in ("team_prebuilt", "team_bound_group", "team_extra")]

    def static(*, member_messaging, member_subtasks, **kwargs):
        seen["capabilities"] = (member_messaging, member_subtasks)
        return [_section("team_role")]

    monkeypatch.setattr(policy.prompt_sections, "build_leader_static_sections", prefix, raising=False)
    monkeypatch.setattr(policy, "build_team_static_sections", static)
    bus = ExtensionEventBus()
    register_team_event_types(bus)
    team_ops.register(bus, SimpleNamespace(resolve=AsyncMock(return_value=None)))
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    output = await build_team_policy_sections(
        TeamPolicyBuildInput(team_name="t", role=TeamRole.LEADER),
        TeamPolicyContext(
            member_messaging=True,
            member_subtasks=True,
            bootstrap_mode="framework",
            dispatch_mode="scheduled",
            bound_group_name="experts",
        ),
        bus=bus,
        session_id="s1",
        loader=load_template,
    )
    assert seen == {"prefix": ("framework", "experts"), "capabilities": (True, True)}
    assert [s.name for s in policy.select_policy_prefix_sections(output.sections)] == [
        "team_prebuilt",
        "team_bound_group",
        "team_extra",
    ]
    assert [s.name for s in policy.select_policy_disclosure_sections(output.sections)] == ["team_role"]


def test_unsupported_enabled_capability_is_not_silently_discarded(monkeypatch):
    def legacy(**kwargs):
        return []

    monkeypatch.setattr(policy, "build_team_static_sections", legacy)
    with pytest.raises(ValueError, match="member_messaging"):
        policy.build_default_policy_sections(
            TeamPolicyBuildInput(team_name="t", role=TeamRole.TEAMMATE),
            TeamPolicyContext(member_messaging=True),
        )


def test_autonomous_dispatch_does_not_add_bound_group_prefix():
    sections = policy.build_default_policy_sections(
        TeamPolicyBuildInput(team_name="t", role=TeamRole.LEADER),
        TeamPolicyContext(dispatch_mode="autonomous", bound_group_name="unused-group"),
    )
    assert "team_bound_group" not in {section.name for section in sections}


def test_backend_without_member_gates_leaves_capabilities_unspecified():
    assert policy.get_member_capability_context(SimpleNamespace()) == {}
    assert policy.get_member_capability_context(None) == {}


def test_backend_member_gates_report_effective_values():
    backend = SimpleNamespace(member_messaging_enabled=lambda: True, member_subtasks_enabled=lambda: False)
    assert policy.get_member_capability_context(backend) == {"member_messaging": True, "member_subtasks": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("messaging,subtasks", [(True, False), (False, True)])
async def test_build_team_disclosure_uses_effective_backend_capabilities(messaging, subtasks):
    bus = ExtensionEventBus()
    register_team_event_types(bus)
    team_ops.register(bus, SimpleNamespace(resolve=AsyncMock(return_value=None)))
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    seen = []

    async def capture(event, ops, next_handler):
        from openjiuwen.agent_teams.extensions.models import TeamPolicyBuildOutput

        seen.append(current_policy_context())
        return TeamPolicyBuildOutput(sections=(_section("effective_policy"),))

    bus.register_event_handler("team.build_policy", capture, match_pattern={})
    backend = SimpleNamespace(
        db=None,
        messager=None,
        team_name="t",
        member_name="leader",
        rejects_rebuild=AsyncMock(return_value=False),
        build_team=AsyncMock(),
        hitt_enabled=lambda: False,
        task_verification_enabled=lambda: False,
        team_taken_over=lambda: False,
        member_messaging_enabled=lambda: messaging,
        member_subtasks_enabled=lambda: subtasks,
    )
    tool = BuildTeamTool(backend, lambda *args, **kwargs: "stub", dispatch_mode="scheduled", extension_bus=bus)
    result = await tool.invoke(
        {"display_name": "team", "team_desc": "team", "leader_display_name": "lead", "leader_desc": "lead"}
    )
    assert result.success is True
    assert result.data["policy_disclosure"] == "effective_policy"
    assert len(seen) == 1
    assert seen[0].member_messaging is messaging
    assert seen[0].member_subtasks is subtasks
    backend.build_team.assert_awaited_once()


def test_framework_rejects_missing_renderer_before_building_sections(monkeypatch):
    from openjiuwen.agent_teams import inbound_render

    monkeypatch.delattr(inbound_render, "render_team_policy_inline", raising=False)
    with pytest.raises(ValueError, match="inline policy renderer"):
        TeamPolicyRail(role=TeamRole.LEADER, bootstrap_mode="framework")


@pytest.mark.asyncio
async def test_deferred_rail_exposes_effective_capabilities_to_handler():
    """The real rail enters the extension chain with backend gates in its snapshot."""
    bus = ExtensionEventBus()
    register_team_event_types(bus)
    team_ops.register(bus, SimpleNamespace(resolve=AsyncMock(return_value=None)))
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    seen = []

    async def capture(event, ops, next_handler):
        from openjiuwen.agent_teams.extensions.models import TeamPolicyBuildOutput

        seen.append(current_policy_context())
        return TeamPolicyBuildOutput(sections=(_section("team_role"),))

    bus.register_event_handler("team.build_policy", capture, match_pattern={})
    backend = SimpleNamespace(
        team_name="t",
        hitt_enabled=lambda: False,
        member_messaging_enabled=lambda: True,
        member_subtasks_enabled=lambda: True,
    )
    rail = TeamPolicyRail(role=TeamRole.TEAMMATE, team_backend=backend, extension_bus=bus)
    await rail._ensure_sections_built(SimpleNamespace(session=None))
    assert len(seen) == 1
    assert seen[0].member_messaging is True
    assert seen[0].member_subtasks is True
    await rail._ensure_sections_built(SimpleNamespace(session=None))
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_deferred_framework_policy_uses_extension_output(monkeypatch):
    """Framework disclosure comes from the extension result and omits its prefix."""
    from openjiuwen.agent_teams import inbound_render
    from openjiuwen.agent_teams.extensions.models import TeamPolicyBuildOutput

    monkeypatch.setattr(
        inbound_render,
        "render_team_policy_inline",
        lambda *, body: f"<team-policy>{body}</team-policy>",
        raising=False,
    )
    if "policy_text" not in signature(TeamContextTracker).parameters:
        # A renderer alone cannot make an older tracker deliver policy text.
        with pytest.raises(ValueError, match="policy_text delivery support"):
            TeamPolicyRail(role=TeamRole.LEADER, bootstrap_mode="framework")
        return
    bus = ExtensionEventBus()
    register_team_event_types(bus)
    team_ops.register(bus, SimpleNamespace(resolve=AsyncMock(return_value=None)))
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))

    async def custom_policy(event, ops, next_handler):
        return TeamPolicyBuildOutput(
            sections=(
                _section("team_prebuilt"),
                _section("team_extra"),
                _section("business_policy"),
            )
        )

    bus.register_event_handler("team.build_policy", custom_policy, match_pattern={})
    rail = TeamPolicyRail(role=TeamRole.LEADER, bootstrap_mode="framework", extension_bus=bus)
    assert getattr(rail._tracker, "_policy_text", None) is None
    await rail._ensure_sections_built(SimpleNamespace(session=None))
    assert rail._tracker._policy_text == "<team-policy>business_policy</team-policy>"
    assert [section.name for section in rail._static_sections] == ["team_prebuilt", "team_extra"]
    assert "<team-policy>business_policy</team-policy>" in await rail._tracker.snapshot_text()
