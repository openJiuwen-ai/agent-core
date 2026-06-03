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
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent import (
    AgentCard,
)


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
    def __init__(self, team_name: str, *, stream_label: str, spec: TeamAgentSpec | None = None):
        self.team_name = team_name
        self.stream_label = stream_label
        self.spec = spec
        self.resume_calls: list[str] = []
        self.pause_calls = 0
        self.cancel_calls = 0
        self.stop_calls = 0
        self.recover_calls = 0
        self.interactions: list[str] = []
        self.invoke_calls = 0
        self.stream_calls = 0

    def persist_session_manifest(self, session) -> None:
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

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
                    "db_config": {
                        "db_type": "sqlite",
                        "connection_string": ":memory:",
                    },
                },
            },
        )

    async def invoke(self, inputs: Any, session=None) -> Any:
        self.invoke_calls += 1
        self.persist_session_manifest(session)
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
@pytest.mark.level0
async def test_runner_run_agent_team_streaming_flushes_team_manifest_before_runtime_ready(isolated_checkpointer):
    from openjiuwen.agent_teams.runtime.metadata import read_team_namespace

    await Runner.start()
    session_id = f"team_manifest_{uuid.uuid4().hex}"
    team_name = "manifest_team"
    spec = TeamAgentSpec.model_construct(team_name=team_name, agents={})
    agent = FakeTeamAgent(team_name, stream_label="team.chunk")

    stream = Runner.run_agent_team_streaming(
        agent_team=spec,
        inputs={"query": "hello"},
        session=session_id,
    )
    try:
        with patch.object(TeamAgentSpec, "build", return_value=agent):
            ready_chunk = await stream.__anext__()

        assert ready_chunk.payload["event_type"] == "team.runtime_ready"

        restored = create_agent_team_session(session_id=session_id)
        await restored.pre_run()
        bucket = read_team_namespace(restored, team_name)

        assert bucket is not None
        assert bucket["spec"]["team_name"] == team_name
        assert bucket["context"]["db_config"]["connection_string"] == ":memory:"
    finally:
        await stream.aclose()
        await Runner.stop()
        await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_runner_run_agent_team_streaming_without_stream_logger(isolated_checkpointer, tmp_path):
    """Omitting ``stream_logger`` leaves no diagnostic file behind."""
    await Runner.start()
    session_id = f"team_spec_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="spec_team", agents={})
    agent = FakeTeamAgent("spec_team", stream_label="team.chunk")
    target = tmp_path / "stream.log"

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
    assert not target.exists()

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_runner_run_agent_team_streaming_with_stream_logger_writes_file(isolated_checkpointer, tmp_path):
    """Passing a ``TeamStreamLogger`` writes aggregated records to its file
    without perturbing the streamed chunks."""
    from openjiuwen.agent_teams.monitor import TeamStreamLogger

    await Runner.start()
    session_id = f"team_spec_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="spec_team", agents={})
    agent = FakeTeamAgent("spec_team", stream_label="team.chunk")
    log_path = tmp_path / "stream.log"
    stream_logger = TeamStreamLogger(log_path)

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "hello"},
                session=session_id,
                stream_logger=stream_logger,
            )
        ]

    # Stream is unperturbed by the observer.
    assert len(chunks) == 2
    assert chunks[0].payload["event_type"] == "team.runtime_ready"
    assert chunks[1].payload["event_type"] == "team.chunk"
    # File got the runtime_ready record at minimum; flush ran in finally.
    text = log_path.read_text(encoding="utf-8")
    assert "category=runtime_ready" in text
    assert "stream end" in text

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_team_runtime_manager_cold_recover_reinjects_runtime_spec():
    from openjiuwen.agent_teams.runtime.dispatch import RunActionKind
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

    session_id = f"cold_recover_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="cold_recover_team", agents={})
    spec.agent_customizer = lambda *_args, **_kwargs: None
    agent = FakeTeamAgent("cold_recover_team", stream_label="team.chunk")

    manager = TeamRuntimeManager()
    manager._pool.add = AsyncMock()

    with (
        patch.object(
            TeamRuntimeManager,
            "_inspect_session",
            AsyncMock(return_value=(True, True, None)),
        ),
        patch.object(
            TeamAgent,
            "recover_from_session",
            return_value=agent,
        ) as recover_mock,
    ):
        activation = await manager.activate(spec, session_id, {"query": "recover"})

    assert activation.action.kind is RunActionKind.COLD_RECOVER
    recover_mock.assert_called_once()
    args, kwargs = recover_mock.call_args
    # Call site: TeamAgent.recover_from_session(team_session, team_name, runtime_spec=spec)
    assert args[1] == "cold_recover_team"
    assert kwargs["runtime_spec"] is spec
    assert agent.recover_calls == 1


@pytest.mark.asyncio
async def test_team_runtime_manager_recreates_pending_session_bucket():
    from openjiuwen.agent_teams.runtime.dispatch import RunActionKind
    from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
    from openjiuwen.agent_teams.runtime.metadata import TEAM_DB_STATE_PENDING_CREATE

    session_id = f"pending_recreate_{uuid.uuid4().hex}"
    team_name = "pending_recreate_team"
    spec = TeamAgentSpec.model_construct(team_name=team_name, agents={})
    agent = FakeTeamAgent(team_name, stream_label="team.chunk")

    manager = TeamRuntimeManager()
    manager._pool.add = AsyncMock()

    with (
        patch.object(
            TeamRuntimeManager,
            "_inspect_session",
            AsyncMock(return_value=(True, False, TEAM_DB_STATE_PENDING_CREATE)),
        ),
        patch.object(TeamAgentSpec, "build", return_value=agent) as build_mock,
    ):
        activation = await manager.activate(spec, session_id, {"query": "create"})

    assert activation.action.kind is RunActionKind.CREATE
    build_mock.assert_called_once()
    manager._pool.add.assert_awaited_once()


@pytest.mark.asyncio
async def test_runner_session_switch_stops_and_rebuilds(
    isolated_checkpointer,
    stateful_team_db,
):
    """Switching sessions for the same team tears down the pool entry
    (stop_coordination + pool.remove) before redispatching cold.

    Replaces the old warm-session-switch path (WARM_RECOVER /
    NEW_TEAM_IN_SESSION_WARM) which reused the same TeamAgent instance
    across sessions and was prone to state bleed. The new contract:
    every cross-session activation starts from a freshly built or
    cold-recovered agent against an empty pool slot.
    """
    await Runner.start()
    session_one = f"resume_{uuid.uuid4().hex}"
    session_two = f"resume_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="persistent_team", agents={})
    active_agent = FakeTeamAgent("persistent_team", stream_label="active.chunk")

    with patch.object(TeamAgentSpec, "build", return_value=active_agent):
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

    assert first_chunks[0].payload["activation_kind"] == "create"
    # session_two has no bucket for the team yet (round 1 wrote into
    # session_one). The stale session_one pool entry is torn down before
    # dispatch, so the cold path picks NEW_TEAM_IN_SESSION (team_in_db
    # True via the manual create_team above; team_in_session False).
    assert second_chunks[0].payload["activation_kind"] == "new_team_in_session"
    assert active_agent.resume_calls == [session_two]
    # The pool teardown on session switch must have called stop_coordination
    # at least once on the round-one agent (session_one -> session_two).
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
    isolated_checkpointer,
    stateful_team_db,
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
    isolated_checkpointer,
    stateful_team_db,
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
    await Runner.start()
    session_id = f"delete_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="delete_team", agents={})
    agent = FakeTeamAgent("delete_team", stream_label="team.chunk")

    fake_db = AsyncMock()
    fake_db.initialize = AsyncMock()
    fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])
    fake_db.team = AsyncMock()
    fake_db.team.team_exists = AsyncMock(return_value=False)
    fake_db.team.delete_team = AsyncMock(return_value=True)

    with (
        patch.object(TeamAgentSpec, "build", return_value=agent),
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

    from unittest.mock import MagicMock

    from openjiuwen.agent_teams.agent.resources import PrivateAgentResources
    from openjiuwen.agent_teams.agent.state import TeamAgentState
    from openjiuwen.agent_teams.harness import TeamHarness, _MountedRails
    from openjiuwen.agent_teams.schema.team import TeamRole

    fake_deep_agent = SimpleNamespace(deep_config=None)
    fake_rails = _MountedRails(team_tool=MagicMock(), team_policy=MagicMock())
    fake_harness = TeamHarness(
        fake_deep_agent,
        fake_rails,
        role=TeamRole.LEADER,
        member_name="leader",
    )
    fake_blueprint = SimpleNamespace(member_name="leader")
    sc = StreamController(
        blueprint_getter=lambda: fake_blueprint,
        state=TeamAgentState(),
        resources=PrivateAgentResources(harness=fake_harness),
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

    assert agent.session_id == new_session.get_session_id()
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

    assert agent.session_id == existing_session.get_session_id()
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

    assert agent.session_id == session_id


def test_team_agent_recover_from_session_builds_leader_member_handle():
    """A cold-recovered leader gets its TeamMember handle via configure().

    Recovery rebuilds the leader through ``configure()`` -> ``_setup_agent``;
    the team row already exists in the DB, so the handle must be populated
    and able to track status -- not left ``None`` as it was while the
    leader handle depended on a never-firing lazy-init callback.
    """
    from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

    session_id = f"recover_handle_{uuid.uuid4().hex}"
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

    handle = agent._state.team_member
    assert handle is not None
    assert handle.member_name == "leader"


def test_team_agent_recover_from_session_reinjects_runtime_spec_customizer():
    """``runtime_spec.agent_customizer`` must replace the persisted spec's
    customizer slot.

    ``agent_customizer`` is ``Field(exclude=True)``, so the persisted bucket
    never carries it. Without the runtime override, every cold recover would
    silently disable platform adapter callbacks.
    """
    from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

    session_id = f"recover_customizer_{uuid.uuid4().hex}"
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

    customizer = lambda *_args, **_kwargs: None  # noqa: E731 — sentinel callable for identity check
    runtime_spec = TeamAgentSpec.model_construct(team_name="persistent_team", agents={})
    runtime_spec.agent_customizer = customizer

    captured: dict[str, Any] = {}

    def fake_configure(self, spec, context):
        captured["spec"] = spec
        captured["context"] = context
        return self

    with patch.object(TeamAgent, "configure", fake_configure):
        agent = TeamAgent.recover_from_session(
            session,
            "persistent_team",
            runtime_spec=runtime_spec,
        )

    assert captured["spec"].agent_customizer is customizer
    assert agent.session_id == session_id


def test_team_agent_recover_from_session_without_runtime_spec_keeps_customizer_none():
    """Without ``runtime_spec`` the recovered spec keeps the persisted (None)
    customizer slot.

    Guards against accidental shadowing — the override path must only fire
    when the caller explicitly supplies a runtime spec.
    """
    from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

    session_id = f"recover_no_runtime_{uuid.uuid4().hex}"
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

    captured: dict[str, Any] = {}

    def fake_configure(self, spec, context):
        captured["spec"] = spec
        return self

    with patch.object(TeamAgent, "configure", fake_configure):
        TeamAgent.recover_from_session(session, "persistent_team")

    assert captured["spec"].agent_customizer is None


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
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        manager = TeamRuntimeManager()
        session_id = f"release_inactive_{uuid.uuid4().hex}"

        team_session = create_agent_team_session(session_id=session_id)
        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        await team_session.pre_run()
        write_team_namespace(
            team_session,
            "release_team",
            {
                "spec": {"team_name": "release_team"},
                "context": {
                    "role": "leader",
                    "member_name": "leader",
                    "db_config": db_config.model_dump(),
                },
            },
        )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        with patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
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
            TeamSessionReleaseInfo,
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
        release_info = TeamSessionReleaseInfo(team_names=[team_name], db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                return_value=release_info,
            ),
            patch(
                "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
                return_value=fake_db,
            ),
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
        """release_session should raise RuntimeError if all team buckets miss context."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

        manager = TeamRuntimeManager()
        session_id = f"release_no_context_{uuid.uuid4().hex}"

        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        write_team_namespace(team_session, "no_context_team", {"spec": {"team_name": "no_context_team"}})
        await team_session.post_run()

        with pytest.raises(RuntimeError, match="Cannot resolve team session release info"):
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

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_interact_runner_binds_target_team_session_context(self):
        """Runner.interact_agent_team should bind the target session at the entrypoint."""
        from openjiuwen.agent_teams.context import get_session_id
        from openjiuwen.core.runner.team_runner import _global_runner

        await Runner.start()
        try:
            manager = _global_runner()._get_team_runtime_manager()
            team_name = "runner_context_team"
            session_id = f"runner_context_{uuid.uuid4().hex}"

            delivered: list[str] = []
            seen_context: list[str] = []

            class _Agent:
                team_backend = None

                async def deliver_input(self, content):
                    delivered.append(content)
                    seen_context.append(get_session_id())

            await _activate_pool_entry(manager, team_name, session_id, _Agent())

            result = await Runner.interact_agent_team(
                "follow",
                team_name=team_name,
                session_id=session_id,
            )

            assert result.ok is True
            assert delivered == ["follow"]
            assert seen_context == [session_id]
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
    async def test_get_monitor_returns_monitor_for_matching_entry(self):
        """get_monitor should create a monitor only for the exact team/session pair."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "monitor_team"
        session_id = "s1"
        fake_agent = FakeTeamAgent(team_name, stream_label="team.chunk")
        await _activate_pool_entry(manager, team_name, session_id, fake_agent)

        with patch("openjiuwen.agent_teams.runtime.manager.create_monitor", return_value="monitor") as mocked:
            assert await manager.get_monitor(team_name=team_name, session_id=session_id) == "monitor"
            mocked.assert_called_once_with(fake_agent, hide_dm=False)

        with patch("openjiuwen.agent_teams.runtime.manager.create_monitor", return_value="dm_hidden") as mocked:
            assert (
                await manager.get_monitor(team_name=team_name, session_id=session_id, hide_dm=True) == "dm_hidden"
            )
            mocked.assert_called_once_with(fake_agent, hide_dm=True)

        assert await manager.get_monitor(team_name=team_name, session_id="other") is None
        assert await manager.get_monitor(team_name="missing", session_id=session_id) is None

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_stop_team_then_delete_team_succeeds(self):
        """After stop_team, delete_team should be allowed (busy guard cleared)."""
        from openjiuwen.agent_teams.runtime.manager import (
            TeamRuntimeManager,
            TeamSessionReleaseInfo,
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
        release_info = TeamSessionReleaseInfo(team_names=[team_name], db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)
        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=True)
        fake_checkpointer.release = AsyncMock()

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                return_value=release_info,
            ),
            patch(
                "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
                return_value=fake_db,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
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
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        manager = TeamRuntimeManager()
        session_id = f"delete_team_config_{uuid.uuid4().hex}"
        team_name = "delete_config_team"

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string="delete.db")
        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        write_team_namespace(
            team_session,
            team_name,
            {
                "spec": {"team_name": team_name},
                "context": {
                    "role": "leader",
                    "member_name": "leader",
                    "db_config": db_config.model_dump(),
                },
            },
        )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)

        with patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            result = await manager.delete_team(team_name, [session_id])

        assert result is True
        fake_db.initialize.assert_awaited_once()
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        fake_db.team.delete_team.assert_awaited_once_with(team_name)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_uses_first_parseable_release_info(self):
        """delete_team should skip bad sessions and use the first parseable release info."""
        from openjiuwen.agent_teams.runtime.manager import (
            TeamRuntimeManager,
            TeamSessionReleaseInfo,
        )
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        manager = TeamRuntimeManager()
        team_name = "delete_parseable_team"
        bad_session_id = f"bad_{uuid.uuid4().hex}"
        good_session_id = f"good_{uuid.uuid4().hex}"

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        release_info = TeamSessionReleaseInfo(team_names=[team_name], db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)
        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=True)
        fake_checkpointer.release = AsyncMock()

        async def _fake_resolve(session_id: str):
            if session_id == bad_session_id:
                raise RuntimeError(f"Cannot resolve team session release info for {session_id}")
            if session_id == good_session_id:
                return release_info
            return None

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                side_effect=_fake_resolve,
            ),
            patch(
                "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
                return_value=fake_db,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
        ):
            result = await manager.delete_team(team_name, [bad_session_id, good_session_id])

        assert result is True
        fake_db.drop_session_tables_by_id.assert_any_await(bad_session_id)
        fake_db.drop_session_tables_by_id.assert_any_await(good_session_id)
        assert fake_db.drop_session_tables_by_id.await_count == 2
        fake_checkpointer.release.assert_any_await(bad_session_id)
        fake_checkpointer.release.assert_any_await(good_session_id)
        assert fake_checkpointer.release.await_count == 2
        fake_db.team.delete_team.assert_awaited_once_with(team_name)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_skips_sessions_with_no_team_bucket(self):
        """delete_team should skip sessions that resolve to None and pick the next one.

        Production-realistic case: the first session has no persisted team
        bucket (resolver returns ``None``, not raise). Helper must continue
        scanning rather than treat ``None`` as the final answer.
        """
        from openjiuwen.agent_teams.runtime.manager import (
            TeamRuntimeManager,
            TeamSessionReleaseInfo,
        )
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        manager = TeamRuntimeManager()
        team_name = "delete_none_skip_team"
        empty_session_id = f"empty_{uuid.uuid4().hex}"
        good_session_id = f"good_{uuid.uuid4().hex}"

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        release_info = TeamSessionReleaseInfo(team_names=[team_name], db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)
        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=True)
        fake_checkpointer.release = AsyncMock()

        async def _fake_resolve(session_id: str):
            if session_id == empty_session_id:
                return None
            if session_id == good_session_id:
                return release_info
            return None

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                side_effect=_fake_resolve,
            ),
            patch(
                "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
                return_value=fake_db,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
        ):
            result = await manager.delete_team(team_name, [empty_session_id, good_session_id])

        assert result is True
        fake_db.team.delete_team.assert_awaited_once_with(team_name)

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_raises_when_existing_sessions_do_not_resolve(self):
        """delete_team must raise when every existing session is unusable.

        Pins the helper-returns-Optional / caller-raises contract so the
        two never drift back into the old dual-raise shape.
        """
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "delete_all_unusable_team"
        bad_session_id = f"bad_{uuid.uuid4().hex}"
        empty_session_id = f"empty_{uuid.uuid4().hex}"

        async def _fake_resolve(session_id: str):
            if session_id == bad_session_id:
                raise RuntimeError(f"Cannot resolve team session release info for {session_id}")
            return None

        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=True)

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                side_effect=_fake_resolve,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
        ):
            with pytest.raises(RuntimeError, match="any supplied sessions"):
                await manager.delete_team(team_name, [bad_session_id, empty_session_id])

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_delete_team_succeeds_when_supplied_sessions_are_already_released(self):
        """delete_team should be idempotent after supplied sessions are gone."""
        from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager

        manager = TeamRuntimeManager()
        team_name = "delete_already_released_team"
        session_id = f"released_{uuid.uuid4().hex}"
        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=False)
        fake_checkpointer.release = AsyncMock()

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                new_callable=AsyncMock,
            ) as resolve_mock,
            patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db") as get_shared_db_mock,
        ):
            result = await manager.delete_team(team_name, [session_id])

        assert result is True
        fake_checkpointer.session_exists.assert_awaited_once_with(session_id)
        fake_checkpointer.release.assert_not_awaited()
        resolve_mock.assert_not_awaited()
        get_shared_db_mock.assert_not_called()

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
            TeamSessionReleaseInfo,
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
        release_info = TeamSessionReleaseInfo(team_names=[team_name], db_config=db_config)
        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=[])
        fake_db.team = AsyncMock()
        fake_db.team.delete_team = AsyncMock(return_value=True)
        fake_checkpointer = AsyncMock()
        fake_checkpointer.session_exists = AsyncMock(return_value=True)
        fake_checkpointer.release = AsyncMock()

        with (
            patch(
                "openjiuwen.agent_teams.runtime.manager.TeamRuntimeManager.resolve_team_session_release_info",
                return_value=release_info,
            ),
            patch(
                "openjiuwen.agent_teams.spawn.shared_resources.get_shared_db",
                return_value=fake_db,
            ),
            patch(
                "openjiuwen.agent_teams.runtime.manager.CheckpointerFactory.get_checkpointer",
                return_value=fake_checkpointer,
            ),
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

        await Runner.release(session_id)

        # Checkpoint should NOT exist (simple release was called)
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_runner_release_team_session_cleans_dynamic_tables(self, isolated_checkpointer):
        """Runner.release should clean dynamic tables for team session."""
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        await Runner.start()
        session_id = f"team_auto_{uuid.uuid4().hex}"

        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        write_team_namespace(
            team_session,
            "auto_team",
            {
                "spec": {"team_name": "auto_team"},
                "context": {
                    "role": "leader",
                    "member_name": "leader",
                    "db_config": db_config.model_dump(),
                },
            },
        )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        with patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            await Runner.release(session_id)

        # Should have called drop_session_tables_by_id (team cleanup)
        fake_db.initialize.assert_awaited_once()
        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)

        # Checkpoint should be released
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level0
    async def test_runner_release_multi_team_session_cleans_dynamic_tables(self, isolated_checkpointer):
        """Runner.release should clean session tables when one session contains multiple teams."""
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace
        from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType

        await Runner.start()
        session_id = f"team_multi_{uuid.uuid4().hex}"
        db_config = DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")

        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        for team_name in ("first_team", "second_team"):
            write_team_namespace(
                team_session,
                team_name,
                {
                    "spec": {"team_name": team_name},
                    "context": {
                        "role": "leader",
                        "member_name": "leader",
                        "db_config": db_config.model_dump(),
                    },
                },
            )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        with patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            await Runner.release(session_id)

        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_runner_release_team_session_uses_default_db_config(self, isolated_checkpointer):
        """Runner.release should use TeamRuntimeContext defaults when db_config is omitted."""
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

        await Runner.start()
        session_id = f"team_missing_config_{uuid.uuid4().hex}"

        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        write_team_namespace(
            team_session,
            "missing_config_team",
            {
                "spec": {"team_name": "missing_config_team"},
                "context": {
                    "role": "leader",
                    "member_name": "leader",
                },
            },
        )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        with patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            await Runner.release(session_id)

        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()

    @pytest.mark.asyncio
    @pytest.mark.level1
    async def test_runner_release_team_session_invalid_context_uses_defaults(self, isolated_checkpointer):
        """Runner.release should tolerate partially populated TeamRuntimeContext state."""
        from openjiuwen.agent_teams.runtime.metadata import write_team_namespace

        await Runner.start()
        session_id = f"team_bad_context_{uuid.uuid4().hex}"

        team_session = create_agent_team_session(session_id=session_id)
        await team_session.pre_run()
        write_team_namespace(
            team_session,
            "bad_context_team",
            {
                "spec": {"team_name": "bad_context_team"},
                "context": {"invalid": "data"},
            },
        )
        await team_session.post_run()

        fake_db = AsyncMock()
        fake_db.initialize = AsyncMock()
        fake_db.drop_session_tables_by_id = AsyncMock(return_value=["team_task_xxx"])

        with patch("openjiuwen.agent_teams.spawn.shared_resources.get_shared_db", return_value=fake_db):
            await Runner.release(session_id)

        fake_db.drop_session_tables_by_id.assert_awaited_once_with(session_id)
        assert await isolated_checkpointer.session_exists(session_id) is False

        await Runner.stop()


# ---------------------------------------------------------------------------
# Single facade with ``base`` flag: default agent_teams path,
# ``base=True`` switches to multi_agent BaseTeam.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_agent_team_rejects_unactivated_team_name():
    """``str`` input must fail when no pool entry and no session bucket exist.

    The recover path through ``name + old_session`` reads the spec from
    the session bucket when the pool is empty (e.g. after a stop or a
    process restart). With ``session=None`` neither source is available,
    so the request must be rejected with a clear "first-time runs need
    a TeamAgentSpec" message.
    """
    from openjiuwen.core.common.exception.errors import ValidationError

    await Runner.start()
    with pytest.raises(ValidationError, match="has no live pool entry"):
        await Runner.run_agent_team(agent_team="never-seeded-team", inputs={"query": "x"})


@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_agent_team_resolves_team_name_via_pool(isolated_checkpointer, stateful_team_db):
    """After spec activation seeds the pool, follow-up calls may pass team_name as str."""
    await Runner.start()
    team_name = f"reuse_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name=team_name, agents={})
    agent = FakeTeamAgent(team_name, stream_label="team.chunk", spec=spec)

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        first_session = f"sess1_{uuid.uuid4().hex}"
        first = await Runner.run_agent_team(
            agent_team=spec,
            inputs={"query": "first"},
            session=first_session,
        )
        assert first["team_name"] == team_name
        # FakeTeamAgent bypasses BuildTeamTool, so flip the DB-side
        # team_exists signal manually before round 2 (see fixture docstring).
        await stateful_team_db.team.create_team(team_name)
        # Pool now holds an entry whose ``agent.spec`` is reachable —
        # the str-shorthand should resolve through it without a rebuild.
        second_session = f"sess2_{uuid.uuid4().hex}"
        second = await Runner.run_agent_team(
            agent_team=team_name,
            inputs={"query": "second"},
            session=second_session,
        )
        assert second["team_name"] == team_name
        assert agent.invoke_calls == 2


@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_agent_team_rejects_team_agent_instance():
    """``TeamAgent`` instances are no longer accepted on the public entry."""
    from openjiuwen.core.common.exception.errors import ValidationError

    await Runner.start()
    leader_card = AgentCard(id="leader_reject", name="leader", description="leader")
    agent = TeamAgent(card=leader_card)
    with pytest.raises(ValidationError, match="run_agent_team accepts"):
        await Runner.run_agent_team(agent_team=agent, inputs={"query": "x"})


@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_agent_team_base_true_rejects_team_agent_spec():
    """``TeamAgentSpec`` belongs to the default path, not ``base=True``."""
    from openjiuwen.core.common.exception.errors import ValidationError

    await Runner.start()
    spec = TeamAgentSpec.model_construct(team_name="mis_routed", agents={})
    with pytest.raises(ValidationError, match=r"run_agent_team\(base=True\) accepts"):
        await Runner.run_agent_team(agent_team=spec, inputs={"query": "x"}, base=True)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_run_agent_team_base_true_accepts_base_team_instance(isolated_checkpointer):
    """``base=True`` + ``BaseTeam`` instance: invoke + post_run lifecycle wires correctly."""
    from openjiuwen.core.multi_agent import BaseTeam, TeamCard, TeamConfig

    class _StubTeam(BaseTeam):
        def __init__(self) -> None:
            super().__init__(
                card=TeamCard(id="stub_base", name="stub_base", description="stub"),
                config=TeamConfig(max_agents=1),
            )
            self.invoke_calls = 0

        async def invoke(self, message: Any, session=None) -> Any:
            self.invoke_calls += 1
            return {"echo": message, "session_id": session.get_session_id()}

        async def stream(self, message: Any, session=None) -> AsyncIterator[Any]:
            yield await self.invoke(message, session=session)

    await Runner.start()
    team = _StubTeam()
    session_id = f"base_team_{uuid.uuid4().hex}"
    result = await Runner.run_agent_team(
        agent_team=team,
        inputs={"payload": "hello"},
        base=True,
        session=session_id,
    )
    assert team.invoke_calls == 1
    assert result["echo"] == {"payload": "hello"}
    assert result["session_id"] == session_id


@pytest.mark.asyncio
@pytest.mark.level1
async def test_run_agent_team_base_true_resolves_team_id_via_resource_mgr(isolated_checkpointer):
    """``base=True`` + ``str`` resolves through ``Runner.resource_mgr``."""
    from openjiuwen.core.multi_agent import BaseTeam, TeamCard, TeamConfig

    class _StubTeam(BaseTeam):
        def __init__(self) -> None:
            super().__init__(
                card=TeamCard(id="stub_resolve", name="stub_resolve", description="stub"),
                config=TeamConfig(max_agents=1),
            )

        async def invoke(self, message: Any, session=None) -> Any:
            return {"resolved": True, "session_id": session.get_session_id()}

        async def stream(self, message: Any, session=None) -> AsyncIterator[Any]:
            yield await self.invoke(message, session=session)

    await Runner.start()
    team = _StubTeam()
    await Runner.resource_mgr.add_agent_team(team.card, lambda: team)
    try:
        result = await Runner.run_agent_team(
            agent_team=team.card.id,
            inputs={"x": 1},
            base=True,
        )
        assert result["resolved"] is True
    finally:
        await Runner.resource_mgr.remove_agent_team(team_id=team.card.id)
