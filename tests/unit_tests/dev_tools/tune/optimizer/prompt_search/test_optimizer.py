# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""End-to-end coverage of the PromptSearchOptimizer loop, fully offline (no LLM calls) —
every collaborator is overridden with a deterministic stub, matching how the equivalent
loop was tested before this optimizer moved here from jiuwenswarm's Symphony layer.
"""
import asyncio

from openjiuwen.dev_tools.tune.optimizer.prompt_search.drift import NullDriftJudge
from openjiuwen.dev_tools.tune.optimizer.prompt_search.environment import CallableEnvironment
from openjiuwen.dev_tools.tune.optimizer.prompt_search.memory import JsonlPromptMemory
from openjiuwen.dev_tools.tune.optimizer.prompt_search.models import PromptCandidate, PromptTaskCase, PromptTaskSpec
from openjiuwen.dev_tools.tune.optimizer.prompt_search.optimizer import PromptSearchOptimizer
from openjiuwen.dev_tools.tune.optimizer.prompt_search.policy import PolicyRequest, PromptPolicy
from openjiuwen.dev_tools.tune.optimizer.prompt_search.reward import CompositeReward, CustomReward


class VersionPolicy(PromptPolicy):
    """Each iteration proposes prompts tagged with the iteration number."""

    async def generate(self, request: PolicyRequest):
        n = request.iteration
        return [
            PromptCandidate(prompt=f"v{n}", rationale=f"iter {n}")
            for _ in range(request.num_candidates)
        ]


def _runner(system_prompt: str, case_input: str) -> str:
    ver = int(system_prompt[1:])
    return "- item\n" * ver


def _quality(execution, task) -> float:
    outs = execution.visible_results
    scores = [min(1.0, len(r.output.splitlines()) / 4) for r in outs]
    return sum(scores) / max(1, len(scores))


def _make_optimizer(tmp_path, **overrides):
    kwargs = dict(
        candidate_prompts=2,
        max_iterations=6,
        drift_penalty=0.0,
        policy=VersionPolicy(),
        environment=CallableEnvironment(_runner),
        reward_model=CompositeReward(
            [CustomReward("quality", _quality)],
            {"quality": 1.0},
            min_correctness=0.0,
            drift_penalty=0.0,
        ),
        drift_judge=NullDriftJudge(),
        memory=JsonlPromptMemory(tmp_path),
    )
    kwargs.update(overrides)
    return PromptSearchOptimizer(**kwargs)


def test_reward_improves_and_converges(tmp_path):
    optimizer = _make_optimizer(tmp_path)
    task = PromptTaskSpec(objective="produce a 4-item list", cases=[PromptTaskCase.from_text("x")])

    result = asyncio.run(optimizer.optimize(task))

    assert result.success is True
    scores = [it.best_score for it in result.iterations]
    assert scores[0] < scores[-1]  # reward climbed
    assert result.best_score == max(scores)
    assert result.best_prompt == "v4"  # v4 first reaches 4 items -> reward 1.0
    assert result.converged is True


def test_best_prompt_persisted_to_memory(tmp_path):
    optimizer = _make_optimizer(tmp_path)
    task = PromptTaskSpec(objective="produce a 4-item list", cases=[PromptTaskCase.from_text("x")])

    result = asyncio.run(optimizer.optimize(task))

    memory = JsonlPromptMemory(tmp_path)
    records = memory.search_similar(task, top_k=5)
    assert records, "expected the best prompt to be stored"
    assert any(r.prompt == result.best_prompt for r in records)


def test_parallel_and_sequential_agree(tmp_path):
    task = PromptTaskSpec(objective="produce a 4-item list", cases=[PromptTaskCase.from_text("x")])
    par = asyncio.run(_make_optimizer(tmp_path / "p", parallel_execution=True).optimize(task))
    seq = asyncio.run(_make_optimizer(tmp_path / "s", parallel_execution=False).optimize(task))
    assert par.best_prompt == seq.best_prompt
    assert par.best_score == seq.best_score


def test_second_run_on_same_objective_records_prior_best_as_baseline(tmp_path):
    task = PromptTaskSpec(objective="produce a 4-item list", cases=[PromptTaskCase.from_text("x")])

    first = asyncio.run(_make_optimizer(tmp_path).optimize(task))

    memory = JsonlPromptMemory(tmp_path)
    first_record = memory.best_for_objective(task.objective)
    assert first_record is not None
    assert first_record.baseline_reward is None  # nothing existed before this run
    assert first_record.reward == first.best_score

    second = asyncio.run(_make_optimizer(tmp_path).optimize(task))

    memory = JsonlPromptMemory(tmp_path)
    pending = memory.pending(threshold=-1.0)  # include even non-improving records
    second_record = next(r for r in pending if r.baseline_reward is not None)
    assert second_record.baseline_reward == first.best_score
    assert second_record.reward == second.best_score


def test_candidate_prompts_exposes_every_prompt_tried(tmp_path):
    optimizer = _make_optimizer(tmp_path)
    task = PromptTaskSpec(objective="produce a 4-item list", cases=[PromptTaskCase.from_text("x")])

    result = asyncio.run(optimizer.optimize(task))

    # every distinct candidate tried across iterations is exposed, best-first —
    # this is what Trainer.search_prompt_candidates() searches over.
    assert result.candidate_prompts[0] == result.best_prompt
    assert set(result.candidate_prompts) == {f"v{i}" for i in range(1, 7)}
