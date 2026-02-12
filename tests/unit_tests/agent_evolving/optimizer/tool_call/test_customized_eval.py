# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

import pytest

from openjiuwen.agent_evolving.optimizer.tool_call.utils import customized_eval as ce


def _base_config():
    return {"eval_model_id": "gpt-test"}


def test_simple_eval_init_weight_validation():
    with pytest.raises(ValueError):
        ce.SimpleEval(config=_base_config(), fn_call_weight=0.7, output_effectiveness_weight=0.4)


def test_simple_eval_call_aggregate(monkeypatch):
    evaluator = ce.SimpleEval(config=_base_config(), fn_call_weight=0.4, output_effectiveness_weight=0.6)

    def fake_eval_single(example):
        return {
            "fn_call_score": 0.5,
            "output_effectiveness_score": 0.75,
            "weighted_score": 0.65,
            "answer": "ok",
            "errors": [],
        }

    monkeypatch.setattr(evaluator, "_evaluate_single_example", fake_eval_single)
    result = evaluator(tool={"name": "f"}, description="d", examples=[("i", {}, "", "a")], runs=2)
    assert result["score_avg"] == pytest.approx(65.0)
    assert result["fn_call_accuracy"] == pytest.approx(50.0)
    assert result["output_effectiveness"] == pytest.approx(75.0)


