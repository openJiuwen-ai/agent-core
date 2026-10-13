# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""F_117 scenario table, one row per scenario, including threshold edges."""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.duplex.policy import (
    RELEVANCE,
    URGENCY,
    broadcast_allowed,
    final_action,
    plan,
    resolve,
)
from openjiuwen.agent_teams.duplex.policy import (
    DeliveryAction as A,
)
from openjiuwen.agent_teams.duplex.policy import (
    MessageSource as S,
)
from openjiuwen.core.foundation.llm.system_one import ChoiceAnswer


def _ans(choice: str, other: str, p: float) -> ChoiceAnswer:
    return ChoiceAnswer(choice=choice, confidence=p, probabilities={choice: p, other: round(1 - p, 6)})


INT = {URGENCY: _ans("INTERRUPT", "APPEND", 0.9)}  # edge: exactly the threshold keeps INTERRUPT
INT_LOW = {URGENCY: _ans("INTERRUPT", "APPEND", 0.89)}
SKIP = {RELEVANCE: _ans("SKIP", "DELIVER", 0.8), URGENCY: _ans("INTERRUPT", "APPEND", 0.99)}
SKIP_LOW = {RELEVANCE: _ans("SKIP", "DELIVER", 0.79), URGENCY: _ans("INTERRUPT", "APPEND", 0.95)}
REL = {RELEVANCE: _ans("DELIVER", "SKIP", 0.9), URGENCY: _ans("APPEND", "INTERRUPT", 0.9)}

ROWS = [
    # source, idle, answers, expected
    (S.USER, True, None, A.DELIVER_NOW),
    (S.USER, False, None, A.STEER),
    (S.DIRECT, True, None, A.DELIVER_NOW),
    (S.DIRECT, False, INT, A.INTERRUPT),
    (S.DIRECT, False, INT_LOW, A.APPEND),
    (S.DIRECT, False, None, A.APPEND),  # classifier failed
    (S.GROUP_MENTION, False, INT, A.INTERRUPT),
    (S.BROADCAST, True, SKIP, A.VISIBLE_ONLY),
    (S.BROADCAST, True, REL, A.DELIVER_NOW),
    (S.BROADCAST, True, None, A.DELIVER_NOW),  # classifier failed
    (S.BROADCAST, False, SKIP, A.VISIBLE_ONLY),
    (S.BROADCAST, False, SKIP_LOW, A.STEER),  # never INTERRUPT for a broadcast
    (S.BROADCAST, False, REL, A.APPEND),
    (S.BROADCAST, False, None, A.APPEND),  # classifier failed
    (S.LEADER_BROADCAST, True, None, A.DELIVER_NOW),  # never judged, never rate limited
    (S.LEADER_BROADCAST, False, None, A.STEER),
    (S.GROUP_PLAIN, False, None, A.VISIBLE_ONLY),
    (S.SYSTEM, False, None, A.PASSTHROUGH),
]


@pytest.mark.level0
def test_scenario_table() -> None:
    for source, idle, answers, expected in ROWS:
        planned = plan(source, idle=idle)
        assert (planned.action is None) == bool(planned.questions), source
        got = resolve(source, planned, answers, idle=idle).action
        assert got is expected, (source, idle, answers, got)
    assert plan(S.BROADCAST, idle=True).questions == (RELEVANCE,)
    assert plan(S.USER, idle=False).questions == ()  # user messages are never judged
    assert final_action(A.INTERRUPT, idle_now=True) is A.DELIVER_NOW  # re-read state
    assert final_action(A.DELIVER_NOW, idle_now=False) is A.APPEND  # became busy: queue, never steer
    assert final_action(A.VISIBLE_ONLY, idle_now=True) is A.VISIBLE_ONLY
    assert broadcast_allowed(None, 0.0) and not broadcast_allowed(0.0, 9.9) and broadcast_allowed(0.0, 10.0)
