# coding: utf-8

import asyncio

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.organization.events import OrgEvent, OrgEventMessage, OrgTaskCreatedEvent, OrgTopic
from openjiuwen.agent_teams.organization.pool import clear_process_org_managers
from openjiuwen.agent_teams.organization.runtime import OrganizationRuntimeManager
from openjiuwen.agent_teams.organization.schema import (
    OrgAssignmentType,
    OrgTaskCreator,
    OrgTaskReviewStatus,
    OrgTaskStatus,
)
from openjiuwen.agent_teams.organization.task_pool import OrgTaskManager
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

    def add_tool(self, tool) -> None:
        self.tools.append(tool)


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
    def __init__(self, backend) -> None:
        self.team_backend = backend
        self.member_name = backend.member_name
        self.harness = FakeHarness()
        self.spec = type("Spec", (), {"metadata": {"capabilities": ["analysis"]}})()


@pytest_asyncio.fixture
async def org_manager():
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    messager = FakeMessager()
    manager = OrgTaskManager(
        db=db,
        organization_id="org-1",
        messager=messager,
        session_id="session-1",
    )
    yield manager, messager
    await db.close()


@pytest_asyncio.fixture
async def active_organization_runtime():
    clear_process_org_managers()
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    runtime = TeamRuntimeManager()
    session_id = "session-organization"
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
async def test_claim_and_delegate_use_single_assignment(org_manager):
    manager, _ = org_manager
    result = await manager.create_task(
        task_id="task-1",
        title="Analyze logs",
        description="Find the root cause.",
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
        ),
    )
    assert result.ok

    claimed = await manager.claim_task(task_id="task-1", team_id="team-a", leader_id="leader-a")
    assert claimed.ok
    assert claimed.task.status == OrgTaskStatus.CLAIMED
    assert claimed.task.assignment.assignment_type == OrgAssignmentType.CLAIMED
    assert claimed.task.assignment.team_id == "team-a"
    assert claimed.task.assignment.assigned_by_team_id is None

    delegated = await manager.delegate_task(
        task_id="task-1",
        from_team_id="team-a",
        to_team_id="team-b",
        to_leader_id="leader-b",
    )
    assert delegated.ok
    assert delegated.task.status == OrgTaskStatus.DELEGATED
    assert delegated.task.assignment.assignment_type == OrgAssignmentType.DELEGATED
    assert delegated.task.assignment.team_id == "team-b"
    assert delegated.task.assignment.leader_id == "leader-b"
    assert delegated.task.assignment.assigned_by_team_id == "team-a"


@pytest.mark.asyncio
async def test_completed_event_points_to_db_result(org_manager):
    manager, messager = org_manager
    await manager.create_task(
        task_id="task-2",
        title="Patch bug",
        description="Apply the fix.",
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
        ),
    )
    await manager.claim_task(task_id="task-2", team_id="team-a", leader_id="leader-a")
    completed = await manager.complete_task(
        task_id="task-2",
        team_id="team-a",
        output_context={"result_uri": "https://example.com/result.json", "result_type": "report"},
        output_abstract="Fixed by updating config.",
    )
    assert completed.ok

    completed_events = [m for _, m in messager.published if m.event_type == OrgEvent.TASK_COMPLETED]
    assert completed_events
    payload = completed_events[-1].payload
    assert payload["task_id"] == "task-2"
    assert "output_abstract" not in payload
    assert "result_uri" not in payload

    task = await manager.get_task("task-2")
    assert task.output_abstract == "Fixed by updating config."
    assert task.output_context.result_uri == "https://example.com/result.json"


@pytest.mark.asyncio
async def test_leader_message_event_excludes_content_but_db_keeps_it(org_manager):
    manager, messager = org_manager
    result = await manager.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-a",
        to_team_id="team-b",
        content="Please take the API compatibility slice.",
    )
    assert result.ok

    message_events = [m for _, m in messager.published if m.event_type == OrgEvent.LEADER_MESSAGE]
    assert message_events
    payload = message_events[-1].payload
    assert payload["message_id"] == result.data["message_id"]
    assert "content" not in payload

    messages = await manager.list_leader_messages(team_id="team-b")
    assert messages[0]["content"] == "Please take the API compatibility slice."


@pytest.mark.asyncio
async def test_child_task_completion_creates_pending_review(org_manager):
    manager, messager = org_manager
    await manager.create_task(
        task_id="parent-1",
        title="Build report",
        description="Create the final report.",
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
        ),
    )
    await manager.claim_task(task_id="parent-1", team_id="team-a", leader_id="leader-a")
    child = await manager.create_task(
        task_id="child-1",
        parent_task_id="parent-1",
        title="Analyze risk",
        description="Risk analysis slice.",
        created_by=OrgTaskCreator(
            creator_type="team_leader",
            creator_id="leader-a",
            organization_id="org-1",
            team_id="team-a",
        ),
    )
    assert child.ok
    await manager.claim_task(task_id="child-1", team_id="team-b", leader_id="leader-b")

    blocked_parent = await manager.complete_task(task_id="parent-1", team_id="team-a")
    assert not blocked_parent.ok
    assert "child task is not completed" in blocked_parent.reason

    completed_child = await manager.complete_task(
        task_id="child-1",
        team_id="team-b",
        output_abstract="Risk is manageable.",
    )
    assert completed_child.ok

    review = await manager.get_task_review("child-1")
    assert review.review_status == OrgTaskReviewStatus.PENDING
    assert review.reviewer_team_id == "team-a"
    pending = await manager.list_pending_reviews(team_id="team-a")
    assert pending[0]["task"]["task_id"] == "child-1"

    wrong_reviewer = await manager.review_task(
        task_id="child-1",
        reviewer_team_id="team-c",
        review_status=OrgTaskReviewStatus.ACCEPTED,
    )
    assert not wrong_reviewer.ok

    accepted = await manager.review_task(
        task_id="child-1",
        reviewer_team_id="team-a",
        review_status=OrgTaskReviewStatus.ACCEPTED,
        verdict="Good enough for parent report.",
    )
    assert accepted.ok
    assert await manager.can_complete_parent_task(parent_task_id="parent-1", team_id="team-a")

    completed_parent = await manager.complete_task(task_id="parent-1", team_id="team-a")
    assert completed_parent.ok

    requested_events = [m for _, m in messager.published if m.event_type == OrgEvent.TASK_REVIEW_REQUESTED]
    reviewed_events = [m for _, m in messager.published if m.event_type == OrgEvent.TASK_REVIEWED]
    assert requested_events[-1].payload["task_id"] == "child-1"
    assert reviewed_events[-1].payload["review_status"] == OrgTaskReviewStatus.ACCEPTED.value


@pytest.mark.asyncio
async def test_summary_task_sources_read_completed_outputs(org_manager):
    manager, _ = org_manager
    await manager.create_task(
        task_id="source-1",
        title="Finance analysis",
        description="Analyze finance.",
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
        ),
    )
    await manager.claim_task(task_id="source-1", team_id="team-finance", leader_id="leader-finance")

    early_summary = await manager.create_summary_task(
        task_id="summary-early",
        title="Summary",
        description="Summarize all slices.",
        source_task_ids=["source-1"],
        created_by=OrgTaskCreator(
            creator_type="team_leader",
            creator_id="leader-root",
            organization_id="org-1",
            team_id="team-root",
        ),
    )
    assert not early_summary.ok
    assert "source task is not completed" in early_summary.reason

    await manager.complete_task(
        task_id="source-1",
        team_id="team-finance",
        output_context={"result_uri": "https://example.com/finance.json", "result_type": "report"},
        output_abstract="Revenue is growing.",
    )
    summary = await manager.create_summary_task(
        task_id="summary-1",
        title="Summary",
        description="Summarize all slices.",
        source_task_ids=["source-1"],
        created_by=OrgTaskCreator(
            creator_type="team_leader",
            creator_id="leader-root",
            organization_id="org-1",
            team_id="team-root",
        ),
    )
    assert summary.ok

    sources = await manager.list_summary_sources(summary_task_id="summary-1")
    assert sources[0].source_task_id == "source-1"
    inputs = await manager.get_summary_inputs(summary_task_id="summary-1")
    assert inputs["summary_task"]["task_id"] == "summary-1"
    assert inputs["source_tasks"][0]["task"]["output_abstract"] == "Revenue is growing."
    assert inputs["source_tasks"][0]["task"]["output_context"]["result_uri"] == "https://example.com/finance.json"


@pytest.mark.asyncio
async def test_active_teams_can_create_and_join_organization(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime

    await runtime.ensure_control_tools(agents["team-a"], session_id=session_id)
    owner_tools = {tool.card.name: tool for tool in agents["team-a"].harness.tools}
    created = await owner_tools["org_create_organization"].invoke(
        {"organization_id": "org-active", "display_name": "Active Organization"}
    )
    assert created.success
    assert created.data["owner_team_id"] == "team-a"
    assert agents["team-a"].team_backend.org_task_manager.organization_id == "org-active"
    owner_tools = {tool.card.name: tool for tool in agents["team-a"].harness.tools}
    owner_tool_names = set(owner_tools)
    assert {"org_create_organization", "org_invite_team", "org_view_tasks"} <= owner_tool_names

    joined_result = await owner_tools["org_invite_team"].invoke(
        {"organization_id": "org-active", "team_id": "team-b"}
    )
    assert joined_result.success
    assert {leader["team_id"] for leader in joined_result.data["leaders"]} == {"team-a", "team-b"}
    assert agents["team-b"].team_backend.org_task_manager is agents["team-a"].team_backend.org_task_manager
    member_tool_names = {tool.card.name for tool in agents["team-b"].harness.tools}
    assert {"org_view_tasks", "org_review_task", "org_create_summary_task"} <= member_tool_names
    assert agents["team-b"].team_backend.messager.subscriptions

    with pytest.raises(ValueError, match="only the organization owner"):
        await runtime.invite_team(
            organization_id="org-active",
            inviter_team_id="team-b",
            target_team_id="team-c",
            session_id=session_id,
        )


@pytest.mark.asyncio
async def test_joined_leader_is_woken_to_consider_open_org_task(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    await runtime.create_organization(
        organization_id="org-autoclaim",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-autoclaim",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )

    turns = []

    async def run_organization_turn(**kwargs):
        turns.append(kwargs)
        return True

    runtime._team_runtime_manager.run_organization_turn = run_organization_turn
    topic_id = OrgTopic.TASK.build(session_id, "org-autoclaim")
    handler = next(handler for topic, handler in agents["team-b"].team_backend.messager.subscriptions if topic == topic_id)
    await handler(
        OrgEventMessage.from_event(
            OrgTaskCreatedEvent(
                organization_id="org-autoclaim",
                team_id="team-a",
                leader_id="leader-team-a",
                task_id="task-autoclaim",
                root_task_id="task-autoclaim",
            )
        )
    )
    await asyncio.sleep(0)

    assert turns[0]["team_name"] == "team-b"
    assert turns[0]["session_id"] == session_id
    assert "task-autoclaim" in turns[0]["inputs"]["query"]
