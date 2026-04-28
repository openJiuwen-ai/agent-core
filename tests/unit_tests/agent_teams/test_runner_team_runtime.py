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

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent_team import create_agent_team_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import InMemoryCheckpointer
from openjiuwen.core.single_agent import (
    AgentCard,
)
from openjiuwen.core.session.stream import OutputSchema


@pytest.fixture
def isolated_checkpointer():
    original = CheckpointerFactory.get_checkpointer()
    checkpointer = InMemoryCheckpointer()
    CheckpointerFactory.set_default_checkpointer(checkpointer)
    try:
        yield checkpointer
    finally:
        CheckpointerFactory.set_default_checkpointer(original)


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
        self.invoke_calls += 1
        session.update_state(
            {
                "team_name": self.team_name,
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
            }
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
async def test_runner_team_runtime_manager_resumes_new_session_and_recovers_history(isolated_checkpointer):
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

    with patch.object(TeamAgentSpec, "build", return_value=active_agent), patch(
        "openjiuwen.agent_teams.factory.resume_persistent_team",
        side_effect=_resume,
    ), patch(
        "openjiuwen.agent_teams.factory.recover_for_existing_session",
        side_effect=_recover_existing,
    ):
        first_chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "first"},
                session=session_one,
            )
        ]
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
    assert second_chunks[0].payload["activation_kind"] == "resume"
    assert active_agent.resume_calls == [session_two, session_one]
    assert third_chunks[0].payload["activation_kind"] == "recover"
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
    assert second_chunks == []
    assert agent.stream_calls == 1
    assert agent.invoke_calls == 1
    assert agent.resume_calls == []

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_same_session_after_pause_resumes_paused_runtime(isolated_checkpointer):
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
    assert second_chunks[0].payload["activation_kind"] == "resume_paused"
    assert second_chunks[1].payload["event_type"] == "team.chunk"
    assert agent.pause_calls == 1
    assert agent.stream_calls == 2
    assert agent.invoke_calls == 2

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_paused_same_session_resume_uses_same_prepared_session(isolated_checkpointer):
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
async def test_runner_existing_session_without_team_name_short_circuits(isolated_checkpointer):
    session_id = f"invalid_missing_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="invalid_team", agents={})
    agent = FakeTeamAgent("invalid_team", stream_label="team.chunk")
    await Runner.start()

    existing_session = create_agent_team_session(session_id=session_id, team_id="invalid_team")
    await existing_session.pre_run(inputs={"query": "seed"})
    existing_session.update_state({"spec": {"team_name": "invalid_team"}})
    await existing_session.post_run()

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        result = await Runner.run_agent_team(
            agent_team=spec,
            inputs={"query": "should short circuit"},
            session=session_id,
        )

    assert result is None
    assert agent.invoke_calls == 0
    assert agent.stream_calls == 0

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_existing_session_with_wrong_team_name_short_circuits(isolated_checkpointer):
    session_id = f"invalid_mismatch_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="target_team", agents={})
    agent = FakeTeamAgent("target_team", stream_label="team.chunk")
    await Runner.start()

    existing_session = create_agent_team_session(session_id=session_id, team_id="other_team")
    await existing_session.pre_run(inputs={"query": "seed"})
    existing_session.update_state(
        {
            "team_name": "other_team",
            "spec": {"team_name": "other_team"},
        }
    )
    await existing_session.post_run()

    with patch.object(TeamAgentSpec, "build", return_value=agent):
        chunks = [
            chunk
            async for chunk in Runner.run_agent_team_streaming(
                agent_team=spec,
                inputs={"query": "should short circuit"},
                session=session_id,
            )
        ]

    assert chunks == []
    assert agent.invoke_calls == 0
    assert agent.stream_calls == 0

    await Runner.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_runner_interact_pause_and_delete_agent_team_route_through_team_runtime_manager(isolated_checkpointer):
    await Runner.start()
    session_id = f"delete_{uuid.uuid4().hex}"
    spec = TeamAgentSpec.model_construct(team_name="delete_team", agents={})
    agent = FakeTeamAgent("delete_team", stream_label="team.chunk")

    fake_database = AsyncMock()
    fake_database.delete_team.return_value = True

    with patch.object(TeamAgentSpec, "build", return_value=agent), patch(
        "openjiuwen.agent_teams.runtime_manager.TeamDatabase",
        return_value=fake_database,
    ):
        async for _ in Runner.run_agent_team_streaming(
            agent_team=spec,
            inputs={"query": "hello"},
            session=session_id,
        ):
            pass

        assert await Runner.interact_agent_team(
            "follow-up",
            team_name="delete_team",
            session_id=session_id,
        ) is True
        assert agent.interactions == ["follow-up"]

        assert await Runner.pause_agent_team(team_name="delete_team", session_id=session_id) is True
        assert agent.pause_calls == 1

        assert await Runner.delete_agent_team(team_name="delete_team", session_ids=[session_id]) is True
        fake_database.initialize.assert_awaited_once()
        fake_database.delete_team.assert_awaited_once_with("delete_team")
        fake_database.close.assert_awaited_once()

    assert await isolated_checkpointer.session_exists(session_id) is False

    await Runner.stop()


@pytest.mark.asyncio
async def test_team_agent_cancelled_round_does_not_restart_follow_up():
    leader_card = AgentCard(id=f"leader_{uuid.uuid4().hex}", name="leader", description="leader")
    agent = TeamAgent(card=leader_card)
    agent._stream_queue = object()
    agent._pending_inputs = ["follow-up"]
    agent._execute_round = AsyncMock(side_effect=asyncio.CancelledError())
    agent._start_agent = AsyncMock()

    with pytest.raises(asyncio.CancelledError):
        await agent._run_one_round("cancelled")

    assert agent._start_agent.await_count == 0
    assert agent._pending_inputs == ["follow-up"]
    assert agent._agent_task is None


@pytest.mark.asyncio
async def test_team_agent_resume_for_new_session_rebinds_only_live_teammates():
    leader_card = AgentCard(id=f"leader_{uuid.uuid4().hex}", name="leader", description="leader")
    agent = TeamAgent(card=leader_card)
    agent._ctx = SimpleNamespace(
        role=TeamRole.LEADER,
        member_name="leader",
        team_spec=SimpleNamespace(team_name="persistent_team"),
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
    agent._team_backend = fake_backend
    agent._spawned_handles = {
        "worker_busy": object(),
        "worker_ready": object(),
    }
    agent._cleanup_teammate = AsyncMock()
    agent._restart_teammate = AsyncMock(return_value=True)

    new_session = create_agent_team_session(
        session_id=f"session_{uuid.uuid4().hex}",
        team_id="persistent_team",
    )

    await agent.resume_for_new_session(new_session)

    assert agent._session_id == new_session.get_session_id()
    fake_db.create_cur_session_tables.assert_awaited_once()
    assert agent._cleanup_teammate.await_args_list == [
        call("worker_busy"),
        call("worker_ready"),
    ]
    assert agent._restart_teammate.await_args_list == [
        call("worker_busy"),
        call("worker_ready"),
    ]
    assert fake_db.update_member_status.await_args_list == [
        call("worker_busy", "persistent_team", "error"),
        call("worker_busy", "persistent_team", "restarting"),
        call("worker_ready", "persistent_team", "error"),
        call("worker_ready", "persistent_team", "restarting"),
    ]


@pytest.mark.asyncio
async def test_team_agent_recover_for_existing_session_rebinds_live_teammates():
    leader_card = AgentCard(id=f"leader_{uuid.uuid4().hex}", name="leader", description="leader")
    agent = TeamAgent(card=leader_card)
    agent._ctx = SimpleNamespace(
        role=TeamRole.LEADER,
        member_name="leader",
        team_spec=SimpleNamespace(team_name="persistent_team"),
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
    agent._team_backend = fake_backend
    agent._spawned_handles = {
        "worker_busy": object(),
        "worker_ready": object(),
    }
    agent._stop_coordination = AsyncMock()
    agent._restart_teammate = AsyncMock(return_value=True)

    existing_session = create_agent_team_session(
        session_id=f"recover_{uuid.uuid4().hex}",
        team_id="persistent_team",
    )

    await agent.recover_for_existing_session(existing_session)

    assert agent._session_id == existing_session.get_session_id()
    agent._stop_coordination.assert_awaited_once()
    fake_db.create_cur_session_tables.assert_awaited_once()
    assert agent._restart_teammate.await_args_list == [
        call("worker_busy"),
        call("worker_ready"),
    ]
    assert fake_db.update_member_status.await_args_list == [
        call("worker_busy", "persistent_team", "error"),
        call("worker_busy", "persistent_team", "restarting"),
        call("worker_ready", "persistent_team", "error"),
        call("worker_ready", "persistent_team", "restarting"),
    ]


def test_team_agent_recover_from_session_restores_session_id():
    session_id = f"recover_state_{uuid.uuid4().hex}"
    session = create_agent_team_session(session_id=session_id, team_id="persistent_team")
    session.update_state(
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
        }
    )

    agent = TeamAgent.recover_from_session(session)

    assert agent._session_id == session_id


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
