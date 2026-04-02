# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Hierarchical Tools Team Configuration Module."""
from pydantic import Field

from openjiuwen.core.multi_agent.config import TeamConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class HierarchicalTeamConfig(TeamConfig):
    """Configuration for HierarchicalTeam (agents-as-tools mode).

    Attributes:
        root_agent: Top-level entry agent AgentCard (required).
    """

    root_agent: AgentCard = Field(
        ...,
        description="Top-level entry agent (root), required.",
    )
