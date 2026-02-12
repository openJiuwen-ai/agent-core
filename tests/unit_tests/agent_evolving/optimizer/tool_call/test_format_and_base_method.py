# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.agent_evolving.optimizer.tool_call.utils import base_method, format as fmt


def test_parse_json_prefers_header_and_fallback_literal_eval():
    text = 'noise {"answer": "ok", "x": 1} tail'
    assert fmt.parse_json(text, header="answer") == {"answer": "ok", "x": 1}

    literal = "{'answer': 'ok', 'x': 2}"
    assert base_method.parse_json(literal) == {"answer": "ok", "x": 2}


def test_format_prompt_llama_and_print_bold_noop():
    assert fmt.format_prompt_llama("sys", "user") == "sysuser"
    assert base_method.format_prompt_llama("a", "b") == "ab"
    assert base_method.print_bold("hello") is None


def test_base_method_produce_answer_from_api_call_success(monkeypatch):
    calls = {}

    def fake_get_rits_response(model_id, prompt, api_key, verify_fn, **kwargs):
        calls["model_id"] = model_id
        calls["prompt"] = prompt
        calls["api_key"] = api_key
        calls["kwargs"] = kwargs
        return verify_fn('{"answer": "final answer"}')

    monkeypatch.setattr(base_method, "get_rits_response", fake_get_rits_response)

    method = base_method.BaseMethod(
        {"gen_model_id": "gpt-x", "llm_api_key": "k", "verbose": False}
    )
    out = method.produce_answer_from_api_call("inst", "doc", "api_result")
    assert out == "final answer"
    assert calls["model_id"] == "gpt-x"
    assert calls["api_key"] == "k"
    assert "inst" in calls["prompt"]


def test_base_method_produce_answer_from_api_call_verify_error(monkeypatch):
    def fake_get_rits_response(model_id, prompt, api_key, verify_fn, **kwargs):
        return verify_fn('{"error":"bad"}')

    monkeypatch.setattr(base_method, "get_rits_response", fake_get_rits_response)

    method = base_method.BaseMethod(
        {"gen_model_id": "gpt-x", "llm_api_key": "k", "verbose": False}
    )
    with pytest.raises(ValueError):
        method.produce_answer_from_api_call("inst", "doc", "api_result")
