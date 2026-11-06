# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared core group discovery through invitation, task delivery and review."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.organization.expert_adapters import (
    LaunchedExpertTeam,
)
from openjiuwen.agent_teams.experts import PackageLocation
from openjiuwen.agent_teams.tools.tool_expert import ViewExpertGroupsTool
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.organization.pool import clear_process_org_managers
from openjiuwen.agent_teams.organization.runtime import OrganizationRuntimeManager
from openjiuwen.agent_teams.organization.tools import (
    OrgClaimTaskTool,
    OrgCreateAndInviteExpertTeamTool,
    OrgCreateTaskTool,
    OrgReviewTaskTool,
    OrgUpdateTaskTool,
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
async def wired_organization(tmp_path):
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
    org_runtime.set_expert_team_launcher(HostLauncher(team_runtime, db))
    package = tmp_path / "sample-expert-group"
    package.mkdir()
    manifest = {"name": package.name, "package_type": "agent_group",
                "description": "UI specialists", "instruction": "Internal execution rules",
                "display_name": {"en": "Sample Experts", "zh": "Sample"},
                "capabilities": ["frontend", "ui"], "agents": ["leader"]}
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    member = package / "agents" / "leader"
    member.mkdir(parents=True)
    (member / "manifest.json").write_text(
        json.dumps({"name": "leader", "package_type": "agent_template", "description": "Lead"}),
        encoding="utf-8",
    )
    location = PackageLocation(name=package.name, path=package, source="local")
    catalog = HostLocator(location)
    previous = TeamRuntimeManager.expert_catalog()
    TeamRuntimeManager.set_expert_catalog(catalog)
    yield org_runtime, session_id, team_runtime
    TeamRuntimeManager.set_expert_catalog(previous)
    await org_runtime.close()
    clear_process_org_managers()
    await db.close()


@pytest.mark.asyncio
async def test_e2e_list_then_create_and_invite_expert_team(wired_organization):
    org_runtime, session_id, team_runtime = wired_organization

    list_tool = ViewExpertGroupsTool(team_runtime.expert_catalog(), make_translator("en"), language="en")
    listed = await list_tool.invoke({"capabilities": ["frontend"]})
    assert listed.success
    assert listed.data["groups"][0]["name"] == "sample-expert-group"
    assert listed.data["groups"][0]["display_name"] == "Sample Experts"
    assert listed.data["groups"][0]["description"] == "UI specialists"
    assert await team_runtime.pool.get("sample-expert-group") is None

    await org_runtime.create_organization(
        organization_id="org-e2e",
        owner_team_id="owner-team",
        session_id=session_id,
    )

    create_tool = OrgCreateAndInviteExpertTeamTool(org_runtime, team_id="owner-team", session_id=session_id)
    created = await create_tool.invoke(
        {
            "organization_id": "org-e2e",
            "agent_group_name": listed.data["groups"][0]["name"],
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

    owner_entry = await team_runtime.pool.get("owner-team")
    owner_manager = owner_entry.agent.team_backend.org_task_manager
    owner_create = OrgCreateTaskTool(owner_manager, "owner-team", "leader-owner")
    root = await owner_create.invoke({
        "task_id": "root", "title": "Review", "description": "Review the frontend",
        "required_capabilities": ["backend"],
    })
    assert root.success, root.error
    claimed = await OrgClaimTaskTool(owner_manager, "owner-team", "leader-owner").invoke({"task_id": "root"})
    assert claimed.success, claimed.error
    owner_update = OrgUpdateTaskTool(owner_manager, "owner-team", "leader-owner")
    mode = await owner_update.invoke({
        "task_id": "root", "action": "set_aggregation_mode", "aggregation_mode": "HIERARCHICAL",
    })
    assert mode.success, mode.error
    started = await owner_update.invoke({"task_id": "root", "action": "start"})
    assert started.success, started.error
    child = await owner_create.invoke({
        "task_id": "ui-review", "parent_task_id": "root", "title": "UI review",
        "description": "Report one UI finding", "required_capabilities": ["frontend"],
        "delegated_to_team_id": expert_team_id,
    })
    assert child.success, child.error
    expert_manager = pooled.agent.team_backend.org_task_manager
    expert_update = OrgUpdateTaskTool(expert_manager, expert_team_id, pooled.agent.member_name)
    started = await expert_update.invoke({"task_id": "ui-review", "action": "start"})
    assert started.success, started.error
    completed = await expert_update.invoke({
        "task_id": "ui-review", "action": "complete", "output_abstract": "Add an accessible button label",
    })
    assert completed.success, completed.error
    persisted = await owner_manager.get_task("ui-review")
    assert persisted.status.value == "COMPLETED"
    assert persisted.assignment.team_id == expert_team_id
    assert persisted.output_abstract == "Add an accessible button label"
    reviewed = await OrgReviewTaskTool(owner_manager, "owner-team", "leader-owner").invoke({
        "task_id": "ui-review", "review_status": "ACCEPTED", "verdict": "Verified finding",
    })
    assert reviewed.success, reviewed.error


class HostLocator:
    def __init__(self, location):
        self.location = location

    async def list_group_packages(self):
        return [self.location]

    async def locate_group(self, name):
        if name != self.location.name:
            raise ValueError("not installed")
        return self.location

    async def list_expert_packages(self):
        return []

    async def locate_expert(self, name):
        raise ValueError("not installed")
