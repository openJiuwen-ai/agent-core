# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility shim for the team skill evolution rail."""

from openjiuwen.agent_evolving.signal import TeamSignalType, TrajectoryIssue, UserIntent
from openjiuwen.harness.rails.evolution.team_skill_evolution_rail import (
    TeamSkillEvolutionRail,
)

TeamSkillRail = TeamSkillEvolutionRail

__all__ = [
    "TeamSignalType",
    "TeamSkillEvolutionRail",
    "TeamSkillRail",
    "TrajectoryIssue",
    "UserIntent",
]
