# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Expert-table derivation: AgentGroup members become swarmflow expert workers."""
from __future__ import annotations

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import (
    BuiltinToolSpec,
    DeepAgentSpec,
    ProgressiveToolSpec,
    RailSpec,
)
from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec, ExternalCliMemberSpec, TeamMemberSpec, TeamRole
from openjiuwen.agent_teams.workflow.backends.expert_specs import derive_swarmflow_expert_specs

_SNAPSHOT = {"agent_card": {"id": "clip", "name": "视频镜头师"}}


def _team(**overrides) -> TeamAgentSpec:
    agents = overrides.pop(
        "agents",
        {
            "leader": DeepAgentSpec(),
            "teammate": DeepAgentSpec(tools=[], rails=[RailSpec(type="core.team.skill_use")]),
            "clip": DeepAgentSpec(
                tools=[],
                rails=[RailSpec(type="core.team.skill_use")],
                agent_template_spec=_SNAPSHOT,
                enable_task_loop=True,
            ),
        },
    )
    members = overrides.pop(
        "predefined_members",
        [TeamMemberSpec(member_name="clip", display_name="视频镜头师", prompt="PERSONA")],
    )
    return TeamAgentSpec(
        agents=agents, team_name="wf", enable_swarmflow=True, predefined_members=members, **overrides
    )


def test_native_member_becomes_expert_entry_with_persona_and_snapshot():
    experts = derive_swarmflow_expert_specs(_team(), [RailSpec(type="core.team.observability")])
    entry = experts["clip"]
    assert entry.display_name == "视频镜头师"
    assert entry.persona == "PERSONA"
    assert entry.external_cli is None and entry.is_external is False
    assert entry.spec.agent_template_spec == _SNAPSHOT
    assert entry.spec.enable_task_loop is True


def test_expert_spec_keeps_rails_appends_observability_and_clears_tools():
    base_rail = RailSpec(type="core.team.skill_use")
    obs_rail = RailSpec(type="core.team.observability")
    tool = BuiltinToolSpec(type="core.web_search")
    team = _team(agents={
        "leader": DeepAgentSpec(),
        "clip": DeepAgentSpec(tools=[tool], rails=[base_rail], agent_template_spec=_SNAPSHOT),
    })
    entry = derive_swarmflow_expert_specs(team, [obs_rail])["clip"]
    assert entry.spec.tools == []
    assert entry.spec.rails == [base_rail, obs_rail]
    assert team.agents["clip"].tools == [tool]  # source spec untouched


def test_leader_human_bridge_are_not_experts():
    team = _team(predefined_members=[
        TeamMemberSpec(member_name="leader", display_name="L", role_type=TeamRole.LEADER),
        TeamMemberSpec(member_name="clip", display_name="C", prompt="P"),
        TeamMemberSpec(member_name="guest", display_name="G", role_type=TeamRole.HUMAN_AGENT),
    ])
    assert set(derive_swarmflow_expert_specs(team, [])) == {"clip"}


def test_external_member_is_listed_and_flagged():
    team = _team(predefined_members=[
        ExternalCliMemberSpec(
            member_name="writer", display_name="W", prompt="P",
            external_cli=ExternalCliAgentSpec(cli_agent="codex"),
        ),
    ])
    entry = derive_swarmflow_expert_specs(team, [])["writer"]
    assert entry.is_external is True
    assert entry.spec is None
    assert entry.persona == "P"


def test_native_member_without_snapshot_is_not_an_expert():
    """An ordinary predefined teammate (no template snapshot) is skipped, not an error."""
    team = _team(agents={"leader": DeepAgentSpec(), "clip": DeepAgentSpec(tools=[])})
    assert derive_swarmflow_expert_specs(team, []) == {}


def test_progressive_tool_follows_team_switch():
    entry = derive_swarmflow_expert_specs(_team(progressive_tool=True), [])["clip"]
    assert entry.spec.progressive_tool == ProgressiveToolSpec(enabled=True)
    assert derive_swarmflow_expert_specs(_team(), [])["clip"].spec.progressive_tool is None


def test_rails_argument_must_be_a_list_not_the_spec_factory():
    """The configurator imports ``observability_rail_specs`` as a factory and, elsewhere,
    passes its evaluated list. Handing the bare function here raised ``TypeError`` only
    when a team actually had an expert — i.e. only in the AgentGroup scenario this feature
    exists for — and broke team setup. Fail loudly on any non-list, with a pointer."""
    import pytest

    def observability_rail_specs():
        return []

    with pytest.raises(TypeError, match="observability_rail_specs\(\)"):
        derive_swarmflow_expert_specs(_team(), observability_rail_specs)


def test_bound_group_loader_assembles_members_like_the_expert_table(tmp_path):
    import asyncio

    from openjiuwen.agent_teams.experts import load_agent_group_package
    from openjiuwen.agent_teams.experts.template_member import compose_template_member
    from openjiuwen.agent_teams.workflow.backends.expert_specs import BoundGroupExpertLoader
    from tests.unit_tests.agent_teams.tools.test_tool_expert import FakeCatalog, _group_package

    group_dir = _group_package(tmp_path / "designer")
    base = DeepAgentSpec(language="cn", tools=[BuiltinToolSpec(type="core.web_search")], rails=[RailSpec(type="r")])
    loader = BoundGroupExpertLoader(
        catalog=FakeCatalog({}, {"designer": group_dir}), group="designer", worker_base_spec=base
    )

    clip = asyncio.run(loader.load("clip"))
    external = asyncio.run(loader.load("cli-member"))

    expected = compose_template_member(base, load_agent_group_package(group_dir)["clip"])
    assert (clip.member_name, clip.display_name, clip.persona) == ("clip", "视频镜头师", expected.persona)
    assert clip.spec.tools == [], "capabilities come from the template snapshot alone"
    assert clip.spec.agent_template_spec == expected.spec.agent_template_spec
    assert clip.spec.rails == base.rails
    assert external.is_external and external.spec is None
    assert asyncio.run(loader.load("leader")) is None
    assert asyncio.run(loader.load("ghost")) is None
