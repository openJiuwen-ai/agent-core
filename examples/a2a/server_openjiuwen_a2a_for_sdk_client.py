# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""openjiuwen **Runner + AgentAdapter** A2A entry for a third-party **a2a-sdk** JSON-RPC client.

``distributed_mode=True`` and ``enable_a2a=True`` register an ``AgentAdapter`` that exposes the
same JSON-RPC surface as ``A2AServer``, forwarding to ``Runner.run_agent`` / ``run_agent_streaming``
for the local ``BaseAgent`` below.

**Terminal 1 (this file)**::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/server_openjiuwen_a2a_for_sdk_client.py

**Terminal 2 (a2a-sdk client)**::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/client_open_a2a_sdk_to_jiuwen_server.py

``LISTEN_PORT`` and ``AGENT_ID`` must match the client module constants.
"""

from __future__ import annotations

import asyncio

from openjiuwen.core.controller.schema.task import TaskStatus
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.runner_config import (
    DistributedConfig,
    MessageQueueConfig,
    MessageQueueType,
    RunnerConfig,
    set_runner_config,
)
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.schema.agent_result import AgentResult, Artifact, Part

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8772
# ``A2AServer`` normalizes JSON-RPC path and agent-card URL to a trailing slash (avoids HTTP 307).
INTERFACE_URL = f"http://{LISTEN_HOST}:{LISTEN_PORT}/a2a/jsonrpc/"
AGENT_ID = "jiuwen-a2a-sdk-target"


class SdkTargetEchoAgent(BaseAgent):
    """Local agent invoked by ``AgentAdapter`` over A2A (same behavior as the former ``A2AServer`` handler)."""

    def configure(self, config):
        self._config = config
        return self

    async def invoke(self, inputs, session=None):
        q = str(inputs.get("query", "")) if isinstance(inputs, dict) else ""
        return AgentResult(
            status=TaskStatus.COMPLETED,
            artifacts=[
                Artifact(
                    name="response",
                    parts=[Part(text=f"[openjiuwen Runner+AgentAdapter] {q}")],
                )
            ],
        )

    async def stream(self, inputs, session=None, stream_modes=None):
        yield await self.invoke(inputs, session)


async def main() -> None:
    set_runner_config(
        RunnerConfig(
            distributed_mode=True,
            enable_a2a=True,
            distributed_config=DistributedConfig(
                message_queue_config=MessageQueueConfig(type=MessageQueueType.FAKE),
            ),
        )
    )
    card = AgentCard(id=AGENT_ID, name=AGENT_ID, interface_url=INTERFACE_URL)
    await Runner.start()
    try:
        result = Runner.resource_mgr.add_agent(
            card,
            lambda: SdkTargetEchoAgent(card),
        )
        if result.is_err():
            raise result.error()

        print(f"A2A (Runner + AgentAdapter) at {INTERFACE_URL} — Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
    finally:
        await Runner.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
