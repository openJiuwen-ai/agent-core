# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Offline signal adapter for EvaluatedCase to EvolutionSignal conversion."""

from typing import List, Optional

from openjiuwen.agent_evolving.dataset import EvaluatedCase
from openjiuwen.agent_evolving.signal.base import (
    EvolutionCategory,
    EvolutionSignal,
)


def from_evaluated_case(
    case: EvaluatedCase,
    operator_id: str = "",
    *,
    score_threshold: Optional[float] = None,
) -> Optional[EvolutionSignal]:
    """Convert an offline EvaluatedCase to EvolutionSignal.

    Args:
        case: Evaluated case from offline evaluation.
        operator_id: Operator identifier to attach as skill_name.
        score_threshold: Minimum score to skip signal generation.
            - None (default): no filtering, all cases produce signals.
            - float value: only cases with score < threshold produce signals.

    Returns:
        EvolutionSignal if applicable, None if filtered out by threshold.
    """
    if score_threshold is not None and case.score >= score_threshold:
        return None

    signal_type = "low_score" if case.score == 0 else "evaluated"

    return EvolutionSignal(
        signal_type=signal_type,
        evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
        section="Troubleshooting",
        excerpt=f"score={case.score:.2f}",
        skill_name=operator_id or None,
        context={
            "question": str(case.case.inputs),
            "label": str(case.case.label),
            "answer": str(case.answer),
            "reason": case.reason or "",
            "score": case.score,
        },
    )


def from_evaluated_cases(
    cases: List[EvaluatedCase],
    operator_id: str = "",
    *,
    score_threshold: Optional[float] = None,
) -> List[EvolutionSignal]:
    """Batch convert EvaluatedCase list to EvolutionSignal list.

    Args:
        cases: List of evaluated cases.
        operator_id: Operator identifier to attach as skill_name.
        score_threshold: Minimum score to skip signal generation.
            None means no filtering.

    Returns:
        List of EvolutionSignal.
    """
    signals: List[EvolutionSignal] = []
    for case in cases:
        signal = from_evaluated_case(case, operator_id, score_threshold=score_threshold)
        if signal is not None:
            signals.append(signal)
    return signals