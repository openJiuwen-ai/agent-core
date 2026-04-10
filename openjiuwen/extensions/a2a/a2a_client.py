# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from collections.abc import AsyncIterator
from typing import Any, AsyncGenerator, Dict, Optional

from a2a.client import ClientConfig, ClientFactory
from a2a.client.client import ClientEvent
from a2a.types import SendMessageRequest, AgentCard

from openjiuwen.extensions.a2a.a2a_transformer import A2ATransformer
from openjiuwen.core.single_agent.schema.agent_result import AgentResult


class A2AClient:
    """Minimal A2A SDK wrapper for openjiuwen."""

    def __init__(
        self,
        card: Optional[AgentCard] = None,
    ):
        self.card = card

        try:
            factory = ClientFactory(ClientConfig())
            self.client = factory.create(self.card)
        except Exception as exc:
            raise RuntimeError(f"Failed to create A2A client: {exc}") from exc

    async def stop(self) -> None:
        await self.client.close()

    def _send_message(self, request: SendMessageRequest) -> AsyncIterator[ClientEvent]:
        return self.client.send_message(request)

    async def invoke(self, inputs: Dict[str, Any]) -> AgentResult:
        request = A2ATransformer.to_a2a_request(inputs)
        event_stream = self._send_message(request)
        latest = None

        async for event in event_stream:
            latest = event

        if latest is None:
            return AgentResult()

        return A2ATransformer.from_a2a_response(latest)

    async def stream(self, inputs: Dict[str, Any]) -> AsyncGenerator[AgentResult, None]:
        request = A2ATransformer.to_a2a_request(inputs)
        event_stream = self._send_message(request)
        async for event in event_stream:
            yield A2ATransformer.from_a2a_response(event)

    async def __aenter__(self):
        """Return the client itself for async context management."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close the client when leaving the async context."""
        await self.stop()
