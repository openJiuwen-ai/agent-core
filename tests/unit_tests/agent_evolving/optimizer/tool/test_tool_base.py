# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from copy import deepcopy

import pytest

from openjiuwen.agent_evolving.optimizer.tool.base import ToolOptimizerBase
from openjiuwen.agent_evolving.optimizer.tool import base as tool_base


def test_tool_optimizer_base_init_and_default_targets(tmp_path):
    cfg_eg = {"x": 1}
    cfg_desc = {"y": 2}
    optimizer = ToolOptimizerBase(
        max_turns=2,
        llm_api_key="k",
        config_eg=cfg_eg,
        config_desc=cfg_desc,
        path_save_dir=str(tmp_path),
        tool_name="search",
    )
    assert optimizer.default_targets() == ["enabled", "max_retries"]
    assert optimizer.config_eg["save_dir"].endswith("examples")
    assert optimizer.config_desc["save_dir"].endswith("descriptions")
    assert optimizer.config_desc["examples_dir"].endswith("examples")
    assert optimizer.config_desc["neg_ex_input_path"].endswith("search.json")


def test_tool_optimizer_optimize_tool_with_mocks(monkeypatch, tmp_path):
    class _FakeReviewer:
        def __init__(self, eval_model_id, llm_api_key):
            self.eval_model_id = eval_model_id
            self.llm_api_key = llm_api_key
            self.calls = []

        def process(self, data, ori_tool, steps):
            self.calls.append((data, ori_tool, steps))
            return {"processed": data, "ori": ori_tool}

        @staticmethod
        def format(schema, processed, example=None):
            return {"schema": schema, "processed": processed}

    desc_iter = iter(
        [
            [[{"description": "desc-1"}]],
            [[{"description": "desc-2"}]],
        ]
    )

    def fake_pipeline(stage, tool, tool_callable=None, config=None):
        if stage == "example":
            return [{"example": True}]
        return next(desc_iter)

    monkeypatch.setattr(tool_base, "customized_pipeline", fake_pipeline)
    monkeypatch.setattr(tool_base, "extract_schema", lambda ori_desc: {"name": ""})
    monkeypatch.setattr(tool_base, "ToolDescriptionReviewer", _FakeReviewer)

    optimizer = ToolOptimizerBase(
        max_turns=2,
        llm_api_key="api-key",
        config_eg=deepcopy({"eval_model_id": "x"}),
        config_desc=deepcopy({"eval_model_id": "eval-m"}),
        path_save_dir=str(tmp_path),
        tool_name="search",
    )
    tool = {"name": "search", "description": '{"name":"search"}'}
    out = optimizer.optimize_tool(tool, tool_callable=lambda x: x)
    assert out["schema"] == {"name": ""}
    assert out["processed"]["processed"] == "desc-2"


# def test_tool_optimizer_update_returns_none():
#     optimizer = ToolOptimizerBase()
#     with pytest.raises(AttributeError):
#         optimizer._update()
