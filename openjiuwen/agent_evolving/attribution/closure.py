# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared attribution closure rules (the check tool and the run loop use one copy).

A signal counts only while it is not marked invalid; a ``skill``/``experience``
obligation exists only after the agent marked the signal that way.  Disabled
switches contribute no obligation at all.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_evolving.attribution.model import DiagnosticSignal, SignalSource


def completion_state(
    runtime: Any,
    signals: list[DiagnosticSignal],
    *,
    skill_evolution: bool,
    ttse_advice: bool,
) -> dict[str, Any]:
    """Return the pending groups of one run; ``all_done`` when every group is empty."""
    active = {signal.signal_id for signal in signals}
    invalid = set(getattr(runtime, "invalid_signals", ()) or ())
    marks: dict[str, dict[str, Any]] = getattr(runtime, "marks", {}) or {}

    reward_pending: list[str] = []
    for signal in signals:
        if signal.source != SignalSource.REWARD:
            continue
        if signal.signal_id in invalid or signal.signal_id in runtime.reward_attributions:
            continue
        reward_pending.append(signal.signal_id)
    reward_pending.sort()

    marked_skill = sorted(
        signal_id
        for signal_id, categories in marks.items()
        if signal_id in active and signal_id not in invalid and "skill" in categories
    ) if skill_evolution else []
    skill_cause_pending = sorted(
        signal_id for signal_id in marked_skill if signal_id not in runtime.skill_attributions
    )
    skill_keys = {
        categories["skill"].skill_key
        for signal_id, categories in marks.items()
        if signal_id in marked_skill and categories["skill"].skill_key
    }
    missing_skill_globals = sorted(key for key in skill_keys if key not in runtime.skill_evolutions)

    marked_experience = sorted(
        signal_id
        for signal_id, categories in marks.items()
        if signal_id in active and signal_id not in invalid and "experience" in categories
    ) if ttse_advice else []
    experience_cause_pending = sorted(
        signal_id for signal_id in marked_experience if signal_id not in runtime.experience_attributions
    )
    missing_experience_evolution = bool(marked_experience) and runtime.experience_evolution is None

    missing_trajectory_summary = not str(getattr(runtime, "trajectory_summary", None) or "").strip()

    all_done = not (
        reward_pending
        or skill_cause_pending
        or experience_cause_pending
        or missing_skill_globals
        or missing_experience_evolution
        or missing_trajectory_summary
    )
    return {
        "all_done": all_done,
        "pending_reward_signal_ids": reward_pending,
        "pending_skill_cause_signal_ids": skill_cause_pending,
        "pending_experience_cause_signal_ids": experience_cause_pending,
        "missing_skill_globals": missing_skill_globals,
        "missing_experience_evolution": missing_experience_evolution,
        "missing_trajectory_summary": missing_trajectory_summary,
    }


__all__ = ["completion_state"]
