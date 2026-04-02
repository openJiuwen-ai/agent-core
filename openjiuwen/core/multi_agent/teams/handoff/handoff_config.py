# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Handoff configuration classes for HandoffTeam."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from pydantic import Field

from openjiuwen.core.multi_agent.config import TeamConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@dataclass(frozen=True)
class HandoffRoute:
    """Routing rule: ``source`` agent may hand off to ``target`` agent.

    Args:
        source: ID of the source agent.
        target: ID of the target agent.
    """
    source: str
    target: str


@dataclass
class HandoffConfig:
    """Orchestration parameters for :class:`HandoffTeam`.

    Args:
        start_agent:           AgentCard of the first agent to run.
                               Defaults to the first agent added via ``add_agent()``.
        max_handoffs:          Maximum number of handoff transfers after the
                               initial hop.  ``max_handoffs=2`` allows A→B→C
                               but blocks a 4th hop.  Default: ``10``.
        routes:                Explicit routing rules.  When empty, any agent
                               may hand off to any other agent (full-mesh).
                               Also controls which HandoffTools are injected.
        termination_condition: Optional async callable ``(HandoffOrchestrator) -> bool``
                               that triggers early termination when it returns ``True``.
    """

    start_agent: Optional[AgentCard] = None
    max_handoffs: int = 10
    routes: List[HandoffRoute] = field(default_factory=list)
    termination_condition: Optional[Callable] = None


class HandoffTeamConfig(TeamConfig):
    """Full configuration for :class:`HandoffTeam`.

    Extends :class:`~openjiuwen.core.multi_agent.config.TeamConfig` with
    handoff-specific orchestration parameters.

    Args:
        handoff: Handoff orchestration config.  Defaults to :class:`HandoffConfig`.
    """

    handoff: HandoffConfig = Field(
        default_factory=HandoffConfig,
        description="Handoff orchestration configuration",
    )

    model_config = {"extra": "allow", "arbitrary_types_allowed": True}
