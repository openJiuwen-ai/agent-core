# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Frontend model reasoning cannot override the RSI backend policy."""

import json
from copy import deepcopy

import pytest

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.reasoning import resolve_reasoning_plan
from openjiuwen.rsi.harness_rsi.member_optimizer.agents.factory import load_member_optimizer_model
from openjiuwen.rsi.harness_rsi.member_optimizer.model_config import load_model_config_ref, with_rsi_reasoning_policy


@pytest.mark.parametrize("nested", [False, True])
def test_frontend_enabled_is_overridden_without_mutating_config(tmp_path, nested):
    model = {
        "model_client_config": {"client_provider": "OpenAI", "api_key": "test",
                                "api_base": "https://api.deepseek.com/v1"},
        "model_request_config": {
            "model": "deepseek-v4-pro", "max_tokens": 100000,
            "reasoning": {"mode": "enabled"}, "reasoning_effort": "high",
            "extra_body": {"thinking": {"type": "enabled"}, "custom_option": "keep"},
        },
    }
    source = {"model": model} if nested else model
    original = deepcopy(source)
    adjusted = with_rsi_reasoning_policy(source)
    assert source == original
    path = tmp_path / "model.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    assert load_model_config_ref(str(path)) == adjusted
    built = load_member_optimizer_model(str(path))
    assert built.model_config.reasoning.mode == "disabled"
    assert built.model_config.max_tokens == 100000
    request = adjusted.get("model", adjusted)["model_request_config"]
    assert request["extra_body"] == {"custom_option": "keep"}
    plan = resolve_reasoning_plan(ModelClientConfig(**model["model_client_config"]), ModelRequestConfig(**request))
    assert plan.extra_body == {"thinking": {"type": "disabled"}}
    assert json.loads(path.read_text(encoding="utf-8")) == original
