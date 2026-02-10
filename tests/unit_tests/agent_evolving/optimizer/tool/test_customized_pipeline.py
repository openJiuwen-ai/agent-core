# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

import pytest

from openjiuwen.agent_evolving.optimizer.tool.utils import customized_pipline as cp


class _DummyBeamSearch:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    @staticmethod
    def search(tool):
        return [[{"description": f"generated-{tool['name']}"}]]


class _DummyMethod:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _config(tmp_path):
    return {
        "beam_width": 1,
        "expand_num": 1,
        "max_depth": 1,
        "num_workers": 1,
        "verbose": False,
        "top_k": 1,
        "save_dir": str(tmp_path),
    }


def test_customized_pipeline_example_and_merge(monkeypatch, tmp_path):
    monkeypatch.setattr(cp, "SimpleAPIWrapperFromCallable", lambda tool_callable, name, config: "api-wrapper")
    monkeypatch.setattr(cp, "SimpleEval", lambda api_wrapper, config: "eval-fn")
    monkeypatch.setattr(cp, "APICallToExampleMethod", _DummyMethod)
    monkeypatch.setattr(cp, "ToolDescriptionMethod", _DummyMethod)
    monkeypatch.setattr(cp, "BeamSearch", _DummyBeamSearch)

    tool = {"name": "search"}
    cfg = _config(tmp_path)

    out = cp.customized_pipeline("example", tool, cfg, tool_callable=lambda x: x)
    assert out == [[{"description": "generated-search"}]]

    saved = tmp_path / "search.json"
    assert saved.exists()
    assert json.loads(saved.read_text(encoding="utf-8")) == out

    saved.write_text(json.dumps([[{"description": "old"}]], ensure_ascii=False), encoding="utf-8")
    out2 = cp.customized_pipeline("description", tool, cfg, tool_callable=lambda x: x)
    assert len(out2) == 2
    assert out2[0][0]["description"] == "old"


def test_customized_pipeline_error_paths(tmp_path):
    cfg = _config(tmp_path)
    with pytest.raises(NotImplementedError):
        cp.customized_pipeline("example", {"name": "t"}, {**cfg, "fn_call_path": "x"}, tool_callable=lambda x: x)

    with pytest.raises(ValueError):
        cp.customized_pipeline("example", {"name": "t"}, cfg, tool_callable=None)

    with pytest.raises(ValueError):
        cp.customized_pipeline("bad", {"name": "t"}, cfg, tool_callable=lambda x: x)
