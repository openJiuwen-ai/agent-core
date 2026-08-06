# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for teammate-visible context under team worktree isolation."""

from __future__ import annotations

from types import SimpleNamespace

from openjiuwen.agent_teams.agent import agent_configurator as configurator_module
from openjiuwen.agent_teams.agent.agent_configurator import AgentConfigurator
from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def test_worktree_teammate_build_context_uses_worktree_as_project_dir(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(workspace=None, sys_operation=None, model=None)

    monkeypatch.setattr(configurator_module.TeamHarness, "build", fake_build)

    configurator = AgentConfigurator(card=AgentCard(id="team", name="team", description="team"))
    source_project = "/repo/source"
    worktree_path = "/repo/.worktrees/dev-writer"
    spec = TeamAgentSpec(
        team_name="team",
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        build_context=BuildContext(project_dir=source_project),
        build_context_seed={"project_dir": source_project},
    )
    team_spec = TeamSpec(team_name="team", display_name="team", leader_member_name="leader")
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="dev-writer",
        team_spec=team_spec,
        worktree_path=worktree_path,
    )

    configurator.setup_agent(spec, ctx)

    assert captured["role"] is TeamRole.TEAMMATE
    assert captured["build_context"].project_dir == worktree_path
    # Isolation moves the cwd into the worktree; the workspace stays the
    # member's own directory so artifacts survive worktree removal.
    assert captured["agent_spec"].cwd == worktree_path
    assert captured["agent_spec"].workspace.root_path != worktree_path
    assert "dev-writer" in captured["agent_spec"].workspace.root_path


def test_project_cwd_keeps_member_workspace_private(monkeypatch):
    """Without isolation, cwd follows the spec while the workspace stays private.

    cwd and workspace are separate layers: a member told to work in a project
    directory still keeps its own workspace for artifacts, memory and the
    ``.team`` mount.
    """
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(workspace=None, sys_operation=None, model=None)

    monkeypatch.setattr(configurator_module.TeamHarness, "build", fake_build)

    configurator = AgentConfigurator(card=AgentCard(id="team", name="team", description="team"))
    project_dir = "/repo/source"
    spec = TeamAgentSpec(
        team_name="team",
        agents={
            "leader": DeepAgentSpec(),
            "teammate": DeepAgentSpec(cwd=project_dir, project_root=project_dir),
        },
        build_context=BuildContext(project_dir=project_dir),
        build_context_seed={"project_dir": project_dir},
    )
    team_spec = TeamSpec(team_name="team", display_name="team", leader_member_name="leader")
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="dev-writer",
        team_spec=team_spec,
    )

    configurator.setup_agent(spec, ctx)

    agent_spec = captured["agent_spec"]
    assert agent_spec.cwd == project_dir
    assert agent_spec.project_root == project_dir
    assert agent_spec.workspace.root_path != project_dir
    assert "dev-writer" in agent_spec.workspace.root_path
