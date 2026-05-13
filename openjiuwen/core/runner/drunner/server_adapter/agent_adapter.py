# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, AsyncIterator

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.runner.drunner.server_adapter import create_server_adapter
from openjiuwen.core.runner.drunner.server_adapter.mq_server_adapter import MqServerAdapter
from openjiuwen.core.runner.runner_config import get_runner_config


class AgentAdapter:
    """AgentAdapter"""

    def __init__(
        self,
        agent_id: str,
        version: str = "",
        agent_card: AgentCard | None = None,
    ):
        self.agent_id = agent_id
        self.version = version
        self.agent_card = agent_card
        self.interface_url = agent_card.interface_url if agent_card is not None else None
        enable_a2a = get_runner_config().enable_a2a
        self.enable_a2a = enable_a2a
        if enable_a2a and agent_card is None:
            raise ValueError("agent_card is required when enable_a2a is True")
        self.topic = get_runner_config().agent_topic_template().format(agent_id=agent_id, version=version)

        self.server = MqServerAdapter(
            adapter_id=agent_id,
            topic=self.topic,
            invoke_handler=self._handle_invoke,
            stream_handler=self._handle_stream
        )

        self.a2a_server = None
        if enable_a2a:
            self.a2a_server = create_server_adapter(
                "A2A",
                adapter_id=agent_id,
                version=version,
                agent_card=agent_card,
                invoke_handler=self._handle_invoke,
                stream_handler=self._handle_stream,
            )
            if self.a2a_server is None:
                raise RuntimeError("failed to create server adapter for A2A")

    def start(self) -> None:
        self.server.start()
        if self.a2a_server is not None:
            self.a2a_server.start()

    async def stop(self):
        await self.server.stop()
        if self.a2a_server is not None:
            await self.a2a_server.stop()

    async def _handle_invoke(self, inputs: dict) -> Any:
        from openjiuwen.core.runner import Runner
        agent_result = await Runner.run_agent(self.agent_id, inputs)
        return agent_result

    async def _handle_stream(self, inputs: dict) -> AsyncIterator[Any]:
        from openjiuwen.core.runner import Runner
        async for item in Runner.run_agent_streaming(self.agent_id, inputs):
            yield item
