# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unified upstream HTTP transport with retry support.

This module isolates all direct HTTP interaction with the upstream LLM service.
Forwarders consume the interface only, which makes tests easier to mock and
keeps retry logic centralized.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

import httpx

logger = logging.getLogger("online_rl.gateway")

RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.WriteError,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for upstream HTTP operations."""

    max_retries: int = 2
    backoff_base_sec: float = 0.2
    backoff_max_sec: float = 2.0

    def backoff_for_attempt(self, attempt: int) -> float:
        """Return exponential backoff in seconds for a retry attempt index.

        Args:
            attempt: 1-based retry attempt index.

        Returns:
            Backoff delay in seconds, clamped by configured min/max.
        """
        if attempt <= 0:
            return 0.0
        value = self.backoff_base_sec * math.pow(2, attempt - 1)
        return max(0.0, min(value, self.backoff_max_sec))


class UpstreamGatewayClient(Protocol):
    """Interface for all gateway -> upstream network calls."""

    async def post_chat_completions(self, *, json_body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        """POST ``/v1/chat/completions``.

        Args:
            json_body: Upstream request JSON body.
            headers: Upstream request headers.

        Returns:
            Raw upstream HTTP response.
        """

    async def request(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, Any],
        headers: dict[str, str],
        content: bytes,
    ) -> httpx.Response:
        """Forward arbitrary request to the upstream service.

        Args:
            method: HTTP method.
            url: Fully-qualified upstream URL.
            params: Query parameters.
            headers: Upstream headers.
            content: Raw request body bytes.

        Returns:
            Raw upstream HTTP response.
        """


class HTTPXUpstreamGatewayClient:
    """HTTPX-backed upstream client with retry for transient failures."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        llm_url: str,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        """Initialize HTTPX-backed upstream client.

        Args:
            http_client: Shared HTTPX async client.
            llm_url: Upstream LLM service base URL.
            retry_policy: Optional retry policy override.
        """
        self._http_client = http_client
        self._llm_url = llm_url.rstrip("/")
        self._retry_policy = retry_policy or RetryPolicy()

    async def post_chat_completions(self, *, json_body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        """POST chat completions with centralized retry.

        Args:
            json_body: Upstream request JSON body.
            headers: Upstream request headers.

        Returns:
            Raw upstream HTTP response.
        """
        return await self._request_with_retry(
            operation="chat.completions",
            send=lambda: self._http_client.post(
                f"{self._llm_url}/v1/chat/completions",
                json=json_body,
                headers=headers,
            ),
        )

    async def request(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, Any],
        headers: dict[str, str],
        content: bytes,
    ) -> httpx.Response:
        """Send arbitrary upstream request with centralized retry.

        Args:
            method: HTTP method.
            url: Fully-qualified upstream URL.
            params: Query parameters.
            headers: Upstream request headers.
            content: Raw request body bytes.

        Returns:
            Raw upstream HTTP response.
        """
        return await self._request_with_retry(
            operation=f"proxy.{method.lower()}",
            send=lambda: self._http_client.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
                content=content,
            ),
        )

    async def _request_with_retry(
        self,
        *,
        operation: str,
        send: Callable[[], Awaitable[httpx.Response]],
    ) -> httpx.Response:
        attempt = 0
        while True:
            try:
                resp = await send()
            except RETRYABLE_EXCEPTIONS as exc:
                if attempt >= self._retry_policy.max_retries:
                    raise
                attempt += 1
                await self._sleep_before_retry(operation=operation, attempt=attempt, reason=str(exc))
                continue

            if resp.status_code in RETRYABLE_STATUS_CODES and attempt < self._retry_policy.max_retries:
                attempt += 1
                logger.warning(
                    "[Upstream] %s status=%d retry=%d/%d",
                    operation,
                    resp.status_code,
                    attempt,
                    self._retry_policy.max_retries,
                )
                await resp.aclose()
                await self._sleep_before_retry(
                    operation=operation,
                    attempt=attempt,
                    reason=f"status={resp.status_code}",
                )
                continue

            return resp

    async def _sleep_before_retry(self, *, operation: str, attempt: int, reason: str) -> None:
        delay = self._retry_policy.backoff_for_attempt(attempt)
        logger.warning(
            "[Upstream] %s transient error, retry=%d/%d backoff=%.2fs reason=%s",
            operation,
            attempt,
            self._retry_policy.max_retries,
            delay,
            reason,
        )
        if delay > 0:
            await asyncio.sleep(delay)
