import asyncio

import pytest

from openjiuwen.agent.config.base import AgentConfig
from openjiuwen.core.agent.agent import AgentRuntime
from openjiuwen.core.runtime.config import Config
from openjiuwen.core.runtime.wrapper import TaskRuntime

pytestmark = pytest.mark.asyncio


async def test_agent_stream():
    session_id = "test"
    config = Config()
    config.set_agent_config(AgentConfig(id="test_agent_checkpoint"))
    agent_runtime = AgentRuntime(config)

    runtime = await agent_runtime.pre_run(session_id=session_id)

    async def consumer():
        i = runtime.stream_iterator()
        async for chunk in i:
            # logger.info("hello")
            print(chunk)

    async def producer():
        for i in range(10):
            await runtime.write_stream({"type": "event", "index": 0, "payload": {"name": "hi"}})
        await runtime.post_run()

    task1 = asyncio.create_task(consumer())
    task2 = asyncio.create_task(producer())

    await asyncio.gather(task1, task2)