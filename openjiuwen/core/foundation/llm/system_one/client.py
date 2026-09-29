# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lightweight client for TypeSafe's Jev model on the System One API."""

import asyncio
import math
import ssl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, Final, Self

import httpx

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import LogEventType, llm_logger
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.foundation.llm.system_one.schema import (
    SystemOneQuestion,
    SystemOneResponse,
    SystemOneState,
    _SystemOneRequest,
)

_SYSTEM_ONE_ENDPOINT: Final = "/v1/systemone"
_RETRYABLE_STATUS_CODES: Final = frozenset({429, 529})
_RESERVED_HEADERS: Final = frozenset({"authorization", "content-type"})


def _convert_state(value: Any) -> Any:
    """Convert messages nested in state to JSON-compatible role/content objects."""
    if isinstance(value, BaseMessage):
        return {"role": value.role, "content": value.content}
    if isinstance(value, dict):
        return {key: _convert_state(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_convert_state(item) for item in value]
    return value


def _finite_nonnegative_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _retry_after_seconds(headers: httpx.Headers) -> float | None:
    """Parse retry-after-ms or Retry-After as a delay in seconds."""
    milliseconds = _finite_nonnegative_number(headers.get("retry-after-ms"))
    if milliseconds is not None:
        return milliseconds / 1000

    value = headers.get("Retry-After")
    seconds = _finite_nonnegative_number(value)
    if seconds is not None:
        return seconds
    if value is not None:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    return None


class JevSystemOneClient:
    """Direct async client for Jev's typed System One evaluations.

    This is deliberately not a ``BaseModelClient``: System One accepts
    ``state`` and typed ``questions`` instead of chat messages and returns
    typed answers instead of an ``AssistantMessage``.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str = "https://api.typesafe.ai",
        model_name: str = "jev-latest",
        timeout: float = 360.0,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        verify_ssl: bool | str | ssl.SSLContext = True,
        custom_headers: dict[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        endpoint_path: str = _SYSTEM_ONE_ENDPOINT,
    ) -> None:
        if not api_key:
            raise build_error(
                StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                error_msg="api_key is required for JevSystemOneClient.",
            )
        if not api_base:
            raise build_error(
                StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                error_msg="api_base is required for JevSystemOneClient.",
            )
        if not model_name:
            raise build_error(StatusCode.MODEL_CONFIG_ERROR, error_msg="model_name cannot be empty.")
        if max_retries < 0:
            raise build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR, error_msg="max_retries cannot be negative.")
        if retry_backoff < 0:
            raise build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR, error_msg="retry_backoff cannot be negative.")
        if (
            not isinstance(endpoint_path, str)
            or not endpoint_path.startswith("/")
            or endpoint_path.startswith("//")
            or any(char.isspace() or char in "?#\\" for char in endpoint_path)
            or any(part in {".", ".."} for part in endpoint_path.split("/"))
        ):
            raise build_error(
                StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                error_msg="endpoint_path must be an absolute URL path without query, fragment or traversal.",
            )

        self._api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model_name = model_name
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.endpoint_path = endpoint_path
        self._custom_headers = dict(custom_headers or {})
        self._owns_http_client = http_client is None
        self._http_client = (
            httpx.AsyncClient(timeout=timeout, verify=verify_ssl) if http_client is None else http_client
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the internally created HTTP client."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def system_one(
        self,
        state: SystemOneState | BaseMessage,
        questions: dict[str, SystemOneQuestion],
        *,
        model: str | None = None,
        request_timeout: float | None = None,
    ) -> SystemOneResponse:
        """Evaluate state against typed questions and return typed answers."""
        resolved_model = self.model_name if model is None else model
        if not resolved_model:
            raise build_error(StatusCode.MODEL_CONFIG_ERROR, error_msg="model cannot be empty.")

        try:
            request = _SystemOneRequest(
                state=_convert_state(state),
                model=resolved_model,
                questions=questions,
            )
        except ValueError as exc:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg=f"invalid System One request: {exc}",
                cause=exc,
            ) from exc

        headers = {key: value for key, value in self._custom_headers.items() if key.lower() not in _RESERVED_HEADERS}
        headers.update(
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
        )
        url = f"{self.api_base}{self.endpoint_path}"

        llm_logger.info(
            "Before request Jev System One, request params ready.",
            event_type=LogEventType.LLM_CALL_START,
            model_name=resolved_model,
            model_provider="jev",
        )

        try:
            response = await self._post_with_retry(
                url,
                json=request.model_dump(exclude_none=True),
                headers=headers,
                request_timeout=request_timeout,
            )
            response.raise_for_status()
            # A boolean/string confidence must not become a valid float before
            # callers enforce their decision thresholds and distributions.
            return SystemOneResponse.model_validate(response.json(), strict=True)
        except (httpx.HTTPError, ValueError) as exc:
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"Jev System One request failed: {exc}",
                cause=exc,
            ) from exc

    async def _post_with_retry(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        request_timeout: float | None,
    ) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            response = await self._http_client.post(
                url,
                json=json,
                headers=headers,
                timeout=self.timeout if request_timeout is None else request_timeout,
            )
            if response.status_code not in _RETRYABLE_STATUS_CODES or attempt == self.max_retries:
                return response

            retry_after = _retry_after_seconds(response.headers)
            delay = retry_after if retry_after is not None else self.retry_backoff * (2**attempt)
            await asyncio.sleep(delay)

        raise build_error(StatusCode.MODEL_CALL_FAILED, error_msg="System One retry loop returned no response.")
