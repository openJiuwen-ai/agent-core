# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Helpers for driving a real DeepAgent through interrupt / resume rounds.

The model is scripted with ``MockLLMModel``; rails, the interrupt handler and
the resume path are the real ones.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Tuple
from unittest.mock import patch

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from tests.unit_tests.fixtures.mock_llm import MockLLMModel

os.environ.setdefault("LLM_SSL_VERIFY", "false")


class CountingTool(Tool):
    """Server tool that records how often it ran."""

    def __init__(self, name: str):
        super().__init__(ToolCard(
            id=name,
            name=name,
            description=f"{name} test tool",
            input_params={"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
        ))
        self.invoke_count = 0

    async def invoke(self, inputs, **kwargs):
        self.invoke_count += 1
        return {"ran": self.card.name, "invoke_count": self.invoke_count}

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


def fake_model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-fake-typed-interrupts",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="gpt-4o-mini"),
    )


def make_deep_agent(workspace, *, tools=None, rails=None, permissions=None, name: str = "TypedInterrupts"):
    from openjiuwen.harness.factory import create_deep_agent

    return create_deep_agent(
        model=fake_model(),
        card=AgentCard(id=f"{name}_{uuid.uuid4().hex[:8]}", name=name),
        workspace=str(workspace),
        tools=tools or [],
        rails=rails or [],
        permissions=permissions,
        enable_task_loop=False,
        max_iterations=10,
        system_prompt="Follow the scripted model.",
    )


def ask_permissions(*tool_names: str) -> dict:
    """Permissions config that asks for ``tool_names`` and allows everything else."""
    return {
        "enabled": True,
        "defaults": {"*": "allow"},
        "tools": {name: {"*": "ask"} for name in tool_names},
    }


@contextmanager
def scripted(mock_llm: MockLLMModel) -> Iterator[MockLLMModel]:
    """Route model calls to ``mock_llm``; the image-capability probe is disabled
    so it does not consume a scripted response."""
    with patch("openjiuwen.harness.deep_agent.schedule_image_support_probe", lambda *a, **k: None), \
            patch("openjiuwen.core.foundation.llm.model.Model.stream", side_effect=mock_llm.stream), \
            patch("openjiuwen.core.foundation.llm.model.Model.invoke", side_effect=mock_llm.invoke):
        yield mock_llm


async def run(agent, query: Any, conversation_id: str) -> dict:
    return await Runner.run_agent(agent=agent, inputs={"query": query, "conversation_id": conversation_id})


def pending(result: dict) -> List[Tuple[str, Any]]:
    """``[(tool_call_id, ToolCallInterruptRequest), ...]`` of an interrupt result."""
    if result.get("result_type") != "interrupt":
        return []
    return [(item.payload.id, item.payload.value) for item in result.get("state", [])]


def by_interrupt_id(value: Any, answer: Any) -> InteractiveInput:
    interactive_input = InteractiveInput()
    interactive_input.answer(value.interrupt_id, answer)
    return interactive_input


def by_call_id(tool_call_id: str, answer: Any) -> InteractiveInput:
    interactive_input = InteractiveInput()
    interactive_input.update(tool_call_id, answer)
    return interactive_input


APPROVE = {"approved": True, "feedback": "", "auto_confirm": False}


def _field(message: Any, name: str) -> Any:
    if isinstance(message, dict):
        return message.get(name)
    return getattr(message, name, None)


def tool_results_seen(mock_llm: MockLLMModel, call_index: int = -1) -> List[Tuple[Optional[str], Any]]:
    """``[(tool_call_id, content), ...]`` of the tool messages sent in one model call."""
    if not mock_llm.call_history:
        return []
    messages = mock_llm.call_history[call_index]
    return [
        (_field(message, "tool_call_id"), _field(message, "content"))
        for message in messages
        if _field(message, "role") == "tool"
    ]


def messages_seen(mock_llm: MockLLMModel, call_index: int = -1) -> list:
    """The messages sent in one model call."""
    if not mock_llm.call_history:
        return []
    return list(mock_llm.call_history[call_index])
