# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team-domain signal helpers for team-skill evolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openjiuwen.agent_evolving.protocols import TRAJECTORY_ISSUE_SIGNAL, USER_INTENT_SIGNAL
from openjiuwen.agent_evolving.signal.base import EvolutionSignal, make_evolution_signal
from openjiuwen.agent_evolving.trajectory.types import Trajectory
from openjiuwen.core.common.logging import logger


class TeamSignalType(str, Enum):
    """Team-domain signal types.

    `USER_REQUEST` is kept as a compatibility synonym for older API/tests.
    """

    USER_INTENT = USER_INTENT_SIGNAL
    USER_REQUEST = "user_request"
    TRAJECTORY_ISSUE = TRAJECTORY_ISSUE_SIGNAL


@dataclass(frozen=True)
class UserIntent:
    """Parsed user improvement intention."""

    is_improvement: bool
    intent: str


@dataclass(frozen=True)
class TrajectoryIssue:
    """Normalized trajectory issue detected from team execution traces."""

    issue_type: str
    description: str
    affected_role: str = ""
    severity: str = "medium"


def build_team_trajectory_summary(trajectory: Trajectory) -> str:
    """Summarize trajectory steps with higher detail for collaboration-critical tools."""
    tool_budget = 20000
    llm_budget = 10000
    key_tools = {"spawn_member", "create_task", "build_team", "view_task", "send_message"}
    tool_lines: list[str] = []
    llm_lines: list[str] = []
    llm_count = 0
    tool_count = 0

    for step in trajectory.steps:
        if step.kind == "tool" and step.detail:
            tool_count += 1
            tool_name = getattr(step.detail, "tool_name", "unknown")
            is_key = tool_name in key_tools
            args_limit = 500 if is_key else 150
            result_limit = 500 if is_key else 200
            args = str(getattr(step.detail, "call_args", ""))[:args_limit]
            result = str(getattr(step.detail, "call_result", ""))[:result_limit]
            tool_lines.append(f"[Tool:{tool_name}] args={args} result={result}")
        elif step.kind == "llm" and step.detail:
            llm_count += 1
            response = getattr(step.detail, "response", None)
            if response:
                llm_lines.append(f"[LLM] {str(response)[:300]}")

    tool_section = "\n".join(tool_lines)
    if len(tool_section) > tool_budget:
        tool_section = tool_section[:tool_budget] + "\n... (tool section truncated)"

    llm_section = "\n".join(llm_lines)
    if len(llm_section) > llm_budget:
        llm_section = llm_section[:llm_budget] + "\n... (LLM section truncated)"

    summary = f"### Tool Calls ({tool_count})\n{tool_section}\n\n### LLM Responses ({llm_count})\n{llm_section}"
    logger.info(
        "[TeamSignal] trajectory summary: %d LLM steps, %d tool steps, tool_section_len=%d, "
        "llm_section_len=%d, total_len=%d",
        llm_count,
        tool_count,
        len(tool_section),
        len(llm_section),
        len(summary),
    )
    return summary


def make_team_user_intent_signal(
    *,
    skill_name: str,
    user_intent: str,
) -> EvolutionSignal:
    """Build the standard explicit-request signal for team skill evolution."""
    return make_evolution_signal(
        signal_type=TeamSignalType.USER_INTENT.value,
        section="Instructions",
        excerpt=user_intent,
        skill_name=skill_name,
        source="explicit_request",
    )


_TEAM_TRAJECTORY_ISSUES_KEY = "trajectory_issues"
_TEAM_SKILL_CONTENT_KEY = "skill_content"


def make_team_trajectory_signal(
    *,
    skill_name: str,
    skill_content: str,
    trajectory_issues: list[dict[str, str]],
) -> EvolutionSignal:
    """Build the canonical passive trajectory signal for team-skill evolution."""
    return make_evolution_signal(
        signal_type=TeamSignalType.TRAJECTORY_ISSUE.value,
        section="",
        excerpt="Detected team skill trajectory issues requiring evolution.",
        skill_name=skill_name,
        source="passive_trajectory",
        context={
            _TEAM_TRAJECTORY_ISSUES_KEY: list(trajectory_issues),
            _TEAM_SKILL_CONTENT_KEY: skill_content,
        },
    )


def get_team_trajectory_issues(signal: EvolutionSignal) -> list[dict[str, str]]:
    """Read normalized trajectory issues from a team-domain signal."""
    context = signal.context or {}
    issues = context.get(_TEAM_TRAJECTORY_ISSUES_KEY)
    if not isinstance(issues, list):
        return []
    return [item for item in issues if isinstance(item, dict)]


def get_team_signal_skill_content(signal: EvolutionSignal) -> str | None:
    """Read the associated team-skill content from a team-domain signal."""
    context = signal.context or {}
    skill_content = context.get(_TEAM_SKILL_CONTENT_KEY)
    return str(skill_content) if skill_content is not None else None


__all__ = [
    "TeamSignalType",
    "TrajectoryIssue",
    "UserIntent",
    "build_team_trajectory_summary",
    "get_team_signal_skill_content",
    "get_team_trajectory_issues",
    "make_team_trajectory_signal",
    "make_team_user_intent_signal",
]
