"""LLM port for distill analyzers (via openjiuwen Model)."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Iterable, Protocol

from openjiuwen.core.foundation.llm import SystemMessage, UserMessage

logger = logging.getLogger(__name__)

DEFAULT_COMPLETE_MAX_ATTEMPTS = 3
DEFAULT_COMPLETE_BACKOFF_SECONDS = (1.0, 2.0)

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 422}
_HTTP_STATUS_IN_MESSAGE = re.compile(
    r"\b(?:error(?:\s+code)?|status(?:\s+code)?|http)[=:\s]+(\d{3})\b",
    re.I,
)
_BARE_RETRYABLE_STATUS_IN_MESSAGE = re.compile(r"\b(429|500|502|503|504)\b")
_RETRYABLE_MESSAGE_TOKENS = (
    "timed out",
    "timeout",
    "rate limit",
    "temporarily unavailable",
    "connection reset",
    "connection aborted",
    "connection refused",
    "connect error",
)


class LlmPort(Protocol):
    async def complete(self, *, system: str, user: str) -> str:
        """Return assistant text for one distill call."""


def _extract_assistant_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    parser_content = getattr(response, "parser_content", None)
    if parser_content is not None:
        text = str(parser_content).strip()
        if text:
            return text
    finish_reason = str(getattr(response, "finish_reason", "") or "").strip().lower()
    if finish_reason in {"length", "max_tokens"}:
        raise RuntimeError("distill LLM output truncated with empty content")
    raise RuntimeError("distill LLM returned empty content")


def _iter_exception_chain(exc: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        nested = getattr(current, "cause", None)
        if isinstance(nested, BaseException) and id(nested) not in seen:
            current = nested
            continue
        current = current.__cause__ or current.__context__


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code",):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


def _status_from_message(message: str) -> int | None:
    match = _HTTP_STATUS_IN_MESSAGE.search(message)
    if match:
        return int(match.group(1))
    match = _BARE_RETRYABLE_STATUS_IN_MESSAGE.search(message)
    if match:
        return int(match.group(1))
    return None


def _is_retryable_message(message: str) -> bool:
    lowered = message.lower()
    if "empty content" in lowered or "truncated with empty content" in lowered:
        return True
    for token in _RETRYABLE_MESSAGE_TOKENS:
        if token in lowered:
            return True
    status = _status_from_message(lowered)
    if status is None:
        return False
    if status in _NON_RETRYABLE_STATUS:
        return False
    return status in _RETRYABLE_STATUS


def is_retryable_complete_error(exc: BaseException) -> bool:
    for item in _iter_exception_chain(exc):
        if isinstance(item, (TimeoutError, asyncio.TimeoutError, ConnectionError, BrokenPipeError)):
            return True

        name = type(item).__name__.lower()
        if any(token in name for token in ("timeout", "ratelimit", "internalserver", "apiconnection")):
            return True
        if any(token in name for token in ("authentication", "permission", "badrequest", "notfound")):
            return False

        status = _status_code(item)
        if status is not None:
            if status in _NON_RETRYABLE_STATUS:
                return False
            if status in _RETRYABLE_STATUS or status >= 500:
                return True
            return False

        if _is_retryable_message(str(item)):
            return True

    return False


class OpenJiuwenLlm:
    """Async ``LlmPort`` over openjiuwen ``Model.invoke``."""

    def __init__(
        self,
        model: Any,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout_seconds: float = 180.0,
        max_attempts: int = DEFAULT_COMPLETE_MAX_ATTEMPTS,
        backoff_seconds: tuple[float, ...] = DEFAULT_COMPLETE_BACKOFF_SECONDS,
    ) -> None:
        self._model = model
        self._temperature = float(temperature)
        self._max_tokens = int(max_tokens)
        self._timeout_seconds = float(timeout_seconds)
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_seconds = tuple(float(item) for item in backoff_seconds)

    async def complete(self, *, system: str, user: str) -> str:
        last_error: BaseException | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._model.invoke(
                    [SystemMessage(content=system), UserMessage(content=user)],
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    timeout=self._timeout_seconds,
                )
                return _extract_assistant_text(response)
            except BaseException as exc:  # noqa: BLE001 — classify then retry/raise
                last_error = exc
                if attempt >= self._max_attempts or not is_retryable_complete_error(exc):
                    raise
                delay = self._backoff_seconds[min(attempt - 1, len(self._backoff_seconds) - 1)]
                logger.warning(
                    "distill LLM complete retry %s/%s after %.1fs: %s",
                    attempt,
                    self._max_attempts,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        if last_error is None:
            raise RuntimeError("distill LLM complete exhausted without a captured error")
        raise last_error
