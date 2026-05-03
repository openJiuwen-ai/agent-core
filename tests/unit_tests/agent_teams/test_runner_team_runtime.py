# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import uuid
from types import SimpleNamespace
from typing import (
    Any,
    AsyncIterator,
)
from unittest.mock import (
    AsyncMock,
    call,
    patch,
)

import pytest

from openjiuwen.agent_teams.agent.blueprint import TeamAgentBlueprint
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.blueprint import (
    DeepAgentSpec,
    TeamAgentSpec,
)
from openjiuwen.agent_teams.schema.team import (
    TeamRole,
    TeamRuntimeContext,
    TeamSpec,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent_team import create_agent_team_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import InMemoryCheckpointer
from openjiuwen.core.single_agent import (
    AgentCard,
)
from openjiuwen.core.session.stream import OutputSchema


async def _activate_pool_entry(manager, team_name: str, session_id: str, agent) -> None:
    """Test helper: insert an ActiveTeam directly into the manager pool."""
    from openjiuwen.agent_teams.runtime.pool import ActiveTeam, RuntimeState

    await manager.pool.add(
        ActiveTeam(
            team_name=team_name,
            agent=agent,
            current_session_id=session_id,
            state=RuntimeState.RUNNING,
        )
    )


@pytest.fixture
def isolated_checkpointer():
    original = CheckpointerFactory.get_checkpointer()
    checkpointer = InMemoryCheckpointer()
    CheckpointerFactory.set_default_checkpointer(checkpointer)
    try:
        yield checkpointer
    finally:
        CheckpointerFactory.set_default_checkpointer(original)


@pytest.fixture
def stateful_team_db():
    """Stateful fake TeamDatabase shared via the ``get_shared_db`` patch.

    The dispatch path now queries ``db.team.team_exists`` for the
    authoritative ``team_in_db`` signal. Tests using ``FakeTeamAgent``
    bypass production's ``BuildTeamTool`` (which is what writes the
    ``team_info`` row in round 1), so they must mark the team as
    persisted by hand — call ``await fake.team.create_team(...)`` after
    round 1 to flip ``team_exists`` for the next round.
    """
    created: set[str] = set()

    async def _create_team(team_name, **_kwargs):
        created.add(team_name)
        return True

    async def _team_exists(team_name):
        return team_name in created

    fake = AsyncMock()
    fake.initialize = AsyncMock()
    fake.drop_session_tables_by_id = AsyncMock(return_value=[])
    fake.team = AsyncMock()
    fake.team.create_team = AsyncMock(side_effect=_create_team)
    fake.team.team_exists = AsyncMock(side_effect=_team_exists)
    fake.team.delete_team = AsyncMock(return_value=True)

    with patch(
        "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
        return_value=fake,
    ):
        yield fake


class FakeTeamAgent:
    def __init__(self, team_name: str, *, stream_label: str):
        self.team_name = team_name
        self.stream_label = stream_label
        self.resume_calls: list[str] = []
        self.pause_calls = 0
        self.cancel_calls = 0
        self.stop_calls = 0
        self.recover_calls = 0
        self.interactions: list[str] = []
        self.invoke_calls = 0
        self.stream_calls = 0

    async def invoke(self, inputs: Any, session=None) -> Any:
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

        self.invoke_calls += 1
        write_team_namespace(
            session,
            self.team_name,
            {
                "spec": {"team_name": self.team_name},
                "context": {
                    "role": "leader",
                    "member_name": "leader",
                    "persona": "leader",
                    "team_spec": {
                        "team_name": self.team_name,
                        "display_name": self.team_name,
                        "leader_member_name": "leader",
                    },
                    "messager_config": None,
                    "db_config": None,
                },
            },
        )
        return {"team_name": self.team_name, "session_id": session.get_session_id()}

    async def stream(self, inputs: Any, session=None) -> AsyncIterator[Any]:
        self.stream_calls += 1
        await self.invoke(inputs, session=session)
        yield OutputSchema(
            type="message",
            index=1,
            payload={"event_type": self.stream_label, "session_id": session.get_session_id()},
        )

    async def resume_for_new_session(self, session) -> None:
        self.resume_calls.append(session.get_session_id())

    async def recover_for_existing_session(self, session) -> None:
        self.resume_calls.append(session.get_session_id())
        self.stop_calls += 1

    async def recover_team(self) -> list[str]:
        self.recover_calls += 1
        return []

    async def _pause_coordination(self) -> None:
        self.pause_calls += 1

    async def pause_coordination(self) -> None:
        await self._pause_coordination()

    async def interact(self, message: str) -> None:
        self.interactions.append(message)

    async def cancel_agent(self) -> None:
        self.cancel_calls += 1

    async def _stop_coordination(self) -> None:
        self.stop_calls += 1

    async def stop_coordination(self) -> None:
        await self._stop_coordination()


@pytest.mark.asyncio
async def test_runner_run_agent_team_streaming_accepts_spec_and_emits_runtime_ready(isolated_checkpointer):
    await Runner.start()
    session_id = f"team_spec_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="spec_team", agents={})
    agent = FakeTeamAgent("spec_team", stream_label="team.chunk")

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "hello"},
                session=session_id,
            )
        ]

    assert len(chunks) == 2
    assert chunks[0].payload["event_type"] == "team.runtime_ready"
    assert chunks[0].payload["activation_kind"] == "create"
    assert chunks[0].payload["team_name"] == "spec_team"
    assert chunks[0].payload["session_id"] == session_id
    assert chunks[1].payload["event_type"] == "team.chunk"

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_team_runtime_manager_resumes_new_session_and_recovers_history(
    isolated_checkpointer, stateful_team_db,
):
    await Runner.start()
    session_one = f"resume_{uuid.uuid4().hex}"
    session_two = f"resume_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="persistent_team", agents={})
    active_agent = FakeTeamAgent("persistent_team", stream_label="active.chunk")

    async def _resume(agent, session):
        await agent.resume_for_new_session(session)
        return agent

    async def _recover_existing(agent, session):
        await agent.recover_for_existing_session(session)
        return agent

    with (
        patch.object(TeamAgentSpec, "build", return_value=active_agent),
        patch(
            "openjiuwen.agent_teams.runtime.manager.resume_persistent_team",
            side_effect=_resume,
        ),
        patch(
            "openjiuwen.agent_teams.runtime.manager.recover_for_existing_session",
            side_effect=_recover_existing,
        ),
    ):
        first_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "first"},
                session=session_one,
            )
        ]
        # Stand in for BuildTeamTool persisting the team_info row during
        # round 1; subsequent dispatch reads team_exists from the DB.
        await stateful_team_db.team.create_team(
            team_name="persistent_team",
            display_name="persistent_team",
            leader_member_name="leader",
        )
        second_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "second"},
                session=session_two,
            )
        ]
        third_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "third"},
                session=session_one,
            )
        ]

    assert first_chunks[0].payload["activation_kind"] == "create"
    # session_two has no bucket yet for the team; pool already holds the
    # team from round one, so this is the warm "new team in session" path.
    assert second_chunks[0].payload["activation_kind"] == "new_team_in_session_warm"
    assert active_agent.resume_calls == [session_two, session_one]
    # round 3 reuses session_one which already carries the team bucket.
    assert third_chunks[0].payload["activation_kind"] == "warm_recover"
    assert active_agent.stop_calls >= 1

    await Runner.stop()
    await isolated_checkpointer.release(session_one)
    await isolated_checkpointer.release(session_two)


@pytest.mark.asyncio
async def test_runner_same_session_streaming_short_circuits_and_skips_second_stream(isolated_checkpointer):
    session_id = f"same_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="same_team", agents={})
    agent = FakeTeamAgent("same_team", stream_label="active.chunk")
    await Runner.start()

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        first_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "first"},
                session=session_id,
            )
        ]
        second_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "same-session"},
                session=session_id,
            )
        ]

    assert first_chunks[0].payload["activation_kind"] == "create"
    # Same (team, session) running -> dispatch rejects with reject_running.
    assert second_chunks == []
    assert agent.stream_calls == 1
    assert agent.invoke_calls == 1
    assert agent.resume_calls == []

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_same_session_after_pause_resumes_paused_runtime(
    isolated_checkpointer, stateful_team_db,
):
    session_id = f"paused_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="paused_team", agents={})
    agent = FakeTeamAgent("paused_team", stream_label="team.chunk")
    await Runner.start()

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        first_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "first"},
                session=session_id,
            )
        ]
        await stateful_team_db.team.create_team(
            team_name="paused_team",
            display_name="paused_team",
            leader_member_name="leader",
        )
        assert await Runner.pause_agent_team(team_name="paused_team", session_id=session_id) is True

        second_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "resume paused"},
                session=session_id,
            )
        ]

    assert first_chunks[0].payload["activation_kind"] == "create"
    assert second_chunks[0].payload["activation_kind"] == "resume_from_pause"
    assert second_chunks[1].payload["event_type"] == "team.chunk"
    assert agent.pause_calls == 1
    assert agent.stream_calls == 2
    assert agent.invoke_calls == 2

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_paused_same_session_resume_uses_same_prepared_session(
    isolated_checkpointer, stateful_team_db,
):
    session_id = f"paused_same_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="paused_same_team", agents={})
    agent = FakeTeamAgent("paused_same_team", stream_label="team.chunk")
    await Runner.start()

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        initial_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "first"},
                session=session_id,
            )
        ]
        assert initial_chunks[0].payload["activation_kind"] == "create"
        await stateful_team_db.team.create_team(
            team_name="paused_same_team",
            display_name="paused_same_team",
            leader_member_name="leader",
        )
        assert await Runner.pause_agent_team(team_name="paused_same_team", session_id=session_id) is True

        resumed_result = await Runner.run_agent_team(
            agent_team=spec,
            inputs={"query": "resume invoke"},
            session=session_id,
        )

    assert resumed_result["session_id"] == session_id
    assert agent.invoke_calls == 2
    assert agent.pause_calls == 1

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_interact_pause_and_delete_agent_team_route_through_team_runtime_manager(isolated_checkpointer):
    from openjiuwen.agent_teams.runtime.manager import TeamSessionMetadata
    from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

    await Runner.start()
    session_id = f"delete_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="delete_team", agents={})
    agent = FakeTeamAgent("delete_team", stream_label="team.chunk")

    db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    metadata = TeamSessionMetadata(team_name="delete_team", db_config=db_config)

    fake_db = AsyncMock()
    fake_db.initialize = AsyncMock()
    fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])
    fake_db.team = AsyncMock()
    fake_db.team.team_exists = AsyncMock(return_value=False)
    fake_db.team.delete_team = AsyncMock(return_value=True)

    with (
        patch.object(TeamAgentSpec, "build", return_value=agent),
        patch("openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_metadata", return_value=metadata), \
         patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db),
    ):
        async for _ in Runner.run_agent_team_streaming(
            agent_team=spec,
            inputs={"query": "hello"},
            session=session_id,
        ):
            pass

        # The stream has ended, so the InteractGate is closed: late
        # interact_team calls must be rejected with gate_closed rather
        # than reaching the agent.
        post_stream_result = await Runner.interact_agent_team(
            "follow-up",
            team_name="delete_team",
            session_id=session_id,
        )
        assert post_stream_result.ok is False
        assert post_stream_result.reason == "gate_closed"
        assert agent.interactions == []

        assert await Runner.pause_agent_team(team_name="delete_team", session_id=session_id) is True
        assert agent.pause_calls == 1

        # Static precondition: paused runtime still occupies the pool, so
        # the team must be stopped before delete is allowed.
        assert await Runner.stop_agent_team(team_name="delete_team", session_id=session_id) is True

        assert await Runner.delete_agent_team(team_name="delete_team", session_ids=[session_id]) is True
        # ``initialize`` is also called by the dispatch DB-existence probe
        # in ``_inspect_session``, so assert it ran at least once instead of
        # exactly once.
        fake_db.initialize.assert_awaited()
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        fake_db.team.delete_team.assert_awaited_once_with("delete_team")

    assert await isolated_checkpointer.session_exists(session_id) is False

    await Runner.stop()


@pytest.mark.asyncio
async def test_team_agent_cancelled_round_does_not_restart_follow_up():
    # The cancel-skip logic lives on StreamController (where the round
    # actually runs), so drive it directly instead of bypassing TeamAgent
    # construction with private attribute injection.
    from openjiuwen.agent_teams.agent.stream_controller import StreamController

    async def _noop(*_a, **_kw):
        return None

    from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
    from openjiuwen.agent_teams.agent.state import TeamAgentState

    fake_deep_agent = SimpleNamespace(deep_config=None)
    fake_blueprint = SimpleNamespace(member_name="leader")
    sc = StreamController(
        blueprint_getter=lambda: fake_blueprint,
        state=TeamAgentState(),
        resources=PrivateAgentResources(deep_agent=fake_deep_agent),
        status_updater=_noop,
        execution_updater=_noop,
    )
    sc.stream_queue = object()
    sc.pending_inputs = ["follow-up"]
    sc._execute_round = AsyncMock(side_effect=asyncio.CancelledError())
    sc.start_round = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await sc._run_one_round("cancelled")

    assert sc.start_round.await_count == 0
    assert sc.pending_inputs == ["follow-up"]
    assert sc.agent_task is None


@pytest.mark.asyncio
async def test_team_agent_resume_for_new_session_rebinds_only_live_teammates():
    leader_card = AgentCard(id=f"leader_{uuid.uuid4().hex}", name="leader", description="leader")
    agent = TeamAgent(card=leader_card)
    team_spec = TeamSpec(team_name="persistent_team", display_name="persistent_team")
    ctx = TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader", team_spec=team_spec)
    agent._configurator._blueprint = TeamAgentBlueprint(
        card=leader_card,
        spec=TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="persistent_team"),
        ctx=ctx,
        role_policy="",
        language="en",
    )

    fake_db = AsyncMock()
    fake_backend = SimpleNamespace(
        team_name="persistent_team",
        db=fake_db,
        list_members=AsyncMock(
            return_value=[
                SimpleNamespace(member_name="leader", status="ready"),
                SimpleNamespace(member_name="worker_busy", status="busy"),
                SimpleNamespace(member_name="worker_ready", status="ready"),
                SimpleNamespace(member_name="worker_idle_no_handle", status="ready"),
                SimpleNamespace(member_name="worker_shutdown", status="shut_down"),
            ]
        ),
    )
    agent._configurator.team_backend = fake_backend
    agent._spawn_manager.spawned_handles = {
        "worker_busy": object(),
        "worker_ready": object(),
    }
    agent._spawn_manager.cleanup_teammate = AsyncMock()
    agent._spawn_manager.restart_teammate = AsyncMock(return_value=True)

    new_session = create_agent_team_session(
        session_id=f"session_{uuid.uuid4().hex}",
        team_id="persistent_team",
    )

    await agent.resume_for_new_session(new_session)

    assert agent._session_manager.session_id == new_session.get_session_id()
    fake_db.create_cur_session_tables.assert_awaited_once()
    assert agent._spawn_manager.cleanup_teammate.await_args_list == [
        call("worker_busy"),
        call("worker_ready"),
    ]
    assert agent._spawn_manager.restart_teammate.await_args_list == [
        call("worker_busy"),
        call("worker_ready"),
    ]
    assert fake_db.member.update_member_status.await_args_list == [
        call("worker_busy", "persistent_team", "error"),
        call("worker_busy", "persistent_team", "restarting"),
        call("worker_ready", "persistent_team", "error"),
        call("worker_ready", "persistent_team", "restarting"),
    ]


@pytest.mark.asyncio
async def test_team_agent_recover_for_existing_session_rebinds_live_teammates():
    leader_card = AgentCard(id=f"leader_{uuid.uuid4().hex}", name="leader", description="leader")
    agent = TeamAgent(card=leader_card)
    team_spec = TeamSpec(team_name="persistent_team", display_name="persistent_team")
    ctx = TeamRuntimeContext(role=TeamRole.LEADER, member_name="leader", team_spec=team_spec)
    agent._configurator._blueprint = TeamAgentBlueprint(
        card=leader_card,
        spec=TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="persistent_team"),
        ctx=ctx,
        role_policy="",
        language="en",
    )

    fake_db = AsyncMock()
    fake_backend = SimpleNamespace(
        team_name="persistent_team",
        db=fake_db,
        list_members=AsyncMock(
            return_value=[
                SimpleNamespace(member_name="leader", status="ready"),
                SimpleNamespace(member_name="worker_busy", status="busy"),
                SimpleNamespace(member_name="worker_ready", status="ready"),
                SimpleNamespace(member_name="worker_idle_no_handle", status="ready"),
                SimpleNamespace(member_name="worker_shutdown", status="shut_down"),
            ]
        ),
    )
    agent._configurator.team_backend = fake_backend
    agent._spawn_manager.spawned_handles = {
        "worker_busy": object(),
        "worker_ready": object(),
    }
    agent._coordination.stop = AsyncMock()
    agent._spawn_manager.restart_teammate = AsyncMock(return_value=True)

    existing_session = create_agent_team_session(
        session_id=f"recover_{uuid.uuid4().hex}",
        team_id="persistent_team",
    )

    await agent.recover_for_existing_session(existing_session)

    assert agent._session_manager.session_id == existing_session.get_session_id()
    agent._coordination.stop.assert_awaited_once()
    fake_db.create_cur_session_tables.assert_awaited_once()
    assert agent._spawn_manager.restart_teammate.await_args_list == [
        call("worker_busy"),
        call("worker_ready"),
    ]
    assert fake_db.member.update_member_status.await_args_list == [
        call("worker_busy", "persistent_team", "error"),
        call("worker_busy", "persistent_team", "restarting"),
        call("worker_ready", "persistent_team", "error"),
        call("worker_ready", "persistent_team", "restarting"),
    ]


def test_team_agent_recover_from_session_restores_session_id():
    from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

    session_id = f"recover_state_{uuid.uuid4().hex}"
    session = create_agent_team_session(session_id=session_id, team_id="persistent_team")
    write_team_namespace(
        session,
        "persistent_team",
        {
            "spec": {
                "team_name": "persistent_team",
                "agents": {
                    "leader": {},
                },
            },
            "context": {
                "role": "leader",
                "member_name": "leader",
                "persona": "leader",
                "team_spec": {
                    "team_name": "persistent_team",
                    "display_name": "persistent_team",
                    "leader_member_name": "leader",
                },
                "messager_config": {},
                "db_config": {},
            },
        },
    )

    agent = TeamAgent.recover_from_session(session, "persistent_team")

    assert agent._session_manager.session_id == session_id


@pytest.mark.asyncio
async def test_team_session_forwards_child_stream_output_with_source_tags(isolated_checkpointer):
    session_id = f"stream_{uuid.uuid4().hex}"
    team_session = create_agent_team_session(session_id=session_id, team_id="stream_team")
    await team_session.pre_run(inputs={"query": "hello"})

    child_session = team_session.create_agent_session(agent_id="worker_a")
    await child_session.pre_run(inputs={"payload": "child"})
    await child_session.write_stream({"kind": "agent"})
    await child_session.post_run()

    await team_session.write_stream({"kind": "team"})
    await team_session.post_run()

    chunks = []
    async for chunk in team_session.stream_iterator():
        chunks.append(chunk)

    assert any(
        getattr(chunk, "payload", {}).get("source_agent_id") == "worker_a"
        and getattr(chunk, "payload", {}).get("source_team_id") == "stream_team"
        for chunk in chunks
    )
    assert any(
        getattr(chunk, "payload", {}).get("kind") == "team"
        and getattr(chunk, "payload", {}).get("source_team_id") == "stream_team"
        for chunk in chunks
    )

    await isolated_checkpointer.release(session_id)


# ---------------------------------------------------------------------------
# TeamRuntimeManager release_session tests
# ---------------------------------------------------------------------------


class TestTeamRuntimeManagerReleaseSession:
    """Test TeamRuntimeManager.release_session."""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_release_session_drops_tables_for_inactive_session(self, isolated_checkpointer):
        """release_session should drop dynamic tables for a non-active session."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager, TeamSessionMetadata
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        manager = TeamRuntimeManager()
        session_id = f"release_inactive_{uuid.uuid4().hex}"

        # Create a session with db_config in context
        team_session = create_agent_team_session(session_id=session_id, team_id="release_team")
        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        await team_session.pre_run()
        team_session.update_state({
            "team_name": "release_team",
            "context": {
                "role": "leader",
                "member_name": "leader",
                "db_config": db_config.model_dump(),
            },
        })
        await team_session.post_run()

        # Mock get_shared_db and drop_session_tables_by_id
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        metadata = TeamSessionMetadata(team_name="release_team", db_config=db_config)
        with patch("openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_metadata", return_value=metadata), \
             patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            await manager.release_session(session_id)

        fake_db.initialize.assert_awaited_once()
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)

        # No team should be active for the released session.
        assert await manager.pool.teams_for_session(session_id) == []

        await isolated_checkpointer.release(session_id)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_release_session_rejects_when_team_active(self):
        """release_session must refuse while a team is active on that session."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
        from openjiuwen.core.common.exception.errors import ValidationError

        manager = TeamRuntimeManager()
        session_id = f"release_active_{uuid.uuid4().hex}"
        team_name = "active_release_team"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        with pytest.raises(ValidationError, match="busy"):
            await manager.release_session(session_id)

        # The runtime keeps holding the pool entry until stop_team is called.
        entry = await manager.pool.get(team_name)
        assert entry is not None
        assert entry.current_session_id == session_id
        assert entry.agent is fake_agent
        assert fake_agent.stop_calls == 0

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_release_session_force_stops_active_teams_before_cleanup(self):
        """release_session(force=True) tears down active teams before dropping tables."""
        from openjiuwen.agent_teams.runtime.manager import (
            TeamRuntimeManager,
            TeamSessionMetadata,
        )
        from openjiuwen.agent_teams.tools.database import (
            DatabaseConfig,
            DatabaseType,
        )

        manager = TeamRuntimeManager()
        session_id = f"release_force_{uuid.uuid4().hex}"
        team_name = "force_release_team"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        metadata = TeamSessionMetadata(team_name=team_name, db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])

        with patch(
            "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_metadata",
            return_value=metadata,
        ), patch(
            "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
            return_value=fake_db,
        ):
            await manager.release_session(session_id, force=True)

        assert fake_agent.stop_calls == 1
        assert await manager.pool.has_active(team_name) is False
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_release_session_empty_session_id_returns_early(self):
        """release_session should return early for empty session_id."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        # Should not raise or do anything
        await manager.release_session("")
        await manager.release_session(None)

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_release_session_raises_on_missing_context(self, isolated_checkpointer):
        """release_session should raise RuntimeError if context is missing."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        session_id = f"release_no_context_{uuid.uuid4().hex}"

        # Create a session with team_name but no context
        team_session = create_agent_team_session(session_id=session_id, team_id="no_context_team")
        await team_session.pre_run()
        team_session.update_state({"team_name": "no_context_team"})  # No context
        await team_session.post_run()

        with pytest.raises(RuntimeError, match="Cannot resolve team session metadata"):
            await manager.release_session(session_id)

        await isolated_checkpointer.release(session_id)


class TestTeamRuntimeManagerInteract:
    """Test TeamRuntimeManager.interact payload dispatch and gate behaviour."""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_interact_god_view_routes_to_deliver_input(self):
        """GodViewMessage must invoke ``agent.deliver_input`` and report success."""
        from openjiuwen.agent_teams.interaction.payload import GodViewMessage
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "god_team"
        session_id = "s-god"

        delivered: list[str] = []

        class _Agent:
            team_backend = None

            async def deliver_input(self, content):
                delivered.append(content)

        await _activate_pool_entry(manager, team_name, session_id, _Agent())

        result = await manager.interact(
            GodViewMessage(body="hi"),
            team_name=team_name,
            session_id=session_id,
        )
        assert result.ok is True
        assert delivered == ["hi"]

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_interact_returns_not_active_when_no_pool_entry(self):
        from openjiuwen.agent_teams.interaction.payload import GodViewMessage
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        result = await manager.interact(
            GodViewMessage(body="x"),
            team_name="missing",
            session_id="missing",
        )
        assert result.ok is False
        assert result.reason == "not_active"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_interact_returns_gate_closed_after_close_and_drain(self):
        from openjiuwen.agent_teams.interaction.payload import GodViewMessage
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "drained"
        session_id = "s1"

        class _Agent:
            team_backend = None

            async def deliver_input(self, content):
                pass

        await _activate_pool_entry(manager, team_name, session_id, _Agent())
        entry = await manager.pool.get(team_name)
        assert entry is not None
        await entry.interact_gate.close_and_drain()

        result = await manager.interact(
            GodViewMessage(body="x"),
            team_name=team_name,
            session_id=session_id,
        )
        assert result.ok is False
        assert result.reason == "gate_closed"

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_interact_string_input_via_runner_treated_as_god_view(self):
        """Runner.interact_agent_team accepts a bare str as god-view sugar."""
        await Runner.start()
        try:
            session_id = f"runner_god_{uuid.uuid4().hex}"
            spec = TeamAgentSpec.model_construct(team_name="god_runner_team", agents={})
            agent = FakeTeamAgent("god_runner_team", stream_label="team.chunk")

            with patch.object(TeamAgentSpec, "build", return_value=agent):
                async for _ in Runner.run_agent_team_streaming(
                    agent_team=spec,
                    inputs={"query": "first"},
                    session=session_id,
                ):
                    pass

                # Stream ended -> gate closed; god-view interact should be
                # rejected via DeliverResult, not raise.
                result = await Runner.interact_agent_team(
                    "follow",
                    team_name="god_runner_team",
                    session_id=session_id,
                )
                assert result.ok is False
                assert result.reason == "gate_closed"
        finally:
            await Runner.stop()


class TestTeamRuntimeManagerStopTeam:
    """Test TeamRuntimeManager.stop_team."""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_stop_team_returns_true_and_clears_pool_entry(self):
        """stop_team must clear active pointers and remove the pool entry."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "stop_team"
        session_id = f"stop_{uuid.uuid4().hex}"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)
        assert await manager.pool.has_active(team_name) is True

        result = await manager.stop_team(team_name=team_name, session_id=session_id)
        assert result is True
        assert fake_agent.stop_calls == 1
        assert await manager.pool.has_active(team_name) is False
        assert await manager.pool.list_team_names() == []

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_stop_team_returns_false_when_team_mismatch(self):
        """stop_team must refuse a mismatched (team, session) pair."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "active_team"
        session_id = "s1"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        result = await manager.stop_team(team_name="other_team", session_id=session_id)
        assert result is False
        assert fake_agent.stop_calls == 0
        assert await manager.pool.has_active(team_name) is True

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_stop_team_then_delete_team_succeeds(self):
        """After stop_team, delete_team should be allowed (busy guard cleared)."""
        from openjiuwen.agent_teams.runtime.manager import (
            TeamRuntimeManager,
            TeamSessionMetadata,
        )
        from openjiuwen.agent_teams.tools.database import (
            DatabaseConfig,
            DatabaseType,
        )

        manager = TeamRuntimeManager()
        team_name = "stop_then_delete"
        session_id = f"std_{uuid.uuid4().hex}"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)
        await manager.stop_team(team_name=team_name, session_id=session_id)

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        metadata = TeamSessionMetadata(team_name=team_name, db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)
        fake_checkpointer = AsyncMock()
        fake_checkpointer.release = AsyncMock()

        with patch(
            "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_metadata",
            return_value=metadata,
        ), patch(
            "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
            return_value=fake_db,
        ), patch(
            "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
            return_value=fake_checkpointer,
        ):
            result = await manager.delete_team(team_name, [session_id])

        assert result is True
        fake_db.team.delete_team.assert_awaited_once_with(team_name)


class TestTeamRuntimeManagerDeleteTeam:
    """Test TeamRuntimeManager.delete_team db_config resolution."""

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_delete_team_fetches_db_config_from_session(self, isolated_checkpointer):
        """delete_team should fetch db_config from session state."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager, TeamSessionMetadata
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        manager = TeamRuntimeManager()
        session_id = f"delete_team_config_{uuid.uuid4().hex}"
        team_name = "delete_config_team"

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string="delete.db")

        # Mock get_shared_db
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)

        metadata = TeamSessionMetadata(team_name=team_name, db_config=db_config)
        with patch("openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_metadata", return_value=metadata), \
             patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            result = await manager.delete_team(team_name, [session_id])

        assert result is True
        fake_db.initialize.assert_awaited_once()
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        fake_db.team.delete_team.assert_awaited_once_with(team_name)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_rejects_when_team_active(self):
        """delete_team must refuse while the target team is active in the pool."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
        from openjiuwen.core.common.exception.errors import ValidationError

        manager = TeamRuntimeManager()
        team_name = "delete_active_team"
        session_id = f"delete_active_{uuid.uuid4().hex}"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        with pytest.raises(ValidationError, match="busy"):
            await manager.delete_team(team_name, [session_id])

        # Pool entry must remain after the rejection so the caller can stop it.
        entry = await manager.pool.get(team_name)
        assert entry is not None
        assert entry.agent is fake_agent

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_force_stops_active_runtime_before_cleanup(self):
        """delete_team(force=True) stops the active runtime in-line."""
        from openjiuwen.agent_teams.runtime.manager import (
            TeamRuntimeManager,
            TeamSessionMetadata,
        )
        from openjiuwen.agent_teams.tools.database import (
            DatabaseConfig,
            DatabaseType,
        )

        manager = TeamRuntimeManager()
        team_name = "force_delete_team"
        session_id = f"force_del_{uuid.uuid4().hex}"

        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        metadata = TeamSessionMetadata(team_name=team_name, db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)
        fake_checkpointer = AsyncMock()
        fake_checkpointer.release = AsyncMock()

        with patch(
            "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_metadata",
            return_value=metadata,
        ), patch(
            "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
            return_value=fake_db,
        ), patch(
            "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
            return_value=fake_checkpointer,
        ):
            ok = await manager.delete_team(team_name, [session_id], force=True)

        assert ok is True
        assert fake_agent.stop_calls == 1
        assert await manager.pool.has_active(team_name) is False
        fake_db.team.delete_team.assert_awaited_once_with(team_name)


class TestRunnerReleaseAutoDispatch:
    """Test Runner.release() auto-dispatch between team and non-team sessions."""

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_runner_release_non_team_session_simple_checkpoint_release(self, isolated_checkpointer):
        """Runner.release should only release checkpoint for non-team session."""
        await Runner.start()
        session_id = f"non_team_{uuid.uuid4().hex}"

        # resolve_team_session_metadata should return None for non-team session
        with patch("openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_metadata", return_value=None):
            await Runner.release(session_id)

        # Checkpoint should NOT exist (simple release was called)
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_runner_release_team_session_cleans_dynamic_tables(self, isolated_checkpointer):
        """Runner.release should clean dynamic tables for team session."""
        from openjiuwen.agent_teams.runtime.manager import TeamSessionMetadata
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        await Runner.start()
        session_id = f"team_auto_{uuid.uuid4().hex}"

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        metadata = TeamSessionMetadata(team_name="auto_team", db_config=db_config)

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        # Mock resolve_team_session_metadata to return team metadata
        with patch("openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_metadata", return_value=metadata), \
             patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            await Runner.release(session_id)

        # Should have called drop_session_tables_by_id (team cleanup)
        fake_db.initialize.assert_awaited_once()
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)

        # Checkpoint should be released
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_runner_release_team_session_db_config_missing_does_not_release_checkpoint(self, isolated_checkpointer):
        """Runner.release should NOT release checkpoint if team session db_config is missing."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        await Runner.start()
        session_id = f"team_missing_config_{uuid.uuid4().hex}"

        # Create a team session with team_name but missing db_config in context
        team_session = create_agent_team_session(session_id=session_id, team_id="missing_config_team")
        await team_session.pre_run()
        team_session.update_state({
            "team_name": "missing_config_team",
            "context": {
                "role": "leader",
                "member_name": "leader",
                # db_config is missing
            },
        })
        await team_session.post_run()

        # resolve_team_session_metadata should raise RuntimeError for missing db_config
        with patch.object(TeamRuntimeManager, "resolve_team_session_metadata", side_effect=RuntimeError("db_config is missing")):
            with pytest.raises(RuntimeError, match="db_config is missing"):
                await Runner.release(session_id)

        # Checkpoint should NOT be released
        assert await isolated_checkpointer.session_exists(session_id) is True

        await isolated_checkpointer.release(session_id)
        await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_runner_release_team_session_context_parse_failure_does_not_release_checkpoint(self, isolated_checkpointer):
        """Runner.release should NOT release checkpoint if team session context parsing fails."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        await Runner.start()
        session_id = f"team_bad_context_{uuid.uuid4().hex}"

        # Create a team session with team_name but malformed context
        team_session = create_agent_team_session(session_id=session_id, team_id="bad_context_team")
        await team_session.pre_run()
        team_session.update_state({
            "team_name": "bad_context_team",
            "context": {"invalid": "data"},  # malformed context
        })
        await team_session.post_run()

        # resolve_team_session_metadata should raise RuntimeError for parsing failure
        with patch.object(TeamRuntimeManager, "resolve_team_session_metadata", side_effect=RuntimeError("context parsing failed")):
            with pytest.raises(RuntimeError, match="context parsing failed"):
                await Runner.release(session_id)

        # Checkpoint should NOT be released
        assert await isolated_checkpointer.session_exists(session_id) is True

        await isolated_checkpointer.release(session_id)
        await Runner.stop()
