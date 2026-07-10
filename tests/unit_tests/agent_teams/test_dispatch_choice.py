# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for F_62 dispatch-mode configuration: static spec wiring."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.team import CapabilityOverrides, TeamBackend
from openjiuwen.agent_teams.tools.tool_team import BuildTeamTool

TEAM = "choice_team"


@pytest_asyncio.fixture
async def db():
    token = set_session_id("choice_session")
    database = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    try:
        await database.initialize()
        yield database
    finally:
        reset_session_id(token)
        await database.close()


def _backend(db, *, dispatch_mode: str = "scheduled", enable_task_verification: bool = False) -> TeamBackend:
    return TeamBackend(
        team_name=TEAM,
        member_name="leader",
        is_leader=True,
        db=db,
        messager=AsyncMock(spec=Messager),
        dispatch_mode=dispatch_mode,
        enable_task_verification=enable_task_verification,
    )


async def _build(backend: TeamBackend, overrides: CapabilityOverrides | None = None) -> None:
    await backend.build_team(
        display_name="Choice",
        desc="d",
        leader_display_name="Leader",
        leader_desc="ld",
        overrides=overrides,
    )


# ---------------------------------------------------------------------------
# Static spec mode wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_backend_and_task_manager_follow_spec_mode(db):
    scheduled = _backend(db, dispatch_mode="scheduled")
    assert scheduled.dispatch_mode == "scheduled"
    assert scheduled.task_manager._dispatch_mode == "scheduled"

    autonomous = TeamBackend(
        team_name="auto_team",
        member_name="leader",
        is_leader=True,
        db=db,
        messager=AsyncMock(spec=Messager),
    )
    assert autonomous.dispatch_mode == "autonomous"
    assert autonomous.task_manager._dispatch_mode == "autonomous"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_team_records_spec_mode_on_team_row(db):
    backend = _backend(db, dispatch_mode="scheduled")
    await _build(backend)
    team = await db.team.get_team(TEAM)
    assert team.dispatch_mode == "scheduled"
    # The spec stays the runtime source of truth — the row is a record.
    assert backend.dispatch_mode == "scheduled"


# ---------------------------------------------------------------------------
# enable_task_verification override (the only build_team-time knob here)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.level0
async def test_task_verification_override_persists(db):
    backend = _backend(db, enable_task_verification=False)
    await _build(backend, CapabilityOverrides(enable_task_verification=True))
    assert backend.task_verification_enabled() is True
    team = await db.team.get_team(TEAM)
    assert bool(team.enable_task_verification) is True


@pytest.mark.asyncio
@pytest.mark.level1
async def test_build_team_tool_has_no_dispatch_choice(db):
    tool = BuildTeamTool(_backend(db), make_translator("cn"))
    properties = tool.card.input_params["properties"]
    assert "dispatch_mode" not in properties
    assert "enable_task_verification" in properties

    result = await tool.invoke(
        {
            "display_name": "Choice",
            "team_desc": "d",
            "leader_display_name": "Leader",
            "leader_desc": "ld",
            "enable_task_verification": True,
        }
    )
    assert result.success
    assert result.data["enable_task_verification"] is True
    assert "dispatch_mode" not in result.data


# ---------------------------------------------------------------------------
# Spec knobs validation
# ---------------------------------------------------------------------------


@pytest.mark.level1
def test_spec_review_knobs_validation():
    base = {"agents": {"leader": DeepAgentSpec()}, "spawn_mode": "inprocess"}
    spec = TeamAgentSpec(**base)
    assert spec.verify_vote_threshold == pytest.approx(2 / 3)
    assert spec.default_max_review_rounds == 3
    assert spec.review_stall_timeout == 1800

    with pytest.raises(ValueError):
        TeamAgentSpec(**base, verify_vote_threshold=0)
    with pytest.raises(ValueError):
        TeamAgentSpec(**base, verify_vote_threshold=1.5)
    with pytest.raises(ValueError):
        TeamAgentSpec(**base, default_max_review_rounds=0)
    with pytest.raises(ValueError):
        TeamAgentSpec(**base, review_stall_timeout=0)
