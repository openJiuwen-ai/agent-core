# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Shared helpers for trajectory tests."""
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
from openjiuwen.core.session.tracer.tracer import Tracer
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


async def emit_llm_step(tracer: Tracer, content: str = "hi") -> None:
    """Emit one ``on_llm_end`` through `tracer` carrying fixed usage numbers."""
    span = tracer.tracer_agent_span_manager.create_agent_span()
    message = AssistantMessage(
        content=content,
        usage_metadata=UsageMetadata(model_name="m", input_tokens=3, output_tokens=1, total_tokens=4),
    )
    await tracer.trigger("tracer_agent", "on_llm_end", span=span, outputs={"outputs": message})


def make_react_agent(agent_id: str) -> ReActAgent:
    """A configured ``ReActAgent`` whose ``_get_llm`` is meant to be patched with a mock."""
    agent = ReActAgent(card=AgentCard(id=agent_id))
    config = ReActAgentConfig()
    config.configure_model_client(
        provider="OpenAI",
        api_key="mock-api-key",
        api_base="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        verify_ssl=False,
    )
    config.configure_prompt_template([{"role": "system", "content": "You are a helpful assistant."}])
    agent.configure(config)
    return agent
