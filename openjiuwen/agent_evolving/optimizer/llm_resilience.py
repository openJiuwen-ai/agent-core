# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Lightweight LLM resilience helpers for evolution flows."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model


@dataclass(frozen=True)
class LLMInvokePolicy:
    """Policy for a single evolution-layer LLM invocation."""

    attempt_timeout_secs: float
    total_budget_secs: float
    max_attempts: int = 2
    backoff_base_secs: float = 1.0
    retry_empty_response: bool = True


def _response_to_text(response: Any) -> str:
    if hasattr(response, "content"):
        return str(response.content or "")
    if isinstance(response, dict):
        return str(response.get("content", "") or response.get("text", "") or "")
    return str(response or "")


async def invoke_text_with_retry(
    llm: Model,
    model: str,
    prompt: str,
    *,
    policy: LLMInvokePolicy,
    retry_prompt: str | None = None,
    temperature: float | None = None,
    is_result_usable: Callable[[str], bool] | None = None,
    **kwargs: Any,
) -> str:
    raw, _ = await invoke_text_with_retry_and_prompt(
        llm=llm,
        model=model,
        prompt=prompt,
        policy=policy,
        retry_prompt=retry_prompt,
        temperature=temperature,
        is_result_usable=is_result_usable,
        **kwargs,
    )
    return raw


async def invoke_text_with_retry_and_prompt(
    llm: Model,
    model: str,
    prompt: str,
    *,
    policy: LLMInvokePolicy,
    retry_prompt: str | None = None,
    temperature: float | None = None,
    is_result_usable: Callable[[str], bool] | None = None,
    **kwargs: Any,
) -> tuple[str, str]:
    """Invoke Model with evolution-layer usability retry and total budget control."""
    if policy.total_budget_secs <= 0:
        _raise_llm_resilience_error(
            StatusCode.TOOLCHAIN_EVOLVING_TOOL_CALL_LLM_CALL_EXECUTION_ERROR,
            reason="total_budget_exceeded",
            attempts=0,
        )

    started_at = time.monotonic()
    attempts_started = 0
    last_error: Exception | None = None
    last_response = ""
    use_retry_prompt = False

    try:
        async with asyncio.timeout(policy.total_budget_secs):
            for attempt in range(1, max(policy.max_attempts, 1) + 1):
                attempts_started = attempt
                elapsed = time.monotonic() - started_at
                remaining_budget = policy.total_budget_secs - elapsed
                if remaining_budget <= 0:
                    _raise_llm_resilience_error(
                        StatusCode.TOOLCHAIN_EVOLVING_TOOL_CALL_LLM_CALL_EXECUTION_ERROR,
                        reason="total_budget_exceeded",
                        attempts=attempt - 1,
                        last_error=last_error,
                        last_response=last_response,
                    )

                timeout_secs = min(policy.attempt_timeout_secs, remaining_budget)
                current_prompt = retry_prompt if use_retry_prompt and retry_prompt is not None else prompt
                try:
                    response = await llm.invoke(
                        model=model,
                        messages=[{"role": "user", "content": current_prompt}],
                        temperature=temperature,
                        timeout=timeout_secs,
                        **kwargs,
                    )
                except Exception as exc:
                    last_error = exc
                    if (
                        retry_prompt is not None
                        and attempt < policy.max_attempts
                        and _is_timeout_like(exc)
                    ):
                        use_retry_prompt = True
                        logger.info(
                            "[llm_resilience] attempt %d/%d timed out; retrying with shorter prompt",
                            attempt,
                            policy.max_attempts,
                        )
                        await _sleep_before_retry(policy, started_at, attempt)
                        continue
                    _raise_llm_resilience_error(
                        StatusCode.TOOLCHAIN_EVOLVING_TOOL_CALL_LLM_CALL_EXECUTION_ERROR,
                        reason="invoke_failed",
                        attempts=attempt,
                        last_error=exc,
                        last_response=last_response,
                        cause=exc,
                    )

                raw = _response_to_text(response)
                last_response = raw

                if policy.retry_empty_response and not raw.strip():
                    if attempt >= policy.max_attempts:
                        _raise_llm_resilience_error(
                            StatusCode.TOOLCHAIN_EVOLVING_TOOL_CALL_OUTPUT_PARSE_ERROR,
                            reason="empty_response",
                            attempts=attempt,
                            last_error=last_error,
                            last_response=raw,
                        )
                    await _sleep_before_retry(policy, started_at, attempt)
                    continue

                if is_result_usable is not None:
                    try:
                        usable = is_result_usable(raw)
                    except Exception as exc:
                        usable = False
                        last_error = exc

                    if not usable:
                        if attempt >= policy.max_attempts:
                            _raise_llm_resilience_error(
                                StatusCode.TOOLCHAIN_EVOLVING_TOOL_CALL_OUTPUT_PARSE_ERROR,
                                reason="unusable_response",
                                attempts=attempt,
                                last_error=last_error,
                                last_response=raw,
                            )
                        await _sleep_before_retry(policy, started_at, attempt)
                        continue

                return raw, current_prompt
    except TimeoutError as exc:
        _raise_llm_resilience_error(
            StatusCode.TOOLCHAIN_EVOLVING_TOOL_CALL_LLM_CALL_EXECUTION_ERROR,
            reason="total_budget_exceeded",
            attempts=attempts_started,
            last_error=last_error,
            last_response=last_response,
            cause=exc,
        )

    _raise_llm_resilience_error(
        StatusCode.TOOLCHAIN_EVOLVING_TOOL_CALL_OUTPUT_PARSE_ERROR,
        reason="unusable_response",
        attempts=policy.max_attempts,
        last_error=last_error,
        last_response=last_response,
    )


def _is_timeout_like(exc: Exception) -> bool:
    if isinstance(exc, asyncio.TimeoutError):
        return True
    if "timeout" in type(exc).__name__.lower():
        return True
    message = str(exc).lower()
    return "timeout" in message or "timed out" in message


async def _sleep_before_retry(policy: LLMInvokePolicy, started_at: float, attempt: int) -> None:
    if policy.backoff_base_secs <= 0:
        return

    remaining_budget = policy.total_budget_secs - (time.monotonic() - started_at)
    if remaining_budget <= 0:
        return

    backoff_secs = policy.backoff_base_secs * (2 ** max(attempt - 1, 0))
    await asyncio.sleep(min(backoff_secs, remaining_budget))


def _raise_llm_resilience_error(
    status: StatusCode,
    *,
    reason: str,
    attempts: int,
    last_error: Exception | None = None,
    last_response: str = "",
    cause: BaseException | None = None,
) -> None:
    raise build_error(
        status,
        cause=cause or last_error,
        error_msg=reason,
        details={
            "reason": reason,
            "attempts": attempts,
            "last_response": last_response,
            "last_error": str(last_error) if last_error is not None else "",
        },
    )
