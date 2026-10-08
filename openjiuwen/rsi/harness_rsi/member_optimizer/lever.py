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
    """Return supported surfaces; an upstream label is advice, not a sandbox."""
    groups = {str(group).strip().lower() for group in allowed_action_groups}
    if lever == LEVER_CONFIGURATION:
        return [group for group in ("config", "configuration") if group in groups]
    if lever == LEVER_UNRESOLVED:
        return []
    return [
        "prompt_section" if group == "prompt" else group
        for group in ("prompt", "skill", "tool", "rail", "subagent", "processor")
        if group in groups
    ]


def build_hypothesis_lever_policy(
    *,
    target_ref: str,
    target_case_ids: list[str],
    decisive_probe: dict[str, Any],
) -> dict[str, Any]:
    """Build an optimizer-only, surface-independent lever decision policy."""
    lever = target_ref_lever(target_ref)
    return {
        "recommended_lever": lever,
        "target_ref": str(target_ref).strip(),
        "why_this_lever": "Advisory classification from target_ref; select components using the observed operation.",
        "why_not_other_levers": {},
        "predicted_affected_case_ids": sorted({str(case_id) for case_id in target_case_ids}),
        "retroactive_check": {
            "decisive_probe": dict(decisive_probe),
            "falsification_rule": (
                "Check the predicted behavior against paired evidence independently of task score. "
                "A score change alone neither confirms nor refutes the cause."
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
