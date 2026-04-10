# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from typing import AsyncGenerator, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.runner.drunner.remote_client.a2a_remote_client import A2ARemoteClient
from openjiuwen.core.runner.drunner.remote_client.mq_remote_clent import MqRemoteClient
from openjiuwen.core.runner.drunner.remote_client.remote_client import RemoteClient
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import RemoteClientConfig, ProtocolEnum
from openjiuwen.core.runner.runner_config import get_runner_config
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class RemoteAgent:

    def __init__(self, agent_id: str, version: str = "", description: str = None, topic: str = None,
                 protocol: str = ProtocolEnum.MQ, config: dict = None, card: AgentCard = None):
        self.agent_id = agent_id
        self.version = version
        self.description = description
        self.card = card
        # Use template if topic not provided
        self.topic = topic or get_runner_config().agent_topic_template().format(agent_id=agent_id,
                                                                                version=self.version)
        self.protocol = protocol
        self.config = RemoteClientConfig(id=agent_id, protocol=protocol, topic=self.topic, **(config or {}))
        self.client = self._create_client()

    def _create_client(self) -> RemoteClient | None:
        if self.protocol == ProtocolEnum.MQ:
            client = MqRemoteClient(config=self.config)
            return client
        if self.protocol == ProtocolEnum.A2A:
            client = A2ARemoteClient(config=self.config, card=self.card)
            return client
        return None

    async def invoke(self, inputs: dict, timeout: float = None):
        try:
            await self.client.start()
            return await self.client.invoke(inputs, timeout=timeout)
        except asyncio.CancelledError as e:
            # Timeout cancellation call set externally
            raise build_error(StatusCode.REMOTE_AGENT_EXECUTION_ERROR, cause=e, agent_id=self.agent_id,
                              reason="cancelled")
        except TimeoutError as e:
            raise build_error(StatusCode.REMOTE_AGENT_EXECUTION_TIMEOUT, cause=e, agent_id=self.agent_id,
                              timeout=timeout)

    async def stream(self, inputs: dict, timeout: float = None) -> AsyncGenerator:
        try:
            await self.client.start()
            async for chunk in self.client.stream(inputs, timeout=timeout):
                yield chunk
        except asyncio.CancelledError as e:
            # Runner stop causes client cancellation
            raise build_error(StatusCode.REMOTE_AGENT_EXECUTION_ERROR, cause=e, agent_id=self.agent_id,
                              reason="cancelled")
        except TimeoutError as e:
            raise build_error(StatusCode.REMOTE_AGENT_EXECUTION_TIMEOUT, cause=e, agent_id=self.agent_id,
                              timeout=timeout)
