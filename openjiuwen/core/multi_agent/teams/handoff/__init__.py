# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Handoff multi-agent team -- event-driven sequential agent handoffs."""
from openjiuwen.core.multi_agent.teams.handoff.handoff_orchestrator import HandoffOrchestrator
from openjiuwen.core.multi_agent.teams.handoff.interrupt import TeamInterruptSignal
from openjiuwen.core.multi_agent.teams.handoff.handoff_team import HandoffTeam
from openjiuwen.core.multi_agent.teams.handoff.handoff_config import (
    HandoffConfig,
    HandoffTeamConfig,
    HandoffRoute,
)
from openjiuwen.core.multi_agent.teams.handoff.handoff_signal import (
    HandoffSignal,
    extract_handoff_signal,
    HANDOFF_TARGET_KEY,
    HANDOFF_MESSAGE_KEY,
    HANDOFF_REASON_KEY,
)

__all__ = [
    "HandoffTeam",
    "HandoffOrchestrator",
    "TeamInterruptSignal",
    "HandoffConfig",
    "HandoffTeamConfig",
    "HandoffRoute",
    "HandoffSignal",
    "extract_handoff_signal",
    "HANDOFF_TARGET_KEY",
    "HANDOFF_MESSAGE_KEY",
    "HANDOFF_REASON_KEY",
]
