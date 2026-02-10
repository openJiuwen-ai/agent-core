# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

from openjiuwen.agent_evolving.optimizer.tool.utils.customized_api import (
    SimpleAPIWrapper,
    SimpleAPIWrapperFromCallable,
    load_custom_data,
)


def test_simple_api_wrapper_call_success_not_found_and_exception():
    def ok_fn(params):
        return {"echo": params}

    wrapper = SimpleAPIWrapper(fn_call_name="ok_fn", custom_functions={"ok_fn": ok_fn})
    payload, code = wrapper({"name": "ok_fn"}, {"a": 1})
    assert code == 0
    assert json.loads(payload)["response"] == {"echo": {"a": 1}}

    missing = SimpleAPIWrapper(fn_call_name="missing", custom_functions={"ok_fn": ok_fn})
    payload2, code2 = missing({"name": "missing"}, {"a": 1})
    assert code2 == 12
    assert "no function" in json.loads(payload2)["error"]

    def bad_fn(params):
        raise ValueError("boom")

    bad = SimpleAPIWrapper(fn_call_name="bad_fn", custom_functions={"bad_fn": bad_fn})
    payload3, code3 = bad({"name": "bad_fn"}, {"a": 1})
    assert code3 == 12
    assert "boom" in json.loads(payload3)["error"]


def test_simple_api_wrapper_load_module_and_add_function(tmp_path):
    mod = tmp_path / "toy_module.py"
    mod.write_text(
        "def ping(params):\n"
        "    return {'pong': params.get('x')}\n"
        "def _hidden():\n"
        "    return 0\n",
        encoding="utf-8",
    )
    wrapper = SimpleAPIWrapper(tool_path=str(mod), fn_call_name="ping")
    payload, code = wrapper({"name": "ping"}, {"x": 9})
    assert code == 0
    assert json.loads(payload)["response"] == {"pong": 9}

    wrapper.add_function("sum2", lambda p: p["a"] + p["b"])
    wrapper.fn_call_name = "sum2"
    payload2, code2 = wrapper({"name": "sum2"}, {"a": 1, "b": 2})
    assert code2 == 0
    assert json.loads(payload2)["response"] == 3


def test_simple_api_wrapper_from_callable():
    wrapper = SimpleAPIWrapperFromCallable(lambda p: p["v"] * 2, "f", {})
    payload, code = wrapper({"name": "f"}, {"v": 3})
    assert code == 0
    assert json.loads(payload)["response"] == 6


def test_load_custom_data_jsonl_and_json(tmp_path):
    jsonl = tmp_path / "x.jsonl"
    jsonl.write_text(
        json.dumps({"function": {"name": "f1"}}) + "\n"
        + json.dumps({"function": [{"name": "f2"}, {"name": "f3"}]}),
        encoding="utf-8",
    )
    tools = load_custom_data(str(jsonl), api_wrapper=None)
    assert [t["function"]["name"] for t in tools] == ["f1", "f2", "f3"]

    as_list = tmp_path / "list.json"
    as_list.write_text(json.dumps([{"function": {"name": "a"}}, {"name": "b"}]), encoding="utf-8")
    tools2 = load_custom_data(str(as_list), api_wrapper=None)
    assert [t["function"]["name"] for t in tools2] == ["a", "b"]

    as_obj = tmp_path / "obj.json"
    as_obj.write_text(json.dumps({"functions": [{"name": "c"}]}), encoding="utf-8")
    tools3 = load_custom_data(str(as_obj), api_wrapper=None)
    assert [t["function"]["name"] for t in tools3] == ["c"]
