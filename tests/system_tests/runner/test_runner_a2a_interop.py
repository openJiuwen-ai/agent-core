# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from a2a.client import ClientConfig, ClientFactory
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import AgentCard as A2AAgentCard
from a2a.types import AgentCapabilities, AgentInterface, AgentProvider, AgentSkill, Message, Part as A2APart, Role, SendMessageRequest, Task, TaskState, TaskStatus as A2ATaskStatus

from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import ProtocolEnum
from openjiuwen.extensions.a2a.a2a_server_adapter import A2AServerAdapter
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.schema.agent_result import AgentResult, Artifact, Part
from openjiuwen.extensions.a2a.a2a_agentcard_adapter import A2AAgentCardAdapter
from openjiuwen.extensions.a2a.a2a_transformer import A2ATransformer


def _server_base_url(server_id: str) -> str:
    return f"http://{server_id}.test"


def _rpc_url(server_id: str) -> str:
    return f"{_server_base_url(server_id)}/a2a/jsonrpc/"


def _make_openjiuwen_result(
    *,
    server_id: str,
    task_id: str,
    conversation_id: str,
    query: str,
    phase: str,
    status: TaskStatus,
) -> AgentResult:
    return AgentResult(
        task_id=task_id,
        sessionId=conversation_id,
        status=status,
        artifacts=[
            Artifact(
                artifactId=f"{server_id}-{phase}",
                name="response",
                parts=[
                    Part(
                        text=f"{server_id} {phase}: {query}",
                        metadata={"server_id": server_id, "phase": phase},
                    )
                ],
                metadata={"server_id": server_id, "phase": phase},
            )
        ],
        metadata={"server_id": server_id, "phase": phase, "query": query},
    )


def _assert_stream_result(
    chunks: list[AgentResult],
    *,
    server_id: str,
    query: str,
    session_id: str | None = None,
) -> None:
    assert chunks
    assert chunks[-1].status == TaskStatus.COMPLETED
    assert chunks[-1].sessionId
    assert all(chunk.task_id == chunks[0].task_id for chunk in chunks)
    assert all(chunk.sessionId == chunks[0].sessionId for chunk in chunks)
    if session_id is not None:
        assert all(chunk.sessionId == session_id for chunk in chunks)
    if len(chunks) > 1:
        assert chunks[0].status in {TaskStatus.SUBMITTED, TaskStatus.WORKING}
        assert any(chunk.status in {TaskStatus.SUBMITTED, TaskStatus.WORKING} for chunk in chunks[:-1])
    else:
        assert chunks[0].status == TaskStatus.COMPLETED
    assert any(
        artifact.parts
        and artifact.parts[0].text
        and server_id in artifact.parts[0].text
        and query in artifact.parts[0].text
        for chunk in chunks
        for artifact in chunk.artifacts
    )


def _extract_openjiuwen_conversation_id(payload: dict[str, object], fallback: str) -> str:
    conversation_id = payload.get("conversation_id") or payload.get("sessionId")
    return str(conversation_id) if conversation_id else fallback


def _create_openjiuwen_a2a_server(server_id: str) -> tuple[A2AServerAdapter, FastAPI]:
    agent_card = AgentCard(
        id=server_id,
        name=server_id,
        description=f"openjiuwen A2A server {server_id}",
    )

    async def invoke_handler(payload: dict[str, object]) -> AgentResult:
        conversation_id = _extract_openjiuwen_conversation_id(payload, server_id)
        query = str(payload.get("query") or "")
        return _make_openjiuwen_result(
            server_id=server_id,
            task_id=f"{server_id}-invoke-{uuid.uuid4().hex}",
            conversation_id=conversation_id,
            query=query,
            phase="invoke",
            status=TaskStatus.COMPLETED,
        )

    async def stream_handler(payload: dict[str, object]):
        conversation_id = _extract_openjiuwen_conversation_id(payload, server_id)
        query = str(payload.get("query") or "")
        task_id = f"{server_id}-stream-{uuid.uuid4().hex}"
        yield _make_openjiuwen_result(
            server_id=server_id,
            task_id=task_id,
            conversation_id=conversation_id,
            query=query,
            phase="working",
            status=TaskStatus.WORKING,
        )
        await asyncio.sleep(0.05)
        yield _make_openjiuwen_result(
            server_id=server_id,
            task_id=task_id,
            conversation_id=conversation_id,
            query=query,
            phase="complete",
            status=TaskStatus.COMPLETED,
        )

    adapter = A2AServerAdapter(
        adapter_id=server_id,
        agent_card=agent_card,
        invoke_handler=invoke_handler,
        stream_handler=stream_handler,
        interface_url=_rpc_url(server_id),
        rpc_url="/a2a/jsonrpc/",
    )
    return adapter, adapter.server.build_app()


class _ThirdPartyA2AExecutor(AgentExecutor):
    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        self.running_tasks: set[str] = set()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        self.running_tasks.discard(task_id)
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context.context_id or "",
        )
        await updater.cancel()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        payload = A2ATransformer.from_a2a_request(context)
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
            message=updater.new_agent_message(parts=[A2APart(text=f"{self.server_id} working")])
        )

        query = str(payload.get("query") or context.get_user_input() or "")
        await asyncio.sleep(0.05)
        if task_id not in self.running_tasks:
            return

        await updater.add_artifact(
            parts=[A2APart(text=f"{self.server_id} processing: {query}")],
            name="response",
            last_chunk=False,
        )
        await asyncio.sleep(0.05)
        if task_id not in self.running_tasks:
            return

        await updater.add_artifact(
            parts=[A2APart(text=f"{self.server_id} complete: {query}")],
            name="response",
            last_chunk=True,
        )
        await updater.complete()


def _create_third_party_a2a_server(server_id: str) -> tuple[FastAPI, A2AAgentCard]:
    agent_card = A2AAgentCard(
        name=server_id,
        description=f"third-party A2A server {server_id}",
        provider=AgentProvider(
            organization="third-party",
            url="https://example.com",
        ),
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=[
            AgentSkill(
                id=f"{server_id}-skill",
                name="Echo",
                description="Echo user input",
                tags=["interop"],
                examples=["hello"],
                input_modes=["text/plain"],
                output_modes=["text/plain", "application/json"],
            )
        ],
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=_rpc_url(server_id),
            )
        ],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=_ThirdPartyA2AExecutor(server_id),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    app = FastAPI()
    app.router.routes.extend(
        create_agent_card_routes(agent_card=agent_card, card_url="/.well-known/agent-card.json")
    )
    app.router.routes.extend(
        create_jsonrpc_routes(
            request_handler=request_handler,
            rpc_url="/a2a/jsonrpc/",
            enable_v0_3_compat=True,
        )
    )
    return app, agent_card


def _create_a2a_sdk_client(app: FastAPI, interface_url: str):
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=_server_base_url(interface_url.split("//", 1)[-1].split("/", 1)[0]),
    )
    factory = ClientFactory(ClientConfig(httpx_client=httpx_client))
    client_card = A2AAgentCardAdapter.to_a2a_agent_card(
        AgentCard(
            id="sdk-client",
            name="sdk-client",
            description="A2A SDK client for system tests",
        ),
        interface_url=interface_url,
        protocol_binding="JSONRPC",
        protocol_version="1.0",
    )
    assert client_card is not None
    return factory.create(client_card), httpx_client


def _build_a2a_send_message_request(query: str, conversation_id: str | None = None) -> SendMessageRequest:
    message = Message(
        message_id=uuid.uuid4().hex,
        role=Role.ROLE_USER,
    )
    message.parts.append(A2APart(text=query))
    if conversation_id:
        message.context_id = conversation_id
    return SendMessageRequest(message=message)


def _merge_agent_results(base: AgentResult, update: AgentResult) -> AgentResult:
    artifacts = [*base.artifacts, *update.artifacts]
    metadata = {**base.metadata, **update.metadata}
    session_id = update.sessionId or base.sessionId
    task_id = update.task_id or base.task_id
    status = update.status if update.status not in (None, TaskStatus.UNKNOWN) else base.status
    return base.model_copy(
        update={
            "task_id": task_id,
            "sessionId": session_id,
            "status": status,
            "artifacts": artifacts,
            "metadata": metadata,
        }
    )


async def _collect_a2a_invoke_result(client, query: str, conversation_id: str | None = None) -> AgentResult:
    aggregate = AgentResult()
    async for event in client.send_message(_build_a2a_send_message_request(query, conversation_id=conversation_id)):
        aggregate = _merge_agent_results(aggregate, A2ATransformer.from_a2a_response(event))
    return aggregate


async def _collect_a2a_stream_chunks(client, query: str, conversation_id: str | None = None) -> list[AgentResult]:
    chunks: list[AgentResult] = []
    aggregate = AgentResult()
    async for event in client.send_message(_build_a2a_send_message_request(query, conversation_id=conversation_id)):
        aggregate = _merge_agent_results(aggregate, A2ATransformer.from_a2a_response(event))
        chunks.append(aggregate)
    return chunks


@pytest_asyncio.fixture
async def started_runner():
    await Runner.start()
    try:
        yield
    finally:
        await Runner.stop()


@pytest_asyncio.fixture
async def openjiuwen_a2a_remote_agent_factory(monkeypatch):
    created_clients: list[httpx.AsyncClient] = []

    def _factory(*, agent_id: str, base_url: str, app: FastAPI) -> RemoteAgent:
        httpx_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=base_url,
        )
        created_clients.append(httpx_client)

        class _TestClientFactory(ClientFactory):
            def __init__(self, config: ClientConfig, consumers=None):
                config.httpx_client = httpx_client
                super().__init__(config)

        monkeypatch.setattr("openjiuwen.extensions.a2a.a2a_client.ClientFactory", _TestClientFactory)

        remote_agent = RemoteAgent(
            agent_id=agent_id,
            protocol=ProtocolEnum.A2A,
            config={
                "url": base_url,
                "kwargs": {
                    "card": AgentCard(id=agent_id, name=agent_id, description=f"remote agent {agent_id}"),
                },
            },
        )
        return remote_agent

    yield _factory

    for client in created_clients:
        await client.aclose()


@pytest.mark.asyncio
async def test_openjiuwen_agent_remote_interop_should_support_invoke_and_stream(
    started_runner,
    openjiuwen_a2a_remote_agent_factory,
):
    server_id = "ojw-server-1"
    server_adapter, server_app = _create_openjiuwen_a2a_server(server_id)

    remote_agent_id = "ojw-client-1"
    remote_agent = openjiuwen_a2a_remote_agent_factory(
        agent_id=remote_agent_id,
        base_url=_server_base_url(server_id),
        app=server_app,
    )
    Runner.resource_mgr.add_agent(AgentCard(id=remote_agent_id), agent=remote_agent)

    try:
        invoke_query = "hello a2a"
        invoke_session_id = "conv-1"
        invoke_result = await Runner.run_agent(
            remote_agent_id,
            {"query": invoke_query, "conversation_id": invoke_session_id},
        )
        assert invoke_result.status == TaskStatus.SUBMITTED
        assert invoke_result.task_id
        assert invoke_result.sessionId == invoke_session_id
        assert invoke_result.task_id != invoke_result.sessionId
        assert not invoke_result.artifacts

        stream_query = "stream a2a"
        stream_session_id = "conv-2"
        chunks = []
        async for chunk in Runner.run_agent_streaming(
            remote_agent_id,
            {"query": stream_query, "conversation_id": stream_session_id},
        ):
            chunks.append(chunk)
        _assert_stream_result(chunks, server_id=server_id, query=stream_query, session_id=stream_session_id)
    finally:
        Runner.resource_mgr.remove_agent(agent_id=remote_agent_id)
        await server_adapter.stop()


@pytest.mark.asyncio
async def test_openjiuwen_agent_client_should_access_third_party_a2a_server_for_invoke_and_stream(
    started_runner,
    openjiuwen_a2a_remote_agent_factory,
):
    server_id = "tp-server-1"
    server_app, _ = _create_third_party_a2a_server(server_id)

    remote_agent_id = "ojw-client-2"
    remote_agent = openjiuwen_a2a_remote_agent_factory(
        agent_id=remote_agent_id,
        base_url=_server_base_url(server_id),
        app=server_app,
    )
    Runner.resource_mgr.add_agent(AgentCard(id=remote_agent_id), agent=remote_agent)

    try:
        invoke_query = "hello third-party"
        invoke_session_id = "conv-3"
        invoke_result = await Runner.run_agent(
            remote_agent_id,
            {"query": invoke_query, "conversation_id": invoke_session_id},
        )
        assert invoke_result.status == TaskStatus.SUBMITTED
        assert invoke_result.sessionId == invoke_session_id
        assert invoke_result.task_id
        assert invoke_result.task_id != invoke_result.sessionId
        assert not invoke_result.artifacts

        stream_query = "stream third-party"
        stream_session_id = "conv-4"
        chunks = []
        async for chunk in Runner.run_agent_streaming(
            remote_agent_id,
            {"query": stream_query, "conversation_id": stream_session_id},
        ):
            chunks.append(chunk)
        _assert_stream_result(chunks, server_id=server_id, query=stream_query, session_id=stream_session_id)
    finally:
        Runner.resource_mgr.remove_agent(agent_id=remote_agent_id)


@pytest.mark.asyncio
async def test_third_party_a2a_client_should_access_openjiuwen_server_for_invoke_and_stream():
    server_id = "ojw-server-2"
    server_adapter, server_app = _create_openjiuwen_a2a_server(server_id)
    client, httpx_client = _create_a2a_sdk_client(server_app, _rpc_url(server_id))

    try:
        invoke_query = "hello openjiuwen"
        invoke_session_id = "conv-5"
        invoke_result = await _collect_a2a_invoke_result(client, invoke_query, conversation_id=invoke_session_id)
        assert invoke_result.status == TaskStatus.COMPLETED
        assert invoke_result.sessionId == invoke_session_id
        assert invoke_result.task_id
        assert invoke_result.task_id != invoke_result.sessionId
        assert any(
            artifact.parts
            and artifact.parts[0].text
            and server_id in artifact.parts[0].text
            and invoke_query in artifact.parts[0].text
            for artifact in invoke_result.artifacts
        )

        stream_query = "stream openjiuwen"
        stream_session_id = "conv-6"
        chunks = await _collect_a2a_stream_chunks(client, stream_query, conversation_id=stream_session_id)
        _assert_stream_result(chunks, server_id=server_id, query=stream_query, session_id=stream_session_id)
        assert all(chunk.sessionId == stream_session_id for chunk in chunks)
    finally:
        await client.close()
        await httpx_client.aclose()
        await server_adapter.stop()


@pytest.mark.asyncio
async def test_openjiuwen_a2a_server_should_support_multiple_instances_in_one_process():
    specs = [
        ("ojw-multi-1", "conv-7", "conv-8"),
        ("ojw-multi-2", "conv-9", "conv-10"),
        ("ojw-multi-3", "conv-11", "conv-12"),
    ]

    servers: list[tuple[str, A2AServerAdapter, FastAPI, object, httpx.AsyncClient]] = []
    try:
        for server_id, _invoke_session_id, _stream_session_id in specs:
            adapter, app = _create_openjiuwen_a2a_server(server_id)
            client, httpx_client = _create_a2a_sdk_client(app, _rpc_url(server_id))
            servers.append((server_id, adapter, app, client, httpx_client))

            invoke_query = f"hello {server_id}"
            invoke_result = await _collect_a2a_invoke_result(client, invoke_query, conversation_id=_invoke_session_id)
            assert invoke_result.status == TaskStatus.COMPLETED
            assert invoke_result.sessionId == _invoke_session_id
            assert invoke_result.task_id
            assert invoke_result.task_id != invoke_result.sessionId
            assert any(
                artifact.parts
                and artifact.parts[0].text
                and server_id in artifact.parts[0].text
                and invoke_query in artifact.parts[0].text
                for artifact in invoke_result.artifacts
            )

            stream_query = f"stream {server_id}"
            chunks = await _collect_a2a_stream_chunks(client, stream_query, conversation_id=_stream_session_id)
            _assert_stream_result(chunks, server_id=server_id, query=stream_query, session_id=_stream_session_id)
    finally:
        for _, adapter, _, client, httpx_client in servers:
            await client.close()
            await httpx_client.aclose()
            await adapter.stop()
