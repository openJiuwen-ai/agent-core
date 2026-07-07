# coding: utf-8

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.organization.events import OrgEvent
from openjiuwen.agent_teams.organization.schema import (
    OrgAssignmentType,
    OrgTaskCreator,
    OrgTaskStatus,
)
from openjiuwen.agent_teams.organization.task_pool import OrgTaskManager
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase


class FakeMessager:
    def __init__(self) -> None:
        self.published = []

    async def publish(self, topic_id, message):
        self.published.append((topic_id, message))


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
