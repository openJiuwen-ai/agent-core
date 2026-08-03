# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Analyzer issue to evolution signal mapping."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.rsi.team_skill_optimizer.artifacts import ANALYSIS_SIGNAL_SOURCE


def build_signals(
    *,
    issues: list[dict[str, Any]],
    skill_name: str,
    skill_content: str,
    eval_ref_path: str,
    analysis_result_path: str,
) -> list[EvolutionSignal]:
    signals: list[EvolutionSignal] = []
    for issue in issues:
        normalized_issue = normalize_trajectory_issue(issue)
        signals.append(
            EvolutionSignal(
                signal_type="trajectory_issue",
                section="",
                excerpt=issue_excerpt(issue),
                skill_name=skill_name,
                context={
                    "source": ANALYSIS_SIGNAL_SOURCE,
                    "trajectory_issues": [normalized_issue],
                    "skill_content": skill_content,
                    "analysis_issue": issue,
                    "eval_ref_path": eval_ref_path,
                    "analysis_result_path": analysis_result_path,
                },
            )
        )
    return signals


def normalize_trajectory_issue(issue: dict[str, Any]) -> dict[str, str]:
    severity = str(issue.get("severity") or "medium").strip().lower()
    if severity not in {"low", "medium", "high"}:
        severity = "medium"
    return {
        "issue_type": issue_type(issue),
        "description": issue_description(issue),
        "affected_role": affected_role(issue),
        "severity": severity,
    }


def issue_type(issue: dict[str, Any]) -> str:
    metadata = issue.get("metadata")
    attribution = metadata.get("attribution") if isinstance(metadata, dict) else None
    target_ref = ""
    if isinstance(attribution, dict):
        target_ref = str(attribution.get("target_ref") or "")
    category = str(issue.get("category") or "")
    lookup = f"{target_ref} {category}".lower()
    if "team_skill.routing_policy" in lookup or "routing_policy" in lookup:
        return "routing_policy"
    if "team_skill.handoff_protocol" in lookup or "handoff_protocol" in lookup:
        return "handoff_protocol"
    if "team_skill.shared_context_contract" in lookup or "shared_context_contract" in lookup:
        return "shared_context_contract"
    if "team_skill.final_answer_verification" in lookup or "final_answer_verification" in lookup:
        return "final_answer_verification"
    if "team_skill.stop_condition" in lookup or "stop_condition" in lookup:
        return "stop_condition"
    return "team_coordination"


def issue_description(issue: dict[str, Any]) -> str:
    parts: list[str] = []
    for label, key in (
        ("summary", "summary"),
        ("recommendation", "recommendation"),
    ):
        value = str(issue.get(key) or "").strip()
        if value:
            parts.append(f"{label}: {value}")

    metadata = issue.get("metadata")
    attribution = metadata.get("attribution") if isinstance(metadata, dict) else None
    if isinstance(attribution, dict):
        for key in ("general_mechanism", "root_cause", "critical_mistake", "target_ref"):
            value = str(attribution.get(key) or "").strip()
            if value:
                parts.append(f"{key}: {value}")
    return "\n".join(parts) or issue_excerpt(issue)


def affected_role(issue: dict[str, Any]) -> str:
    affected_components = issue.get("affected_components")
    if isinstance(affected_components, list) and affected_components:
        return str(affected_components[0] or "")
    evidence = issue.get("evidence")
    if isinstance(evidence, dict):
        return str(evidence.get("affected_component") or "")
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict) and item.get("affected_component"):
                return str(item.get("affected_component") or "")
    return ""


def issue_excerpt(issue: dict[str, Any]) -> str:
    for key in ("summary", "recommendation", "description"):
        value = str(issue.get(key) or "").strip()
        if value:
            return value[:1000]
    issue_id = str(issue.get("issue_id") or issue.get("id") or "team_skill_issue")
    return f"Analyzer Team Skill issue: {issue_id}"


def issue_ids(issues: list[dict[str, Any]]) -> list[str]:
    return [
        str(issue.get("issue_id") or issue.get("id") or f"team_skill_issue_{index:03d}")
        for index, issue in enumerate(issues, start=1)
    ]


def build_user_query(issues: list[dict[str, Any]]) -> str:
    recommendations = [str(issue.get("recommendation") or "").strip() for issue in issues]
    recommendations = [item for item in recommendations if item]
    if recommendations:
        return "\n".join(f"- {item}" for item in recommendations)
    return "\n".join(f"- {issue_excerpt(issue)}" for issue in issues)
