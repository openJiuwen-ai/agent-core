# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    Task,
    TaskState,
    TaskStatus as A2ATaskStatus,
    Part,
)

from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import ProtocolEnum
from openjiuwen.core.single_agent import AgentCard as OJWAgentCard
from openjiuwen.core.controller.schema.task import TaskStatus


class A2AExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.running_tasks: set[str] = set()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        if task_id in self.running_tasks:
            self.running_tasks.remove(task_id)

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id or "",
            context_id=context.context_id or "",
        )
        await updater.cancel()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_message = context.message
        task_id = context.task_id
        context_id = context.context_id

        if not user_message or not task_id or not context_id:
            return

        self.running_tasks.add(task_id)

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id,
        )

        await updater.event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=context_id,
                status=A2ATaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[user_message],
            )
        )

        await updater.start_work(
            message=updater.new_agent_message(parts=[Part(text="processing")])
        )

        query = context.get_user_input() or ""
        await asyncio.sleep(0.1)

        if task_id not in self.running_tasks:
            return

        await updater.add_artifact(
            parts=[Part(text=f"echo: {query}")],
            name="response",
            last_chunk=True,
        )
        await updater.complete()


def build_test_app() -> FastAPI:
    agent_card = AgentCard(
        name="System Test A2A Agent",
        description="A minimal A2A server for runner system tests",
        provider=AgentProvider(
            organization="openjiuwen-test",
            url="https://example.com",
        ),
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text"],
        default_output_modes=["text", "task-status"],
        skills=[
            AgentSkill(
                id="echo",
                name="Echo",
                description="Echo user input",
                tags=["test"],
                examples=["hello"],
                input_modes=["text"],
                output_modes=["text", "task-status"],
            )
        ],
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url="http://testserver/a2a/jsonrpc/",
            ),
        ],
    )

    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=A2AExecutor(),
        task_store=task_store,
    )

    app_builder = A2AFastAPIApplication(
        agent_card=agent_card,
        http_handler=request_handler,
        enable_v0_3_compat=True,
    )
    return app_builder.build(agent_card_url="/.well-known/agent-card.json", rpc_url="/a2a/jsonrpc/")


@pytest_asyncio.fixture
async def a2a_server_app():
    return build_test_app()


@pytest_asyncio.fixture
async def started_runner():
    await Runner.start()
    try:
        yield
    finally:
        await Runner.stop()


@pytest_asyncio.fixture
async def registered_a2a_remote_agent(a2a_server_app, started_runner, monkeypatch):
    agent_id = "remote-a2a-agent"
    remote_card = OJWAgentCard(
        id=agent_id,
        name="System Test A2A Agent",
        description="A2A remote card for runner tests",
    )
    transport = httpx.ASGITransport(app=a2a_server_app)
    httpx_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    class _TestClientFactory(ClientFactory):
        def __init__(self, config: ClientConfig, consumers=None):
            config.httpx_client = httpx_client
            super().__init__(config)

    monkeypatch.setattr("openjiuwen.extensions.a2a.a2a_client.ClientFactory", _TestClientFactory)

    agent = RemoteAgent(
        agent_id=agent_id,
        protocol=ProtocolEnum.A2A,
        config={"url": "http://testserver", "kwargs": {"card": remote_card}},
    )

    Runner.resource_mgr.add_agent(OJWAgentCard(id=agent_id), agent=agent)
    try:
        yield agent_id
    finally:
        Runner.resource_mgr.remove_agent(agent_id=agent_id)
        await httpx_client.aclose()


@pytest.mark.asyncio
async def test_runner_should_return_agent_result_from_a2a_remote_agent(registered_a2a_remote_agent):
    response = await Runner.run_agent(
        registered_a2a_remote_agent,
        {"query": "hello a2a", "conversation_id": "c-a2a-1"},
    )

    assert response.status == TaskStatus.COMPLETED
    assert response.task_id
    assert response.sessionId


@pytest.mark.asyncio
async def test_runner_should_stream_agent_result_from_a2a_remote_agent(registered_a2a_remote_agent):
    chunks = []
    async for chunk in Runner.run_agent_streaming(
        registered_a2a_remote_agent,
        {"query": "stream a2a", "conversation_id": "c-a2a-2"},
    ):
        chunks.append(chunk)

    assert chunks
    assert any(chunk.artifacts for chunk in chunks)
    assert chunks[-1].status == TaskStatus.COMPLETED
