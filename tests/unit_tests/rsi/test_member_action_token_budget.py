# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Artifact generation must respect the configured model output budget."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import certifi
import httpx
import pytest
import yaml
from openai import AsyncOpenAI

from openjiuwen.core.foundation.llm import ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
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
@pytest.mark.parametrize("action_group", ["skill", "tool"])
@pytest.mark.parametrize(
    "model_name, extra_body",
    [
        ("deepseek-v4-pro", {"thinking": {"type": "disabled"}}),
        ("deepseek-v4-pro", {"thinking": {"type": "enabled"}}),
        ("qwen-plus", {"enable_thinking": False, "custom_option": "preserved"}),
        ("custom-model", {}),
    ],
)
async def test_artifact_generation_preserves_wire_model_options(
    tmp_path,
    monkeypatch,
    action_group,
    model_name,
    extra_body,
):
    config_path = tmp_path / "model.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "model_client_config": {
                    "client_provider": "OpenAI",
                    "api_base": "https://example.test/v1",
                    "api_key": "test-only",
                    "ssl_cert": certifi.where(),
                    "max_retries": 0,
                },
                "model_request_config": {
                    "model": model_name,
                    "max_tokens": 16384,
                    "extra_body": extra_body,
                },
            }
        ),
        encoding="utf-8",
    )
    requests = []
    content = '{"status":"succeeded","file_writes":[]}'

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": model_name,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
            },
        )

    async with AsyncOpenAI(
        api_key="test-only",
        base_url="https://example.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as sdk:
        monkeypatch.setattr(OpenAIModelClient, "_create_async_openai_client", lambda self, **kwargs: sdk)
        agent = action_executor.MemberActionExecutorAgent(str(config_path))
        invoke = getattr(agent, f"_invoke_direct_{action_group}_action")
        assert await invoke("Generate the declared artifact.") == content

    assert len(requests) == 1
    body = requests[0]
    assert body["model"] == model_name
    assert body["max_tokens"] == 100000
    if model_name.startswith("deepseek"):
        assert body["thinking"] == {"type": "disabled"}
    elif model_name == "qwen-plus":
        assert body["enable_thinking"] is False
        assert body["custom_option"] == "preserved"
    else:
        assert "thinking" not in body
        assert "enable_thinking" not in body


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
