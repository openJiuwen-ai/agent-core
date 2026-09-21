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
    assert adjusted.get("model", adjusted)["model_request_config"].get("max_tokens") is None
    # Analyzer uses the shared reference loader; Task and Improver use the Model loader.
    loaded = load_model_config_ref(str(path))
    assert loaded.get("model", loaded)["model_request_config"].get("max_tokens") is None
    built = load_member_optimizer_model(str(path))
    assert built.model_config.max_tokens is None
    assert built.model_config.temperature == 0.5

    monkeypatch.setattr(judge_runtime, "create_deep_agent", lambda **kwargs: kwargs)
    judge = judge_runtime.build_judge_agent(
        EvaluatorConfig(judge_model_config_ref=str(path)), tmp_path, tmp_path / "tools.jsonl",
    )
    assert judge["model"].model_config.max_tokens is None
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_missing_request_config_gets_budget_and_invalid_config_is_not_hidden():
    assert with_rsi_output_budget({})["model_request_config"]["max_tokens"] is None
    assert with_rsi_output_budget({"model_request_config": "invalid"})["model_request_config"] == "invalid"


@pytest.mark.parametrize("configured", [None, 8192, 2000000])
def test_known_model_does_not_receive_an_output_cap(configured):
    request = {"model": "deepseek-v4-pro", "max_tokens": configured}
    assert with_rsi_output_budget({"model_request_config": request})["model_request_config"]["max_tokens"] is None
    assert request["max_tokens"] == configured


def test_legacy_output_caps_are_removed_without_changing_shared_config():
    source = {"model_request_config": {"model": "custom", "max_tokens": 8192,
              "max_completion_tokens": 8192, "extra_body": {"max_tokens": 4096,
              "max_completion_tokens": 4096, "custom_option": True}}}
    original = deepcopy(source)
    request = with_rsi_output_budget(source)["model_request_config"]
    assert request["max_tokens"] is None
    assert "max_completion_tokens" not in request
    assert request["extra_body"] == {"custom_option": True}
    assert source == original


@pytest.mark.parametrize("name", ["deepseek-v4-pro", "qwen-plus", "custom-gateway-model"])
def test_per_request_remaining_context_and_no_mutation(name):
    from types import SimpleNamespace

    from openjiuwen.rsi.harness_rsi.member_optimizer.budget_model import BudgetedRsiModel
    model = SimpleNamespace(model_config=SimpleNamespace(
        max_tokens=393216, model_name=name, context_window=20000,
    ))
    options = {"max_tokens": 393216}
    result = BudgetedRsiModel._budget_options(model, "a" * 10000, options)
    assert "max_tokens" not in result
    assert options["max_tokens"] == model.model_config.max_tokens == 393216
    assert BudgetedRsiModel._budget_options(model, "a" * 20000, options) == {}


def test_unknown_output_capacity_does_not_reject_input_locally():
    from types import SimpleNamespace

    from openjiuwen.rsi.harness_rsi.member_optimizer.budget_model import BudgetedRsiModel

    model = SimpleNamespace(model_config=SimpleNamespace(
        max_tokens=None, model_name="custom-gateway-model", context_window=20000,
    ))
    assert BudgetedRsiModel._budget_options(model, "hello", {}) == {}
    assert BudgetedRsiModel._budget_options(model, "a" * 20000, {}) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_large_input_reaches_provider_unchanged(monkeypatch, streaming):
    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
    from openjiuwen.rsi.harness_rsi.member_optimizer.budget_model import BudgetedRsiModel

    messages = [{"role": "user", "content": "evidence" * 10000}]
    tools = [{"type": "function", "function": {"name": "read", "description": "schema" * 10000}}]
    original = deepcopy((messages, tools))
    seen = []
    provider_error = RuntimeError("provider context limit")

    async def invoke(self, **kwargs):
        seen.append(kwargs)
        raise provider_error

    async def stream(self, **kwargs):
        seen.append(kwargs)
        yield "first chunk"
        raise provider_error

    monkeypatch.setattr(Model, "invoke", invoke)
    monkeypatch.setattr(Model, "stream", stream)
    model = BudgetedRsiModel(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="test", api_base="https://example.test/v1",
        ),
        model_config=ModelRequestConfig(model="custom", context_window=100),
    )
    chunks = []
    with pytest.raises(RuntimeError) as caught:
        if streaming:
            async for chunk in model.stream(messages, tools=tools, max_tokens=10, max_completion_tokens=10):
                chunks.append(chunk)
        else:
            await model.invoke(messages, tools=tools, max_tokens=10, max_completion_tokens=10)
    assert chunks == (["first chunk"] if streaming else [])
    assert caught.value is provider_error
    assert seen == [{"messages": messages, "tools": tools}]
    assert (messages, tools) == original
