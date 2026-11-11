# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Explicit RSI output limits survive the complete provider request path."""
import json

import certifi
import httpx
import pytest
from openai import AsyncOpenAI

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.rsi.harness_rsi.config import EvaluatorConfig
from openjiuwen.rsi.harness_rsi.evaluator.judger.judge_runtime import _judge_model
from openjiuwen.rsi.harness_rsi.member_optimizer.agents.factory import load_member_optimizer_model


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['judge', 'member'])
@pytest.mark.parametrize('request_config, expected', [
    ({}, {}),
    ({'max_tokens': None}, {}),
    ({'max_completion_tokens': None}, {}),
    ({'extra_body': {'max_tokens': None, 'max_completion_tokens': None}}, {}),
    ({'max_tokens': 8192}, {'max_tokens': 8192}),
    ({'max_completion_tokens': 12288}, {'max_completion_tokens': 12288}),
    ({'extra_body': {'max_tokens': 16384}}, {'max_tokens': 16384}),
    ({'extra_body': {'max_completion_tokens': 20000}}, {'max_completion_tokens': 20000}),
])
async def test_configured_budget_reaches_wire_and_none_is_omitted(tmp_path, monkeypatch, role, request_config, expected):
    config = tmp_path / 'model.json'
    config.write_text(json.dumps({
        'model_client_config': {'client_provider': 'OpenAI', 'api_key': 'test-only',
                                'api_base': 'https://example.test/v1', 'ssl_cert': certifi.where()},
        'model_request_config': {'model': 'custom-model', **request_config},
    }))
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            'id': 'test', 'object': 'chat.completion', 'created': 0, 'model': 'custom-model',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}],
        })

    async with AsyncOpenAI(api_key='test-only', base_url='https://example.test/v1',
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as sdk:
        monkeypatch.setattr(OpenAIModelClient, '_create_async_openai_client', lambda _self, **_kwargs: sdk)
        model = (_judge_model(EvaluatorConfig(judge_model_config_ref=str(config))) if role == 'judge'
                 else load_member_optimizer_model(str(config)))
        assert (await model.invoke([UserMessage(content='Grade the supplied evidence')])).content == 'ok'
    assert len(requests) == 1
    assert {key: requests[0][key] for key in ('max_tokens', 'max_completion_tokens') if key in requests[0]} == expected
