# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optimization-lever policy shared by planning and experiment tracking."""

from __future__ import annotations

from typing import Any

LEVER_CONFIGURATION = "configuration"
LEVER_CONTROL = "control"
LEVER_ACTION = "action"
LEVER_INSTRUCTION = "instruction"
LEVER_UNRESOLVED = "unresolved"

LEVER_VALUES = frozenset(
    {
        LEVER_CONFIGURATION,
        LEVER_CONTROL,
        LEVER_ACTION,
        LEVER_INSTRUCTION,
        LEVER_UNRESOLVED,
    }
)

_ACTION_GROUP_LEVERS = {
    "prompt": LEVER_INSTRUCTION,
    "skill": LEVER_INSTRUCTION,
    "tool": LEVER_ACTION,
    "subagent": LEVER_ACTION,
    "rail": LEVER_CONTROL,
    "processor": LEVER_CONTROL,
    "config": LEVER_CONFIGURATION,
    "configuration": LEVER_CONFIGURATION,
}

_TARGET_VARIABLE_LEVERS = {
    "prompt": LEVER_INSTRUCTION,
    "prompt_section": LEVER_INSTRUCTION,
    "identity": LEVER_INSTRUCTION,
    "soul": LEVER_INSTRUCTION,
    "skill": LEVER_INSTRUCTION,
    "instruction": LEVER_INSTRUCTION,
    "tool": LEVER_ACTION,
    "subagent": LEVER_ACTION,
    "action": LEVER_ACTION,
    "workflow": LEVER_CONTROL,
    "context": LEVER_CONTROL,
    "memory": LEVER_CONTROL,
    "processor": LEVER_CONTROL,
    "rail": LEVER_CONTROL,
    "control": LEVER_CONTROL,
    "execution_budget": LEVER_CONFIGURATION,
    "budget": LEVER_CONFIGURATION,
    "config": LEVER_CONFIGURATION,
    "configuration": LEVER_CONFIGURATION,
}


def action_group_lever(action_group: str) -> str:
    """Return the HarnessX-style lever implemented by an action group."""
    return _ACTION_GROUP_LEVERS.get(str(action_group).strip().lower(), LEVER_UNRESOLVED)


def target_ref_lever(target_ref: str) -> str:
    """Map an analyzer target variable to a modification lever."""
    variable = str(target_ref).strip().lower().replace("-", "_").split(".")[-1]
    return _TARGET_VARIABLE_LEVERS.get(variable, LEVER_UNRESOLVED)


def available_surfaces_for_lever(
    lever: str,
    allowed_action_groups: list[str] | tuple[str, ...] | set[str],
) -> list[str]:
    """Return executable surfaces without crossing a causal lever boundary."""
    groups = {str(group).strip().lower() for group in allowed_action_groups}
    if lever == LEVER_INSTRUCTION:
        surfaces: list[str] = []
        if "prompt" in groups:
            surfaces.append("prompt_section")
        if "skill" in groups:
            surfaces.append("skill")
        return surfaces
    if lever == LEVER_ACTION:
        return [group for group in ("tool", "subagent") if group in groups]
    if lever == LEVER_CONTROL:
        return [group for group in ("rail", "processor") if group in groups]
    if lever == LEVER_CONFIGURATION:
        return [group for group in ("config", "configuration") if group in groups]
    return []


def build_hypothesis_lever_policy(
    *,
    target_ref: str,
    target_case_ids: list[str],
    decisive_probe: dict[str, Any],
) -> dict[str, Any]:
    """Build an optimizer-only, surface-independent lever decision policy."""
    lever = target_ref_lever(target_ref)
    why_this = {
        LEVER_INSTRUCTION: (
            "The diagnosed variable is behavioral guidance or reusable method; no "
            "missing deterministic operation or runtime-control defect is evidenced."
        ),
        LEVER_ACTION: ("The diagnosis requires executable capability that instructions alone cannot supply."),
        LEVER_CONTROL: (
            "The defect concerns lifecycle, context, retry, routing, or result-flow "
            "control rather than task methodology."
        ),
        LEVER_CONFIGURATION: (
            "The defect concerns an existing runtime parameter or environment setting, "
            "not model guidance or a new task capability."
        ),
        LEVER_UNRESOLVED: (
            "The analyzer did not identify a supported harness variable; do not guess a runtime surface."
        ),
    }[lever]
    rejected = {other: reason for other, reason in _alternative_rejections(lever).items() if other != lever}
    return {
        "recommended_lever": lever,
        "target_ref": str(target_ref).strip(),
        "why_this_lever": why_this,
        "why_not_other_levers": rejected,
        "predicted_affected_case_ids": sorted({str(case_id) for case_id in target_case_ids}),
        "retroactive_check": {
            "decisive_probe": dict(decisive_probe),
            "falsification_rule": (
                "The selected lever is falsified unless paired evaluation improves every "
                "failing target case without regressing protected cases."
            ),
        },
    }


def build_action_lever_decision(
    *,
    action_group: str,
    selected_surface: str,
    policies: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind a concrete action to its immutable optimizer-only lever policy."""
    recommended = list(
        dict.fromkeys(str(policy.get("recommended_lever", "") or LEVER_UNRESOLVED) for policy in policies)
    )
    actual = action_group_lever(action_group)
    return {
        "selected_lever": actual,
        "selected_surface": str(selected_surface),
        "recommended_levers": recommended,
        "lever_matches_diagnosis": bool(recommended) and set(recommended) == {actual},
        "why_this_lever": [str(policy.get("why_this_lever", "") or "") for policy in policies],
        "why_not_other_levers": [
            dict(policy.get("why_not_other_levers", {}))
            for policy in policies
            if isinstance(policy.get("why_not_other_levers"), dict)
        ],
        "predicted_affected_case_ids": _predicted_affected_case_ids(policies),
        "retroactive_checks": [
            dict(policy.get("retroactive_check", {}))
            for policy in policies
            if isinstance(policy.get("retroactive_check"), dict)
        ],
    }


def _predicted_affected_case_ids(policies: list[dict[str, Any]]) -> list[str]:
    case_ids: set[str] = set()
    for policy in policies:
        for case_id in policy.get("predicted_affected_case_ids", []):
            normalized = str(case_id)
            if normalized:
                case_ids.add(normalized)
    return sorted(case_ids)


def _alternative_rejections(selected: str) -> dict[str, str]:
    defaults = {
        LEVER_CONFIGURATION: "No runtime parameter or environment defect is evidenced.",
        LEVER_CONTROL: "No lifecycle, routing, retry, context, or result-flow defect is evidenced.",
        LEVER_ACTION: "No missing deterministic capability is evidenced.",
        LEVER_INSTRUCTION: "Guidance cannot repair a lower-level executable or runtime defect.",
    }
    if selected == LEVER_CONFIGURATION:
        defaults[LEVER_CONFIGURATION] = "Selected by the diagnosed configuration variable."
    elif selected == LEVER_CONTROL:
        defaults[LEVER_CONTROL] = "Selected by the diagnosed control-flow variable."
    elif selected == LEVER_ACTION:
        defaults[LEVER_ACTION] = "Selected by the diagnosed executable capability variable."
    elif selected == LEVER_INSTRUCTION:
        defaults[LEVER_INSTRUCTION] = "Selected by the diagnosed instruction or method variable."
    return defaults


__all__ = [
    "LEVER_ACTION",
    "LEVER_CONFIGURATION",
    "LEVER_CONTROL",
    "LEVER_INSTRUCTION",
    "LEVER_UNRESOLVED",
    "LEVER_VALUES",
    "action_group_lever",
    "available_surfaces_for_lever",
    "build_action_lever_decision",
    "build_hypothesis_lever_policy",
    "target_ref_lever",
]
