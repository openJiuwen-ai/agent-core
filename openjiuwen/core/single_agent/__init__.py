# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Single Agent Module

This module provides exports for single agent functionality.
Legacy implementations are in the legacy/ directory and should be
imported from openjiuwen.core.single_agent.legacy explicitly.

For migration guide, see: docs/AGENT_MIGRATION_GUIDE.md

Note: Legacy classes have been moved to the legacy submodule.
Please use 'from openjiuwen.core.single_agent.legacy import ...' for
legacy classes like LegacyReActAgent, AgentConfig, etc.
"""
from typing import Union

# Legacy classes
from openjiuwen.core.single_agent.legacy import LegacyBaseAgent
# New classes (current API)
from openjiuwen.core.single_agent.agent import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.single_agent.agents.react_agent import (
    ReActAgent,
    ReActAgentConfig
)
from openjiuwen.core.session.agent import Session, create_agent_session

BaseAgentAlias = Union[BaseAgent, LegacyBaseAgent]

__all__ = [
    # New classes
    "AgentCard",
    "ReActAgent",
    "ReActAgentConfig",
    "Session",
    "create_agent_session",
    # For compatibility
    "BaseAgentAlias",
]
