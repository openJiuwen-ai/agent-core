# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the lightweight Jev System One client and wire schema."""

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation import llm
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UserMessage
from openjiuwen.core.foundation.llm.system_one import (
    ChoiceAnswer,
    ChoiceQuestion,
    JevSystemOneClient,
    NoulAnswer,
    NoulCriteria,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneResponse,
)


def _response(data: dict, status_code: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://api.typesafe.ai/v1/systemone")
    return httpx.Response(status_code, json=data, headers=headers, request=request)


def _response_data(answer: dict, *, model: str = "jev-1.13.0") -> dict:
    return {
        "model": model,
        "answers": {"result": answer},
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }


def _make_client(response: httpx.Response | None = None, **overrides) -> tuple[JevSystemOneClient, AsyncMock]:
    http_client = AsyncMock(spec=httpx.AsyncClient)
    if response is not None:
        http_client.post.return_value = response
    kwargs = {
        "api_key": "test-key",
        "api_base": "https://api.typesafe.ai",
        "http_client": http_client,
        **overrides,
    }
    return JevSystemOneClient(**kwargs), http_client


class TestJevSystemOneClientConstruction:
    def test_public_api_is_scoped_to_system_one_package(self):
        assert "JevSystemOneClient" not in llm.__all__

    def test_is_a_standalone_system_one_client(self):
        client, _ = _make_client()

        assert not isinstance(client, BaseModelClient)
        assert client.model_name == "jev-latest"

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"api_key": ""}, "api_key"),
            ({"api_base": ""}, "api_base"),
            ({"model_name": ""}, "model_name"),
            ({"max_retries": -1}, "max_retries"),
            ({"retry_backoff": -1}, "retry_backoff"),
        ],
    )
    def test_rejects_invalid_configuration(self, overrides, message):
        with pytest.raises(BaseError, match=message):
            _make_client(**overrides)

    @pytest.mark.asyncio
    async def test_context_manager_closes_owned_http_client(self):
        owned_http_client = AsyncMock(spec=httpx.AsyncClient)
        with patch(
            "openjiuwen.core.foundation.llm.system_one.client.httpx.AsyncClient",
            return_value=owned_http_client,
        ):
            async with JevSystemOneClient(api_key="test-key"):
                pass

        owned_http_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aclose_does_not_close_injected_http_client(self):
        client, http_client = _make_client()

        await client.aclose()

        http_client.aclose.assert_not_awaited()


class TestSystemOneSchema:
    def test_noul_criteria_fields_are_independently_optional(self):
        question = NoulQuestion(
            instructions="Is this urgent?",
            criteria=NoulCriteria(true="Explicitly time-sensitive"),
        )

        assert question.model_dump(exclude_none=True)["criteria"] == {"true": "Explicitly time-sensitive"}

    def test_choice_criteria_accepts_null_descriptions(self):
        question = ChoiceQuestion(
            instructions="What is the tone?",
            criteria={"calm": None, "angry": "Upset or hostile"},
        )

        assert question.criteria["calm"] is None

    def test_score_accepts_structured_criteria_and_legend(self):
        question = ScoreQuestion(
            instructions={"task": "Rate severity"},
            criteria=[["cosmetic", "minor"], {"level": "blocking"}],
        )
        response = SystemOneResponse.model_validate(
            _response_data(
                {
                    "type": "score",
                    "score": 0.7,
                    "confidence": 0.8,
                    "legend": {"0": ["cosmetic", "minor"], "1": {"level": "blocking"}},
                    "probabilities": {"0": 0.3, "1": 0.7},
                }
            )
        )

        assert question.criteria[0] == ["cosmetic", "minor"]
        answer = response.answers["result"]
        assert isinstance(answer, ScoreAnswer)
        assert answer.legend["0"] == ["cosmetic", "minor"]

    def test_response_accepts_typed_openrouter_extensions(self):
        payload = _response_data({"type": "noul", "noul": 0.8}, model="typesafe/jev-1.13-20260917")
        payload.update({"id": "gen-dec-123", "provider": "TypeSafe"})
        payload["usage"]["cost"] = 0.00003

        response = SystemOneResponse.model_validate(payload)

        assert response.id == "gen-dec-123"
        assert response.provider == "TypeSafe"
        assert response.usage.cost == 0.00003

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"model": "jev-1.13.0", "answers": {}, "usage": {"input_tokens": 1, "output_tokens": 1}},
            {"model": "jev-1.13.0", "answers": {"result": {"type": "noul", "noul": 0.5}}},
        ],
    )
    def test_response_requires_official_wire_fields(self, payload):
        with pytest.raises(ValidationError):
            SystemOneResponse.model_validate(payload)

    def test_score_requires_at_least_two_levels(self):
        with pytest.raises(ValidationError):
            ScoreQuestion(instructions="Rate it", criteria=["low"])


class TestJevSystemOneClientRequest:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("api_base", "endpoint_path", "expected_url"),
        [
            ("https://api.typesafe.ai/v1/", "/systemone", "https://api.typesafe.ai/v1/systemone"),
            ("https://openrouter.ai/api/alpha/", "/decisions", "https://openrouter.ai/api/alpha/decisions"),
        ],
    )
    async def test_explicit_endpoint_preserves_existing_gateway_prefix(self, api_base, endpoint_path, expected_url):
        client, http = _make_client(
            _response(_response_data({"type": "noul", "noul": 0.5})),
            api_base=api_base, endpoint_path=endpoint_path,
        )
        await client.system_one(state="hello", questions={"result": NoulQuestion(instructions="Greeting?")})
        assert http.post.call_args.args[0] == expected_url

    @pytest.mark.parametrize(
        "endpoint_path", ["", "systemone", "https://example.test", "//example.test", "/x?q=1",
                          "/x#fragment", "/x\\y", "/x\ny", "/../x", None],
    )
    def test_endpoint_override_cannot_be_an_origin_or_ambiguous_path(self, endpoint_path):
        with pytest.raises(BaseError, match="endpoint_path"):
            _make_client(endpoint_path=endpoint_path)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["confidence", "probability"])
    @pytest.mark.parametrize("value", [True, "0.9"])
    async def test_wire_probabilities_are_not_coerced_before_caller_validation(self, field, value):
        answer = {"type": "choice", "choice": "yes", "confidence": 0.9,
                  "probabilities": {"yes": 0.9, "no": 0.1}}
        if field == "confidence":
            answer["confidence"] = value
        else:
            answer["probabilities"]["yes"] = value
        client, http = _make_client(_response(_response_data(answer)))
        with pytest.raises(BaseError) as error:
            await client.system_one(state="hello", questions={"result": ChoiceQuestion(
                instructions="Greeting?", criteria={"yes": None, "no": None},
            )})
        assert isinstance(error.value.cause, ValidationError)
        http.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancellation_is_propagated_without_retry(self):
        client, http = _make_client()
        http.post.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await client.system_one(state="hello", questions={"result": NoulQuestion(instructions="Greeting?")})
        http.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sends_official_request_shape_and_parses_noul_answer(self):
        client, http_client = _make_client(_response(_response_data({"type": "noul", "noul": 0.99})))

        result = await client.system_one(
            state="Please let me talk to a person.",
            questions={"result": NoulQuestion(instructions="Is a human requested?")},
        )

        call = http_client.post.call_args
        assert call.args[0] == "https://api.typesafe.ai/v1/systemone"
        assert call.kwargs["json"] == {
            "state": "Please let me talk to a person.",
            "model": "jev-latest",
            "questions": {
                "result": {
                    "type": "noul",
                    "instructions": "Is a human requested?",
                }
            },
        }
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-key"
        assert result.model == "jev-1.13.0"
        answer = result.answers["result"]
        assert isinstance(answer, NoulAnswer)
        assert answer.noul == 0.99

    @pytest.mark.asyncio
    async def test_preserves_openrouter_api_base_path(self):
        client, http_client = _make_client(
            _response(_response_data({"type": "noul", "noul": 0.5})),
            api_base="https://openrouter.ai/api/",
        )

        await client.system_one(
            state="hello",
            questions={"result": NoulQuestion(instructions="Is this a greeting?")},
        )

        assert http_client.post.call_args.args[0] == "https://openrouter.ai/api/v1/systemone"

    @pytest.mark.asyncio
    async def test_converts_nested_messages_to_role_content_objects(self):
        client, http_client = _make_client(_response(_response_data({"type": "noul", "noul": 0.5})))

        await client.system_one(
            state={
                "conversation": [
                    UserMessage(content="hi"),
                    AssistantMessage(content="hello"),
                ]
            },
            questions={"result": NoulQuestion(instructions="Is attention needed?")},
        )

        assert http_client.post.call_args.kwargs["json"]["state"] == {
            "conversation": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        }

    @pytest.mark.asyncio
    async def test_serializes_choice_null_criteria(self):
        client, http_client = _make_client(
            _response(
                _response_data(
                    {
                        "type": "choice",
                        "choice": "calm",
                        "confidence": 0.9,
                        "probabilities": {"calm": 0.95, "angry": 0.05},
                    }
                )
            )
        )

        result = await client.system_one(
            state="Thanks for your help.",
            questions={
                "result": ChoiceQuestion(
                    instructions="What is the tone?",
                    criteria={"calm": None, "angry": "Upset or hostile"},
                )
            },
        )

        sent_criteria = http_client.post.call_args.kwargs["json"]["questions"]["result"]["criteria"]
        assert sent_criteria == {"calm": None, "angry": "Upset or hostile"}
        assert isinstance(result.answers["result"], ChoiceAnswer)

    @pytest.mark.asyncio
    async def test_model_and_request_timeout_can_be_overridden_per_call(self):
        client, http_client = _make_client(_response(_response_data({"type": "noul", "noul": 0.5})))

        await client.system_one(
            state="hello",
            questions={"result": NoulQuestion(instructions="Is this a greeting?")},
            model="jev-1.13.0",
            request_timeout=12.0,
        )

        assert http_client.post.call_args.kwargs["json"]["model"] == "jev-1.13.0"
        assert http_client.post.call_args.kwargs["timeout"] == 12.0

    @pytest.mark.asyncio
    async def test_authorization_header_cannot_be_replaced_by_custom_headers(self):
        client, http_client = _make_client(
            _response(_response_data({"type": "noul", "noul": 0.5})),
            custom_headers={
                "authorization": "Bearer wrong-key",
                "content-type": "text/plain",
                "X-Title": "agent-core",
            },
        )

        await client.system_one(
            state="hello",
            questions={"result": NoulQuestion(instructions="Is this a greeting?")},
        )

        headers = http_client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["Content-Type"] == "application/json"
        assert headers["X-Title"] == "agent-core"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [429, 529])
    @pytest.mark.parametrize(
        ("headers", "expected_delay"),
        [
            ({"Retry-After": "0"}, 0.0),
            ({"retry-after-ms": "125"}, 0.125),
        ],
    )
    async def test_retries_documented_transient_statuses(self, status_code, headers, expected_delay):
        client, http_client = _make_client(max_retries=1, retry_backoff=0.25)
        http_client.post.side_effect = [
            _response({"error": "retry"}, status_code=status_code, headers=headers),
            _response(_response_data({"type": "noul", "noul": 0.5})),
        ]

        with patch("openjiuwen.core.foundation.llm.system_one.client.asyncio.sleep", new=AsyncMock()) as sleep:
            result = await client.system_one(
                state="hello",
                questions={"result": NoulQuestion(instructions="Is this a greeting?")},
            )

        answer = result.answers["result"]
        assert isinstance(answer, NoulAnswer)
        assert answer.noul == 0.5
        assert http_client.post.await_count == 2
        sleep.assert_awaited_once_with(expected_delay)

    @pytest.mark.asyncio
    async def test_does_not_retry_other_http_statuses(self):
        client, http_client = _make_client(_response({"error": "bad gateway"}, status_code=502))

        with pytest.raises(BaseError):
            await client.system_one(
                state="hello",
                questions={"result": NoulQuestion(instructions="Is this a greeting?")},
            )

        http_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("state", "questions", "model"),
        [
            ({"not-json-serializable"}, {"result": NoulQuestion(instructions="Question?")}, None),
            ("hello", {}, None),
            ("hello", {"result": NoulQuestion(instructions="Question?")}, ""),
        ],
    )
    async def test_rejects_invalid_requests_before_sending(self, state, questions, model):
        client, http_client = _make_client()

        with pytest.raises(BaseError):
            await client.system_one(state=state, questions=questions, model=model)

        http_client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wraps_transport_errors_with_original_cause(self):
        client, http_client = _make_client()
        transport_error = httpx.ConnectError("boom")
        http_client.post.side_effect = transport_error

        with pytest.raises(BaseError) as error:
            await client.system_one(
                state="hello",
                questions={"result": NoulQuestion(instructions="Is this a greeting?")},
            )

        assert error.value.cause is transport_error

    @pytest.mark.asyncio
    async def test_wraps_response_validation_errors_with_original_cause(self):
        client, _ = _make_client(_response({"answers": {}}))

        with pytest.raises(BaseError) as error:
            await client.system_one(
                state="hello",
                questions={"result": NoulQuestion(instructions="Is this a greeting?")},
            )

        assert isinstance(error.value.cause, ValidationError)
