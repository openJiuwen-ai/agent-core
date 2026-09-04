# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
import asyncio
from typing import Optional

from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import (
    CaseResult,
    Execution,
    PromptCandidate,
    PromptTaskCase,
    PromptTaskSpec,
)
from openjiuwen.dev_tools.tune.optimizer.prompt_search.reward import (
    CompositeReward,
    CorrectnessReward,
    CustomReward,
)


class FakeCorrectness(CorrectnessReward):
    """Correctness stub with no evaluator: distinct visible/hidden scores."""

    def __init__(self, visible: float, hidden: Optional[float] = None) -> None:
        super().__init__(evaluator=None)
        self._visible = visible
        self._hidden = hidden if hidden is not None else visible

    async def score(self, execution, task):
        return self._visible

    async def correctness_on(self, results, task):
        if results and results[0].hidden:
            return self._hidden
        return self._visible


def _execution(cid="c1", *, hidden_case=False):
    results = [CaseResult(case_input="i", output="- out", hidden=False)]
    if hidden_case:
        results.append(CaseResult(case_input="h", output="- out", hidden=True))
    return Execution(
        candidate=PromptCandidate(prompt="p", candidate_id=cid),
        case_results=results,
        latency_s=1.0,
        token_usage={"total": {"total_tokens": 100}},
    )


def _task(hidden=False):
    cases = [PromptTaskCase.from_text("i")]
    if hidden:
        cases.append(PromptTaskCase.from_text("h", hidden=True))
    return PromptTaskSpec(objective="o", cases=cases)


def test_weighted_sum_without_correctness():
    reward = CompositeReward(
        [CustomReward("a", lambda e, t: 1.0), CustomReward("b", lambda e, t: 0.0)],
        {"a": 3.0, "b": 1.0},
        min_correctness=0.0,
        drift_penalty=0.0,
    )
    [bd] = asyncio.run(reward.evaluate([_execution()], _task(), {}))
    assert bd.score == 0.75  # (3*1 + 1*0) / 4


def test_min_correctness_gate_caps_reward():
    reward = CompositeReward(
        [FakeCorrectness(0.2), CustomReward("fast", lambda e, t: 1.0)],
        {"correctness": 1.0, "fast": 1.0},
        min_correctness=0.5,
        drift_penalty=0.0,
    )
    [bd] = asyncio.run(reward.evaluate([_execution()], _task(), {}))
    assert bd.gated is True
    assert bd.score <= 0.2  # cannot exceed correctness once gated


def test_drift_penalty_subtracts():
    reward = CompositeReward(
        [CustomReward("a", lambda e, t: 1.0)],
        {"a": 1.0},
        min_correctness=0.0,
        drift_penalty=0.5,
    )
    [bd] = asyncio.run(reward.evaluate([_execution("c1")], _task(), {"c1": 1.0}))
    assert bd.drift == 1.0
    assert bd.score == 0.5  # 1.0 - 0.5*1.0


def test_overfitting_penalty_on_hidden_gap():
    reward = CompositeReward(
        [FakeCorrectness(visible=0.9, hidden=0.4)],
        {"correctness": 1.0},
        min_correctness=0.0,
        drift_penalty=0.0,
    )
    [bd] = asyncio.run(reward.evaluate([_execution(hidden_case=True)], _task(hidden=True), {}))
    # gap 0.5 > margin 0.25 -> penalty 0.5*0.5 = 0.25 subtracted from 0.9
    assert any("overfitting" in n for n in bd.notes)
    assert abs(bd.score - 0.65) < 1e-9


def test_execution_error_scores_zero():
    ex = _execution()
    ex.error = "boom"
    reward = CompositeReward(
        [CustomReward("a", lambda e, t: 1.0)], {"a": 1.0},
        min_correctness=0.0, drift_penalty=0.0,
    )
    [bd] = asyncio.run(reward.evaluate([ex], _task(), {}))
    assert bd.score == 0.0
