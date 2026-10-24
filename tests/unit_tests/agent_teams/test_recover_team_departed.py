# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""recover_team must not resurrect soft-stopped members or strand passive humans."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.agent.recovery_manager import RecoveryManager
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import MemberRosterEntry, TeamRole

pytestmark = pytest.mark.level0


def _manager(members, *, restart=None, transitions=None) -> RecoveryManager:
    transitions = transitions if transitions is not None else []

    async def _transition(name, team, frm, to):
        transitions.append((name, frm, to))
        return True

    team_backend = SimpleNamespace(
        restore_external_cli_specs_from_db=AsyncMock(),
        list_members=AsyncMock(return_value=members),
        list_member_roster=AsyncMock(
            return_value=[
                MemberRosterEntry(
                    member_name=m.member_name,
                    display_name=getattr(m, "display_name", m.member_name),
                    status=m.status,
                )
                for m in members
            ]
        ),
        db=SimpleNamespace(
            member=SimpleNamespace(
                update_member_status=AsyncMock(return_value=True),
                try_transition_member_status=_transition,
            )
        ),
    )
    spawn_manager = SimpleNamespace(
        has_live_handle=lambda _name: False,
        restart_teammate=restart or AsyncMock(return_value=True),
    )
    configurator = SimpleNamespace(
        member_name="leader",
        team_name="team",
        team_backend=team_backend,
        role=TeamRole.LEADER,
    )
    return RecoveryManager(configurator, spawn_manager)


@pytest.mark.asyncio
async def test_recover_skips_shutdown_members():
    restart = AsyncMock(return_value=True)
    mgr = _manager(
        [
            SimpleNamespace(
                member_name="gone",
                status=MemberStatus.SHUTDOWN.value,
                role=TeamRole.TEAMMATE.value,
            ),
            SimpleNamespace(
                member_name="leaving",
                status=MemberStatus.SHUTDOWN_REQUESTED.value,
                role=TeamRole.TEAMMATE.value,
            ),
            SimpleNamespace(
                member_name="alive",
                status=MemberStatus.READY.value,
                role=TeamRole.TEAMMATE.value,
            ),
        ],
        restart=restart,
    )
    restarted = await mgr.recover_team()
    assert restarted == ["alive"]
    restart.assert_awaited_once_with("alive")


@pytest.mark.asyncio
async def test_recover_reads_role_from_full_member_rows():
    """Roster projection has no role; recovery must use list_members instead."""
    restart = AsyncMock(return_value=True)
    transitions: list = []
    mgr = _manager(
        [
            SimpleNamespace(
                member_name="pm1",
                status=MemberStatus.READY.value,
                role=TeamRole.PASSIVE_HUMAN.value,
            ),
            SimpleNamespace(
                member_name="dev",
                status=MemberStatus.READY.value,
                role=TeamRole.TEAMMATE.value,
            ),
        ],
        restart=restart,
        transitions=transitions,
    )
    restarted = await mgr.recover_team()
    assert restarted == ["dev"]
    restart.assert_awaited_once_with("dev")
    assert transitions == []
    mgr._configurator.team_backend.list_members.assert_awaited()


@pytest.mark.asyncio
async def test_recover_settles_passive_restarting_without_harness_restart():
    restart = AsyncMock(return_value=True)
    transitions: list = []
    mgr = _manager(
        [
            SimpleNamespace(
                member_name="pm1",
                status=MemberStatus.RESTARTING.value,
                role=TeamRole.PASSIVE_HUMAN.value,
            ),
            SimpleNamespace(
                member_name="pm2",
                status=MemberStatus.READY.value,
                role=TeamRole.PASSIVE_HUMAN.value,
            ),
            SimpleNamespace(
                member_name="pm3",
                status=MemberStatus.SHUTDOWN.value,
                role=TeamRole.PASSIVE_HUMAN.value,
            ),
            SimpleNamespace(
                member_name="pm4",
                status=MemberStatus.STOPPED.value,
                role=TeamRole.PASSIVE_HUMAN.value,
            ),
            SimpleNamespace(
                member_name="pm5",
                status=MemberStatus.PAUSED.value,
                role=TeamRole.PASSIVE_HUMAN.value,
            ),
        ],
        restart=restart,
        transitions=transitions,
    )
    restarted = await mgr.recover_team()
    assert restarted == []
    restart.assert_not_awaited()
    assert transitions == [
        ("pm1", MemberStatus.RESTARTING, MemberStatus.READY),
        ("pm4", MemberStatus.STOPPED, MemberStatus.READY),
        ("pm5", MemberStatus.PAUSED, MemberStatus.READY),
    ]


@pytest.mark.asyncio
async def test_recover_team_with_real_roster_rows_does_not_attribute_error():
    """list_member_roster entries lack role; recover must use full member rows."""
    from openjiuwen.agent_teams.context import reset_session_id, set_session_id
    from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
    from openjiuwen.agent_teams.tools.team import TeamBackend

    token = set_session_id("recover-role-session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await database.initialize()
    try:
        backend = TeamBackend(
            team_name="recover-role-team",
            member_name="team_leader",
            is_leader=True,
            db=database,
            messager=AsyncMock(),
            enable_hitt=True,
        )
        await backend.build_team(
            display_name="Recover",
            desc="d",
            leader_display_name="Leader",
            leader_desc="leads",
        )
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        await backend.spawn_passive_human(member_name="pm1", display_name="PM", desc="decides")
        await backend.spawn_member(
            member_name="dev1",
            display_name="Dev",
            agent_card=AgentCard(id="dev1", name="Dev", description="builds"),
            status=MemberStatus.READY,
            role=TeamRole.TEAMMATE,
        )
        roster = await backend.list_member_roster()
        assert all(not hasattr(row, "role") for row in roster)

        restart = AsyncMock(return_value=True)
        mgr = RecoveryManager(
            SimpleNamespace(
                member_name="team_leader",
                team_name="recover-role-team",
                team_backend=backend,
                role=TeamRole.LEADER,
            ),
            SimpleNamespace(has_live_handle=lambda _n: False, restart_teammate=restart),
        )
        restarted = await mgr.recover_team()
        assert "dev1" in restarted
        restart.assert_awaited_once_with("dev1")
        pm = await database.member.get_member("pm1", "recover-role-team")
        assert pm.status == MemberStatus.READY.value
    finally:
        await database.close()
        reset_session_id(token)
