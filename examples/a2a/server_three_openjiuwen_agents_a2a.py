# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Single process, **three** openjiuwen ``BaseAgent`` instances as **three A2A JSON-RPC servers** (distinct ports).

Each agent uses ``AgentCard(..., interface_url=...)`` so ``AgentAdapter`` exposes the right JSON-RPC URL.
``AgentAdapter`` starts a separate uvicorn thread per agent (``distributed_mode`` + ``enable_a2a``).

**Process 1 (this file)**::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/server_three_openjiuwen_agents_a2a.py

**Process 2 (a2a-sdk client)**::

    PYTHONPATH=. uv run --extra all-a2a python examples/a2a/client_open_a2a_sdk_three_jiuwen_servers.py

Ports and agent ids must stay in sync with the client module.
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
# Three listeners — client must use the same bases (see client script).
AGENT_SERVERS: tuple[tuple[str, int], ...] = (
    ("jiuwen-a2a-multi-1", 8781),
    ("jiuwen-a2a-multi-2", 8782),
    ("jiuwen-a2a-multi-3", 8783),
)


def _interface_url(port: int) -> str:
    return f"http://{LISTEN_HOST}:{port}/a2a/jsonrpc/"


class MultiPortEchoAgent(BaseAgent):
    """Echo agent; reply prefix includes ``card.id`` so you can tell which server answered."""

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
                    parts=[Part(text=f"[{self.card.id}] {q}")],
                )
            ],
        )

    async def stream(self, inputs, session=None, stream_modes=None):
        yield await self.invoke(inputs, session)


def _require_ok(result, what: str) -> None:
    if result.is_err():
        raise RuntimeError(f"{what}: {result.error()}") from result.error()


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
    await Runner.start()
    try:
        for agent_id, port in AGENT_SERVERS:
            iface = _interface_url(port)
            card = AgentCard(id=agent_id, name=agent_id, interface_url=iface)
            _require_ok(
                Runner.resource_mgr.add_agent(
                    card,
                    lambda c=card: MultiPortEchoAgent(c),
                ),
                f"add_agent({agent_id})",
            )
            print(f"A2A ready: agent_id={agent_id!r} — {iface}")

        print("Three servers running — Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Normal when the event loop stops on SIGINT (Ctrl+C); ``finally`` still runs ``Runner.stop``.
            pass
    finally:
        await Runner.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
