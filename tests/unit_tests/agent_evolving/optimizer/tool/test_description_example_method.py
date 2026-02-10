# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

from openjiuwen.agent_evolving.optimizer.tool.utils.description_example_method import ToolDescriptionMethod


def _config(tmp_path):
    return {
        "gen_model_id": "gpt-gen",
        "eval_model_id": "gpt-eval",
        "llm_api_key": "k",
        "verbose": False,
        "num_feedback_steps": 2,
        "num_examples_for_desc": 3,
        "examples_dir": str(tmp_path),
        "neg_ex_input_path": str(tmp_path / "neg.json"),
    }


def _tool():
    return {"name": "search", "description": 'The description of this function is: "origin-desc"'}


def test_step_and_generate_and_eval_loop(monkeypatch, tmp_path):
    eval_res = {"score_avg": 77.0, "score_std": 1.0, "results": []}
    method = ToolDescriptionMethod(_config(tmp_path), eval_fn=lambda *args, **kwargs: eval_res)

    out0, desc0, score0 = method.step(_tool(), examples=[("i", {}, "", "a")], it=0)
    assert out0["description"] == "origin-desc"
    assert desc0 == "origin-desc"
    assert score0 == 77.0

    monkeypatch.setattr(method, "get_negative_examples", lambda fn: [("bad", {}, "", "")])
    monkeypatch.setattr(method, "generate", lambda *args, **kwargs: {"description": "new-desc", "iteration": 1})
    out1, desc1, score1 = method.step(_tool(), examples=[("i", {}, "", "a")], prev_outputs=[{"iteration": 0}], it=1)
    assert out1["description"] == "new-desc"
    assert desc1 == "new-desc"
    assert score1 == 77.0

    method2 = ToolDescriptionMethod(_config(tmp_path), eval_fn=lambda *args, **kwargs: eval_res)
    monkeypatch.setattr(
        method2, 
        "generate_description_from_documentation", 
        lambda *args, 
        **kwargs: {"description": "g"})
    generated = method2.generate(_tool(), examples={"examples": [], "neg_examples": []}, prev_outputs=[], it=3)
    assert generated["iteration"] == 3


def test_critique_methods_and_generate_description(monkeypatch, tmp_path):
    method = ToolDescriptionMethod(_config(tmp_path), eval_fn=lambda *args, **kwargs: {})

    prev_outputs = [
        {
            "iteration": 0, 
            "description": "d0", 
            "results": [{"answer": "a", "errors": []}], 
            "score_avg": 70.0, 
            "score_std": 2.0
        },
        {
            "iteration": 1, 
            "description": "d1", 
            "results": [{
                "answer": "a", 
                "errors": [{
                    "function_name": "f", 
                    "arguments": {}, 
                    "error_msg": "e"
                }]
            }], 
            "score_avg": 40.0, 
            "score_std": 10.0
        },
    ]
    examples = [("inst", {"name": "search", "arguments": {}}, "fn_out", "ans")]

    validjson = json.dumps(
        {
            "description": {
                "type": "function",
                "name": "search",
                "description": "ok",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            }
        }
    )

    outputs = iter(
        [
            "desc-analysis",
            "neg-analysis",
            "all-analysis",
            validjson,
        ]
    )

    def fake_get_rits_response(model, prompt, key, verify_fn, **kwargs):
        return verify_fn(next(outputs))

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.optimizer.tool.utils.description_example_method.get_rits_response",
        fake_get_rits_response,
    )

    c1 = method.critique_descriptions(_tool(), examples=examples, prev_outputs=prev_outputs)
    assert "analysis" in c1
    c_neg = method.critique_negative_examples(_tool(), examples=examples)
    assert c_neg["analysis"] == "neg-analysis"
    c2 = method.critique_all_descriptions(
        _tool(), 
        examples={"examples": examples, "neg_examples": examples}, 
        prev_outputs=prev_outputs)
    assert "analysis" in c2

    monkeypatch.setattr(method, "critique_descriptions", lambda *args, **kwargs: {"analysis": "A"})
    monkeypatch.setattr(method, "critique_all_descriptions", lambda *args, **kwargs: {"analysis": "B"})
    out = method.generate_description_from_documentation(
        _tool(), 
        {"examples": examples, "neg_examples": examples}, prev_outputs=prev_outputs)
    assert "description" in out


def test_load_examples_and_negative_examples_and_get_examples(tmp_path):
    cfg = _config(tmp_path)
    method = ToolDescriptionMethod(cfg, eval_fn=lambda *args, **kwargs: {})

    good_data = [
        [
            {
                "instructions": ["inst1"],
                "fn_call": {"name": "search", "arguments": {"q": "x"}},
                "tool_results": "result1",
                "answers": ["ans1"],
                "scores": [3],
            }
        ],
        [
            {
                "instructions": ["inst2"],
                "fn_call": {"name": "search", "arguments": {"q": "y"}},
                "tool_results": "result2",
                "answers": ["ans2"],
                "scores": [2],
            }
        ],
    ]
    (tmp_path / "search.json").write_text(json.dumps(good_data, ensure_ascii=False), encoding="utf-8")
    loaded = method.load_examples(str(tmp_path), "search", max_num_examples=5)
    assert len(loaded) == 1
    assert loaded[0][0] == "inst1"

    (tmp_path / "neg.json").write_text(json.dumps(good_data, ensure_ascii=False), encoding="utf-8")
    neg = method.get_negative_examples("search")
    assert len(neg) == 1
    assert neg[0][0] == "inst2"

    cfg2 = _config(tmp_path)
    cfg2["neg_ex_input_path"] = str(tmp_path / "missing-neg.json")
    method2 = ToolDescriptionMethod(cfg2, eval_fn=lambda *args, **kwargs: {})
    neg2 = method2.get_negative_examples("search")
    assert len(neg2) >= 1

    ex = method.get_examples(_tool())
    assert len(ex) == 1
