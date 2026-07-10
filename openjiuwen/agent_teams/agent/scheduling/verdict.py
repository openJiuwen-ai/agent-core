# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure review-vote verdict math (F_62). No IO, no state.

The verdict policy lives here — outside ``verify_task``'s transaction — so a
future policy (weighted votes, veto reviewers, LLM arbitration) replaces this
function without touching the vote store or the state machine.
"""

import math

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_UNDECIDED = "undecided"


def judge(pass_count: int, fail_count: int, reviewer_count: int, threshold: float) -> str:
    """Judge one review round's tally against a pass-quorum threshold.

    The quorum is ``ceil(threshold * reviewer_count)`` pass votes. The round
    fails as soon as that quorum becomes unreachable — i.e. the fail votes
    exceed the slack ``reviewer_count - quorum`` — so a doomed round settles
    on the decisive vote instead of waiting for stragglers. A single reviewer
    under the default 2/3 threshold degenerates to first-verdict-wins.

    Args:
        pass_count: Distinct reviewers currently voting pass.
        fail_count: Distinct reviewers currently voting fail.
        reviewer_count: Total reviewers assigned to the task.
        threshold: Pass quorum fraction in (0, 1].

    Returns:
        ``VERDICT_PASS`` / ``VERDICT_FAIL`` / ``VERDICT_UNDECIDED``.
    """
    if reviewer_count <= 0:
        return VERDICT_UNDECIDED
    quorum = math.ceil(threshold * reviewer_count)
    if pass_count >= quorum:
        return VERDICT_PASS
    if fail_count > reviewer_count - quorum:
        return VERDICT_FAIL
    return VERDICT_UNDECIDED
