# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Artifact generation must respect the configured model output budget."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.foundation.llm import ModelRequestConfig
from openjiuwen.rsi.harness_rsi.member_optimizer import action_executor


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,expected", [(4096, 4096), (16384, 16384), (None, 8192)])
async def test_artifact_generation_respects_configured_budget(monkeypatch, configured, expected):
    model = SimpleNamespace(
        model_config=ModelRequestConfig(max_tokens=configured),
        invoke=AsyncMock(return_value=SimpleNamespace(content='{"status":"succeeded"}')),
    )
    monkeypatch.setattr(action_executor, "load_member_optimizer_model", lambda _: model)
    agent = action_executor.MemberActionExecutorAgent("mock-model.yaml")

    output = await agent._invoke_direct_action("Generate the declared artifact.")

    assert output == '{"status":"succeeded"}'
    assert model.invoke.await_count == 1
    assert model.invoke.await_args.kwargs["max_tokens"] == expected
    assert model.invoke.await_args.kwargs["tools"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", '{"status":"succeeded","file_writes":[]}'])
async def test_truncated_artifact_response_is_not_an_answer_or_format_retry(monkeypatch, content):
    model = SimpleNamespace(
        model_config=ModelRequestConfig(max_tokens=16384),
        invoke=AsyncMock(return_value=SimpleNamespace(content=content, finish_reason="length")),
    )
    monkeypatch.setattr(action_executor, "load_member_optimizer_model", lambda _: model)
    agent = action_executor.MemberActionExecutorAgent("mock-model.yaml")

    with pytest.raises(RuntimeError, match="finish_reason=length"):
        await agent._invoke_direct_action("Generate the declared artifact.")

    assert model.invoke.await_count == 1
