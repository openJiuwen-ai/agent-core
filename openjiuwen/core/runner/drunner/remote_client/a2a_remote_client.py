# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from typing import Any, AsyncGenerator, Dict, Optional

from a2a.types import AgentCard as A2AAgentCard

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.drunner.remote_client.remote_client import RemoteClient
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import RemoteClientConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.schema.agent_result import AgentResult
from openjiuwen.extensions.a2a.a2a_agentcard_adapter import A2AAgentCardAdapter
from openjiuwen.extensions.a2a.a2a_client import A2AClient


class A2ARemoteClient(RemoteClient):
    """Minimal remote client for the A2A protocol."""

    def __init__(self, config: RemoteClientConfig, card: Optional[AgentCard]):
        self.config = config
        self.card = card
        self._started = False

        try:
            a2a_card = self._convert_to_a2a_card(card)
            self.client = A2AClient(card=a2a_card)
            logger.info(f"[A2ARemoteClient] Initialized client for {config.id}, url={config.url}")
        except Exception as exc:
            logger.error(f"[A2ARemoteClient] Failed to initialize client for {config.id}: {exc}")
            raise

    def _convert_to_a2a_card(self, card: AgentCard) -> Optional[A2AAgentCard]:
        """Convert an openjiuwen AgentCard to an A2A AgentCard."""
        interface_url = None
        if self.config.url:
            normalized = self.config.url.rstrip("/")
            if normalized.endswith("/a2a/jsonrpc"):
                interface_url = normalized + "/"
            else:
                interface_url = f"{normalized}/a2a/jsonrpc/"

        return A2AAgentCardAdapter.to_a2a_agent_card(
            card,
            interface_url=interface_url,
            protocol_binding="JSONRPC",
            protocol_version="1.0",
        )

    async def start(self):
        """Mark the remote client as started."""
        self._started = True
        logger.debug(f"[A2ARemoteClient] Started client for {self.config.id}")

    async def stop(self):
        """Stop the underlying client if it has been started."""
        if self._started:
            await self.client.stop()
            self._started = False
            logger.debug(f"[A2ARemoteClient] Stopped client for {self.config.id}")

    async def invoke(self, inputs: Dict[str, Any], timeout: float = None) -> AgentResult:
        """Invoke the remote A2A agent and return an AgentResult."""
        logger.debug(f"[A2ARemoteClient] Invoke {self.config.id}")

        try:
            invoke_coro = self.client.invoke(inputs=inputs)
            if timeout:
                return await asyncio.wait_for(invoke_coro, timeout=timeout)
            return await invoke_coro
        except asyncio.TimeoutError:
            logger.error(f"[A2ARemoteClient] Invoke timeout for {self.config.id}")
            await self.stop()
            raise
        except Exception as exc:
            logger.error(f"[A2ARemoteClient] Invoke failed for {self.config.id}: {exc}")
            await self.stop()
            raise

    async def stream(self, inputs: Dict[str, Any],
                     timeout: float = None) -> AsyncGenerator[AgentResult, None]:
        """Stream AgentResult chunks from the remote A2A agent."""
        logger.debug(f"[A2ARemoteClient] Stream {self.config.id}")

        try:
            if timeout:
                async with asyncio.timeout(timeout):
                    async for chunk in self.client.stream(inputs=inputs):
                        yield chunk
            else:
                async for chunk in self.client.stream(inputs=inputs):
                    yield chunk
        except asyncio.TimeoutError:
            logger.error(f"[A2ARemoteClient] Stream timeout for {self.config.id}")
            await self.stop()
            raise
        except Exception as exc:
            logger.error(f"[A2ARemoteClient] Stream failed for {self.config.id}: {exc}")
            await self.stop()
            raise
