# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure review-vote verdict math (F_62). No IO, no state.

Multi-type reviewer settlement: ``settle_review_tally`` merges the
binary-vote pool (verifier + challenger) and the score pool (inspector) into
a single three-value verdict. The binary pool uses one-vote veto — any fail
vote fails the round; the inspector pool requires every inspector's
average score to meet or exceed 0.85.
"""

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_UNDECIDED = "undecided"


def settle_review_tally(tally: dict, inspector_threshold: float = 0.85) -> str:
    """Judge a multi-type review tally.

    Binary pool (verifier + challenger): waits for all votes, then any
    fail fails the round.

    Score pool (inspector): waits for all votes, then the average must
    meet or exceed ``inspector_threshold``.

    Either pool still waiting (voted < total) returns UNDECIDED.
    """
    # — binary pool: verifier + challenger —
    bin_total = tally.get("verdict_total", 0)
    if bin_total > 0:
        if tally.get("verdict_voted", 0) < bin_total:
            return VERDICT_UNDECIDED
        if tally.get("verdict_fail_count", 0) > 0:
            return VERDICT_FAIL

    # — score pool: inspector —
    insp_total = tally.get("inspector_count", 0)
    if insp_total > 0:
        if tally.get("inspector_voted", 0) < insp_total:
            return VERDICT_UNDECIDED
        avg = tally.get("inspector_avg")
        if avg is None or avg < inspector_threshold:
            return VERDICT_FAIL

    return VERDICT_PASS
