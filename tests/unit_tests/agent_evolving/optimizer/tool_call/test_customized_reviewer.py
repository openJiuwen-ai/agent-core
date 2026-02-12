# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

import pytest

from openjiuwen.agent_evolving.optimizer.tool_call.utils.customized_reviewer import ToolDescriptionReviewer


def test_format_clean_cross_check_translate(monkeypatch):
    reviewer = ToolDescriptionReviewer(eval_model_id="gpt-eval", llm_api_key="k")
    responses = iter(
        [
            json.dumps({"name": "f", "description": "d", "parameters": {}}),
            json.dumps({"name": "f", "description": "d2", "parameters": {}}),
            json.dumps({"name": "f", "description": "d3", "parameters": {}}),
            json.dumps({"name": "f", "description": "中文", "parameters": {}}),
        ]
    )

    def fake_get_rits_response(model, prompt, key, verify_output=None, **kwargs):
        payload = next(responses)
        return verify_output(payload) if verify_output else payload

    monkeypatch.setattr(
        "openjiuwen.agent_evolving.optimizer.tool_call.utils.customized_reviewer.get_rits_response",
        fake_get_rits_response,
    )

    schema = {"name": "", "description": "", "parameters": {}}
    formatted = reviewer.format(schema, "raw desc")
    assert formatted["name"] == "f"

    cleaned = reviewer.clean_and_deduplicate(formatted)
    assert cleaned["description"] == "d2"

    checked = reviewer.cross_check(cleaned, ori_tool="ori")
    assert checked["description"] == "d3"

    monkeypatch.setattr(reviewer, "_is_mostly_english", lambda text: True)
    translated = reviewer.translate_to_chinese({"text": "hello world"})
    assert translated["description"] == "中文"

    monkeypatch.setattr(reviewer, "_is_mostly_english", lambda text: False)
    no_translate = reviewer.translate_to_chinese({"text": "你好"})
    assert no_translate == {"text": "你好"}


def test_process_with_steps_and_unknown_step(monkeypatch):
    reviewer = ToolDescriptionReviewer(eval_model_id="gpt-eval", llm_api_key="k")
    monkeypatch.setattr(reviewer, "clean_and_deduplicate", lambda data: {"c": data})
    monkeypatch.setattr(reviewer, "cross_check", lambda data, ori_tool: {"x": data, "ori": ori_tool})
    monkeypatch.setattr(reviewer, "translate_to_chinese", lambda data: {"t": data})

    out = reviewer.process({"a": 1}, ori_tool="ori", steps=["clean", "translate"])
    assert out == {"t": {"c": {"a": 1}}}

    out2 = reviewer.process({"a": 1}, ori_tool="ori", steps=["cross_check"])
    assert out2["ori"] == "ori"

    with pytest.raises(ValueError):
        reviewer.process({"a": 1}, ori_tool="ori", steps=["bad"])
