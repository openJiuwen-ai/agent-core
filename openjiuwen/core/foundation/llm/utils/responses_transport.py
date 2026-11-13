# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HTTP transport for the OpenAI account Responses backend.

This module is responsible only for HTTP I/O: sending requests and
iterating SSE events. All request-body construction and response
parsing is handled by :mod:`responses_utils`.
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Optional, Union

import httpx

from openjiuwen.core.common.logging import llm_logger as logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.utils.responses_utils import (
    ResponsesStreamParser,
    build_headers,
    message_from_stream_chunk,
    parse_sse_block,
    raise_for_http_error,
)
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import LLMCallEvents
from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import DEFAULT_OPENAI_ACCOUNT_BASE_URL

# Optional request fields that some Responses endpoints refuse (OpenAI answers
# "400 Unknown parameter: 'stop'") while others honour them (e.g. Qwen and
# DeepSeek through OpenRouter).
# They are sent as configured; when an endpoint refuses one, the request is
# retried once without it, and the (base_url, model, field) triple is
# remembered so later requests leave it out.
_DROPPABLE_PARAMS = ("stop",)
_REFUSED_PARAMS: set[tuple[str, str, str]] = set()
# Same status rule as the Chat disabled-thinking fallback in openai_model_client.
_PARAM_REFUSAL_NON_RETRY_STATUSES = {401, 403, 408, 429}
_PARAM_REFUSAL_MARKERS = (
    "unknown parameter",
    "unrecognized parameter",
    "unsupported",
    "not support",
    "does not support",
    "doesn't support",
    "not allowed",
    "not permitted",
    "不支持",
    "不允许",
    "未知参数",
    "无法识别",
)
_PARAM_REFUSAL_CODES = {"unknown_parameter", "unsupported_parameter", "unrecognized_parameter"}


def _mentions_field(text: str, field: str) -> bool:
    return re.search(rf"(?<![a-z0-9_]){re.escape(field)}(?![a-z0-9_])", text) is not None


def _refused_param(response: httpx.Response, body: dict[str, Any]) -> Optional[str]:
    """Return the droppable field that ``response`` says the endpoint refused, if any.

    Both checks need evidence of a refusal. A structured OpenAI error counts when its
    ``param`` is the field and its ``code`` or message says the parameter is unknown or
    unsupported; otherwise the error text must name the field and use refusal wording.
    Value errors such as "Invalid type for 'stop'" or "stop: too many items", and errors
    about other fields, are raised as before.
    """
    status_code = response.status_code
    if status_code < 400 or status_code >= 500 or status_code in _PARAM_REFUSAL_NON_RETRY_STATUSES:
        return None
    candidates = [param for param in _DROPPABLE_PARAMS if param in body]
    if not candidates:
        return None

    try:
        payload = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("param") in candidates:
        # ``param`` is also set for value/type errors about the field, so require
        # refusal wording (or an unknown/unsupported code) before dropping it.
        code = str(error.get("code") or "").lower()
        message = str(error.get("message") or "").lower()
        if code in _PARAM_REFUSAL_CODES or any(marker in message for marker in _PARAM_REFUSAL_MARKERS):
            return error["param"]

    # The whole body is searched, so errors that a gateway wraps (e.g. OpenRouter's
    # error.metadata.raw) are found too. The decoded JSON is added for servers that
    # send non-ASCII text as \uXXXX escapes.
    text = response.text or ""
    if payload is not None:
        text += "\n" + json.dumps(payload, ensure_ascii=False)
    text = text.lower()
    if not any(marker in text for marker in _PARAM_REFUSAL_MARKERS):
        return None
    for param in candidates:
        if _mentions_field(text, param):
            return param
    return None


class OpenAIAccountResponsesTransport:
    """HTTP-only transport for the OpenAI account Responses API.

    Handles streaming, SSE iteration, and auth headers.  Format
    conversion (messages → input items, tools, parsing response
    payloads) lives in :mod:`responses_utils`.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OPENAI_ACCOUNT_BASE_URL,
        timeout_seconds: float = 60.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        verify: Union[bool, str, Any] = True,
        proxy: Optional[str] = None,
        max_retries: int = 0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._verify = verify
        self._proxy = proxy
        self._max_retries = max(0, int(max_retries or 0))
        self._client: Optional[httpx.AsyncClient] = None

    async def create_response(
        self,
        *,
        body: dict[str, Any],
        access_token: str,
        model_name: Optional[str] = None,
        session_id: Optional[str] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> AssistantMessage:
        """Send a request and accumulate the streamed chunks into a single message."""
        final_chunk: Optional[AssistantMessageChunk] = None
        async for chunk in self._stream(
            body=body,
            access_token=access_token,
            model_name=model_name,
            session_id=session_id,
            extra_headers=extra_headers,
            is_stream=False,
        ):
            final_chunk = final_chunk + chunk if final_chunk else chunk
        return message_from_stream_chunk(final_chunk)

    async def stream_response(
        self,
        *,
        body: dict[str, Any],
        access_token: str,
        model_name: Optional[str] = None,
        session_id: Optional[str] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Stream response chunks from the Responses API."""
        async for chunk in self._stream(
            body=body,
            access_token=access_token,
            model_name=model_name,
            session_id=session_id,
            extra_headers=extra_headers,
            is_stream=True,
        ):
            yield chunk

    async def _stream(
        self,
        *,
        body: dict[str, Any],
        access_token: str,
        model_name: Optional[str],
        session_id: Optional[str],
        extra_headers: Optional[dict[str, str]],
        is_stream: bool,
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Send the request over SSE; ``is_stream`` says whether the caller streams."""
        effective_model = model_name or str(body.get("model") or "")
        stream_body = {
            key: value for key, value in body.items() if (self.base_url, effective_model, key) not in _REFUSED_PARAMS
        }
        stream_body["stream"] = True
        client = await self._get_client()
        headers = build_headers(
            access_token=access_token,
            session_id=session_id,
            extra_headers=extra_headers,
        )
        while True:
            async with client.stream("POST", self._responses_url(), json=stream_body, headers=headers) as response:
                if response.status_code >= 400:
                    await response.aread()
                    refused = _refused_param(response, stream_body)
                    if refused:
                        # Each retry removes one field that is present, so this loop ends.
                        _REFUSED_PARAMS.add((self.base_url, effective_model, refused))
                        stream_body.pop(refused)
                        logger.warning(
                            "Responses endpoint rejected '%s'; retrying without it and leaving it out "
                            "of later requests.",
                            refused,
                            event_type=LogEventType.LLM_CALL_ERROR,
                            model_name=effective_model,
                            is_stream=is_stream,
                            extra_params={"api_base": self.base_url, "dropped_param": refused},
                        )
                        continue
                raise_for_http_error(response)
                # httpx transport retries only cover failed connects and are not
                # reported back, so the retry count is unknown here.
                await trigger(
                    LLMCallEvents.LLM_RESPONSE_STARTED,
                    model_name=effective_model,
                    retry_count=None,
                )
                parser = ResponsesStreamParser(model_name=effective_model)
                buffer: list[str] = []
                async for line in response.aiter_lines():
                    if line == "":
                        event = parse_sse_block(buffer)
                        buffer = []
                        chunk = parser.parse(event)
                        if chunk:
                            yield chunk
                    else:
                        buffer.append(line)
                if buffer:
                    event = parse_sse_block(buffer)
                    chunk = parser.parse(event)
                    if chunk:
                        yield chunk
                return

    async def aclose(self) -> None:
        """Close the cached HTTP client, if one has been created."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _responses_url(self) -> str:
        return f"{self.base_url}/responses"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._make_client()
        elif self._client.is_closed:
            old_client = self._client
            self._client = None
            try:
                await old_client.aclose()
            except Exception as exc:
                logger.warning(
                    "Failed to close stale OpenAI account Responses client before recreating it: %s",
                    exc,
                )
            self._client = self._make_client()
        return self._client

    def _make_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": httpx.Timeout(max(5.0, float(self.timeout_seconds)))}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        else:
            kwargs["transport"] = httpx.AsyncHTTPTransport(
                verify=self._verify,
                proxy=self._proxy,
                retries=self._max_retries,
            )
        return httpx.AsyncClient(**kwargs)
