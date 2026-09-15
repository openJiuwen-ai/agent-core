# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression coverage for code brackets outside a fenced Judge verdict."""

import json

import pytest

from openjiuwen.rsi.harness_rsi.evaluator.judger.scoring import parse_judge_output, score_judge_output


@pytest.mark.parametrize(
    "prose",
    [
        "Review best_action[h].",
        "Use {name}.",
        "See [reference] and x[i].",
        "A = [1,2] + [1000,2000] = [1001,2002]",
        'Data: {} and {"shape": [1, 2]}',
        'Data: {"example": "{\\"score\\": 0}"}',
    ],
)
def test_code_brackets_do_not_create_another_verdict(prose):
    verdict = {"overall_reason": "Checked best_action[h]", "behaviors": [], "forbidden_hits": []}
    assert parse_judge_output(f"{prose}\n```json\n{json.dumps(verdict)}\n```\n{prose}") == verdict


@pytest.mark.parametrize(
    "other",
    [
        '{"status":"unavailable","reason":"unreadable"}',
        '[{"score":0}]',
        '{"overall_reason":"other", "behaviors":[]}',
        '{"forbidden_hits":[]}',
    ],
)
def test_actual_additional_payload_stays_ambiguous(other):
    with pytest.raises(ValueError, match="ambiguous"):
        parse_judge_output(f"Review best_action[h].\n```json\n{{}}\n```\n{other}")


def test_tolerating_prose_does_not_bypass_verdict_validation():
    parsed = parse_judge_output("Review best_action[h].\n```json\n{}\n```\n")
    with pytest.raises(ValueError, match="overall_reason"):
        score_judge_output(parsed, [], [])
