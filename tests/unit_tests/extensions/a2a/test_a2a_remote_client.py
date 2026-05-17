# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio

import pytest

from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.drunner import remote_client
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import ProtocolEnum, RemoteClientConfig
from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.schema.agent_result import AgentResult, Artifact, Part
from openjiuwen.extensions.a2a.a2a_remote_client import A2ARemoteClient


@pytest.mark.asyncio
class TestA2ARemoteClient:
    async def test_a2a_remote_client_should_require_card(self):
        with pytest.raises(ValueError, match="card is required when protocol is A2A"):
            A2ARemoteClient(
                RemoteClientConfig(
                    id="a2a-agent",
                    protocol=ProtocolEnum.A2A,
                    url="http://127.0.0.1:41241",
                )
            )

    async def test_a2a_remote_client_should_pass_polling_to_a2a_client(self, monkeypatch):
        captured = {}

        class FakeA2AClient:
            def __init__(self, **kwargs):
                captured["init_kwargs"] = kwargs

        monkeypatch.setattr(
            "openjiuwen.extensions.a2a.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        A2ARemoteClient(
            RemoteClientConfig(
                id="a2a-agent",
                protocol=ProtocolEnum.A2A,
                url="http://127.0.0.1:41241",
                kwargs={"card": AgentCard(id="a2a-agent", name="a2a-agent"), "polling": True},
            )
        )

        assert captured["init_kwargs"]["polling"] is True
        assert captured["init_kwargs"]["card"] is not None

    async def test_a2a_remote_client_should_pass_card_from_kwargs_to_a2a_client(self, monkeypatch):
        captured = {}

        class FakeA2AClient:
            def __init__(self, **kwargs):
                captured["init_kwargs"] = kwargs

        monkeypatch.setattr(
            "openjiuwen.extensions.a2a.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        card = AgentCard(id="a2a-agent", name="a2a-agent")
        A2ARemoteClient(
            RemoteClientConfig(
                id="a2a-agent",
                protocol=ProtocolEnum.A2A,
                url="http://127.0.0.1:41241",
                kwargs={"card": card},
            )
        )

        assert captured["init_kwargs"]["polling"] is False
        assert captured["init_kwargs"]["card"] is not None

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
                    sessionId="sdk-context-1",
                    status=TaskStatus.COMPLETED,
                )

        monkeypatch.setattr(
            "openjiuwen.extensions.a2a.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        client = A2ARemoteClient(RemoteClientConfig(
            id="a2a-agent",
            protocol=ProtocolEnum.A2A,
            url="http://127.0.0.1:41241",
            kwargs={"card": AgentCard(id="a2a-agent", name="a2a-agent")},
        ))
        await client.start()
        try:
            response = await client.invoke({"query": "hello", "conversation_id": "conv-1"})
        finally:
            await client.stop()

        assert captured["init_kwargs"]["polling"] is False
        assert captured["init_kwargs"]["card"] is not None
        assert captured["invoke_inputs"]["query"] == "hello"
        assert response.status == TaskStatus.COMPLETED
        assert response.sessionId == "conv-1"
        assert captured["closed"] is True

    async def test_cancel_task_should_delegate_to_a2a_client(self, monkeypatch):
        captured = {}

        class FakeA2AClient:
            def __init__(self, **kwargs):
                captured["init_kwargs"] = kwargs

            async def stop(self):
                captured["closed"] = True

            async def cancel_task(self, task_id, tenant=None):
                captured["cancel_task"] = {"task_id": task_id, "tenant": tenant}
                return AgentResult(
                    task_id=task_id,
                    sessionId="sdk-context-cancel-1",
                    status=TaskStatus.CANCELED,
                )

        monkeypatch.setattr(
            "openjiuwen.extensions.a2a.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        client = A2ARemoteClient(RemoteClientConfig(
            id="a2a-agent",
            protocol=ProtocolEnum.A2A,
            url="http://127.0.0.1:41241",
            kwargs={"card": AgentCard(id="a2a-agent", name="a2a-agent")},
        ))
        await client.start()
        try:
            response = await client.cancel_task("task-cancel-1", tenant="tenant-1")
        finally:
            await client.stop()

        assert captured["cancel_task"] == {"task_id": "task-cancel-1", "tenant": "tenant-1"}
        assert response.task_id == "task-cancel-1"
        assert response.status == TaskStatus.CANCELED
        assert response.sessionId == "sdk-context-cancel-1"
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
                    sessionId="sdk-context-1",
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
            "openjiuwen.extensions.a2a.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )
        monkeypatch.setitem(remote_client._CUSTOM_REMOTE_CLIENTS, "A2A", lambda **kwargs: A2ARemoteClient(**kwargs))

        agent = RemoteAgent(
            agent_id="a2a-agent",
            protocol=ProtocolEnum.A2A,
            config={
                "url": "http://127.0.0.1:41241",
                "kwargs": {
                    "card": AgentCard(id="a2a-agent", name="a2a-agent"),
                    "polling": True,
                },
            },
        )
        response = await agent.invoke({"query": "hello a2a", "conversation_id": "conv-1"})
        assert response.status == TaskStatus.COMPLETED
        assert response.sessionId == "conv-1"
        assert response.artifacts[0].parts[0].text == "invoke ok"
        assert agent.client.client.kwargs["polling"] is True

    async def test_remote_agent_should_bootstrap_a2a_registration_without_preimport(self, monkeypatch):
        from openjiuwen.core.runner.drunner import remote_client as remote_client_module

        registry_snapshot = dict(remote_client_module._CUSTOM_REMOTE_CLIENTS)
        remote_client_module._CUSTOM_REMOTE_CLIENTS.clear()

        captured = {}

        class FakeA2AClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def stop(self):
                return None

            async def invoke(self, inputs):
                captured["invoke_inputs"] = inputs
                return AgentResult(
                    task_id="task-send-bootstrap-1",
                    sessionId="sdk-context-bootstrap-1",
                    status=TaskStatus.COMPLETED,
                    artifacts=[
                        Artifact(
                            artifactId="artifact-bootstrap-1",
                            parts=[Part(text="bootstrap invoke ok")],
                        )
                    ],
                    metadata={},
                )

        monkeypatch.setattr(
            "openjiuwen.extensions.a2a.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        def fake_import_module(name):
            assert name == "openjiuwen.extensions.a2a"
            remote_client_module.register_remote_client(
                "A2A", lambda **kwargs: A2ARemoteClient(**kwargs)
            )
            return object()

        monkeypatch.setattr(remote_client_module.importlib, "import_module", fake_import_module)
        monkeypatch.setattr(
            remote_client_module,
            "_resolve_entry_point",
            lambda protocol, kwargs: (_ for _ in ()).throw(AssertionError("entry point fallback should not be used")),
        )

        try:
            agent = RemoteAgent(
                agent_id="a2a-agent",
                protocol=ProtocolEnum.A2A,
                config={
                    "url": "http://127.0.0.1:41241",
                    "kwargs": {
                        "card": AgentCard(id="a2a-agent", name="a2a-agent"),
                    },
                },
            )
            response = await agent.invoke({"query": "hello bootstrap", "conversation_id": "conv-bootstrap-1"})
            assert "A2A" in remote_client_module._CUSTOM_REMOTE_CLIENTS
        finally:
            remote_client_module._CUSTOM_REMOTE_CLIENTS.clear()
            remote_client_module._CUSTOM_REMOTE_CLIENTS.update(registry_snapshot)

        assert captured["invoke_inputs"]["query"] == "hello bootstrap"
        assert response.status == TaskStatus.COMPLETED
        assert response.sessionId == "conv-bootstrap-1"
        assert response.artifacts[0].parts[0].text == "bootstrap invoke ok"

    async def test_remote_agent_cancel_task_should_delegate_for_a2a_protocol(self, monkeypatch):
        captured = {}

        class FakeA2AClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def start(self):
                return None

            async def stop(self):
                return None

            async def cancel_task(self, task_id, tenant=None):
                captured["cancel_task"] = {"task_id": task_id, "tenant": tenant}
                return AgentResult(
                    task_id=task_id,
                    sessionId="sdk-context-cancel-2",
                    status=TaskStatus.CANCELED,
                )

        monkeypatch.setattr(
            "openjiuwen.extensions.a2a.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        agent = RemoteAgent(
            agent_id="a2a-agent",
            protocol=ProtocolEnum.A2A,
            config={
                "url": "http://127.0.0.1:41241",
                "kwargs": {"card": AgentCard(id="a2a-agent", name="a2a-agent")},
            },
        )
        response = await agent.cancel_task("task-cancel-2", tenant="tenant-2")
        assert captured["cancel_task"] == {"task_id": "task-cancel-2", "tenant": "tenant-2"}
        assert response.task_id == "task-cancel-2"
        assert response.status == TaskStatus.CANCELED
        assert response.sessionId == "sdk-context-cancel-2"

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
            "openjiuwen.extensions.a2a.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        client = A2ARemoteClient(RemoteClientConfig(
            id="a2a-agent",
            protocol=ProtocolEnum.A2A,
            url="http://127.0.0.1:41241",
            kwargs={"card": AgentCard(id="a2a-agent", name="a2a-agent")},
        ))
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
            "openjiuwen.extensions.a2a.a2a_remote_client.A2AClient",
            FakeA2AClient,
        )

        client = A2ARemoteClient(
            RemoteClientConfig(
                id="a2a-agent",
                protocol=ProtocolEnum.A2A,
                url="http://127.0.0.1:41241",
                kwargs={"card": AgentCard(id="a2a-agent", name="a2a-agent")},
            ),
        )
        await client.start()

        with pytest.raises(TimeoutError):
            async for _ in client.stream({"query": "slow", "task_id": "task-timeout-1"}, timeout=0.01):
                pass

        assert stopped == [True]
