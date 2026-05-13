# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from unittest.mock import Mock

import pytest
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.client.client_factory import TransportProtocol
from a2a.types.a2a_pb2 import (
    Message,
    Part as A2APart,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from fastapi import FastAPI

from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.schema.agent_result import AgentResult, Artifact, Part
from openjiuwen.extensions.a2a.a2a_server import A2AAgentExecutor, A2AServer


class TestA2AServer:
    def test_server_should_default_to_jsonrpc_when_no_supported_interfaces(self):
        server = A2AServer(
            agent_card=AgentCard(id="demo-a2a-agent", name="Demo A2A Agent", description="demo"),
        )

        app = server.build_app()

        assert isinstance(app, FastAPI)
        assert server.rest_app is None
        assert server._resolve_transport_protocols() == {TransportProtocol.JSONRPC}

    def test_server_should_normalize_jsonrpc_route_and_interface_url(self):
        server = A2AServer(
            agent_card=AgentCard(id="demo-a2a-agent", name="Demo A2A Agent", description="demo"),
            interface_url="http://127.0.0.1:8080/a2a/jsonrpc",
            rpc_url="/a2a/jsonrpc",
        )
        app = server.build_app()
        jsonrpc_paths = [
            r.path for r in app.routes if getattr(r, "path", None) and "jsonrpc" in r.path
        ]
        assert "/a2a/jsonrpc/" in jsonrpc_paths
        assert server._rpc_url == "/a2a/jsonrpc/"
        assert server._a2a_agent_card.supported_interfaces[0].url == "http://127.0.0.1:8080/a2a/jsonrpc/"

    def test_server_should_use_agent_card_interface_url_when_parameter_omitted(self):
        url = "http://127.0.0.1:8091/a2a/jsonrpc"
        server = A2AServer(
            agent_card=AgentCard(
                id="demo-a2a-agent",
                name="Demo A2A Agent",
                description="demo",
                interface_url=url,
            ),
        )
        assert server._a2a_agent_card.supported_interfaces[0].url == "http://127.0.0.1:8091/a2a/jsonrpc/"

    def test_server_should_build_rest_app_when_http_json_is_declared(self):
        server = A2AServer(
            agent_card=AgentCard(id="demo-a2a-agent", name="Demo A2A Agent", description="demo"),
            interface_url="http://example.com/a2a/rest/",
            protocol_binding="HTTP+JSON",
        )

        app = server.build_app()

        assert isinstance(app, FastAPI)
        assert server.rest_app is not None
        assert app is not server.rest_app
        assert server._resolve_transport_protocols() == {TransportProtocol.HTTP_JSON}

    @pytest.mark.asyncio
    async def test_executor_should_publish_a2a_events(self):
        async def fake_invoke(inputs):
            return AgentResult(
                task_id="task-1",
                sessionId=inputs.get("sessionId"),
                status=TaskStatus.COMPLETED,
                artifacts=[
                    Artifact(
                        artifactId="artifact-1",
                        name="summary",
                        parts=[Part(text="hello from executor")],
                    )
                ],
                metadata={"source": "openjiuwen"},
            )

        executor = A2AAgentExecutor(invoke_handler=fake_invoke)
        request = SendMessageRequest(
            message=Message(
                task_id="task-1",
                context_id="conv-1",
                parts=[A2APart(text="hello")],
            )
        )
        context = RequestContext(call_context=Mock(), request=request)
        queue = EventQueue()

        await executor.execute(context, queue)

        events = []
        while True:
            try:
                events.append(queue.queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        first_status_idx = next(
            i for i, event in enumerate(events) if isinstance(event, TaskStatusUpdateEvent)
        )
        first_task_idx = next(i for i, event in enumerate(events) if isinstance(event, Task))
        assert first_task_idx < first_status_idx
        assert any(isinstance(event, TaskStatusUpdateEvent) for event in events)
        assert any(isinstance(event, TaskArtifactUpdateEvent) for event in events)

    @pytest.mark.asyncio
    async def test_executor_should_fail_task_when_invoke_raises(self):
        async def bad_invoke(_inputs):
            raise RuntimeError("invoke failed")

        executor = A2AAgentExecutor(invoke_handler=bad_invoke)
        request = SendMessageRequest(
            message=Message(
                task_id="task-err",
                context_id="conv-err",
                parts=[A2APart(text="hello")],
            )
        )
        context = RequestContext(call_context=Mock(), request=request)
        queue = EventQueue()

        with pytest.raises(RuntimeError, match="invoke failed"):
            await executor.execute(context, queue)

        events = []
        while True:
            try:
                events.append(queue.queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        assert any(
            isinstance(e, TaskStatusUpdateEvent) and e.status.state == TaskState.TASK_STATE_FAILED
            for e in events
        )

    @pytest.mark.asyncio
    async def test_server_should_stop_uvicorn_server(self):
        class FakeUvicornServer:
            def __init__(self):
                self.should_exit = False

        server = A2AServer(
            agent_card=AgentCard(id="demo-a2a-agent", name="Demo A2A Agent", description="demo"),
        )
        uvicorn_server = FakeUvicornServer()
        server._uvicorn_server = uvicorn_server

        await server.stop()

        assert uvicorn_server.should_exit is True

    def test_server_should_reject_grpc_transport(self):
        with pytest.raises(ValueError, match="gRPC transport is not supported"):
            A2AServer(
                agent_card=AgentCard(id="demo-a2a-agent", name="Demo A2A Agent", description="demo"),
                protocol_binding="GRPC",
                interface_url="https://grpc.example.com/a2a",
            )
