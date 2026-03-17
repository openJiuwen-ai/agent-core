# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import uuid
from typing import (
    Any,
    AsyncIterator,
)

import pytest

from openjiuwen.core.multi_agent import (
    BaseGroup,
    GroupCard,
    GroupConfig,
)
from openjiuwen.core.multi_agent.group_runtime import CommunicableAgent
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent_group import create_agent_group_session
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


class CountingGroup(BaseGroup):
    def __init__(self, group_id: str):
        card = GroupCard(id=group_id, name=group_id, description="test group")
        super().__init__(card=card, config=GroupConfig(max_agents=4))
        self.worker_card = AgentCard(id=f"{group_id}_worker", name="worker", description="worker")
        self.add_agent(self.worker_card, lambda: CountingWorker(card=self.worker_card))

    async def invoke(self, message, session=None) -> Any:
        count = (session.get_state("group_count") or 0) + 1
        session.update_state({"group_count": count})
        await self.runtime.start()
        worker_result = await self.runtime.send(
            message=message,
            recipient=self.worker_card.id,
            sender=self.worker_card.id,
            session_id=session.get_session_id(),
        )
        return {"group_count": count, **worker_result}

    async def stream(self, message, session=None) -> AsyncIterator[Any]:
        yield await self.invoke(message, session=session)


@pytest.mark.asyncio
async def test_runneder_run_agent_group_recovers_group_and_child_state(isolated_checkpointer):
    await Runner.start()
    group = CountingGroup(f"group_{uuid.uuid4().hex}")
    session_id = f"session_{uuid.uuid4().hex}"

    result1 = await Runner.run_agent_group(group, {"payload": "first"}, session=session_id)
    result2 = await Runner.run_agent_group(group, {"payload": "second"}, session=session_id)

    assert result1["group_count"] == 1
    assert result1["worker_count"] == 1
    assert result2["group_count"] == 2
    assert result2["worker_count"] == 2

    await group.runtime.stop()
    await isolated_checkpointer.release(session_id)


@pytest.mark.asyncio
async def test_group_session_forwards_child_stream_output_with_source_tags(isolated_checkpointer):
    session_id = f"stream_{uuid.uuid4().hex}"
    group_session = create_agent_group_session(session_id=session_id, group_id="stream_group")
    await group_session.pre_run(inputs={"query": "hello"})

    child_session = group_session.create_agent_session(agent_id="worker_a")
    await child_session.pre_run(inputs={"payload": "child"})
    await child_session.write_stream({"kind": "agent"})
    await child_session.post_run()

    await group_session.write_stream({"kind": "group"})
    await group_session.post_run()

    chunks = []
    async for chunk in group_session.stream_iterator():
        chunks.append(chunk)

    assert any(
        getattr(chunk, "payload", {}).get("source_agent_id") == "worker_a"
        and getattr(chunk, "payload", {}).get("source_group_id") == "stream_group"
        for chunk in chunks
    )
    assert any(
        getattr(chunk, "payload", {}).get("kind") == "group"
        and getattr(chunk, "payload", {}).get("source_group_id") == "stream_group"
        for chunk in chunks
    )

    await isolated_checkpointer.release(session_id)
