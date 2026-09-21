# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The Harness RSI roles share one output budget without editing source models."""

import json
from copy import deepcopy

import pytest

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
    assert adjusted.get("model", adjusted)["model_request_config"].get("max_tokens") == limit
    # Analyzer uses the shared reference loader; Task and Improver use the Model loader.
    loaded = load_model_config_ref(str(path))
    assert loaded.get("model", loaded)["model_request_config"].get("max_tokens") == limit
    built = load_member_optimizer_model(str(path))
    assert built.model_config.max_tokens == limit
    assert built.model_config.temperature == 0.5

    monkeypatch.setattr(judge_runtime, "create_deep_agent", lambda **kwargs: kwargs)
    judge = judge_runtime.build_judge_agent(
        EvaluatorConfig(judge_model_config_ref=str(path)), tmp_path, tmp_path / "tools.jsonl",
    )
    assert judge["model"].model_config.max_tokens == limit
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_missing_request_config_gets_budget_and_invalid_config_is_not_hidden():
    assert "max_tokens" not in with_rsi_output_budget({})["model_request_config"]
    assert with_rsi_output_budget({"model_request_config": "invalid"})["model_request_config"] == "invalid"


@pytest.mark.parametrize("configured,expected", [(None, 393216), (8192, 8192), (2000000, 393216)])
def test_known_model_capacity(configured, expected):
    request = {"model": "deepseek-v4-pro", "max_tokens": configured}
    assert with_rsi_output_budget({"model_request_config": request})["model_request_config"]["max_tokens"] == expected
    assert request["max_tokens"] == configured


@pytest.mark.parametrize("name", ["deepseek-v4-pro", "qwen-plus", "custom-gateway-model"])
def test_per_request_remaining_context_and_no_mutation(name):
    from types import SimpleNamespace

    from openjiuwen.rsi.harness_rsi.member_optimizer.budget_model import BudgetedRsiModel
    model = SimpleNamespace(model_config=SimpleNamespace(
        max_tokens=393216, model_name=name, context_window=20000,
    ))
    options = {"max_tokens": 393216}
    result = BudgetedRsiModel._budget_options(model, "a" * 10000, options)
    assert 0 < result["max_tokens"] < 6000
    assert options["max_tokens"] == model.model_config.max_tokens == 393216
    with pytest.raises(ValueError, match="no positive output budget"):
        BudgetedRsiModel._budget_options(model, "a" * 20000, options)


def test_unknown_output_capacity_still_checks_input_budget():
    from types import SimpleNamespace

    from openjiuwen.rsi.harness_rsi.member_optimizer.budget_model import BudgetedRsiModel

    model = SimpleNamespace(model_config=SimpleNamespace(
        max_tokens=None, model_name="custom-gateway-model", context_window=20000,
    ))
    assert BudgetedRsiModel._budget_options(model, "hello", {}) == {}
    with pytest.raises(ValueError, match="no positive output budget"):
        BudgetedRsiModel._budget_options(model, "a" * 20000, {})
