# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Local ``BaseAgent`` delegates to an A2A ``RemoteAgent`` (invoke + stream).

**Server (terminal 1)** — paired script (keep host/port/agent id in sync below)::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/server_local_agent_delegates_remote_a2a.py

**Client (terminal 2)** — this script::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/client_local_agent_delegates_remote_a2a.py

The local agent id is ``LOCAL_AGENT_ID``. It forwards ``Runner.run_agent`` /
``Runner.run_agent_streaming`` to the remote id, which uses the A2A protocol.

Remote ``invoke`` returns the aggregated final ``AgentResult`` (``COMPLETED`` + artifacts);
``stream`` exposes incremental A2A events.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from example_utils import print_invoke_result, print_stream_chunk

from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.drunner.remote_client.remote_agent import RemoteAgent
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import ProtocolEnum
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

# Keep in sync with ``examples/a2a/server_local_agent_delegates_remote_a2a.py``.
REMOTE_BASE_URL = "http://127.0.0.1:8767"
REMOTE_AGENT_ID = "minimal-a2a-core-server"
LOCAL_AGENT_ID = "local-client-forwarder"


class DelegatingClientAgent(BaseAgent):
    """A normal single agent whose ``invoke`` / ``stream`` call a remote agent by id."""

    def configure(self, config):
        self._config = config
        return self

    async def invoke(self, inputs, session=None):
        return await Runner.run_agent(self._config.remote_agent_id, inputs)

    async def stream(self, inputs, session=None, stream_modes=None):
        async for chunk in Runner.run_agent_streaming(self._config.remote_agent_id, inputs):
            yield chunk


def _require_ok(result, what: str) -> None:
    if result.is_err():
        raise RuntimeError(f"{what}: {result.error()}") from result.error()


async def main() -> None:
    remote_card = AgentCard(id=REMOTE_AGENT_ID, name=REMOTE_AGENT_ID)
    remote = RemoteAgent(
        agent_id=REMOTE_AGENT_ID,
        protocol=ProtocolEnum.A2A,
        config={
            "url": REMOTE_BASE_URL,
            "kwargs": {"card": remote_card},
        },
    )

    local_card = AgentCard(id=LOCAL_AGENT_ID, name=LOCAL_AGENT_ID)
    local_provider = lambda: DelegatingClientAgent(local_card).configure(
        SimpleNamespace(remote_agent_id=REMOTE_AGENT_ID),
    )

    await Runner.start()
    try:
        _require_ok(Runner.resource_mgr.add_agent(remote_card, remote), "add_agent(remote)")
        _require_ok(Runner.resource_mgr.add_agent(local_card, local_provider), "add_agent(local)")

        invoke_query = "hello from local agent"
        inv = await Runner.run_agent(
            LOCAL_AGENT_ID,
            {"query": invoke_query, "conversation_id": "session-delegate-1"},
        )
        print_invoke_result(inv, label="invoke via local agent", expect_text=invoke_query)

        print("--- stream via local agent ---")
        index = 0
        async for chunk in Runner.run_agent_streaming(
            LOCAL_AGENT_ID,
            {"query": "hello stream", "conversation_id": "session-delegate-2"},
        ):
            print_stream_chunk(f"stream[{index}]", chunk)
            index += 1
    finally:
        await Runner.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
