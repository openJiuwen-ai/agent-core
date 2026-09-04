# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Coverage for Trainer.search_prompt_candidates() and TextualParameter's
candidate pool — the "multiple candidate prompts per round" capability
PromptSearchOptimizer unlocks. Trainer.evaluate() is mocked so this exercises
only the candidate-search control flow, not a full agent/evaluator stack.
"""
from unittest.mock import MagicMock, patch

from openjiuwen.dev_tools.tune.optimizer.base import TextualParameter
from openjiuwen.dev_tools.tune.trainer.trainer import Trainer


def _make_llm_call(system_prompt_text="original"):
    llm_call = MagicMock()
    template = MagicMock()
    template.to_messages.return_value = [MagicMock(content=system_prompt_text)]
    llm_call.get_system_prompt.return_value = template
    return llm_call


def _make_trainer_with_candidates(llm_call, candidates):
    optimizer = MagicMock()
    param = TextualParameter(llm_call)
    param.set_candidates("system_prompt", candidates)
    optimizer.parameters.return_value = {"main": param}

    trainer = Trainer(optimizer=optimizer, evaluator=MagicMock())
    return trainer


def test_returns_none_when_no_candidates_recorded():
    llm_call = _make_llm_call()
    optimizer = MagicMock()
    optimizer.parameters.return_value = {"main": TextualParameter(llm_call)}
    trainer = Trainer(optimizer=optimizer, evaluator=MagicMock())
    agent = MagicMock()
    agent.get_llm_calls.return_value = {"main": llm_call}

    assert trainer.search_prompt_candidates(agent, "main", case_loader=MagicMock()) is None


def test_picks_best_scoring_candidate_and_applies_it():
    llm_call = _make_llm_call("original")
    trainer = _make_trainer_with_candidates(llm_call, ["cand-a", "cand-b"])
    agent = MagicMock()
    agent.get_llm_calls.return_value = {"main": llm_call}

    # original -> 0.5, cand-a -> 0.9 (best), cand-b -> 0.3
    scores = iter([(0.5, []), (0.9, []), (0.3, [])])
    with patch.object(Trainer, "evaluate", side_effect=lambda *a, **k: next(scores)):
        best_score, best_prompt, _ = trainer.search_prompt_candidates(agent, "main", case_loader=MagicMock())

    assert best_score == 0.9
    assert best_prompt == "cand-a"
    llm_call.update_system_prompt.assert_called_with("cand-a")


def test_keeps_original_when_no_candidate_beats_it():
    llm_call = _make_llm_call("original")
    trainer = _make_trainer_with_candidates(llm_call, ["cand-a", "cand-b"])
    agent = MagicMock()
    agent.get_llm_calls.return_value = {"main": llm_call}

    # original -> 0.9 (best), both candidates score lower
    scores = iter([(0.9, []), (0.4, []), (0.3, [])])
    with patch.object(Trainer, "evaluate", side_effect=lambda *a, **k: next(scores)):
        best_score, best_prompt, _ = trainer.search_prompt_candidates(agent, "main", case_loader=MagicMock())

    assert best_score == 0.9
    assert best_prompt == "original"
    # last call restores the original prompt (mutated away during the search)
    llm_call.update_system_prompt.assert_called_with("original")


def test_textual_parameter_candidates_default_to_empty():
    param = TextualParameter(_make_llm_call())
    assert param.get_candidates("system_prompt") == []
    param.set_candidates("system_prompt", ["a", "b"])
    assert param.get_candidates("system_prompt") == ["a", "b"]
    # returned list is a copy — mutating it must not affect internal state
    param.get_candidates("system_prompt").append("c")
    assert param.get_candidates("system_prompt") == ["a", "b"]
