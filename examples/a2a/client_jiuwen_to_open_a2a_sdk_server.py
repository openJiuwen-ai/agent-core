# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""jiuwen **RemoteAgent** (A2A protocol) talking to a third-party **a2a-sdk** JSON-RPC server (invoke + stream).

``RemoteAgent`` with ``ProtocolEnum.A2A`` wraps ``A2ARemoteClient``; you can also register the same
instance on ``Runner.resource_mgr`` and use ``Runner.run_agent`` / ``run_agent_streaming``.

``invoke`` drains the underlying A2A SSE event stream and returns the aggregated final result
(``status=COMPLETED`` with echo artifacts). ``stream`` yields each SSE event as it arrives.

**Terminal 1** — start the reference server::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/server_open_a2a_sdk_jsonrpc_echo.py

**Terminal 2** — this script::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/client_jiuwen_to_open_a2a_sdk_server.py

``REMOTE_BASE_URL`` / ``REMOTE_AGENT_ID`` must match ``server_open_a2a_sdk_jsonrpc_echo.py``.
The openjiuwen ``AgentCard`` id is local metadata for the client; use a stable string aligned with
your registration / tests (here same as ``AGENT_CARD_NAME`` on the server for clarity).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from example_utils import print_invoke_result, print_stream_chunk

from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import ProtocolEnum
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

# Must match examples/a2a/server_open_a2a_sdk_jsonrpc_echo.py
REMOTE_BASE_URL = "http://127.0.0.1:8771"
REMOTE_AGENT_ID = "open-a2a-sdk-echo"


async def main() -> None:
    card = AgentCard(id=REMOTE_AGENT_ID, name=REMOTE_AGENT_ID)
    remote = RemoteAgent(
        agent_id=REMOTE_AGENT_ID,
        protocol=ProtocolEnum.A2A,
        config={
            "url": REMOTE_BASE_URL,
            "kwargs": {"card": card},
        },
    )
    try:
        invoke_query = "hello from jiuwen client"
        result = await remote.invoke(
            {"query": invoke_query, "conversation_id": "session-open-sdk-1"},
        )
        print_invoke_result(result, expect_text=invoke_query)

        print("--- stream ---")
        index = 0
        async for chunk in remote.stream(
            {"query": "hello stream", "conversation_id": "session-open-sdk-2"},
        ):
            print_stream_chunk(f"stream[{index}]", chunk)
            index += 1
    finally:
        await remote.client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
