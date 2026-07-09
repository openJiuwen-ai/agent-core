# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HTTP transport for the OpenAI account Responses backend.

This module is responsible only for HTTP I/O: sending requests and
iterating SSE events. All request-body construction and response
parsing is handled by :mod:`responses_utils`.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional, Union

import httpx

from openjiuwen.core.common.logging import llm_logger as logger
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import DEFAULT_OPENAI_ACCOUNT_BASE_URL
from openjiuwen.core.foundation.llm.utils.responses_utils import (
    build_headers,
    message_from_stream_chunk,
    parse_sse_block,
    parse_stream_event,
    raise_for_http_error,
)


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
        async for chunk in self.stream_response(
            body=body,
            access_token=access_token,
            model_name=model_name,
            session_id=session_id,
            extra_headers=extra_headers,
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
        stream_body = dict(body)
        stream_body["stream"] = True
        effective_model = model_name or str(body.get("model") or "")
        client = await self._get_client()
        async with client.stream(
            "POST",
            self._responses_url(),
            json=stream_body,
            headers=build_headers(
                access_token=access_token,
                session_id=session_id,
                extra_headers=extra_headers,
            ),
        ) as response:
            if response.status_code >= 400:
                await response.aread()
            raise_for_http_error(response)
            buffer: list[str] = []
            async for line in response.aiter_lines():
                if line == "":
                    event = parse_sse_block(buffer)
                    buffer = []
                    chunk = parse_stream_event(event, model_name=effective_model)
                    if chunk:
                        yield chunk
                else:
                    buffer.append(line)
            if buffer:
                event = parse_sse_block(buffer)
                chunk = parse_stream_event(event, model_name=effective_model)
                if chunk:
                    yield chunk

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
