import json

import httpx
import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig, UserMessage
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport


def test_context_window_is_formal_model_metadata_and_not_provider_param():
    model_config = ModelRequestConfig(model="test-model", context_window=131072)
    client = OpenAIModelClient(
        model_config=model_config,
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="test-key",
            api_base="http://localhost",
        ),
    )

    request_params = client._build_request_params(
        messages=[UserMessage(content="hello")],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert model_config.context_window == 131072
    assert request_params.get("context_window") is None


_RESPONSES_SSE = (
    "event: response.output_text.delta\n"
    'data: {"delta":"ok"}\n\n'
    "event: response.completed\n"
    'data: {"response":{"id":"resp-1","status":"completed",'
    '"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
).encode()


def _responses_model(sent_bodies: list[dict]) -> Model:
    """A Responses-mode Model whose HTTP calls are answered locally and recorded."""

    async def handler(request: httpx.Request) -> httpx.Response:
        sent_bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, content=_RESPONSES_SSE, headers={"content-type": "text/event-stream"})

    model = Model(
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="mock-api-key",
            api_base="https://api.openai.com/v1",
            api_mode="responses",
        ),
        ModelRequestConfig(model="gpt-5.6-luna", context_window=131072, prompt_cache_key="cache-1"),
    )
    client = model._client
    client._make_responses_transport = lambda **_: OpenAIAccountResponsesTransport(
        transport=httpx.MockTransport(handler),
        request_fallback=client._responses_request_fallback,
    )
    return model


@pytest.mark.asyncio
async def test_responses_invoke_does_not_send_context_window():
    sent_bodies: list[dict] = []
    model = _responses_model(sent_bodies)

    response = await model.invoke("hello")

    assert response.content == "ok"
    assert len(sent_bodies) == 1
    assert "context_window" not in sent_bodies[0]
    # Other extra request fields are still forwarded, and the metadata stays readable.
    assert sent_bodies[0]["prompt_cache_key"] == "cache-1"
    assert model.model_config.context_window == 131072


@pytest.mark.asyncio
async def test_responses_stream_does_not_send_context_window():
    sent_bodies: list[dict] = []
    model = _responses_model(sent_bodies)

    chunks = [chunk async for chunk in model.stream("hello")]

    assert "".join(chunk.content or "" for chunk in chunks) == "ok"
    assert len(sent_bodies) == 1
    assert "context_window" not in sent_bodies[0]
    assert sent_bodies[0]["prompt_cache_key"] == "cache-1"
