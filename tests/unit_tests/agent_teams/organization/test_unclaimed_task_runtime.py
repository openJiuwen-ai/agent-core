# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Organization runtime integration for durable unclaimed-task notifications."""

import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.organization.schema import OrgTaskCreator
from openjiuwen.agent_teams.organization.tools import OrgCreateOrganizationTool
from openjiuwen.agent_teams.runtime.pool import RuntimeState

pytest_plugins = ["tests.unit_tests.agent_teams.organization.test_task_pool"]


@pytest_asyncio.fixture
async def bound_runtime(active_organization_runtime, monkeypatch):
    runtime, agents, session_id = active_organization_runtime
    clock = [1_000_000]
    for module in ("task_pool", "message_service", "unclaimed"):
        monkeypatch.setattr(f"openjiuwen.agent_teams.organization.{module}.get_current_time", lambda: clock[0])
    tool = OrgCreateOrganizationTool(runtime, "team-a", session_id)
    result = await tool.invoke(
        {
            "organization_id": "org",
            "unclaimed_task_policy": {
                "initial_claim_timeout_seconds": 10,
                "description_update_timeout_seconds": 4,
                "post_update_claim_timeout_seconds": 8,
            },
        }
    )
    assert result.success
    assert result.data["unclaimed_task_policy"]["initial_claim_timeout_seconds"] == 10
    await runtime.invite_team(
        organization_id="org",
        inviter_team_id="team-a",
        target_team_id="team-b",
        session_id=session_id,
    )
    assert len(runtime._unclaimed_services) == 1
    service = runtime._unclaimed_services[(session_id, "org")]
    await service.stop()  # Drive deterministic ticks; no wall-clock waiting.
    pool = agents["team-a"].team_backend.org_task_manager
    assert (
        await pool.create_task(
            task_id="task",
            title="Analyze",
            description="Original description",
            required_capabilities=["analysis"],
            created_by=OrgTaskCreator(
                creator_type="team_leader", creator_id="leader-team-a", team_id="team-a", organization_id="org"
            ),
        )
    ).ok
    turns = []

    async def runner(**kwargs):
        turns.append(kwargs)
        return True

    runtime._team_runtime_manager.run_organization_turn = runner
    return runtime, pool, service, session_id, clock, turns


async def drain(runtime):
    await asyncio.gather(*list(runtime._leader_turn_workers.values()))


@pytest.mark.asyncio
async def test_revision_wakes_creator_and_retries_until_handled(bound_runtime):
    runtime, pool, service, session_id, clock, turns = bound_runtime
    clock[0] += 10_000
    await service.scan_once()
    await drain(runtime)
    assert len(turns) == 1
    assert turns[0]["team_name"] == "team-a"
    assert 'action="revise_description"' in turns[0]["inputs"]["query"]
    await service.scan_once()
    await drain(runtime)
    assert len(turns) == 2
    task = await pool.get_task("task")
    assert (
        await pool.revise_unclaimed_task_description(
            task_id="task",
            team_id="team-a",
            leader_id="leader-team-a",
            request_id=task.unclaimed.request_id,
            expected_description_revision=0,
            description="Clarified scope and acceptance criteria",
        )
    ).ok
    await service.scan_once()
    await drain(runtime)
    assert len(turns) == 3
    assert turns[-1]["team_name"] == "team-b"
    assert "org_claim_task" in turns[-1]["inputs"]["query"]
    assert (await pool.claim_task(task_id="task", team_id="team-b")).ok
    await service.scan_once()
    await drain(runtime)
    assert len(turns) == 3
    assert await service.manager.message_service.list_pending_system_notifications() == []


@pytest.mark.asyncio
async def test_queued_revision_is_discarded_when_creator_was_busy(bound_runtime, monkeypatch):
    runtime, pool, service, session_id, clock, turns = bound_runtime
    entry = await runtime._team_runtime_manager.pool.get("team-a")
    entry.state = RuntimeState.RUNNING
    clock[0] += 10_000
    await service.scan_once()
    # Expire while the request is still queued; the worker must re-read state.
    clock[0] += 4_000
    await service.scan_once()
    entry.state = RuntimeState.PAUSED
    monkeypatch.setattr("openjiuwen.agent_teams.organization.runtime._LEADER_TURN_PAUSE_POLL_INTERVAL_SECONDS", 0)
    await drain(runtime)
    assert len(turns) == 1
    assert "recreation_request_id" in turns[0]["inputs"]["query"]
    assert 'action="revise_description"' not in turns[0]["inputs"]["query"]


@pytest.mark.asyncio
async def test_failed_leader_turn_keeps_durable_request(bound_runtime):
    runtime, pool, service, session_id, clock, turns = bound_runtime

    async def fail(**kwargs):
        return False

    runtime._team_runtime_manager.run_organization_turn = fail
    clock[0] += 10_000
    await service.scan_once()
    await drain(runtime)
    assert len(await service.manager.message_service.list_pending_system_notifications()) == 1

    async def success(**kwargs):
        turns.append(kwargs)
        return True

    runtime._team_runtime_manager.run_organization_turn = success
    await service.scan_once()
    await drain(runtime)
    assert len(turns) == 1


@pytest.mark.asyncio
async def test_dissolve_stops_scanner_and_removes_notifications(bound_runtime):
    runtime, pool, service, session_id, clock, turns = bound_runtime
    service.start()
    worker = service._worker
    await runtime.dissolve_organization(organization_id="org", owner_team_id="team-a", session_id=session_id)
    assert worker.done()
    assert runtime._unclaimed_services == {}
    assert await service.manager.message_service.list_pending_system_notifications() == []


@pytest.mark.asyncio
async def test_last_team_release_stops_scanner(bound_runtime):
    runtime, pool, service, session_id, clock, turns = bound_runtime
    service.start()
    worker = service._worker
    await runtime.release_team(team_id="team-a", session_id=session_id)
    assert not worker.done()
    await runtime.release_team(team_id="team-b", session_id=session_id)
    assert worker.done()
    assert runtime._unclaimed_services == {}


@pytest.mark.asyncio
async def test_stop_team_releases_organization_work_before_coordination(bound_runtime):
    runtime, pool, service, session_id, clock, turns = bound_runtime
    host = runtime._team_runtime_manager
    host._organization_runtime_manager = runtime
    entry = await host.pool.get("team-a")

    async def stopped():
        assert (session_id, "team-a") not in runtime._team_organizations

    entry.agent.stop_coordination = AsyncMock(side_effect=stopped)
    assert await host.stop_team(team_name="team-a", session_id=session_id)
    assert await host.pool.get("team-a") is None


@pytest.mark.asyncio
async def test_invalid_policy_is_rejected_by_creation_tool(active_organization_runtime):
    runtime, agents, session_id = active_organization_runtime
    result = await OrgCreateOrganizationTool(runtime, "team-a", session_id).invoke(
        {
            "organization_id": "invalid",
            "unclaimed_task_policy": {"description_update_timeout_seconds": 0},
        }
    )
    assert not result.success
    assert runtime._unclaimed_services == {}
