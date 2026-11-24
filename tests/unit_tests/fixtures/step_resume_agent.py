# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ReActAgent factory with a fixed card id for step-resume tests.

The card id becomes the agent id, which is part of the step record key. It must
be stable across a simulated restart, so callers pass it in explicitly instead
of letting ``AgentCard`` generate one.
"""
import os

from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from tests.unit_tests.core.single_agent.rail.test_rail import (
    _create_add_tool,
    _create_client_config,
    _create_model_config,
    _create_prompt_template,
)


def make_step_agent(
    card_id: str,
    *,
    max_iterations: int = 5,
    enable_iteration_checkpoint: bool = True,
) -> ReActAgent:
    """Create a ReActAgent whose agent id equals ``card_id``."""
    os.environ.setdefault("LLM_SSL_VERIFY", "false")
    card = AgentCard(id=card_id, description="step resume")
    config = ReActAgentConfig(
        model_config_obj=_create_model_config(),
        model_client_config=_create_client_config(),
        prompt_template=_create_prompt_template(),
        max_iterations=max_iterations,
        context_engine_config=ContextEngineConfig(
            enable_iteration_checkpoint=enable_iteration_checkpoint
        ),
    )
    agent = ReActAgent(card=card).configure(config)
    tool = _create_add_tool()
    agent.ability_manager.add(tool.card)
    from openjiuwen.core.runner import Runner
    if Runner.resource_mgr.get_tool(tool.card.id) is None:
        Runner.resource_mgr.add_tool(tool)
    return agent
