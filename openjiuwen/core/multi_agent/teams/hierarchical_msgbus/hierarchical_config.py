# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Hierarchical team configuration."""
from typing import Optional
from pydantic import Field

from openjiuwen.core.multi_agent.config import TeamConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class HierarchicalTeamConfig(TeamConfig):
    """Configuration for :class:`HierarchicalTeam`."""
    supervisor_agent: AgentCard = Field(
        ...,
        description="Top-level entry supervisor AgentCard (required)",
    )
    timeout: Optional[float] = Field(
        default=1800.0,
        description="Timeout in seconds for P2P message communication "
                    "(default: 1800s, enough for nested multi-agent LLM chains)",
    )