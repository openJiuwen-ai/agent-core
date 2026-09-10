# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The Harness RSI roles share one output budget without editing source models."""

import json
from copy import deepcopy

import pytest

from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.judger import judge_runtime
from openjiuwen.rsi.harness_rsi.member_optimizer.agents.factory import load_member_optimizer_model
from openjiuwen.rsi.harness_rsi.member_optimizer.model_config import (
    load_model_config_ref,
    with_rsi_output_budget,
)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("limit", [None, 8192, 16384, 20000])
def test_role_loaders_share_output_budget(tmp_path, monkeypatch, nested, limit):
    model = {
        "model_client_config": {
            "client_provider": "OpenAI", "api_key": "test", "api_base": "https://example.test/v1",
        },
        "model_request_config": {"model": "test", "temperature": 0.5},
    }
    if limit is not None:
        model["model_request_config"]["max_tokens"] = limit
    source = {"model": model} if nested else model
    original = deepcopy(source)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    adjusted = with_rsi_output_budget(source)
    assert source == original
    assert adjusted.get("model", adjusted)["model_request_config"]["max_tokens"] == 100000
    # Analyzer uses the shared reference loader; Task and Improver use the Model loader.
    loaded = load_model_config_ref(str(path))
    assert loaded.get("model", loaded)["model_request_config"]["max_tokens"] == 100000
    built = load_member_optimizer_model(str(path))
    assert built.model_config.max_tokens == 100000
    assert built.model_config.temperature == 0.5

    monkeypatch.setattr(TeamModelConfig, "build", lambda self: self)
    monkeypatch.setattr(judge_runtime, "create_deep_agent", lambda **kwargs: kwargs)
    judge = judge_runtime.build_judge_agent(
        EvaluatorConfig(judge_model_config_ref=str(path)), tmp_path, tmp_path / "tools.jsonl",
    )
    assert judge["model"].model_request_config.max_tokens == 100000
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_missing_request_config_gets_budget_and_invalid_config_is_not_hidden():
    assert with_rsi_output_budget({})["model_request_config"]["max_tokens"] == 100000
    assert with_rsi_output_budget({"model_request_config": "invalid"})["model_request_config"] == "invalid"
