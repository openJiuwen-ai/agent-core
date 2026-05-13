# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any, AsyncGenerator, Dict, Optional

from a2a.client import ClientConfig, ClientFactory
from a2a.types import AgentCard, SendMessageRequest, StreamResponse, CancelTaskRequest

from openjiuwen.extensions.a2a.a2a_transformer import A2ATransformer
from openjiuwen.core.single_agent.schema.agent_result import AgentResult


class A2AClient:
    """Minimal A2A SDK wrapper for openjiuwen."""

    def __init__(
        self,
        card: Optional[AgentCard] = None,
        polling: bool = False,
    ):
        self.card = card
        self.polling = polling

        try:
            config = ClientConfig()
            config.polling = polling
            factory = ClientFactory(config)
            self.client = factory.create(self.card)
        except Exception as exc:
            raise RuntimeError(f"Failed to create A2A client: {exc}") from exc

    async def stop(self) -> None:
        await self.client.close()

    def _send_message(self, request: SendMessageRequest) -> AsyncIterator[StreamResponse]:
        return self.client.send_message(request)

    @staticmethod
    def _resolve_session_id(inputs: Dict[str, Any]) -> str | None:
        session_id = inputs.get("conversation_id") or inputs.get("sessionId")
        return str(session_id) if session_id is not None else None

    @staticmethod
    def _with_session_id(result: AgentResult, session_id: str | None) -> AgentResult:
        if session_id is None:
            return result
        return result.model_copy(update={"sessionId": session_id})

    async def invoke(self, inputs: Dict[str, Any]) -> AgentResult:
        request = A2ATransformer.to_a2a_request(inputs)
        session_id = self._resolve_session_id(inputs)
        event_stream = self._send_message(request)
        async with aclosing(event_stream):
            first_event = await anext(event_stream, None)
            if first_event is not None:
                return self._with_session_id(A2ATransformer.from_a2a_response(first_event), session_id)
        return self._with_session_id(AgentResult(), session_id)

    async def stream(self, inputs: Dict[str, Any]) -> AsyncGenerator[AgentResult, None]:
        request = A2ATransformer.to_a2a_request(inputs)
        session_id = self._resolve_session_id(inputs)
        event_stream = self._send_message(request)
        async for event in event_stream:
            yield self._with_session_id(A2ATransformer.from_a2a_response(event), session_id)

    async def cancel_task(self, task_id: str, tenant: str | None = None) -> AgentResult:
        request = CancelTaskRequest(id=task_id)
        if tenant is not None:
            request.tenant = tenant
        task = await self.client.cancel_task(request)
        return A2ATransformer.from_a2a_response(task)

    async def __aenter__(self):
        """Return the client itself for async context management."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close the client when leaving the async context."""
        await self.stop()
