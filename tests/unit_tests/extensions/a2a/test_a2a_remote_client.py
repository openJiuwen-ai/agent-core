# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio

import pytest

from openjiuwen.core.runner.drunner.remote_client.a2a_remote_client import A2ARemoteClient
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import ProtocolEnum, RemoteClientConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.single_agent.schema.agent_result import AgentResult, Artifact, Part


@pytest.mark.asyncio
class TestA2ARemoteClient:
    async def test_invoke_should_return_agent_result_from_a2a_client(self, monkeypatch):
        captured = {}

        class FakeA2AClient:
            def __init__(self, **kwargs):
                captured["init_kwargs"] = kwargs

            async def stop(self):
                captured["closed"] = True

            async def invoke(self, inputs):
                captured["invoke_inputs"] = inputs
                return AgentResult(
                    task_id="task-send-1",
                    sessionId="conv-1",
                    status=TaskStatus.COMPLETED,
                )

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.remote_client.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        card = AgentCard(id="a2a-agent", name="a2a-agent")
        client = A2ARemoteClient(RemoteClientConfig(
            id="a2a-agent",
            protocol=ProtocolEnum.A2A,
            url="http://127.0.0.1:41241",
        ), card=card)
        await client.start()
        try:
            response = await client.invoke({"query": "hello", "conversation_id": "conv-1"})
        finally:
            await client.stop()

        assert getattr(captured["init_kwargs"]["card"], "name", None) == card.name
        assert captured["invoke_inputs"]["query"] == "hello"
        assert response.status == TaskStatus.COMPLETED
        assert captured["closed"] is True

    async def test_remote_agent_invoke_should_return_agent_result(self, monkeypatch):
        class FakeA2AClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def start(self):
                return None

            async def stop(self):
                return None

            async def invoke(self, inputs):
                return AgentResult(
                    task_id="task-send-1",
                    sessionId=inputs.get("conversation_id"),
                    status=TaskStatus.COMPLETED,
                    artifacts=[
                        Artifact(
                            artifactId="artifact-1",
                            parts=[Part(text="invoke ok")],
                        )
                    ],
                    metadata={},
                )

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.remote_client.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        agent = RemoteAgent(
            agent_id="a2a-agent",
            protocol=ProtocolEnum.A2A,
            config={"url": "http://127.0.0.1:41241"},
            card=AgentCard(id="a2a-agent", name="a2a-agent"),
        )
        response = await agent.invoke({"query": "hello a2a", "conversation_id": "conv-1"})
        assert response.status == TaskStatus.COMPLETED
        assert response.artifacts[0].parts[0].text == "invoke ok"

    async def test_stream_should_propagate_cancelled_error(self, monkeypatch):
        cancelled = []

        class FakeA2AClient:
            def __init__(self, **kwargs):
                return None

            async def stop(self):
                return None

            async def stream(self, inputs):
                yield AgentResult(
                    task_id="task-stream-1",
                    sessionId="context-stream-1",
                    status=TaskStatus.WORKING,
                    artifacts=[Artifact(artifactId="artifact-1", parts=[Part(text="chunk-1")])],
                )
                while True:
                    await asyncio.sleep(1)

            async def cancel_task(self, task_id):
                cancelled.append(task_id)
                return {"id": task_id}

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.remote_client.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        card = AgentCard(id="a2a-agent", name="a2a-agent")
        client = A2ARemoteClient(RemoteClientConfig(
            id="a2a-agent",
            protocol=ProtocolEnum.A2A,
            url="http://127.0.0.1:41241",
        ), card=card)
        await client.start()

        async def consume():
            async for _ in client.stream({"query": "stream please"}):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.1)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        await client.stop()
        assert cancelled == []

    async def test_stream_timeout_should_stop_client(self, monkeypatch):
        stopped = []

        class FakeA2AClient:
            def __init__(self, **kwargs):
                return None

            async def stop(self):
                stopped.append(True)

            async def stream(self, inputs):
                await asyncio.sleep(1)
                yield AgentResult(task_id=inputs.get("task_id"), status=TaskStatus.WORKING)

        monkeypatch.setattr(
            "openjiuwen.core.runner.drunner.remote_client.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        card = AgentCard(id="a2a-agent", name="a2a-agent")
        client = A2ARemoteClient(
            RemoteClientConfig(
                id="a2a-agent",
                protocol=ProtocolEnum.A2A,
                url="http://127.0.0.1:41241",
            ),
            card=card,
        )
        await client.start()

        with pytest.raises(TimeoutError):
            async for _ in client.stream({"query": "slow", "task_id": "task-timeout-1"}, timeout=0.01):
                pass

        assert stopped == [True]
