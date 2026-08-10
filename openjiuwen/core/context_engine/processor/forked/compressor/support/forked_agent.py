from __future__ import annotations

import uuid
from typing import Any, Iterable

from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
    CompressionExecutor,
    CompressionRequest,
    CompressionResult,
    classify_compression_error,
)
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.core.session.agent import create_agent_session


class ForkedAgent(CompressionExecutor):
    """Compression executor backed by an isolated ReActAgent.

    The class deliberately owns the Agent lifecycle but not the ReAct loop.
    The loop, model calls, tool-call parsing and tool-message handling remain
    implemented by ReActAgent.
    """

    def __init__(
        self,
        model: Any,
        *,
        model_config: Any,
        model_client_config: Any,
        agent_name: str,
        agent_description: str,
        prompt_template: list[dict[str, str]] | None = None,
        max_iterations: int = 3,
    ) -> None:
        super().__init__(model)
        self._model_config = model_config
        self._model_client_config = model_client_config
        self._agent_name = agent_name
        self._agent_description = agent_description
        self._prompt_template = list(prompt_template or [])
        self._max_iterations = max_iterations
        self._agent: ReActAgent | None = None
        self._agent_card: AgentCard | None = None
        self._agent_namespace = f"{agent_name}_{uuid.uuid4().hex}"

    @property
    def agent(self) -> ReActAgent | None:
        return self._agent

    def _ensure_agent(self) -> ReActAgent:
        if self._agent is not None:
            return self._agent

        card = AgentCard(
            id=f"{self._agent_namespace}_agent",
            name=self._agent_name,
            description=self._agent_description,
        )
        config = ReActAgentConfig(
            model_name=getattr(self._model_config, "model_name", ""),
            model_client_config=self._model_client_config,
            model_config_obj=self._model_config,
            prompt_template=list(self._prompt_template),
            max_iterations=self._max_iterations,
        )
        agent = ReActAgent(card=card).configure(config)
        agent.set_llm(self._model)
        self._agent_card = card
        self._agent = agent
        self._configure_agent(agent)
        return agent

    def _configure_agent(self, agent: ReActAgent) -> None:
        """Hook for subclasses to install an isolated ability manager."""
        _ = agent

    async def invoke(self, request: CompressionRequest) -> CompressionResult:
        agent = self._ensure_agent()
        seed_messages = self.build_context_messages(request)
        agent_session = self._create_agent_session()
        inputs = {
            "query": request.prompt,
            "conversation_id": self._agent_namespace,
        }

        try:
            await agent_session.pre_run(inputs=inputs)
            await self._prime_context(agent, agent_session, seed_messages)
            response = await agent.invoke(inputs, session=agent_session)
        except Exception as exc:
            raise classify_compression_error(exc) from exc
        finally:
            await agent_session.close_stream()
            await agent_session.commit()

        self._last_response = response
        return CompressionResult(
            response=response,
            usage=self._extract_usage(response),
        )

    def _create_agent_session(self):
        if self._agent_card is None:
            raise RuntimeError("Forked agent card is not initialized")
        return create_agent_session(
            session_id=f"{self._agent_namespace}_{uuid.uuid4().hex[:8]}",
            card=self._agent_card,
        )

    @staticmethod
    async def _prime_context(
        agent: ReActAgent,
        session: Any,
        seed_messages: Iterable[BaseMessage],
    ) -> None:
        init_context = getattr(agent, "_init_context", None)
        if init_context is None:
            raise RuntimeError("ReActAgent does not support context initialization")
        context = await init_context(session)
        if context.get_messages():
            raise RuntimeError("forked agent context must be empty before seeding")
        messages = list(seed_messages)
        if messages:
            await context.add_messages(messages)

    @staticmethod
    def _extract_usage(response: Any) -> Any:
        if isinstance(response, dict):
            return response.get("usage") or response.get("usage_metadata")
        return getattr(response, "usage_metadata", None) or getattr(response, "usage", None)
