# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Profiles for MemberOptimizer DeepAgent definitions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MemberOptimizerAgentProfile:
    """Configuration for one Member Optimizer Agent."""

    key: str
    agent_name: str
    description: str
    prompt_file: str
    max_iterations: int = 3
    enabled_skills: tuple[str, ...] = field(default_factory=tuple)


ROLE_ATTRIBUTION = MemberOptimizerAgentProfile(
    key="role_attribution",
    agent_name="member_role_attributor",
    description="Attributes Team issues to member Expert Harness roles.",
    prompt_file="role_attribution.md",
)

MECHANISM_ATTRIBUTION = MemberOptimizerAgentProfile(
    key="mechanism_attribution",
    agent_name="member_mechanism_attributor",
    description="Diagnoses role-internal Expert Harness failure mechanisms.",
    prompt_file="mechanism_attribution.md",
)

ACTION_PLANNING = MemberOptimizerAgentProfile(
    key="action_planning",
    agent_name="member_action_planner",
    description="Plans constrained member Expert Harness changes.",
    prompt_file="action_planning.md",
)

ACTION_EXECUTION = MemberOptimizerAgentProfile(
    key="action_execution",
    agent_name="member_action_executor",
    description="Executes one constrained Expert Harness package action.",
    prompt_file="action_execution.md",
    max_iterations=8,
)

VERIFICATION_REPAIR = MemberOptimizerAgentProfile(
    key="verification_repair",
    agent_name="member_harness_verifier_repair",
    description="Repairs deterministic verification failures in one role package.",
    prompt_file="verification_repair.md",
    max_iterations=8,
)

_AGENT_PROFILES = (
    ROLE_ATTRIBUTION,
    MECHANISM_ATTRIBUTION,
    ACTION_PLANNING,
    ACTION_EXECUTION,
    VERIFICATION_REPAIR,
)
_PROFILES_BY_KEY = {profile.key: profile for profile in _AGENT_PROFILES}


def get_agent_profile(key: str) -> MemberOptimizerAgentProfile:
    """Return a built-in Member Optimizer Agent profile."""
    try:
        return _PROFILES_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"unknown Member Optimizer Agent profile: {key}") from exc


def iter_agent_profiles() -> tuple[MemberOptimizerAgentProfile, ...]:
    """Return built-in profiles in stable declaration order."""
    return (
        ROLE_ATTRIBUTION,
        MECHANISM_ATTRIBUTION,
        ACTION_PLANNING,
        ACTION_EXECUTION,
        VERIFICATION_REPAIR,
    )


__all__ = [
    "ACTION_EXECUTION",
    "ACTION_PLANNING",
    "MECHANISM_ATTRIBUTION",
    "ROLE_ATTRIBUTION",
    "VERIFICATION_REPAIR",
    "MemberOptimizerAgentProfile",
    "get_agent_profile",
    "iter_agent_profiles",
]
