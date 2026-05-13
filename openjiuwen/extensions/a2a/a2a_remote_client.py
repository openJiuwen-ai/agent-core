# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from typing import Any, AsyncGenerator, Dict

from a2a.types import AgentCard as A2AAgentCard

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.drunner.remote_client.remote_client import RemoteClient
from openjiuwen.core.runner.drunner.remote_client.remote_client_config import RemoteClientConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.schema.agent_result import AgentResult
from openjiuwen.extensions.a2a.a2a_agentcard_adapter import A2AAgentCardAdapter
from openjiuwen.extensions.a2a.a2a_client import A2AClient


class A2ARemoteClient(RemoteClient):
    """A2A remote client plugin implementation."""

    def __init__(self, config: RemoteClientConfig):
        self.config = config
        self._started = False

        try:
            card = self._resolve_a2a_card()
            if card is None:
                raise ValueError("card is required when protocol is A2A")
            polling = bool(self.config.kwargs.get("polling", False))
            self.client = A2AClient(card=card, polling=polling)
            logger.info(f"[A2ARemoteClient] Initialized client for {config.id}, url={config.url}")
        except Exception as exc:
            logger.error(f"[A2ARemoteClient] Failed to initialize client for {config.id}: {exc}")
            raise

    def _resolve_a2a_card(self) -> A2AAgentCard | None:
        card = self.config.kwargs.get("card")
        if card is None:
            return None
        if not isinstance(card, AgentCard):
            raise ValueError("card in config.kwargs must be an openjiuwen AgentCard")
        return self._convert_to_a2a_card(card)

    def _convert_to_a2a_card(self, card: AgentCard) -> A2AAgentCard:
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

    @staticmethod
    def _resolve_session_id(inputs: Dict[str, Any]) -> str | None:
        session_id = inputs.get("conversation_id") or inputs.get("sessionId")
        return str(session_id) if session_id is not None else None

    @staticmethod
    def _with_session_id(result: AgentResult, session_id: str | None) -> AgentResult:
        if session_id is None:
            return result
        return result.model_copy(update={"sessionId": session_id})

    async def start(self):
        """Mark the remote client as started."""
        self._started = True
        logger.debug(f"[A2ARemoteClient] Started client for {self.config.id}")

    def is_started(self) -> bool:
        return self._started

    async def stop(self):
        """Stop the underlying client if it has been started."""
        if self._started:
            await self.client.stop()
            self._started = False
            logger.debug(f"[A2ARemoteClient] Stopped client for {self.config.id}")

    async def invoke(self, inputs: Dict[str, Any], timeout: float = None) -> AgentResult:
        """Invoke the remote A2A agent and return an AgentResult."""
        logger.debug(f"[A2ARemoteClient] Invoke {self.config.id}")
        session_id = self._resolve_session_id(inputs)

        try:
            invoke_coro = self.client.invoke(inputs=inputs)
            if timeout:
                return self._with_session_id(await asyncio.wait_for(invoke_coro, timeout=timeout), session_id)
            return self._with_session_id(await invoke_coro, session_id)
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
        session_id = self._resolve_session_id(inputs)

        try:
            if timeout:
                async with asyncio.timeout(timeout):
                    async for chunk in self.client.stream(inputs=inputs):
                        yield self._with_session_id(chunk, session_id)
            else:
                async for chunk in self.client.stream(inputs=inputs):
                    yield self._with_session_id(chunk, session_id)
        except asyncio.TimeoutError:
            logger.error(f"[A2ARemoteClient] Stream timeout for {self.config.id}")
            await self.stop()
            raise
        except Exception as exc:
            logger.error(f"[A2ARemoteClient] Stream failed for {self.config.id}: {exc}")
            await self.stop()
            raise

    async def cancel_task(self, task_id: str, tenant: str | None = None) -> AgentResult:
        """Request cancellation of a remote A2A task."""
        logger.debug(f"[A2ARemoteClient] Cancel task {task_id} for {self.config.id}")

        try:
            return await self.client.cancel_task(task_id, tenant=tenant)
        except Exception as exc:
            logger.error(f"[A2ARemoteClient] Cancel task failed for {self.config.id}: {exc}")
            await self.stop()
            raise
