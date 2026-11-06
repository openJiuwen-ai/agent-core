# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.utils.responses_utils import build_request_body

_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
    "additionalProperties": False,
}
_JSON_SCHEMA_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "city", "strict": True, "schema": _SCHEMA},
}


def _body(**extra) -> dict:
    return build_request_body(model="gpt-5.6-luna", messages="Name a city.", extra_body=extra)


def test_json_schema_moves_to_text_format_flattened():
    """Responses puts name/schema/strict directly in text.format, not under json_schema."""
    body = _body(response_format=_JSON_SCHEMA_FORMAT)

    assert "response_format" not in body
    assert body["text"] == {
        "format": {"type": "json_schema", "name": "city", "strict": True, "schema": _SCHEMA},
    }


def test_json_object_moves_to_text_format():
    body = _body(response_format={"type": "json_object"})

    assert "response_format" not in body
    assert body["text"] == {"format": {"type": "json_object"}}


def test_text_type_moves_to_text_format():
    body = _body(response_format={"type": "text"})

    assert body["text"] == {"format": {"type": "text"}}


def test_existing_text_settings_are_kept():
    body = _body(response_format={"type": "json_object"}, text={"verbosity": "low"})

    assert body["text"] == {"verbosity": "low", "format": {"type": "json_object"}}


def test_explicit_text_format_is_not_overwritten():
    """A caller already using the Responses field keeps it; the chat-style field is not sent."""
    body = _body(response_format={"type": "json_object"}, text={"format": {"type": "text"}})

    assert "response_format" not in body
    assert body["text"] == {"format": {"type": "text"}}


def test_nested_extra_body_response_format_is_translated():
    body = _body(extra_body={"response_format": {"type": "json_object"}})

    assert "response_format" not in body
    assert body["text"] == {"format": {"type": "json_object"}}


def test_none_response_format_is_not_sent():
    body = _body(response_format=None)

    assert "response_format" not in body
    assert "text" not in body


def test_unrecognised_response_format_is_left_as_is():
    """Values that are not a format dict are not guessed at."""
    assert _body(response_format="json")["response_format"] == "json"
    assert _body(response_format={"schema": _SCHEMA})["response_format"] == {"schema": _SCHEMA}


def test_body_without_response_format_is_unchanged():
    body = _body()

    assert "response_format" not in body
    assert "text" not in body


def test_model_client_keyword_reaches_text_format():
    """``Model.invoke(..., response_format=...)`` forwards the keyword into the Responses body."""
    model = Model(
        ModelClientConfig(
            client_provider="OpenAI",
            api_key="mock-api-key",
            api_base="https://api.openai.com/v1",
            api_mode="responses",
        ),
        ModelRequestConfig(model="gpt-5.6-luna"),
    )

    body = model._client._build_responses_request_body(
        messages="Name a city.",
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        max_tokens=None,
        stop=None,
        response_format=_JSON_SCHEMA_FORMAT,
    )

    assert "response_format" not in body
    assert body["text"]["format"] == {"type": "json_schema", "name": "city", "strict": True, "schema": _SCHEMA}
