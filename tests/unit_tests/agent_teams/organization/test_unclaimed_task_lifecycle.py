# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unclaimed task deadlines, transactional notifications and recreation."""

import asyncio

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select

from openjiuwen.agent_teams.organization.manager import TeamOrganizationManager
from openjiuwen.agent_teams.organization.schema import (
    OrgTaskCreator,
    OrgTaskEventRecord,
    OrgTaskFailureCode,
    OrgTaskStatus,
    OrgUnclaimedPhase,
    OrgUnclaimedTaskPolicy,
)
from openjiuwen.agent_teams.organization.tools import OrgCreateTaskTool, OrgUpdateTaskTool
from openjiuwen.agent_teams.organization.unclaimed import OrgUnclaimedTaskService
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase


@pytest_asyncio.fixture
async def lifecycle(monkeypatch):
    clock = [1_000_000]
    monkeypatch.setattr("openjiuwen.agent_teams.organization.task_pool.get_current_time", lambda: clock[0])
    monkeypatch.setattr("openjiuwen.agent_teams.organization.message_service.get_current_time", lambda: clock[0])
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    manager = TeamOrganizationManager(organization_id="org", db=db, session_id="session")
    await manager.initialize(
        unclaimed_task_policy=OrgUnclaimedTaskPolicy(
            initial_claim_timeout_seconds=10,
            description_update_timeout_seconds=4,
            post_update_claim_timeout_seconds=8,
        )
    )
    for team in ("creator", "worker"):
        await manager.register_leader(team_id=team, leader_id=team, capabilities=["analysis"])
    yield manager, clock
    await db.close()


async def create(manager, task_id="task", **kwargs):
    return await manager.task_pool.create_task(
        task_id=task_id,
        title="Analyze",
        description="Original task",
        required_capabilities=["analysis"],
        created_by=OrgTaskCreator(
            creator_type="team_leader", creator_id="creator", team_id="creator", organization_id="org"
        ),
        **kwargs,
    )


async def request_revision(manager, clock, task_id="task"):
    task = await manager.task_pool.get_task(task_id)
    clock[0] = task.unclaimed.deadline_at
    await manager.task_pool.advance_unclaimed_tasks(now=clock[0])
    return await manager.task_pool.get_task(task_id)


async def revise(manager, task, description="Clarified scope, inputs, deliverables and acceptance criteria"):
    return await manager.task_pool.revise_unclaimed_task_description(
        task_id=task.task_id,
        team_id="creator",
        leader_id="creator",
        request_id=task.unclaimed.request_id,
        expected_description_revision=0,
        description=description,
    )


async def expire(manager, clock, task_id="task"):
    task = await request_revision(manager, clock, task_id)
    clock[0] = task.unclaimed.deadline_at
    await manager.task_pool.advance_unclaimed_tasks(now=clock[0])
    return await manager.task_pool.get_task(task_id)


def test_policy_defaults_and_validation():
    policy = OrgUnclaimedTaskPolicy()
    assert policy.enabled
    assert (
        policy.initial_claim_timeout_seconds,
        policy.description_update_timeout_seconds,
        policy.post_update_claim_timeout_seconds,
        policy.scan_interval_seconds,
    ) == (300, 180, 300, 5)
    for field in (
        "initial_claim_timeout_seconds",
        "description_update_timeout_seconds",
        "post_update_claim_timeout_seconds",
        "scan_interval_seconds",
    ):
        with pytest.raises(ValidationError):
            OrgUnclaimedTaskPolicy(**{field: 0})


@pytest.mark.asyncio
async def test_full_two_stage_deadlines_and_durable_notifications(lifecycle):
    manager, clock = lifecycle
    pool = manager.task_pool
    original = (await create(manager)).task
    assert original.unclaimed.phase is OrgUnclaimedPhase.INITIAL_WAIT
    assert original.unclaimed.deadline_at == clock[0] + 10_000
    assert await pool.advance_unclaimed_tasks(now=original.unclaimed.deadline_at - 1) == 0
    pending = await request_revision(manager, clock)
    assert pending.status is OrgTaskStatus.OPEN
    assert pending.unclaimed.deadline_at == clock[0] + 4_000
    messages = await manager.message_service.list_pending_system_notifications()
    assert len(messages) == 1
    assert messages[0]["to_team_id"] == "creator"
    assert messages[0]["metadata"]["request_id"] == pending.unclaimed.request_id
    assert not (
        await manager.message_service.ack_leader_message(
            message_id=messages[0]["message_id"],
            team_id="creator",
            leader_id="creator",
        )
    ).ok
    clock[0] += 1_000
    result = await revise(manager, pending)
    assert result.ok
    assert result.task.unclaimed.deadline_at == clock[0] + 8_000
    assert result.task.unclaimed.description_revision == 1
    assert (await revise(manager, pending)).task.unclaimed.deadline_at == result.task.unclaimed.deadline_at
    assert not (await revise(manager, pending, "A second revision")).ok
    notifications = await manager.message_service.list_pending_system_notifications()
    updated = [item for item in notifications if item["metadata"]["unclaimed_kind"] == "revised"]
    assert [item["to_team_id"] for item in updated] == ["worker"]
    clock[0] = result.task.unclaimed.deadline_at
    assert await pool.advance_unclaimed_tasks(now=clock[0]) == 1
    task = await pool.get_task("task")
    assert task.status is OrgTaskStatus.FAILED
    assert task.failure_code is OrgTaskFailureCode.EXPIRED
    assert task.failure_reason == "post_update_claim_timeout"
    assert task.description == result.task.description
    async with manager.db_context.sessions.read() as session:
        events = (
            (
                await session.execute(
                    select(OrgTaskEventRecord).where(
                        OrgTaskEventRecord.task_id == "task",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert [event.event_type for event in events].count("org_task_description_revised") == 1
    assert [event.event_type for event in events].count("org_task_failed") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["claim", "delegate"])
@pytest.mark.parametrize("phase", ["initial", "revision", "revised"])
async def test_assignment_closes_all_waiting_phases(lifecycle, operation, phase):
    manager, clock = lifecycle
    await create(manager)
    if phase != "initial":
        task = await request_revision(manager, clock)
        if phase == "revised":
            assert (await revise(manager, task)).ok
    if operation == "claim":
        result = await manager.task_pool.claim_task(task_id="task", team_id="worker")
    else:
        result = await manager.task_pool.delegate_task(task_id="task", from_team_id="creator", to_team_id="worker")
    assert result.ok
    assert result.task.unclaimed.phase is OrgUnclaimedPhase.CLOSED
    assert result.task.unclaimed.deadline_at is None
    assert await manager.task_pool.advance_unclaimed_tasks(now=clock[0] + 999_000) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("revised", [False, True])
async def test_deadline_is_enforced_before_scanner_runs(lifecycle, revised):
    manager, clock = lifecycle
    await create(manager)
    task = await request_revision(manager, clock)
    if revised:
        task = (await revise(manager, task)).task
    clock[0] = task.unclaimed.deadline_at
    assert not (await manager.task_pool.claim_task(task_id="task", team_id="worker")).ok
    assert not (
        await manager.task_pool.delegate_task(
            task_id="task",
            from_team_id="creator",
            to_team_id="worker",
        )
    ).ok
    if not revised:
        assert not (await revise(manager, task)).ok
    assert await manager.task_pool.advance_unclaimed_tasks(now=clock[0]) == 1


@pytest.mark.asyncio
async def test_policy_snapshot_and_exclusions(lifecycle):
    manager, clock = lifecycle
    task = (await create(manager)).task
    await manager.initialize(unclaimed_task_policy=OrgUnclaimedTaskPolicy(enabled=False))
    assert (await create(manager, "disabled")).task.unclaimed is None
    await manager.initialize(unclaimed_task_policy=OrgUnclaimedTaskPolicy(initial_claim_timeout_seconds=20))
    assert (await create(manager, "new")).task.unclaimed.deadline_at == clock[0] + 20_000
    assert (await manager.task_pool.get_task(task.task_id)).unclaimed.deadline_at == clock[0] + 10_000
    assert (await create(manager, "delegated", delegated_to_team_id="worker")).task.unclaimed is None
    assert (await create(manager, "summary", task_type="organization.summary")).task.unclaimed is None
    client = await manager.task_pool.create_task(
        title="Client task",
        description="root",
        required_capabilities=["analysis"],
        created_by=OrgTaskCreator(creator_id="client", organization_id="org"),
    )
    assert client.task.unclaimed is None


@pytest.mark.asyncio
async def test_revision_permissions_and_validation(lifecycle):
    manager, clock = lifecycle
    await create(manager)
    task = await request_revision(manager, clock)
    assert not (await revise(manager, task, "   ")).ok
    assert not (await revise(manager, task, "Original task")).ok
    for team, leader, request in (
        ("worker", "worker", task.unclaimed.request_id),
        ("creator", "imposter", task.unclaimed.request_id),
        ("creator", "creator", "wrong"),
    ):
        result = await manager.task_pool.revise_unclaimed_task_description(
            task_id="task",
            team_id=team,
            leader_id=leader,
            request_id=request,
            expected_description_revision=0,
            description="Updated task",
        )
        assert not result.ok


@pytest.mark.asyncio
async def test_concurrent_scanners_and_revisions(lifecycle):
    manager, clock = lifecycle
    task = (await create(manager)).task
    clock[0] = task.unclaimed.deadline_at
    counts = await asyncio.gather(*(manager.task_pool.advance_unclaimed_tasks(now=clock[0]) for _ in range(3)))
    assert sum(counts) == 1
    task = await manager.task_pool.get_task("task")
    results = await asyncio.gather(revise(manager, task, "Revision A"), revise(manager, task, "Revision B"))
    assert sum(result.ok for result in results) == 1
    assert (
        len(
            [
                m
                for m in await manager.message_service.list_pending_system_notifications()
                if m["metadata"]["unclaimed_kind"] == "revision"
            ]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_claim_revision_race_does_not_expire_assigned_task(lifecycle):
    manager, clock = lifecycle
    await create(manager)
    task = await request_revision(manager, clock)
    claimed, _ = await asyncio.gather(
        manager.task_pool.claim_task(task_id="task", team_id="worker"),
        revise(manager, task),
    )
    assert claimed.ok
    assert (await manager.task_pool.get_task("task")).status is OrgTaskStatus.CLAIMED
    assert await manager.task_pool.advance_unclaimed_tasks(now=clock[0] + 999_000) == 0


@pytest.mark.asyncio
async def test_notification_and_state_rollback_together(lifecycle, monkeypatch):
    manager, clock = lifecycle
    task = (await create(manager)).task

    def failed_notification(*args, **kwargs):
        raise RuntimeError("inbox unavailable")

    monkeypatch.setattr(manager.task_pool, "_add_unclaimed_notification", failed_notification)
    with pytest.raises(RuntimeError, match="inbox unavailable"):
        await manager.task_pool.advance_unclaimed_tasks(now=task.unclaimed.deadline_at)
    assert (await manager.task_pool.get_task("task")).unclaimed.phase is OrgUnclaimedPhase.INITIAL_WAIT
    assert await manager.message_service.list_pending_system_notifications() == []


@pytest.mark.asyncio
async def test_pagination_and_recovery_replay(lifecycle):
    manager, clock = lifecycle
    for index in range(105):
        assert (await create(manager, f"task-{index:03d}")).ok
    clock[0] += 10_000
    assert await manager.task_pool.advance_unclaimed_tasks(now=clock[0], batch_size=17) == 105
    replayed = []

    async def notify(message):
        replayed.append(message["message_id"])

    recovered = TeamOrganizationManager(organization_id="org", db=manager.task_pool.db, session_id="session")
    service = OrgUnclaimedTaskService(recovered, notify, 5)
    await service.scan_once(now=clock[0])
    assert len(set(replayed)) == 105
    await service.scan_once(now=clock[0])
    assert len(replayed) == 210  # No receipt was acknowledged by the simulated runner.
    clock[0] += 4_000
    await service.scan_once(now=clock[0])
    assert all(task.status is OrgTaskStatus.FAILED for task in await recovered.task_pool.list_tasks(limit=200))


@pytest.mark.asyncio
@pytest.mark.parametrize("child", [False, True])
async def test_expiration_recreation_is_idempotent_and_keeps_tree(lifecycle, child):
    manager, clock = lifecycle
    parent_id = None
    if child:
        parent_id = "parent"
        await create(manager, parent_id)
        assert (await manager.task_pool.claim_task(task_id=parent_id, team_id="creator")).ok
    await create(manager, parent_task_id=parent_id)
    expired = await expire(manager, clock)
    assert expired.failure_reason == "description_update_timeout"
    messages = await manager.message_service.list_pending_system_notifications()
    notification = next(message for message in messages if message["metadata"]["unclaimed_kind"] == "expired")
    tool = OrgCreateTaskTool(manager.task_pool, "creator", "creator")
    inputs = {
        "title": "Improved task",
        "description": "Better scope",
        "required_capabilities": ["analysis"],
        "recreation_request_id": notification["message_id"],
    }
    first, second = await asyncio.gather(tool.invoke(inputs), tool.invoke(inputs))
    assert first.success and second.success
    assert first.data["task_id"] == second.data["task_id"]
    new = await manager.task_pool.get_task(first.data["task_id"])
    assert new.task_id != expired.task_id
    assert new.recreated_from_task_id == expired.task_id
    assert new.parent_task_id == parent_id
    assert new.unclaimed.phase is OrgUnclaimedPhase.INITIAL_WAIT
    if child:
        assert new.metadata["repairs_task_id"] == expired.task_id
    else:
        assert new.root_task_id == new.task_id
    assert (await manager.task_pool.get_task("task")).status is OrgTaskStatus.FAILED
    assert not (await OrgCreateTaskTool(manager.task_pool, "worker", "worker").invoke(inputs)).success


@pytest.mark.asyncio
async def test_revision_tool_action(lifecycle):
    manager, clock = lifecycle
    await create(manager)
    task = await request_revision(manager, clock)
    tool = OrgUpdateTaskTool(manager.task_pool, "creator", "creator")
    assert not (await tool.invoke({"action": "revise_description", "task_id": "task"})).success
    result = await tool.invoke(
        {
            "action": "revise_description",
            "task_id": "task",
            "request_id": task.unclaimed.request_id,
            "expected_description_revision": 0,
            "description": "Clarified description",
        }
    )
    assert result.success
    assert result.data["unclaimed"]["description_revision"] == 1


@pytest.mark.asyncio
async def test_failed_broadcast_does_not_lose_expiration_notification(lifecycle):
    manager, clock = lifecycle

    class OfflineMessager:
        async def publish(self, *args):
            raise ConnectionError("transport offline")

    manager.task_pool.messager = OfflineMessager()
    await create(manager)
    task = await expire(manager, clock)
    assert task.failure_code is OrgTaskFailureCode.EXPIRED
    messages = await manager.message_service.list_pending_system_notifications()
    assert len([message for message in messages if message["metadata"]["unclaimed_kind"] == "expired"]) == 1


@pytest.mark.asyncio
async def test_second_repair_uses_original_target_and_respects_budget(lifecycle):
    manager, clock = lifecycle
    await create(manager, "parent")
    await manager.task_pool.claim_task(task_id="parent", team_id="creator")
    await create(manager, metadata={"retry_limit": 1}, parent_task_id="parent")
    await expire(manager, clock)
    expired_request = next(
        message
        for message in await manager.message_service.list_pending_system_notifications()
        if message["metadata"]["unclaimed_kind"] == "expired"
    )
    first = await create(manager, "repair", recreation_request_id=expired_request["message_id"])
    assert first.ok
    assert first.task.metadata["repairs_task_id"] == "task"
    await expire(manager, clock, "repair")
    second_request = next(
        message
        for message in await manager.message_service.list_pending_system_notifications()
        if message["metadata"]["unclaimed_kind"] == "expired" and message["metadata"]["task_id"] == "repair"
    )
    second = await create(manager, "repair-again", recreation_request_id=second_request["message_id"])
    assert not second.ok
    assert "retry_limit reached" in second.reason


@pytest.mark.asyncio
async def test_database_restart_preserves_tr_deadline(tmp_path, monkeypatch):
    clock = [1_000_000]
    monkeypatch.setattr("openjiuwen.agent_teams.organization.task_pool.get_current_time", lambda: clock[0])
    config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=str(tmp_path / "unclaimed.db"))
    db = TeamDatabase(config)
    manager = TeamOrganizationManager(organization_id="org", db=db)
    await manager.initialize()
    await create(manager)
    requested = await request_revision(manager, clock)
    deadline = requested.unclaimed.deadline_at
    await db.close()
    recovered_db = TeamDatabase(config)
    try:
        recovered = TeamOrganizationManager(organization_id="org", db=recovered_db)
        task = await recovered.task_pool.get_task("task")
        assert task.unclaimed.deadline_at == deadline
        assert task.unclaimed.request_id == requested.unclaimed.request_id
        clock[0] = deadline
        assert await recovered.task_pool.advance_unclaimed_tasks(now=clock[0]) == 1
        assert (await recovered.task_pool.get_task("task")).failure_code is OrgTaskFailureCode.EXPIRED
    finally:
        await recovered_db.close()
