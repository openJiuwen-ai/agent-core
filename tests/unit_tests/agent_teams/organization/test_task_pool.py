# coding: utf-8

import asyncio
from collections import deque

import pytest
import pytest_asyncio
from sqlalchemy import select

from openjiuwen.agent_teams.organization.events import (
    OrgEvent,
    OrgEventMessage,
    OrgLeaderMessageEvent,
    OrgTaskClaimedEvent,
    OrgTaskCompletedEvent,
    OrgTaskCreatedEvent,
    OrgTaskDelegatedEvent,
    OrgTopic,
)
from openjiuwen.agent_teams.organization.pool import clear_process_org_managers
from openjiuwen.agent_teams.organization.runtime import OrganizationRuntimeManager
from openjiuwen.agent_teams.organization.schema import (
    ORG_TASK_LEGACY_STATUS_FAILURE_CODES,
    OrgAssignmentType,
    OrgTaskAggregationMode,
    OrgTaskEventRecord,
    OrgTaskCreator,
    OrgTaskFailureCode,
    OrgTaskRecord,
    OrgTaskReviewStatus,
    OrgTaskStatus,
)
from openjiuwen.agent_teams.organization.task_pool import OrgTaskManager
from openjiuwen.agent_teams.organization.tools import OrgCreateTaskTool, OrgReviewTaskTool
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
        self.org_message_service = None


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
async def test_concurrent_org_claim_same_task_single_winner(org_manager):
    manager, _ = org_manager
    assert (
        await manager.create_task(
            task_id="race-task",
            title="Race",
            description="Only one team should claim this.",
            required_capabilities=["analysis"],
            created_by=OrgTaskCreator(
                creator_type="client",
                creator_id="client-1",
                organization_id="org-1",
            ),
        )
    ).ok

    results = await asyncio.gather(
        *[
            manager.claim_task(
                task_id="race-task",
                team_id=f"team-{index}",
            )
            for index in range(10)
        ]
    )
    assert sum(result.ok for result in results) == 1

    task = await manager.get_task("race-task")
    assert task.status == OrgTaskStatus.CLAIMED
    assert task.assignment.team_id is not None


@pytest.mark.asyncio
async def test_claim_and_delegate_use_single_assignment(org_manager):
    manager, _ = org_manager
    result = await manager.create_task(
        task_id="task-1",
        title="Analyze logs",
        description="Find the root cause.",
        required_capabilities=["analysis"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
        ),
    )
    assert result.ok

    claimed = await manager.claim_task(task_id="task-1", team_id="team-a")
    assert claimed.ok
    assert claimed.task.status == OrgTaskStatus.CLAIMED
    assert claimed.task.assignment.assignment_type == OrgAssignmentType.CLAIMED
    assert claimed.task.assignment.team_id == "team-a"
    assert claimed.task.assignment.assigned_by_team_id is None

    delegated = await manager.delegate_task(
        task_id="task-1",
        from_team_id="team-a",
        to_team_id="team-b",
    )
    assert delegated.ok
    assert delegated.task.status == OrgTaskStatus.DELEGATED
    assert delegated.task.assignment.assignment_type == OrgAssignmentType.DELEGATED
    assert delegated.task.assignment.team_id == "team-b"
    assert delegated.task.assignment.assigned_by_team_id == "team-a"


@pytest.mark.asyncio
async def test_create_task_inherits_root_task_id_from_parent_chain(org_manager):
    manager, _ = org_manager
    client = OrgTaskCreator(
        creator_type="client",
        creator_id="client-1",
        organization_id="org-1",
    )
    team_a = OrgTaskCreator(
        creator_type="team_leader",
        creator_id="leader-a",
        organization_id="org-1",
        team_id="team-a",
    )
    assert (
        await manager.create_task(
            task_id="root-1",
            title="Root",
            description="Root task.",
            required_capabilities=["analysis"],
            created_by=client,
        )
    ).ok
    assert (await manager.claim_task(task_id="root-1", team_id="team-a")).ok
    assert (
        await manager.create_task(
            task_id="child-1",
            parent_task_id="root-1",
            title="Child",
            description="Child task.",
            required_capabilities=["analysis"],
            created_by=team_a,
        )
    ).ok
    assert (await manager.claim_task(task_id="child-1", team_id="team-a")).ok

    grandchild = await manager.create_task(
        task_id="grandchild-1",
        parent_task_id="child-1",
        title="Grandchild",
        description="Grandchild task.",
        required_capabilities=["analysis"],
        created_by=team_a,
    )
    assert grandchild.ok
    assert grandchild.task.root_task_id == "root-1"


@pytest.mark.asyncio
async def test_create_task_rejects_conflicting_root_task_id(org_manager):
    manager, _ = org_manager
    client = OrgTaskCreator(
        creator_type="client",
        creator_id="client-1",
        organization_id="org-1",
    )
    team_a = OrgTaskCreator(
        creator_type="team_leader",
        creator_id="leader-a",
        organization_id="org-1",
        team_id="team-a",
    )
    assert (
        await manager.create_task(
            task_id="root-1",
            title="Root",
            description="Root task.",
            required_capabilities=["analysis"],
            created_by=client,
        )
    ).ok

    bad_root = await manager.create_task(
        task_id="root-2",
        root_task_id="not-root-2",
        title="Bad root",
        description="Caller passed a mismatched root_task_id.",
        required_capabilities=["analysis"],
        created_by=client,
    )
    assert not bad_root.ok
    assert "root task root_task_id must equal task_id" in bad_root.reason
    assert await manager.get_task("root-2") is None
    assert (await manager.claim_task(task_id="root-1", team_id="team-a")).ok

    assert (
        await manager.create_task(
            task_id="child-1",
            parent_task_id="root-1",
            title="Child",
            description="Child task.",
            required_capabilities=["analysis"],
            created_by=team_a,
        )
    ).ok

    bad_child = await manager.create_task(
        task_id="child-2",
        parent_task_id="root-1",
        root_task_id="other-root",
        title="Bad child",
        description="Caller passed a mismatched root_task_id.",
        required_capabilities=["analysis"],
        created_by=team_a,
    )
    assert not bad_child.ok
    assert "root_task_id must match parent.root_task_id" in bad_child.reason
    assert await manager.get_task("child-2") is None

    matching = await manager.create_task(
        task_id="child-3",
        parent_task_id="root-1",
        root_task_id="root-1",
        title="Matching child",
        description="Explicit root_task_id matches parent.",
        required_capabilities=["analysis"],
        created_by=team_a,
    )
    assert matching.ok
    assert matching.task.root_task_id == "root-1"

    root = await manager.get_task("root-1")
    assert root is not None
    assert root.aggregation is not None
    assert root.aggregation.final_output_task_id == "root-1"


@pytest.mark.asyncio
async def test_create_task_rejects_invalid_parent(org_manager):
    manager, _ = org_manager
    creator = OrgTaskCreator(
        creator_type="client",
        creator_id="client-1",
        organization_id="org-1",
    )
    missing_parent = await manager.create_task(
        task_id="orphan-1",
        parent_task_id="missing-parent",
        title="Orphan",
        description="Parent does not exist.",
        required_capabilities=["analysis"],
        created_by=creator,
    )
    assert not missing_parent.ok
    assert missing_parent.reason == "parent task not found: missing-parent"

    other_org = OrgTaskManager(
        db=manager.db,
        organization_id="org-2",
        messager=manager.messager,
        session_id=manager.session_id,
    )
    assert (
        await other_org.create_task(
            task_id="other-org-parent",
            title="Other org parent",
            description="Belongs to another organization.",
            required_capabilities=["analysis"],
            created_by=OrgTaskCreator(
                creator_type="client",
                creator_id="client-2",
                organization_id="org-2",
            ),
        )
    ).ok

    cross_org = await manager.create_task(
        task_id="cross-org-child",
        parent_task_id="other-org-parent",
        title="Cross org child",
        description="Parent belongs to another organization.",
        required_capabilities=["analysis"],
        created_by=creator,
    )
    assert not cross_org.ok
    assert cross_org.reason == "parent task not found: other-org-parent"


@pytest.mark.asyncio
async def test_only_assigned_team_can_create_child_tasks(org_manager):
    manager, _ = org_manager
    client = OrgTaskCreator(creator_type="client", creator_id="client-1", organization_id="org-1")
    team_a = OrgTaskCreator(
        creator_type="team_leader",
        creator_id="leader-a",
        organization_id="org-1",
        team_id="team-a",
    )
    team_b = OrgTaskCreator(
        creator_type="team_leader",
        creator_id="leader-b",
        organization_id="org-1",
        team_id="team-b",
    )
    assert (
        await manager.create_task(
            task_id="parent-ownership",
            title="Parent",
            description="Owned by team A.",
            required_capabilities=["analysis"],
            created_by=client,
        )
    ).ok
    assert (await manager.claim_task(task_id="parent-ownership", team_id="team-a")).ok

    wrong_team = await manager.create_task(
        task_id="wrong-team-child",
        parent_task_id="parent-ownership",
        title="Wrong owner child",
        description="Team B must not create it.",
        required_capabilities=["analysis"],
        created_by=team_b,
    )
    assert not wrong_team.ok
    assert wrong_team.reason == "only the parent task's assigned team can create child tasks"

    no_team = await manager.create_task(
        task_id="client-child",
        parent_task_id="parent-ownership",
        title="Client child",
        description="Clients must not create child tasks.",
        required_capabilities=["analysis"],
        created_by=client,
    )
    assert not no_team.ok
    assert no_team.reason == "child task must be created by a team"

    assert (await manager.complete_task(task_id="parent-ownership", team_id="team-a")).ok
    terminal_parent = await manager.create_task(
        task_id="terminal-parent-child",
        parent_task_id="parent-ownership",
        title="Late child",
        description="Completed parents must not gain children.",
        required_capabilities=["analysis"],
        created_by=team_a,
    )
    assert not terminal_parent.ok
    assert terminal_parent.reason == "parent task is terminal: parent-ownership"


@pytest.mark.asyncio
async def test_root_task_gets_default_hierarchical_aggregation(org_manager):
    manager, _ = org_manager
    creator = OrgTaskCreator(
        creator_type="team_leader",
        creator_id="leader-a",
        organization_id="org-1",
        team_id="team-a",
    )
    root = await manager.create_task(
        task_id="root-task-1",
        title="Root",
        description="Root task",
        required_capabilities=["analysis"],
        created_by=creator,
    )
    assert (await manager.claim_task(task_id="root-task-1", team_id="team-a")).ok
    child = await manager.create_task(
        task_id="child-task-1",
        parent_task_id="root-task-1",
        title="Child",
        description="Child task",
        required_capabilities=["analysis"],
        created_by=creator,
    )
    assert root.ok and root.task is not None
    assert child.ok and child.task is not None
    assert root.task.aggregation is not None
    assert root.task.aggregation.mode == OrgTaskAggregationMode.HIERARCHICAL
    assert root.task.aggregation.final_output_task_id == "root-task-1"
    assert child.task.aggregation is None


@pytest.mark.asyncio
async def test_create_task_rejects_summary_team_aggregation(org_manager):
    manager, _ = org_manager
    created = await manager.create_task(
        task_id="root-summary-rejected",
        title="Root with summary team",
        description="SUMMARY_TEAM is not supported yet.",
        required_capabilities=["analysis"],
        aggregation_mode=OrgTaskAggregationMode.SUMMARY_TEAM,
        created_by=OrgTaskCreator(
            creator_type="team_leader",
            creator_id="leader-a",
            organization_id="org-1",
            team_id="team-a",
        ),
    )
    assert not created.ok
    assert "SUMMARY_TEAM aggregation is not supported yet" in created.reason
    assert await manager.get_task("root-summary-rejected") is None


def test_to_task_normalizes_legacy_terminal_statuses():
    for legacy_status, failure_code in ORG_TASK_LEGACY_STATUS_FAILURE_CODES.items():
        row = OrgTaskRecord(
            task_id=f"task-{legacy_status.lower()}",
            organization_id="org-1",
            parent_task_id=None,
            root_task_id=f"task-{legacy_status.lower()}",
            creator_type="team_leader",
            creator_id="leader-a",
            creator_team_id="team-a",
            status=legacy_status,
            created_at=1,
            updated_at=2,
            title=legacy_status,
            description="Legacy row",
            assignment_type=OrgAssignmentType.UNASSIGNED.value,
        )
        task = OrgTaskManager._to_task(row)
        assert task.status == OrgTaskStatus.FAILED
        assert task.failure_code == failure_code


def test_to_task_tolerates_unknown_failure_code_and_status():
    unknown_code_row = OrgTaskRecord(
        task_id="task-unknown-code",
        organization_id="org-1",
        parent_task_id=None,
        root_task_id="task-unknown-code",
        creator_type="team_leader",
        creator_id="leader-a",
        creator_team_id="team-a",
        status=OrgTaskStatus.FAILED.value,
        created_at=1,
        updated_at=2,
        title="Failed",
        description="Unknown failure code",
        assignment_type=OrgAssignmentType.UNASSIGNED.value,
        failure_code="NOT_A_REAL_CODE",
        failure_reason="legacy writer",
        failed_at=3,
    )
    unknown_code_task = OrgTaskManager._to_task(unknown_code_row)
    assert unknown_code_task.status == OrgTaskStatus.FAILED
    assert unknown_code_task.failure_code is None
    assert unknown_code_task.failure_reason == "legacy writer"

    unknown_status_row = OrgTaskRecord(
        task_id="task-unknown-status",
        organization_id="org-1",
        parent_task_id=None,
        root_task_id="task-unknown-status",
        creator_type="team_leader",
        creator_id="leader-a",
        creator_team_id="team-a",
        status="WEIRD",
        created_at=1,
        updated_at=2,
        title="Weird",
        description="Unknown status",
        assignment_type=OrgAssignmentType.UNASSIGNED.value,
        failure_code=OrgTaskFailureCode.EXECUTION_FAILED.value,
    )
    unknown_status_task = OrgTaskManager._to_task(unknown_status_row)
    assert unknown_status_task.status == OrgTaskStatus.FAILED
    assert unknown_status_task.failure_code == OrgTaskFailureCode.EXECUTION_FAILED


@pytest.mark.asyncio
async def test_start_task_status_guards(org_manager):
    manager, _ = org_manager
    created = await manager.create_task(
        task_id="start-guard-task",
        title="Start guard",
        description="Validate start transitions.",
        required_capabilities=["analysis"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
        ),
    )
    assert created.ok

    open_start = await manager.start_task(task_id="start-guard-task", team_id="team-a")
    assert not open_start.ok

    claimed = await manager.claim_task(
        task_id="start-guard-task",
        team_id="team-a",
    )
    assert claimed.ok

    started = await manager.start_task(task_id="start-guard-task", team_id="team-a")
    assert started.ok
    assert started.task.status == OrgTaskStatus.IN_PROGRESS

    again = await manager.start_task(task_id="start-guard-task", team_id="team-a")
    assert again.ok
    assert again.task.status == OrgTaskStatus.IN_PROGRESS

    completed = await manager.complete_task(task_id="start-guard-task", team_id="team-a")
    assert completed.ok

    restarted = await manager.start_task(task_id="start-guard-task", team_id="team-a")
    assert not restarted.ok
    assert restarted.reason == "task is terminal: start-guard-task"
    assert (await manager.get_task("start-guard-task")).status == OrgTaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_start_task_allows_delegated_task(org_manager):
    manager, _ = org_manager
    created = await manager.create_task(
        task_id="delegated-start-task",
        title="Delegated start",
        description="Delegated tasks can be started.",
        required_capabilities=["analysis"],
        delegated_to_team_id="team-b",
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
            team_id="team-a",
        ),
    )
    assert created.ok

    started = await manager.start_task(task_id="delegated-start-task", team_id="team-b")
    assert started.ok
    assert started.task.status == OrgTaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_org_tasks_require_non_empty_capabilities(org_manager):
    manager, _ = org_manager
    created = await manager.create_task(
        task_id="empty-capabilities",
        title="Invalid task",
        description="This must be rejected.",
        required_capabilities=["", "  "],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
        ),
    )
    assert not created.ok
    assert "required_capabilities" in created.reason
    assert await manager.get_task("empty-capabilities") is None

    tool = OrgCreateTaskTool(manager, team_id="team-a", leader_id="leader-a")
    rejected = await tool.invoke(
        {
            "title": "Another invalid task",
            "description": "This must also be rejected.",
            "required_capabilities": [],
        }
    )
    assert not rejected.success
    assert "required_capabilities" in rejected.error


@pytest.mark.asyncio
async def test_org_create_task_tool_exposes_hierarchical_aggregation_and_rejects_summary_team(org_manager):
    manager, _ = org_manager
    tool = OrgCreateTaskTool(manager, team_id="team-a", leader_id="leader-a")
    params = tool.card.input_params["properties"]["aggregation_mode"]
    assert params["enum"] == [OrgTaskAggregationMode.HIERARCHICAL.value]
    assert "root_task_id" not in tool.card.input_params["properties"]

    created = await tool.invoke(
        {
            "task_id": "tool-root-agg",
            "title": "Root via tool",
            "description": "Defaults to hierarchical aggregation.",
            "required_capabilities": ["analysis"],
            "aggregation_mode": OrgTaskAggregationMode.HIERARCHICAL.value,
        }
    )
    assert created.success
    assert created.data["aggregation_mode"] == OrgTaskAggregationMode.HIERARCHICAL

    rejected = await tool.invoke(
        {
            "task_id": "tool-root-summary",
            "title": "Root summary",
            "description": "Must be rejected.",
            "required_capabilities": ["analysis"],
            "aggregation_mode": OrgTaskAggregationMode.SUMMARY_TEAM.value,
        }
    )
    assert not rejected.success
    assert "SUMMARY_TEAM aggregation is not supported yet" in rejected.error
    assert await manager.get_task("tool-root-summary") is None


@pytest.mark.asyncio
async def test_completed_event_points_to_db_result(org_manager):
    manager, messager = org_manager
    await manager.create_task(
        task_id="task-2",
        title="Patch bug",
        description="Apply the fix.",
        required_capabilities=["patching"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
        ),
    )
    await manager.claim_task(task_id="task-2", team_id="team-a")
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
async def test_org_send_leader_message_tool_delivers_via_transport(org_manager):
    from openjiuwen.agent_teams.organization.message_service import OrgMessageService
    from openjiuwen.agent_teams.organization.tools import OrgSendLeaderMessageTool

    manager, messager = org_manager
    message_service = OrgMessageService(
        db=manager.db,
        organization_id=manager.organization_id,
        session_id=manager.session_id,
        messager=messager,
        db_context=manager.db_context,
    )
    await manager.register_leader(team_id="team-a", leader_id="leader-a")
    await manager.register_leader(team_id="team-b", leader_id="leader-b")
    tool = OrgSendLeaderMessageTool(manager, "team-a", "leader-a", message_service=message_service)
    output = await tool.invoke(
        {
            "content": "Please take the API compatibility slice.",
            "to_team_id": "team-b",
        }
    )
    assert output.success
    assert output.data["delivered_to"] == ["team-b"]

    message_events = [m for _, m in messager.published if m.event_type == OrgEvent.LEADER_MESSAGE]
    assert message_events
    payload = message_events[-1].payload
    assert payload["message_id"] == output.data["message_id"]
    assert "content" not in payload

    messages = await message_service.list_leader_messages(team_id="team-b")
    assert messages[0]["content"] == "Please take the API compatibility slice."


@pytest.mark.asyncio
async def test_publish_leader_message_event_skips_team_inbox_delivery(org_manager):
    manager, messager = org_manager
    await manager.publish_event(
        OrgLeaderMessageEvent(
            organization_id="org-1",
            team_id="team-a",
            leader_id="leader-a",
            message_id="msg-inbox-skip",
            from_team_id="team-a",
            to_team_id="team-b",
        ),
        team_inbox_id="team-b",
    )

    inbox_topics = [
        topic
        for topic, message in messager.published
        if message.event_type == OrgEvent.LEADER_MESSAGE
        and topic == OrgTopic.TEAM_INBOX.build("session-1", "org-1", "team-b")
    ]
    assert not inbox_topics

    leader_topics = [
        topic
        for topic, message in messager.published
        if message.event_type == OrgEvent.LEADER_MESSAGE
        and topic == OrgTopic.LEADER.build("session-1", "org-1")
    ]
    assert leader_topics

    await manager.publish_event(
        OrgTaskDelegatedEvent(
            organization_id="org-1",
            team_id="team-a",
            task_id="task-delegated",
            delegated_by_team_id="team-a",
            delegated_to_team_id="team-b",
        ),
        team_inbox_id="team-b",
    )
    delegated_inbox_topics = [
        topic
        for topic, message in messager.published
        if message.event_type == OrgEvent.TASK_DELEGATED
        and topic == OrgTopic.TEAM_INBOX.build("session-1", "org-1", "team-b")
    ]
    assert delegated_inbox_topics


@pytest.mark.asyncio
async def test_leader_message_inbox_event_wakes_target_leader(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    await runtime.create_organization(
        organization_id="org-leader-message",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-leader-message",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )

    turns = []

    async def run_organization_turn(**kwargs):
        turns.append(kwargs)
        return True

    runtime._team_runtime_manager.run_organization_turn = run_organization_turn
    inbox_topic = OrgTopic.TEAM_INBOX.build(session_id, "org-leader-message", "team-b")
    handler = next(
        handler for topic, handler in agents["team-b"].team_backend.messager.subscriptions if topic == inbox_topic
    )
    sent = await agents["team-a"].team_backend.org_message_service.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-team-a",
        to_team_id="team-b",
        content="Please confirm the API contract.",
    )
    message_id = sent.data["message_id"]
    await handler(
        OrgEventMessage(
            event_type=OrgEvent.LEADER_MESSAGE,
            payload={
                "message_id": message_id,
                "organization_id": "org-leader-message",
                "from_team_id": "team-a",
                "to_team_id": "team-b",
            },
            sender_id="team-a",
        )
    )
    await asyncio.sleep(0)

    assert turns[0]["team_name"] == "team-b"
    assert turns[0]["session_id"] == session_id
    assert message_id in turns[0]["inputs"]["query"]
    assert "org_get_leader_message" in turns[0]["inputs"]["query"]
    assert "org_ack_leader_message" in turns[0]["inputs"]["query"]


@pytest.mark.asyncio
async def test_failed_leader_message_turn_can_retry_and_ack_stops_wake(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    await runtime.create_organization(
        organization_id="org-message-dedup",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-message-dedup",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )
    turns = []
    first_turn_started = asyncio.Event()
    release_first_turn = asyncio.Event()

    async def run_organization_turn(**kwargs):
        turns.append(kwargs)
        if len(turns) == 1:
            first_turn_started.set()
            await release_first_turn.wait()
            return False
        return len(turns) > 1

    runtime._team_runtime_manager.run_organization_turn = run_organization_turn
    sent = await agents["team-a"].team_backend.org_message_service.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-team-a",
        to_team_id="team-b",
        content="deduplicate me",
    )
    message_id = sent.data["message_id"]
    inbox_topic = OrgTopic.TEAM_INBOX.build(session_id, "org-message-dedup", "team-b")
    handler = next(
        handler for topic, handler in agents["team-b"].team_backend.messager.subscriptions if topic == inbox_topic
    )
    event = OrgEventMessage(
        event_type=OrgEvent.LEADER_MESSAGE,
        payload={"message_id": message_id, "from_team_id": "team-a", "to_team_id": "team-b"},
        sender_id="team-a",
    )

    await handler(event)
    first_turn = runtime._leader_turn_workers[(session_id, "team-b")]
    await first_turn_started.wait()
    await handler(event)
    assert len(turns) == 1

    release_first_turn.set()
    await first_turn
    await handler(event)
    await runtime._leader_turn_workers[(session_id, "team-b")]
    assert len(turns) == 2

    await agents["team-b"].team_backend.org_message_service.ack_leader_message(
        message_id=message_id,
        team_id="team-b",
        leader_id="leader-team-b",
    )
    await handler(event)
    await asyncio.sleep(0)
    assert len(turns) == 2


@pytest.mark.asyncio
async def test_rebind_recovers_unacknowledged_leader_message(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    await runtime.create_organization(
        organization_id="org-message-recovery",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-message-recovery",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )
    sent = await agents["team-a"].team_backend.org_message_service.send_leader_message(
        from_team_id="team-a",
        from_leader_id="leader-team-a",
        to_team_id="team-b",
        content="recover me",
    )
    turns = []

    async def run_organization_turn(**kwargs):
        turns.append(kwargs)
        return True

    runtime._team_runtime_manager.run_organization_turn = run_organization_turn
    assert await runtime.ensure_team_binding(
        team_id="team-b",
        session_id=session_id,
        agent=agents["team-b"],
    )
    await asyncio.sleep(0)

    assert len(turns) == 1
    assert sent.data["message_id"] in turns[0]["inputs"]["query"]


@pytest.mark.asyncio
async def test_organization_events_are_persisted_for_activity_views(org_manager):
    manager, _ = org_manager
    await manager.create_task(
        task_id="activity-task",
        title="Persist activity",
        description="Create an activity record.",
        required_capabilities=["analysis"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
        ),
    )
    await manager.claim_task(task_id="activity-task", team_id="team-a")

    assert manager.db.session_local is not None
    async with manager.db.session_local() as session:
        rows = (await session.execute(
            select(OrgTaskEventRecord.event_type, OrgTaskEventRecord.task_id).where(
                OrgTaskEventRecord.organization_id == "org-1"
            )
        )).all()
    assert (OrgEvent.TASK_CREATED, "activity-task") in rows
    assert (OrgEvent.TASK_CLAIMED, "activity-task") in rows


@pytest.mark.asyncio
async def test_org_review_task_rejects_invalid_review_status(org_manager):
    manager, _ = org_manager
    tool = OrgReviewTaskTool(manager=manager, team_id="team-a", leader_id="leader-a")
    result = await tool.invoke({"task_id": "child-1", "review_status": "accepted"})
    assert not result.success
    assert result.error == "invalid review_status: 'accepted'"


@pytest.mark.asyncio
async def test_child_task_completion_creates_pending_review(org_manager):
    manager, messager = org_manager
    await manager.create_task(
        task_id="parent-1",
        title="Build report",
        description="Create the final report.",
        required_capabilities=["reporting"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
        ),
    )
    await manager.claim_task(task_id="parent-1", team_id="team-a")
    child = await manager.create_task(
        task_id="child-1",
        parent_task_id="parent-1",
        title="Analyze risk",
        description="Risk analysis slice.",
        required_capabilities=["risk-analysis"],
        created_by=OrgTaskCreator(
            creator_type="team_leader",
            creator_id="leader-a",
            organization_id="org-1",
            team_id="team-a",
        ),
    )
    assert child.ok
    await manager.claim_task(task_id="child-1", team_id="team-b")

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
        required_capabilities=["finance"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client-1",
            organization_id="org-1",
        ),
    )
    await manager.claim_task(task_id="source-1", team_id="team-finance")

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
    assert "next model call" in created.data["next_action"]
    assert created.data["owner_team_id"] == "team-a"
    assert agents["team-a"].team_backend.org_task_manager.organization_id == "org-active"
    owner_prompt = agents["team-a"].harness.system_prompt_builder.sections["organization_owner_lifecycle"]
    assert "org_dissolve_organization" in owner_prompt.content["en"]
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
    manager = agents["team-a"].team_backend.org_task_manager
    await manager.create_task(
        task_id="task-autoclaim",
        title="Analysis task",
        description="A task matching team-b's capabilities.",
        required_capabilities=["analysis"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client",
            organization_id="org-autoclaim",
        ),
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


@pytest.mark.asyncio
async def test_claimed_task_wakes_claiming_team_to_execute(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    agents["team-b"].spec.metadata["capabilities"] = ["testing"]
    await runtime.create_organization(
        organization_id="org-claimed-task",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-claimed-task",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )
    manager = agents["team-a"].team_backend.org_task_manager
    await manager.create_task(
        task_id="claimed-test-task",
        title="Run tests",
        description="Execute the assigned test work.",
        required_capabilities=["testing"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client",
            organization_id="org-claimed-task",
        ),
    )
    assert (await manager.claim_task(
        task_id="claimed-test-task",
        team_id="team-b",
    )).ok

    turns = []

    async def run_organization_turn(**kwargs):
        turns.append(kwargs)
        return True

    runtime._team_runtime_manager.run_organization_turn = run_organization_turn
    topic_id = OrgTopic.TASK.build(session_id, "org-claimed-task")
    handler = next(handler for topic, handler in agents["team-b"].team_backend.messager.subscriptions if topic == topic_id)
    await handler(
        OrgEventMessage.from_event(
            OrgTaskClaimedEvent(
                organization_id="org-claimed-task",
                team_id="team-b",
                leader_id="leader-team-b",
                task_id="claimed-test-task",
                claimed_by_team_id="team-b",
            )
        )
    )
    await asyncio.sleep(0)

    assert turns[0]["team_name"] == "team-b"
    prompt = turns[0]["inputs"]["query"]
    assert "claimed-test-task" in prompt
    assert "org_update_task(action='start')" in prompt
    assert "org_update_task(action='complete')" in prompt
    assert "org_create_task(parent_task_id='claimed-test-task')" in prompt
    assert "org_view_child_tasks" in prompt


@pytest.mark.asyncio
async def test_task_execution_prompts_describe_child_decomposition(active_organization_runtime):
    runtime, _, session_id = active_organization_runtime
    prompts: list[str] = []

    def capture_prompt(**kwargs):
        prompts.append(kwargs["prompt"])

    runtime._schedule_leader_turn = capture_prompt
    runtime._schedule_delegated_turn(
        team_id="team-a",
        session_id=session_id,
        task_id="delegated-parent",
        organization_id="org-1",
    )
    runtime._schedule_parent_completion_turn(
        team_id="team-a",
        session_id=session_id,
        child_task_id="child-1",
        parent_task_id="parent-1",
        organization_id="org-1",
    )

    assert "org_create_task(parent_task_id='delegated-parent')" in prompts[0]
    assert "org_view_child_tasks" in prompts[0]
    assert "org_create_task(parent_task_id='parent-1')" in prompts[1]


@pytest.mark.asyncio
async def test_recreated_leader_regains_org_tools_and_subscription(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    await runtime.create_organization(
        organization_id="org-rebind",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-rebind",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )

    original = agents["team-b"].team_backend
    recreated_backend = FakeBackend(
        team_name="team-b",
        leader_id="leader-team-b",
        db=original.db,
        messager=FakeMessager(),
    )
    recreated_agent = FakeAgent(recreated_backend)
    await runtime._team_runtime_manager.pool.add(
        ActiveTeam(
            team_name="team-b",
            agent=recreated_agent,
            current_session_id=session_id,
            state=RuntimeState.PAUSED,
        )
    )

    assert await runtime.ensure_team_binding(
        team_id="team-b",
        session_id=session_id,
        agent=recreated_agent,
    )
    tool_names = {tool.card.name for tool in recreated_agent.harness.tools}
    assert {"org_view_tasks", "org_claim_task", "org_update_task"} <= tool_names
    assert recreated_backend.messager.subscriptions


@pytest.mark.asyncio
async def test_cold_recovered_leader_rebinds_from_persisted_membership(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    await runtime.create_organization(
        organization_id="org-db-rebind",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-db-rebind",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )
    manager = agents["team-a"].team_backend.org_task_manager
    await manager.create_task(
        task_id="claimed-before-recovery",
        title="Resume test task",
        description="This task must resume after the Team is rebound.",
        required_capabilities=["analysis"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client",
            organization_id="org-db-rebind",
        ),
    )
    assert (await manager.claim_task(
        task_id="claimed-before-recovery",
        team_id="team-b",
    )).ok

    # Model a process restart: the durable tables remain but the in-memory
    # runtime's membership map is empty and the leader is newly reconstructed.
    runtime._team_organizations.clear()
    original = agents["team-b"].team_backend
    recovered_backend = FakeBackend(
        team_name="team-b",
        leader_id="leader-team-b",
        db=original.db,
        messager=FakeMessager(),
    )
    recovered_agent = FakeAgent(recovered_backend)
    await runtime._team_runtime_manager.pool.add(
        ActiveTeam(
            team_name="team-b",
            agent=recovered_agent,
            current_session_id=session_id,
            state=RuntimeState.PAUSED,
        )
    )

    turns = []

    async def run_organization_turn(**kwargs):
        turns.append(kwargs)
        return True

    runtime._team_runtime_manager.run_organization_turn = run_organization_turn

    assert await runtime.ensure_team_binding(
        team_id="team-b",
        session_id=session_id,
        agent=recovered_agent,
    )
    assert recovered_backend.org_task_manager.organization_id == "org-db-rebind"
    assert {tool.card.name for tool in recovered_agent.harness.tools} >= {
        "org_create_task",
        "org_view_tasks",
        "org_review_task",
    }
    await asyncio.sleep(0)
    assert turns[0]["team_name"] == "team-b"
    assert "claimed-before-recovery" in turns[0]["inputs"]["query"]


@pytest.mark.asyncio
async def test_recovered_leader_discovers_matching_open_task(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    agents["team-b"].spec.metadata["capabilities"] = ["testing", "unit-test"]
    await runtime.create_organization(
        organization_id="org-open-recovery",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-open-recovery",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )
    manager = agents["team-a"].team_backend.org_task_manager
    await manager.create_task(
        task_id="open-before-recovery",
        title="Run tests",
        description="This task must be claimed after the Team is rebound.",
        required_capabilities=["testing", "unit-test"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client",
            organization_id="org-open-recovery",
        ),
    )

    runtime._team_organizations.clear()
    original = agents["team-b"].team_backend
    recovered_backend = FakeBackend(
        team_name="team-b",
        leader_id="leader-team-b",
        db=original.db,
        messager=FakeMessager(),
    )
    recovered_agent = FakeAgent(recovered_backend)
    recovered_agent.spec.metadata["capabilities"] = ["testing", "unit-test"]
    await runtime._team_runtime_manager.pool.add(
        ActiveTeam(
            team_name="team-b",
            agent=recovered_agent,
            current_session_id=session_id,
            state=RuntimeState.PAUSED,
        )
    )

    turns = []

    async def run_organization_turn(**kwargs):
        turns.append(kwargs)
        return True

    runtime._team_runtime_manager.run_organization_turn = run_organization_turn
    assert await runtime.ensure_team_binding(
        team_id="team-b",
        session_id=session_id,
        agent=recovered_agent,
    )
    await asyncio.sleep(0)

    assert turns[0]["team_name"] == "team-b"
    assert "open-before-recovery" in turns[0]["inputs"]["query"]
    assert "MUST call org_claim_task" in turns[0]["inputs"]["query"]


@pytest.mark.asyncio
async def test_completed_child_wakes_its_creator_team(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    await runtime.create_organization(
        organization_id="org-completed-child",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-completed-child",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )
    manager = agents["team-a"].team_backend.org_task_manager
    await manager.create_task(
        task_id="parent",
        title="Parent",
        description="Parent task",
        required_capabilities=["coordination"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client",
            organization_id="org-completed-child",
        ),
    )
    await manager.claim_task(task_id="parent", team_id="team-a")
    await manager.create_task(
        task_id="child",
        parent_task_id="parent",
        title="Child",
        description="Child task",
        required_capabilities=["analysis"],
        created_by=OrgTaskCreator(
            creator_type="team_leader",
            creator_id="leader-team-a",
            organization_id="org-completed-child",
            team_id="team-a",
        ),
    )

    turns = []

    async def run_organization_turn(**kwargs):
        turns.append(kwargs)
        return True

    runtime._team_runtime_manager.run_organization_turn = run_organization_turn
    topic_id = OrgTopic.TASK.build(session_id, "org-completed-child")
    handler = next(handler for topic, handler in agents["team-a"].team_backend.messager.subscriptions if topic == topic_id)
    await handler(
        OrgEventMessage.from_event(
            OrgTaskCompletedEvent(
                organization_id="org-completed-child",
                team_id="team-b",
                leader_id="leader-team-b",
                task_id="child",
            )
        )
    )
    await asyncio.sleep(0)

    assert turns[0]["team_name"] == "team-a"
    prompt = turns[0]["inputs"]["query"]
    assert "child" in prompt
    assert "at most one focused repair task" in prompt


@pytest.mark.asyncio
async def test_created_task_only_wakes_capability_matched_team(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    agents["team-b"].spec.metadata["capabilities"] = ["testing", "unit-test", "api-test"]
    await runtime.create_organization(
        organization_id="org-created-task-filter",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-created-task-filter",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )
    manager = agents["team-a"].team_backend.org_task_manager
    await manager.create_task(
        task_id="backend-only",
        title="Backend",
        description="Backend task",
        required_capabilities=["backend", "api"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client",
            organization_id="org-created-task-filter",
        ),
    )

    turns = []

    async def run_organization_turn(**kwargs):
        turns.append(kwargs)
        return True

    runtime._team_runtime_manager.run_organization_turn = run_organization_turn
    topic_id = OrgTopic.TASK.build(session_id, "org-created-task-filter")
    handler = next(handler for topic, handler in agents["team-b"].team_backend.messager.subscriptions if topic == topic_id)
    await handler(
        OrgEventMessage.from_event(
            OrgTaskCreatedEvent(
                organization_id="org-created-task-filter",
                team_id="team-a",
                leader_id="leader-team-a",
                task_id="backend-only",
                root_task_id="backend-only",
            )
        )
    )
    await asyncio.sleep(0)

    assert turns == []


@pytest.mark.asyncio
async def test_completed_task_rewakes_team_for_matching_open_task(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    agents["team-b"].spec.metadata["capabilities"] = ["testing", "unit-test", "api-test"]
    await runtime.create_organization(
        organization_id="org-rewake-open-task",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-rewake-open-task",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )
    manager = agents["team-a"].team_backend.org_task_manager
    await manager.create_task(
        task_id="backend-complete",
        title="Backend",
        description="Backend task",
        required_capabilities=["backend"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client",
            organization_id="org-rewake-open-task",
        ),
    )
    await manager.create_task(
        task_id="test-open",
        title="Tests",
        description="Tests wait for the backend result.",
        required_capabilities=["testing", "unit-test", "api-test"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client",
            organization_id="org-rewake-open-task",
        ),
    )

    turns = []

    async def run_organization_turn(**kwargs):
        turns.append(kwargs)
        return True

    runtime._team_runtime_manager.run_organization_turn = run_organization_turn
    topic_id = OrgTopic.TASK.build(session_id, "org-rewake-open-task")
    handler = next(handler for topic, handler in agents["team-b"].team_backend.messager.subscriptions if topic == topic_id)
    await handler(
        OrgEventMessage.from_event(
            OrgTaskCompletedEvent(
                organization_id="org-rewake-open-task",
                team_id="team-a",
                leader_id="leader-team-a",
                task_id="backend-complete",
            )
        )
    )
    await asyncio.sleep(0)

    assert turns[0]["team_name"] == "team-b"
    prompt = turns[0]["inputs"]["query"]
    assert "test-open" in prompt
    assert "MUST call org_claim_task" in prompt
    assert "org_update_task(action='complete')" in prompt
    assert "Do not wait for another team to fix" in prompt


@pytest.mark.asyncio
async def test_owner_can_dissolve_organization_and_recreate_it(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    await runtime.create_organization(
        organization_id="org-dissolve",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-dissolve",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )
    manager = agents["team-a"].team_backend.org_task_manager
    await manager.create_task(
        task_id="dissolve-task",
        title="Temporary task",
        description="This row must be removed.",
        required_capabilities=["cleanup"],
        created_by=OrgTaskCreator(
            creator_type="client",
            creator_id="client",
            organization_id="org-dissolve",
        ),
    )

    result = await runtime.dissolve_organization(
        organization_id="org-dissolve",
        owner_team_id="team-a",
        session_id=session_id,
    )

    assert result["deleted"]["organization"] == 1
    assert result["deleted"]["tasks"] == 1
    assert agents["team-a"].team_backend.org_task_manager is None
    assert agents["team-b"].team_backend.org_task_manager is None
    assert "organization_owner_lifecycle" not in agents["team-a"].harness.system_prompt_builder.sections
    assert "org_view_tasks" not in {tool.card.name for tool in agents["team-a"].harness.tools}
    assert await manager.get_organization() is None

    recreated = await runtime.create_organization(
        organization_id="org-dissolve",
        owner_team_id="team-a",
        session_id=session_id,
    )
    assert recreated.organization_id == "org-dissolve"


@pytest.mark.asyncio
async def test_non_owner_cannot_dissolve_organization_even_without_bindings(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    await runtime.create_organization(
        organization_id="org-dissolve-guard",
        owner_team_id="team-a",
        session_id=session_id,
    )
    await runtime.invite_team(
        organization_id="org-dissolve-guard",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )
    runtime._team_organizations.clear()

    with pytest.raises(ValueError, match="only the organization owner team can dissolve an organization"):
        await runtime.dissolve_organization(
            organization_id="org-dissolve-guard",
            owner_team_id="team-b",
            session_id=session_id,
        )

    manager = agents["team-a"].team_backend.org_task_manager
    assert manager is not None
    assert (await manager.get_organization()) is not None


@pytest.mark.asyncio
async def test_drain_leader_turns_waits_until_running_team_pauses(active_organization_runtime, monkeypatch):
    org_runtime, _agents, session_id = active_organization_runtime
    sleep_calls: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) == 3:
            entry.state = RuntimeState.PAUSED

    monkeypatch.setattr(asyncio, "sleep", record_sleep)

    entry = await org_runtime._team_runtime_manager.pool.get("team-a")
    entry.state = RuntimeState.RUNNING
    turns = []

    async def run_organization_turn(**kwargs):
        turns.append(kwargs)
        return True

    org_runtime._team_runtime_manager.run_organization_turn = run_organization_turn

    key = (session_id, "team-a")
    org_runtime._leader_turn_queues[key] = deque([{"query": "queued turn"}])
    await org_runtime._drain_leader_turns("team-a", session_id)

    assert turns == [
        {
            "team_name": "team-a",
            "session_id": session_id,
            "inputs": {"query": "queued turn"},
        }
    ]
    assert key not in org_runtime._leader_turn_queues
    assert key not in org_runtime._leader_turn_workers
    assert len(sleep_calls) == 3
