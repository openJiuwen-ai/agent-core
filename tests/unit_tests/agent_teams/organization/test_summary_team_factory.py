# coding: utf-8

import pytest

from openjiuwen.agent_teams.organization.summary import (
    LaunchedSummaryTeam,
    SummaryTeamSpec,
)
from openjiuwen.agent_teams.organization.summary_team_factory import DefaultSummaryTeamFactory


def test_default_summary_team_factory_matches_protocol_surface():
    # The framework's SummaryTeamFactory protocol is not runtime_checkable.
    # Verify the concrete class exposes the four methods the framework calls.
    for attr in ("default_spec", "provision", "recover", "release"):
        assert hasattr(DefaultSummaryTeamFactory, attr), attr


def test_default_summary_team_factory_returns_preset_spec():
    factory = DefaultSummaryTeamFactory(
        summary_team_builder=lambda *args: None,
        summary_team_stopper=lambda *args: None,
    )
    spec = factory.default_spec()
    assert isinstance(spec, SummaryTeamSpec)
    assert spec.capabilities == ("summary",)
    assert spec.display_name == "Summary Team"


@pytest.mark.asyncio
async def test_default_summary_team_factory_provision_forwards_context():
    calls = []

    async def builder(spec, organization_id, root_task_id, summary_task_id, session_id):
        calls.append((spec, organization_id, root_task_id, summary_task_id, session_id))
        return LaunchedSummaryTeam(
            team_id="summary-1",
            leader_id="summary-leader-1",
            root_task_id=root_task_id,
            summary_task_id=summary_task_id,
            spec=spec,
        )

    factory = DefaultSummaryTeamFactory(
        summary_team_builder=builder,
        summary_team_stopper=lambda *args: None,
    )
    launched = await factory.provision(
        organization_id="org-1",
        root_task_id="root-1",
        summary_task_id="summary-1",
        session_id="session-1",
    )

    assert launched.team_id == "summary-1"
    assert launched.leader_id == "summary-leader-1"
    assert launched.root_task_id == "root-1"
    assert launched.summary_task_id == "summary-1"
    assert len(calls) == 1
    spec, organization_id, root_task_id, summary_task_id, session_id = calls[0]
    assert isinstance(spec, SummaryTeamSpec)
    assert organization_id == "org-1"
    assert root_task_id == "root-1"
    assert summary_task_id == "summary-1"
    assert session_id == "session-1"


@pytest.mark.asyncio
async def test_default_summary_team_factory_release_stops_team():
    stopped = []

    async def stopper(execution_id, session_id):
        stopped.append((execution_id, session_id))

    factory = DefaultSummaryTeamFactory(
        summary_team_builder=lambda *args: None,
        summary_team_stopper=stopper,
    )
    await factory.release(execution_id="exec-1", session_id="session-1")

    assert stopped == [("exec-1", "session-1")]


@pytest.mark.asyncio
async def test_default_summary_team_factory_recover_forwards_when_recoverer_supplied():
    calls = []

    async def recoverer(spec, execution_id, organization_id, root_task_id, summary_task_id, session_id):
        calls.append((spec, execution_id, organization_id, root_task_id, summary_task_id, session_id))
        return LaunchedSummaryTeam(
            team_id="summary-rec",
            leader_id="summary-leader-rec",
            root_task_id=root_task_id,
            summary_task_id=summary_task_id,
            spec=spec,
        )

    factory = DefaultSummaryTeamFactory(
        summary_team_builder=lambda *args: None,
        summary_team_stopper=lambda *args: None,
        summary_team_recoverer=recoverer,
    )
    launched = await factory.recover(
        execution_id="exec-1",
        organization_id="org-1",
        root_task_id="root-1",
        summary_task_id="summary-1",
        session_id="session-1",
    )

    assert launched.team_id == "summary-rec"
    assert len(calls) == 1
    spec, execution_id, organization_id, root_task_id, summary_task_id, session_id = calls[0]
    assert isinstance(spec, SummaryTeamSpec)
    assert execution_id == "exec-1"
    assert organization_id == "org-1"
    assert root_task_id == "root-1"
    assert summary_task_id == "summary-1"
    assert session_id == "session-1"


@pytest.mark.asyncio
async def test_default_summary_team_factory_recover_falls_back_to_builder():
    calls = []

    async def builder(spec, organization_id, root_task_id, summary_task_id, session_id):
        calls.append((spec, organization_id, root_task_id, summary_task_id, session_id))
        return LaunchedSummaryTeam(
            team_id="summary-fallback",
            leader_id="summary-leader-fallback",
            root_task_id=root_task_id,
            summary_task_id=summary_task_id,
            spec=spec,
        )

    factory = DefaultSummaryTeamFactory(
        summary_team_builder=builder,
        summary_team_stopper=lambda *args: None,
        # No recoverer supplied: recover() must fall back to a fresh launch.
    )
    launched = await factory.recover(
        execution_id="exec-1",
        organization_id="org-1",
        root_task_id="root-1",
        summary_task_id="summary-1",
        session_id="session-1",
    )

    assert launched.team_id == "summary-fallback"
    assert len(calls) == 1
    spec, organization_id, root_task_id, summary_task_id, session_id = calls[0]
    assert isinstance(spec, SummaryTeamSpec)
    assert organization_id == "org-1"
    assert root_task_id == "root-1"
    assert summary_task_id == "summary-1"
    assert session_id == "session-1"
