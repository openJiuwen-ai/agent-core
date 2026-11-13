# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task clauses cited by a diagnosis must belong to the authoritative input."""

import pytest

from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import _diagnosis_validation_conflicts


@pytest.mark.parametrize("quote,preserves", [
    ("Missing rows may be silently ignored.", True),
    ("All needed rows are supplied.", False),
])
def test_unsupported_or_contract_changing_diagnosis_is_rejected(quote, preserves):
    diagnosis = {"task_contract_checks": [{
        "task_quote": quote,
        "interpretation": "Determine the required table domain before adding fallback behavior.",
        "recommendation_preserves_contract": preserves,
    }]}
    conflicts = _diagnosis_validation_conflicts(diagnosis, {}, public_task="All needed rows are supplied.")
    assert conflicts
    assert "task_contract_checks" in conflicts[0]


def test_supported_contract_interpretation_remains_a_usable_hypothesis():
    diagnosis = {"task_contract_checks": [{
        "task_quote": "All needed rows are supplied.",
        "interpretation": "Investigate the generated test input rather than redefine valid inputs.",
        "recommendation_preserves_contract": True,
    }]}
    assert _diagnosis_validation_conflicts(diagnosis, {}, public_task="All needed rows are supplied.") == []
