# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""E2E: host Catalog/Launcher injection → create_and_invite expert team joins org."""

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


class HostCatalog:
    def list(self, *, capabilities=None):
        groups = [
            ExpertGroupDescriptor(
                agent_group_name="sample-expert-group",
                display_name="Sample Experts",
                description="demo group",
                capabilities=("frontend", "ui"),
            )
        ]
        if not capabilities:
            return groups
        required = set(capabilities)
        return [g for g in groups if required.issubset(set(g.capabilities))]

    def get(self, name: str):
        for group in self.list():
            if group.agent_group_name == name:
                return group
        raise ValueError(f"expert group not found: {name}")


class HostLauncher:
    """Minimal host launcher: activate into the shared TeamRuntimeManager pool."""

    def __init__(self, team_runtime: TeamRuntimeManager, db) -> None:
        self._team_runtime = team_runtime
        self._db = db
        self._seq = 1
        self.stop_calls: list[dict] = []

    async def launch(
        self,
        *,
        organization_id: str,
        agent_group_name: str,
        session_id: str,
        display_name: str | None = None,
        share_db_from_team_id: str | None = None,
    ) -> LaunchedExpertTeam:
        team_id = f"org-{organization_id}-{agent_group_name}-{self._seq}"
        self._seq += 1
        donor_id = share_db_from_team_id or ""
        db = self._db
        if donor_id:
            entry = await self._team_runtime.pool.get(donor_id)
            if entry is not None:
                db = entry.agent.team_backend.db
        backend = FakeBackend(
            team_name=team_id,
            leader_id=f"leader-{team_id}",
            db=db,
            messager=FakeMessager(),
        )
        agent = FakeAgent(backend, capabilities=["frontend", "ui"])
        await self._team_runtime.pool.add(
            ActiveTeam(
                team_name=team_id,
                agent=agent,
                current_session_id=session_id,
                state=RuntimeState.PAUSED,
            )
        )
        return LaunchedExpertTeam(
            team_id=team_id,
            leader_id=backend.leader_member_name,
            capabilities=("frontend", "ui"),
            agent_group_name=agent_group_name,
        )

    async def stop(self, *, team_id: str, session_id: str) -> None:
        self.stop_calls.append({"team_id": team_id, "session_id": session_id})
        await self._team_runtime.pool.remove(team_id)


@pytest_asyncio.fixture
async def wired_organization():
    clear_process_org_managers()
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    team_runtime = TeamRuntimeManager()
    session_id = "session-expert-e2e"
    backend = FakeBackend(
        team_name="owner-team",
        leader_id="leader-owner",
        db=db,
        messager=FakeMessager(),
    )
    owner = FakeAgent(backend, capabilities=["backend"])
    await team_runtime.pool.add(
        ActiveTeam(
            team_name="owner-team",
            agent=owner,
            current_session_id=session_id,
            state=RuntimeState.PAUSED,
        )
    )
    org_runtime = OrganizationRuntimeManager(team_runtime)
    org_runtime.set_expert_group_catalog(HostCatalog())
    org_runtime.set_expert_team_launcher(HostLauncher(team_runtime, db))
    yield org_runtime, session_id, team_runtime
    clear_process_org_managers()
    await db.close()


@pytest.mark.asyncio
async def test_e2e_list_then_create_and_invite_expert_team(wired_organization):
    org_runtime, session_id, team_runtime = wired_organization

    list_tool = OrgListExpertGroupsTool(
        org_runtime, team_id="owner-team", session_id=session_id
    )
    listed = await list_tool.invoke({"capabilities": ["frontend"]})
    assert listed.success
    assert listed.data["expert_groups"][0]["agent_group_name"] == "sample-expert-group"

    await org_runtime.create_organization(
        organization_id="org-e2e",
        owner_team_id="owner-team",
        session_id=session_id,
    )

    create_tool = OrgCreateAndInviteExpertTeamTool(
        org_runtime, team_id="owner-team", session_id=session_id
    )
    created = await create_tool.invoke(
        {
            "organization_id": "org-e2e",
            "agent_group_name": "sample-expert-group",
            "display_name": "Sample Experts",
        }
    )
    assert created.success
    expert_team_id = created.data["team_id"]
    assert expert_team_id.startswith("org-org-e2e-sample-expert-group-")

    pooled = await team_runtime.pool.get(expert_team_id)
    assert pooled is not None
    assert pooled.current_session_id == session_id

    member_ids = {leader["team_id"] for leader in created.data["organization"]["leaders"]}
    assert member_ids == {"owner-team", expert_team_id}

    available = await org_runtime.list_available_teams(session_id=session_id)
    available_ids = {item["team_id"] for item in available}
    assert expert_team_id in available_ids
