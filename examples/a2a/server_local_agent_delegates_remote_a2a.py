# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A2A echo server for ``client_local_agent_delegates_remote_a2a.py``.

**Terminal 1 (this file)**::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/server_local_agent_delegates_remote_a2a.py

**Terminal 2 (client)**::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/client_local_agent_delegates_remote_a2a.py

``LISTEN_PORT`` / ``AGENT_ID`` must stay aligned with the client module constants
``REMOTE_BASE_URL`` and ``REMOTE_AGENT_ID``.
"""

from __future__ import annotations

import asyncio

from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.schema.agent_result import AgentResult, Artifact, Part
from openjiuwen.extensions.a2a.a2a_server import A2AServer

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8767
INTERFACE_URL = f"http://{LISTEN_HOST}:{LISTEN_PORT}/a2a/jsonrpc/"
AGENT_ID = "minimal-a2a-core-server"


async def invoke_handler(payload: dict) -> AgentResult:
    q = str(payload.get("query", ""))
    return AgentResult(
        status=TaskStatus.COMPLETED,
        artifacts=[Artifact(parts=[Part(text=f"[A2AServer] {q}")])],
    )


async def main() -> None:
    card = AgentCard(id=AGENT_ID, name=AGENT_ID, interface_url=INTERFACE_URL)
    server = A2AServer(
        agent_card=card,
        adapter_id=AGENT_ID,
        invoke_handler=invoke_handler,
        rpc_url="/a2a/jsonrpc/",
    )
    print(f"Paired A2A server at {INTERFACE_URL} — Ctrl+C to stop.")
    try:
        await server.start(host=LISTEN_HOST, port=LISTEN_PORT, log_level="info")
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
