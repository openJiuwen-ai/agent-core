# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import uuid
from typing import (
    Any,
    AsyncIterator,
)

import pytest

from openjiuwen.core.multi_agent import (
    BaseTeam,
    TeamCard,
    TeamConfig,
)
from openjiuwen.core.multi_agent.team_runtime import CommunicableAgent
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent_team import create_agent_team_session
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.checkpointer.checkpointer import InMemoryCheckpointer
from openjiuwen.core.single_agent import (
    AgentCard,
    BaseAgent,
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


class CountingWorker(CommunicableAgent, BaseAgent):
    def configure(self, config) -> "CountingWorker":
        return self

    async def invoke(self, inputs: Any, session=None) -> Any:
        count = (session.get_state("worker_count") or 0) + 1
        session.update_state({"worker_count": count})
        await session.write_stream({"kind": "agent", "count": count})
        return {"worker_count": count}

    async def stream(self, inputs: Any, session=None, stream_modes=None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session=session)


class CountingTeam(BaseTeam):
    def __init__(self, team_id: str):
        card = TeamCard(id=team_id, name=team_id, description="test team")
        super().__init__(card=card, config=TeamConfig(max_agents=4))
        self.worker_card = AgentCard(id=f"{team_id}_worker", name="worker", description="worker")
        self.add_agent(self.worker_card, lambda: CountingWorker(card=self.worker_card))

    async def invoke(self, message, session=None) -> Any:
        count = (session.get_state("team_count") or 0) + 1
        session.update_state({"team_count": count})
        await self.runtime.start()
        worker_result = await self.runtime.send(
            message=message,
            recipient=self.worker_card.id,
            sender=self.worker_card.id,
            session_id=session.get_session_id(),
        )
        return {"team_count": count, **worker_result}

    async def stream(self, message, session=None) -> AsyncIterator[Any]:
        yield await self.invoke(message, session=session)


@pytest.mark.asyncio
async def test_runneder_run_agent_team_recovers_team_and_child_state(isolated_checkpointer):
    await Runner.start()
    team = CountingTeam(f"team_{uuid.uuid4().hex}")
    session_id = f"session_{uuid.uuid4().hex}"

    result1 = await Runner.run_agent_team(team, {"payload": "first"}, session=session_id)
    result2 = await Runner.run_agent_team(team, {"payload": "second"}, session=session_id)

    assert result1["team_count"] == 1
    assert result1["worker_count"] == 1
    assert result2["team_count"] == 2
    assert result2["worker_count"] == 2

    await team.runtime.stop()
    await isolated_checkpointer.release(session_id)


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
