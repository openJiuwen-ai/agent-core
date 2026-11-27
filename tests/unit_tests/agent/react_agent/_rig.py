# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared ReActAgent test rig.

Builds an agent whose context engine, model context, session, and model are all
mocked, so a test only has to script the model responses. Kept in one place so
that a change to the framework's context-engine API is a one-file edit.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def build_react_agent_rig(
    *responses: AssistantMessage,
    card_name: str = "react-agent-rig",
    max_iterations: int = 5,
    on_max_iterations: str | None = None,
) -> tuple[ReActAgent, Any, Any, list]:
    """Return ``(agent, session, model, messages)`` with the model scripted.

    ``responses`` are returned by successive ``model.invoke`` calls; a test may
    still replace ``model.invoke`` afterwards to raise or block instead.
    """
    agent = ReActAgent(card=AgentCard(name=card_name))
    config: dict[str, Any] = {"model_name": "fake", "max_iterations": max_iterations}
    if on_max_iterations is not None:
        config["on_max_iterations"] = on_max_iterations
    agent.configure(ReActAgentConfig(**config))

    messages: list = []
    context = MagicMock()
    context.get_messages.side_effect = lambda **_: list(messages)
    context.add_messages = AsyncMock(side_effect=lambda msg: messages.append(msg))

    async def window(**kwargs):
        return MagicMock(
            get_messages=lambda: kwargs["system_messages"] + list(messages),
            get_tools=lambda: kwargs["tools"],
        )

    context.get_context_window = AsyncMock(side_effect=window)
    engine = MagicMock()
    engine.create_context = AsyncMock(return_value=context)
    engine.save_contexts = AsyncMock()
    agent.context_engine = engine

    session = MagicMock()
    session.get_state.return_value = None
    session.write_stream = AsyncMock()

    model = MagicMock()
    model.invoke = AsyncMock(side_effect=list(responses))
    return agent, session, model, messages
