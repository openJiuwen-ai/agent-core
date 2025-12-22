import asyncio

import pytest

from openjiuwen.core.single_agent import AgentConfig, AgentSession
from openjiuwen.core.session.config import Config

pytestmark = pytest.mark.asyncio


async def test_agent_stream():
    session_id = "test"
    config = Config()
    config.set_agent_config(AgentConfig(id="test_agent_checkpoint"))
    agent_session = AgentSession(config)

    session = await agent_session.pre_run(session_id=session_id)

    async def consumer():
        i = session.stream_iterator()
        async for chunk in i:
            # logger.info("hello")
            print(chunk)

    async def producer():
        for i in range(10):
            await session.write_stream({"type": "event", "index": 0, "payload": {"name": "hi"}})
        await session.post_run()

    task1 = asyncio.create_task(consumer())
    task2 = asyncio.create_task(producer())

    await asyncio.gather(task1, task2)