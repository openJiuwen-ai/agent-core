# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

import pytest

from openjiuwen.agent_evolving.optimizer.tool.utils.toolcall_example_method import APICallToExampleMethod


def _config():
    return {
        "gen_model_id": "gpt-gen",
        "eval_model_id": "gpt-eval",
        "llm_api_key": "k",
        "verbose": False,
        "num_init_loop": 2,
        "num_refine_steps": 2,
        "num_feedback_steps": 1,
        "score_eval_weight": 0.5,
    }


def _tool():
    return {"name": "search", "description": 'The description of this function is: "desc"'}


def test_get_original_description():
    method = APICallToExampleMethod(_config(), api_call_fn=lambda *_: None, eval_fn=lambda *_: None)
    assert method.get_original_description(_tool()) == "desc"
    assert method.get_original_description({"name": "x", "description": "plain"}) == "plain"


def test_generate_api_call_from_description(monkeypatch):
    method = APICallToExampleMethod(_config(), api_call_fn=lambda *_: None, eval_fn=lambda *_: None)

    def fake_get_rits_response(model, prompt, key, verify_fn, **kwargs):
        assert model == "gpt-gen"
        assert "search" in prompt
        return verify_fn('{"name":"search","arguments":{"q":"x"}}')

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.optimizer.tool.utils.toolcall_example_method.get_rits_response",
        fake_get_rits_response,
    )
    out = method.generate_api_call_from_description(
        _tool(),
        prev_output=[{"fn_call": {"name": "search"}, "tool_results": {"ok": 1}, "status_code": 0}],
    )
    assert out == {"name": "search", "arguments": {"q": "x"}}


def test_generate_api_call_from_description_validation_error(monkeypatch):
    method = APICallToExampleMethod(_config(), api_call_fn=lambda *_: None, eval_fn=lambda *_: None)

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.optimizer.tool.utils.toolcall_example_method.get_rits_response",
        lambda model, prompt, key, verify_fn, **kwargs: verify_fn('{"name":"other","arguments":{}}'),
    )
    with pytest.raises(ValueError):
        method.generate_api_call_from_description(_tool())


def test_critique_and_instruction_and_batch_methods(monkeypatch):
    method = APICallToExampleMethod(_config(), api_call_fn=lambda *_: None, eval_fn=lambda *_: None)

    payloads = iter(
        [
            '{"analysis":"ok","err_code":0}',
            '{"instruction":"I need weather in Beijing"}',
            '{"analysis":"good","score":3}',
            "reflection",
        ]
    )

    def fake_get_rits_response(model, prompt, key, verify_fn, **kwargs):
        return verify_fn(next(payloads))

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.optimizer.tool.utils.toolcall_example_method.get_rits_response",
        fake_get_rits_response,
    )

    fn_call = {"name": "search", "arguments": {"q": "x"}}
    tool_res = "r" * 3000
    critique = method.critique_api_call(_tool(), fn_call, tool_res)
    assert critique["err_code"] == 0

    inst = method.generate_instruction_from_api_call(
        _tool(), 
        fn_call, 
        "resp", 
        prev_output={
            "instructions": ["a"], 
            "scores": [1], 
            "batch_reflection": "b"
        }
    )
    assert "Beijing" in inst

    scored = method.critique_instruction(_tool(), "inst", fn_call, "resp", "ans")
    assert scored["score"] == 3

    refl = method.batch_reflection_with_scores(_tool(), fn_call, ["i1"], [2], ["a1"])
    assert refl == "reflection"


def test_step_full_flow(monkeypatch):
    eval_calls = {}

    def fake_eval(tool, description, examples, runs):
        eval_calls["examples"] = examples
        return {"score_avg": 50}

    method = APICallToExampleMethod(
        _config(),
        api_call_fn=lambda tool, fn_call: ('{"response":"ok"}', 0),
        eval_fn=fake_eval,
    )

    monkeypatch.setattr(
        method, 
        "generate_api_call_from_description", 
        lambda *args, 
        **kwargs: {"name": "search", "arguments": {"q": "x"}})
    critique_responses = iter(
        [
            {"analysis": "bad", "err_code": -1},
            {"analysis": "", "err_code": 0},
        ]
    )
    monkeypatch.setattr(method, "critique_api_call", lambda *args, **kwargs: next(critique_responses))

    inst_seq = iter(["inst-1", "inst-2"])
    ans_seq = iter(["ans-1", "ans-2"])
    score_seq = iter([{"analysis": "a", "score": 2}, {"analysis": "b", "score": 3}])
    monkeypatch.setattr(method, "generate_instruction_from_api_call", lambda *args, **kwargs: next(inst_seq))
    monkeypatch.setattr(method, "produce_answer_from_api_call", lambda *args, **kwargs: next(ans_seq))
    monkeypatch.setattr(method, "critique_instruction", lambda *args, **kwargs: next(score_seq))
    monkeypatch.setattr(method, "batch_reflection_with_scores", lambda *args, **kwargs: "refl")

    outputs, insts, score = method.step(_tool(), prev_outputs=[])
    assert insts == ["inst-1", "inst-2"]
    assert outputs["status_code"] == 0
    assert outputs["scores"][-1] == 3
    assert score == pytest.approx(3.25)
    assert eval_calls["examples"][0][0] == "inst-2"
