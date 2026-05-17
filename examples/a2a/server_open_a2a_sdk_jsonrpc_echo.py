# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A2A JSON-RPC server built with **a2a-sdk** (1.0.0 style routes; see ``pyproject.toml`` ``all-a2a``).

Uses ``create_agent_card_routes`` + ``create_jsonrpc_routes`` (no legacy ``a2a.server.apps``).

**Terminal 1 (this file)**::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/server_open_a2a_sdk_jsonrpc_echo.py

**Terminal 2 (jiuwen client)**::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/client_jiuwen_to_open_a2a_sdk_server.py

Keep ``LISTEN_HOST`` / ``LISTEN_PORT`` / ``AGENT_CARD_NAME`` aligned with the client module constants.
"""

from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus as A2ATaskStatus,
)

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8771
INTERFACE_URL = f"http://{LISTEN_HOST}:{LISTEN_PORT}/a2a/jsonrpc/"
AGENT_CARD_NAME = "open-a2a-sdk-echo"


class EchoExecutor(AgentExecutor):
    """Minimal executor: SUBMITTED → WORKING → artifact with echo → completed."""

    def __init__(self) -> None:
        self._running: set[str] = set()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or ""
        self._running.discard(task_id)
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context.context_id or "",
        )
        await updater.cancel()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_message = context.message
        task_id = context.task_id
        context_id = context.context_id

        if not user_message or not task_id or not context_id:
            return

        self._running.add(task_id)
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id,
        )

        try:
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
            await asyncio.sleep(0.05)

            if task_id not in self._running:
                return

            await updater.add_artifact(
                parts=[Part(text=f"[open-a2a-sdk] echo: {query}")],
                name="response",
                last_chunk=True,
            )
            await updater.complete()
        finally:
            self._running.discard(task_id)


def build_app() -> FastAPI:
    app = FastAPI(title="open-a2a-sdk echo")
    agent_card = AgentCard(
        name=AGENT_CARD_NAME,
        description="Reference A2A server for jiuwen A2A client interop",
        provider=AgentProvider(organization="example", url="https://example.com"),
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text"],
        default_output_modes=["text", "task-status"],
        skills=[
            AgentSkill(
                id="echo",
                name="Echo",
                description="Echo user text",
                tags=["example"],
                examples=["hello"],
                input_modes=["text"],
                output_modes=["text", "task-status"],
            )
        ],
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=INTERFACE_URL,
            ),
        ],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=EchoExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

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
    return app


def main() -> None:
    print(f"open-a2a-sdk JSON-RPC at {INTERFACE_URL} — Ctrl+C to stop.")
    uvicorn.run(
        build_app(),
        host=LISTEN_HOST,
        port=LISTEN_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
