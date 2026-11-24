# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
from contextlib import AsyncExitStack
from copy import deepcopy

import anthropic
import httpx
import openai
import pytest
import pytest_asyncio

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import (
    LLMApiMode,
    LLMAuthMode,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.foundation.llm.request_context import disabled_thinking_fallback_scope
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport
from openjiuwen.symphony import CapabilityDescriptor, FingerprintService, OrchestrationService, SourceSnapshot
from openjiuwen.symphony.config import FingerprintExtractionConfig, FingerprintPolicyConfig


_TEMPERATURE_ERROR = "invalid temperature: only 1 is allowed for this model"
_ROUTES = {
    "chat": (LLMApiMode.ChatCompletions, "/v1/chat/completions"),
    "responses": (LLMApiMode.Responses, "/v1/responses"),
    "responses_account": (LLMApiMode.Responses, "/v1/responses"),
    "messages": (LLMApiMode.AnthropicMessages, "/v1/messages"),
}


class _Inventory:
    async def inventory_snapshot(self):
        return SourceSnapshot(snapshot_id="test-skills", capability_count=1), [
            CapabilityDescriptor(
                capability_id="extract-text", capability_type="skill", name="Extract text",
            ),
        ]


def _gateway_error(route, message, *, status=400, **fields):
    body = {"error": {"type": "invalid_request_error", "message": message, **fields}}
    if route == "messages":
        body["type"] = "error"
    return httpx.Response(status, json=body)


def _completion(route, model):
    content = '{"description":"Extract text from a document.","inputs":[],"outputs":[]}'
    if route.startswith("responses"):
        event = "event: response.output_text.delta\ndata: " + json.dumps({"delta": content}) + "\n\n"
        return httpx.Response(200, text=event, headers={"Content-Type": "text/event-stream"})
    if route == "messages":
        return httpx.Response(200, json={
            "id": "msg-1", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": content}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 2, "output_tokens": 1},
        })
    return httpx.Response(200, json={
        "id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
    })


@pytest_asyncio.fixture
async def make_model(monkeypatch):
    async with AsyncExitStack() as cleanup:
        def make(route, respond, **request_config):
            mode, path = _ROUTES[route]
            base_url = "https://mock.example.invalid"
            if mode != LLMApiMode.AnthropicMessages:
                base_url += "/v1"
            auth_mode = LLMAuthMode.OpenAIAccountOAuth if route == "responses_account" else LLMAuthMode.ApiKey
            model = Model(
                model_client_config=ModelClientConfig(
                    client_provider="Anthropic" if route == "messages" else "OpenAI",
                    api_mode=mode, auth_mode=auth_mode, api_base=base_url, api_key="mock-key", verify_ssl=False,
                ),
                model_config=ModelRequestConfig(model="fixture-model", **request_config),
            )
            if route == "responses_account":
                monkeypatch.setattr(model._client._auth_manager, "resolve_access_token", lambda: "mock-oauth-token")

            def handler(request):
                assert request.url.path == path
                if route == "responses_account":
                    assert request.headers["Authorization"] == "Bearer mock-oauth-token"
                return respond(json.loads(request.content))

            def make_http_client():
                client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                cleanup.push_async_callback(client.aclose)
                return client

            if mode == LLMApiMode.Responses:
                monkeypatch.setattr(OpenAIAccountResponsesTransport, "_make_client", lambda _: make_http_client())
            elif mode == LLMApiMode.AnthropicMessages:
                sdk = anthropic.AsyncAnthropic(
                    base_url=base_url, api_key="mock-key", http_client=make_http_client(), max_retries=0, timeout=60.0,
                )
                monkeypatch.setattr(model._client, "_create_async_anthropic_client", lambda **_: sdk)
            else:
                sdk = openai.AsyncOpenAI(
                    base_url=base_url, api_key="mock-key", http_client=make_http_client(), max_retries=0,
                )
                monkeypatch.setattr(model._client, "_create_async_openai_client", lambda **_: sdk)
            return model

        yield make


@pytest.mark.asyncio
@pytest.mark.parametrize("route, sends_temperature", [
    ("chat", True), ("responses", True), ("responses_account", True), ("messages", True), ("responses_account", False),
])
async def test_graph_build_uses_model_defaults_after_temperature_rejection(
        tmp_path, make_model, route, sends_temperature,
):
    requests = []

    def respond(body):
        requests.append(body)
        if "temperature" in body:
            return _gateway_error(route, _TEMPERATURE_ERROR)
        return _completion(route, body["model"])

    extra_body = {"routing": "keep"}
    if route.startswith("responses") and sends_temperature:
        extra_body["temperature"] = 0.0
    model = make_model(route, respond, temperature=1.0, extra_body=extra_body)
    fingerprints = FingerprintService(
        _Inventory(), tmp_path / "fingerprints", llm=model,
        config=FingerprintPolicyConfig(extraction=FingerprintExtractionConfig(llm_enabled=True)),
    )
    graph = OrchestrationService(
        graph_artifact_root=tmp_path / "graph", capability_provider=[], model=model, fingerprint_service=fingerprints,
    )

    result = await graph.build()

    payload = json.loads(result.graph_path.read_text(encoding="utf-8"))
    assert [node["properties"]["capability_id"] for node in payload["nodes"]] == ["extract-text"]
    assert graph.status().version == result.version
    if sends_temperature:
        first, retry = requests
        assert first["temperature"] == 0.0
        assert retry == {key: value for key, value in first.items() if key != "temperature"}
    else:
        assert len(requests) == 1
        assert "temperature" not in requests[0]
    assert requests[-1]["routing"] == "keep"
    assert model.model_config.temperature == 1.0
    assert model.model_config.extra_body == extra_body


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["chat", "responses", "messages"])
@pytest.mark.parametrize("temperature_first", [True, False])
async def test_temperature_and_disabled_thinking_each_retry_once(make_model, route, temperature_first):
    requests = []
    thinking_key = "reasoning" if route == "responses" else "thinking"
    thinking = {"effort": "none"} if route == "responses" else {"type": "disabled"}
    thinking_error = (
        "Unsupported value: 'reasoning.effort' does not support 'none' with this model."
        if route == "responses" else "This model does not support disabled thinking."
    )
    rejections = [("temperature", _TEMPERATURE_ERROR), (thinking_key, thinking_error)]
    if not temperature_first:
        rejections.reverse()

    def respond(body):
        requests.append(body)
        for key, error in rejections:
            if key in body:
                return _gateway_error(route, error)
        return _completion(route, body["model"])

    extras = {"temperature": 0.0, thinking_key: thinking, "routing": "keep"}
    original_extras = deepcopy(extras)
    model = make_model(route, respond)
    with disabled_thinking_fallback_scope():
        response = await model.invoke("extract", temperature=0.2, extra_body=extras)

    assert "Extract text" in response.content
    initial, first_retry, second_retry = requests
    first_key, second_key = [key for key, _ in rejections]
    assert initial[thinking_key] == thinking
    assert "temperature" in initial
    assert first_retry == {key: value for key, value in initial.items() if key != first_key}
    assert second_retry == {key: value for key, value in first_retry.items() if key != second_key}
    assert extras == original_extras


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _ROUTES)
async def test_temperature_cache_is_scoped_to_the_model_value_and_thinking(make_model, route):
    temperatures = []
    disabled = {"thinking": {"type": "disabled"}}

    def respond(body):
        temperature = body.get("temperature")
        temperatures.append(temperature)
        if body["model"] == "fixture-model" and body.get("thinking") == disabled["thinking"] and temperature == 0:
            return _gateway_error(route, "This model does not support custom sampling.",
                                  param="temperature", code="unsupported_parameter")
        return _completion(route, body["model"])

    model = make_model(route, respond)
    sampling = {"send_sampling_params": True} if route.startswith("responses") else {}
    with disabled_thinking_fallback_scope():
        await model.invoke("extract", temperature=0, extra_body=disabled, **sampling)
        await model.invoke("extract", temperature=0, extra_body=disabled, timeout=5, **sampling)
        await model.invoke("extract", temperature=1, extra_body=disabled, **sampling)
        await model.invoke("extract", temperature=0, **sampling)
        await model.invoke("extract", temperature=0, extra_body=disabled, model="another-model", **sampling)
    with pytest.raises(BaseError, match="does not support custom sampling"):
        await model.invoke("extract", temperature=0, extra_body=disabled, **sampling)

    assert temperatures == [0, None, None, 1, 0, 0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status, message, fields", [
    (400, "temperature must be between 0 and 1", {"param": "temperature", "code": "invalid_value"}),
    (400, "temperature cannot be set to 3", {"param": "temperature", "code": "invalid_value"}),
    (400, "Unsupported parameter: top_p, which cannot be used with temperature", {"param": "top_p"}),
    (400, "Invalid tool schema", {}),
    (401, _TEMPERATURE_ERROR, {}),
    (422, _TEMPERATURE_ERROR, {}),
    (429, _TEMPERATURE_ERROR, {}),
])
async def test_unrelated_errors_are_not_temperature_rejections(make_model, status, message, fields):
    requests = []

    def respond(body):
        requests.append(body)
        return _gateway_error("responses", message, status=status, **fields)

    model = make_model("responses", respond)
    with disabled_thinking_fallback_scope():
        with pytest.raises(BaseError, match=message):
            await model.invoke("extract", temperature=0, send_sampling_params=True)

    assert len(requests) == 1
    assert requests[0]["temperature"] == 0


@pytest.mark.asyncio
async def test_failed_temperature_retry_is_not_cached(make_model):
    requests = []
    errors = iter([_TEMPERATURE_ERROR, "Invalid tool schema", _TEMPERATURE_ERROR])

    def respond(body):
        requests.append(body)
        error = next(errors, None)
        if error:
            return _gateway_error("chat", error)
        return _completion("chat", body["model"])

    model = make_model("chat", respond)
    with disabled_thinking_fallback_scope():
        with pytest.raises(BaseError, match="Invalid tool schema"):
            await model.invoke("extract", temperature=0)
        await model.invoke("extract", temperature=0)
        await model.invoke("extract", temperature=0)

    assert [body.get("temperature") for body in requests] == [0, None, 0, None, None]


@pytest.mark.asyncio
async def test_responses_does_not_restart_after_receiving_sse(make_model):
    requests = []

    def respond(body):
        requests.append(body)
        return httpx.Response(
            200,
            text=("event: response.output_text.delta\ndata: {\"delta\":\"part\"}\n\n"
                  "event: error\ndata: invalid temperature: only 1 is allowed for this model\n\n"),
            headers={"Content-Type": "text/event-stream"},
        )

    model = make_model("responses", respond)
    chunks = []
    with disabled_thinking_fallback_scope():
        with pytest.raises(BaseError, match="Invalid OpenAI account SSE JSON"):
            async for chunk in model.stream("extract", temperature=0, send_sampling_params=True):
                chunks.append(chunk.content)

    assert chunks == ["part"]
    assert len(requests) == 1
