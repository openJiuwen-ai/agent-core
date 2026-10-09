# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import httpx
import pytest

from openjiuwen.core.foundation.llm.model_clients.openai_model_client import _format_exception_detail
from openjiuwen.core.foundation.llm.utils.provider_error import (
    MAX_PROVIDER_ERROR_CHARS,
    format_provider_exception,
    summarize_provider_error_text,
)
from openjiuwen.core.foundation.llm.utils.responses_utils import (
    OpenAIAccountResponsesError,
    raise_for_http_error,
)

_HTML_404 = (
    "<!DOCTYPE html><html lang='en'><head><title>Not Found | opencode</title></head>"
    "<body><h1>404 - Page Not Found</h1></body></html>"
)
_HTML_502 = (
    "<html><head><title>502 Bad Gateway</title></head>"
    "<body><h1>502 Bad Gateway</h1></body></html>"
)


class _SdkError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def test_empty_timeout_keeps_exception_type():
    assert format_provider_exception(TimeoutError()) == "TimeoutError"
    assert _format_exception_detail(TimeoutError()) == "TimeoutError"


def test_timeout_with_message_is_unchanged():
    exc = TimeoutError("stream frame timeout: stage=idle_chunk, timeout=10s")
    assert format_provider_exception(exc) == (
        "TimeoutError: stream frame timeout: stage=idle_chunk, timeout=10s"
    )
    assert format_provider_exception(exc, include_exc_type=False) == (
        "stream frame timeout: stage=idle_chunk, timeout=10s"
    )


def test_json_api_error_is_unchanged():
    message = "Error code: 400 - {'error': {'message': 'invalid api key'}}"
    exc = _SdkError(message, status_code=400)
    assert format_provider_exception(exc) == f"_SdkError: {message}"
    assert format_provider_exception(exc, include_exc_type=False) == message


def test_html_404_is_summarized_with_status_and_title():
    exc = _SdkError(_HTML_404, status_code=404)
    text = format_provider_exception(exc)

    assert "<!DOCTYPE" not in text
    assert "<html" not in text
    assert "HTTP 404" in text
    assert "Not Found | opencode" in text
    assert "HTML error page" in text
    assert str(len(_HTML_404)) in text


def test_html_502_infers_status_from_title_for_retry():
    text = format_provider_exception(_SdkError(_HTML_502), include_exc_type=False)

    assert "<title>" not in text
    assert "HTTP 502" in text
    assert "502 Bad Gateway" in text


def test_error_code_prefix_plus_html_keeps_http_status():
    text = format_provider_exception(_SdkError(f"Error code: 404 - {_HTML_404}"))

    assert "HTTP 404" in text
    assert "<!DOCTYPE" not in text


def test_short_html_snippet_in_json_is_not_treated_as_error_page():
    message = '{"error": "expected <html> tag"}'
    assert format_provider_exception(_SdkError(message), include_exc_type=False) == message


def test_oversized_plain_text_is_truncated():
    payload = "x" * (MAX_PROVIDER_ERROR_CHARS + 40)
    text = summarize_provider_error_text(payload)

    assert text.startswith("x" * 32)
    assert "[truncated 40 chars]" in text
    assert len(text) < len(payload)


def test_raise_for_http_error_keeps_json_error_message():
    response = httpx.Response(
        400,
        json={"error": {"message": "bad request"}},
        request=httpx.Request("POST", "https://example.test/responses"),
    )

    with pytest.raises(OpenAIAccountResponsesError, match="bad request") as caught:
        raise_for_http_error(response)

    assert caught.value.status_code == 400
    assert "<html" not in str(caught.value)


def test_raise_for_http_error_summarizes_html_body():
    response = httpx.Response(
        404,
        text=_HTML_404,
        request=httpx.Request("POST", "https://example.test/responses"),
    )

    with pytest.raises(OpenAIAccountResponsesError) as caught:
        raise_for_http_error(response)

    message = str(caught.value)
    assert caught.value.status_code == 404
    assert "HTTP 404" in message
    assert "Not Found | opencode" in message
    assert "<!DOCTYPE" not in message
