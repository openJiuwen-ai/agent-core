# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Contract tests for expert-group Catalog / Launcher adapters and org tools."""

from __future__ import annotations

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.organization.expert_adapters import (
    ExpertGroupDescriptor,
    LaunchedExpertTeam,
)
from openjiuwen.agent_teams.organization.pool import clear_process_org_managers
from openjiuwen.agent_teams.organization.runtime import OrganizationRuntimeManager
from openjiuwen.agent_teams.organization.tools import (
    OrgCreateAndInviteExpertTeamTool,
    OrgListExpertGroupsTool,
    create_org_control_tools,
)
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase


class FakeMessager:
    def __init__(self) -> None:
        self.published = []
        self.subscriptions = []

    async def publish(self, topic_id, message):
        self.published.append((topic_id, message))

    async def subscribe(self, topic_id, handler):
        self.subscriptions.append((topic_id, handler))


class FakeHarness:
    def __init__(self) -> None:
        self.tools = []
        self.system_prompt_builder = FakePromptBuilder()

    def add_tool(self, tool) -> None:
        self.tools.append(tool)

    def remove_tool(self, name) -> None:
        self.tools = [tool for tool in self.tools if tool.card.name != name]


class FakePromptBuilder:
    def __init__(self) -> None:
        self.sections = {}

    def add_section(self, section) -> None:
        self.sections[section.name] = section

    def remove_section(self, name) -> None:
        self.sections.pop(name, None)


class FakeBackend:
    def __init__(self, *, team_name, leader_id, db, messager) -> None:
        self.team_name = team_name
        self.member_name = leader_id
        self.leader_member_name = leader_id
        self.is_leader = True
        self.db = db
        self.messager = messager
        self.org_task_manager = None
        self.org_transport = None


class FakeAgent:
    def __init__(self, backend, *, capabilities=None) -> None:
        self.team_backend = backend
        self.member_name = backend.member_name
        self.harness = FakeHarness()
        self.spec = type(
            "Spec",
            (),
            {"metadata": {"capabilities": list(capabilities or ["analysis"])}},
        )()


class FakeCatalog:
    def __init__(self, groups: list[ExpertGroupDescriptor]) -> None:
        self._groups = list(groups)

    def list(self, *, capabilities: set[str] | None = None) -> list[ExpertGroupDescriptor]:
        if not capabilities:
            return list(self._groups)
        required = set(capabilities)
        return [
            group
            for group in self._groups
            if required.issubset(set(group.capabilities))
        ]

    def get(self, name: str) -> ExpertGroupDescriptor:
        for group in self._groups:
            if group.agent_group_name == name:
                return group
        raise ValueError(f"expert group not found: {name}")


class FakeLauncher:
    def __init__(self, *, team_id: str, leader_id: str, agent_group_name: str = "") -> None:
        self.team_id = team_id
        self.leader_id = leader_id
        self.agent_group_name = agent_group_name
        self.launch_calls: list[dict] = []
        self.stop_calls: list[dict] = []
        self.fail_launch = False

    async def launch(
        self,
        *,
        organization_id: str,
        agent_group_name: str,
        session_id: str,
        display_name: str | None = None,
        share_db_from_team_id: str | None = None,
    ) -> LaunchedExpertTeam:
        self.launch_calls.append(
            {
                "organization_id": organization_id,
                "agent_group_name": agent_group_name,
                "session_id": session_id,
                "display_name": display_name,
                "share_db_from_team_id": share_db_from_team_id,
            }
        )
        if self.fail_launch:
            raise ValueError("launch failed")
        return LaunchedExpertTeam(
            team_id=self.team_id,
            leader_id=self.leader_id,
            capabilities=("frontend",),
            agent_group_name=self.agent_group_name or agent_group_name,
        )

    async def stop(self, *, team_id: str, session_id: str) -> None:
        self.stop_calls.append({"team_id": team_id, "session_id": session_id})


@pytest_asyncio.fixture
async def active_organization_runtime():
    clear_process_org_managers()
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    runtime = TeamRuntimeManager()
    session_id = "session-expert-groups"
    agents = {}
    for team_id in ("team-a", "team-b", "team-c"):
        backend = FakeBackend(
            team_name=team_id,
            leader_id=f"leader-{team_id}",
            db=db,
            messager=FakeMessager(),
        )
        agent = FakeAgent(backend)
        agents[team_id] = agent
        await runtime.pool.add(
            ActiveTeam(
                team_name=team_id,
                agent=agent,
                current_session_id=session_id,
                state=RuntimeState.PAUSED,
            )
        )
    yield OrganizationRuntimeManager(runtime), agents, session_id
    clear_process_org_managers()
    await db.close()


@pytest.mark.asyncio
async def test_list_expert_groups_without_catalog_returns_empty(active_organization_runtime):
    org_runtime, _, _ = active_organization_runtime
    assert await org_runtime.list_expert_groups() == []


@pytest.mark.asyncio
async def test_list_expert_groups_filters_capabilities(active_organization_runtime):
    org_runtime, _, _ = active_organization_runtime
    org_runtime.set_expert_group_catalog(
        FakeCatalog(
            [
                ExpertGroupDescriptor(
                    agent_group_name="frontend-group",
                    display_name="Frontend",
                    description="UI experts",
                    capabilities=("frontend", "react"),
                ),
                ExpertGroupDescriptor(
                    agent_group_name="backend-group",
                    display_name="Backend",
                    description="API experts",
                    capabilities=("backend", "api"),
                ),
            ]
        )
    )

    all_groups = await org_runtime.list_expert_groups()
    assert [item["agent_group_name"] for item in all_groups] == [
        "frontend-group",
        "backend-group",
    ]

    filtered = await org_runtime.list_expert_groups(capabilities={"frontend"})
    assert [item["agent_group_name"] for item in filtered] == ["frontend-group"]
    assert filtered[0]["capabilities"] == ["frontend", "react"]


@pytest.mark.asyncio
async def test_list_expert_groups_tool(active_organization_runtime):
    org_runtime, _, session_id = active_organization_runtime
    org_runtime.set_expert_group_catalog(
        FakeCatalog(
            [
                ExpertGroupDescriptor(
                    agent_group_name="sample-expert-group",
                    display_name="Sample",
                    description="demo",
                    capabilities=("analysis",),
                )
            ]
        )
    )
    tool = OrgListExpertGroupsTool(org_runtime, team_id="team-a", session_id=session_id)
    result = await tool.invoke({"capabilities": ["analysis"]})
    assert result.success
    assert result.data["expert_groups"][0]["agent_group_name"] == "sample-expert-group"


@pytest.mark.asyncio
async def test_create_and_invite_requires_launcher(active_organization_runtime):
    org_runtime, _, session_id = active_organization_runtime
    await org_runtime.create_organization(
        organization_id="org-1",
        owner_team_id="team-a",
        session_id=session_id,
    )
    with pytest.raises(ValueError, match="expert team launcher is not configured"):
        await org_runtime.create_and_invite_expert_team(
            organization_id="org-1",
            owner_team_id="team-a",
            agent_group_name="sample-expert-group",
            session_id=session_id,
        )


@pytest.mark.asyncio
async def test_create_and_invite_expert_team_success(active_organization_runtime):
    org_runtime, _, session_id = active_organization_runtime
    await org_runtime.create_organization(
        organization_id="org-1",
        owner_team_id="team-a",
        session_id=session_id,
    )
    launcher = FakeLauncher(
        team_id="team-b",
        leader_id="leader-team-b",
        agent_group_name="sample-expert-group",
    )
    org_runtime.set_expert_team_launcher(launcher)

    result = await org_runtime.create_and_invite_expert_team(
        organization_id="org-1",
        owner_team_id="team-a",
        agent_group_name="sample-expert-group",
        session_id=session_id,
        display_name="Sample Experts",
    )

    assert launcher.launch_calls == [
        {
            "organization_id": "org-1",
            "agent_group_name": "sample-expert-group",
            "session_id": session_id,
            "display_name": "Sample Experts",
            "share_db_from_team_id": "team-a",
        }
    ]
    assert launcher.stop_calls == []
    assert result["team_id"] == "team-b"
    assert result["agent_group_name"] == "sample-expert-group"
    member_ids = {leader["team_id"] for leader in result["organization"]["leaders"]}
    assert member_ids == {"team-a", "team-b"}


@pytest.mark.asyncio
async def test_create_and_invite_rolls_back_when_invite_fails(active_organization_runtime):
    org_runtime, _, session_id = active_organization_runtime
    await org_runtime.create_organization(
        organization_id="org-1",
        owner_team_id="team-a",
        session_id=session_id,
    )
    launcher = FakeLauncher(team_id="missing-team", leader_id="leader-x")
    org_runtime.set_expert_team_launcher(launcher)

    with pytest.raises(ValueError):
        await org_runtime.create_and_invite_expert_team(
            organization_id="org-1",
            owner_team_id="team-a",
            agent_group_name="sample-expert-group",
            session_id=session_id,
        )

    assert launcher.stop_calls == [{"team_id": "missing-team", "session_id": session_id}]


@pytest.mark.asyncio
async def test_create_and_invite_tool_owner_only(active_organization_runtime):
    org_runtime, _, session_id = active_organization_runtime
    await org_runtime.create_organization(
        organization_id="org-1",
        owner_team_id="team-a",
        session_id=session_id,
    )
    org_runtime.set_expert_team_launcher(
        FakeLauncher(team_id="team-b", leader_id="leader-team-b")
    )

    non_owner = OrgCreateAndInviteExpertTeamTool(
        org_runtime, team_id="team-c", session_id=session_id
    )
    rejected = await non_owner.invoke(
        {"organization_id": "org-1", "agent_group_name": "sample-expert-group"}
    )
    assert not rejected.success
    assert "owner" in rejected.error

    owner = OrgCreateAndInviteExpertTeamTool(
        org_runtime, team_id="team-a", session_id=session_id
    )
    accepted = await owner.invoke(
        {"organization_id": "org-1", "agent_group_name": "sample-expert-group"}
    )
    assert accepted.success
    assert accepted.data["team_id"] == "team-b"


def test_control_tools_include_expert_group_tools():
    names = [
        tool.card.name
        for tool in create_org_control_tools(
            runtime_manager=OrganizationRuntimeManager(TeamRuntimeManager()),
            team_id="team-a",
            session_id="session-1",
        )
    ]
    assert "org_list_expert_groups" in names
    assert "org_create_and_invite_expert_team" in names
