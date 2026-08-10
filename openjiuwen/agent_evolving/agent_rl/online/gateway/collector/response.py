# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Strict extraction of rollout truth from vLLM OpenAI responses."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from typing import Any

from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.types import _CopyableCodedError


@dataclass(frozen=True, slots=True)
class UpstreamGenerationData:
    """Provider response fields required to build one training sample."""

    assistant_message: dict[str, Any]
    prompt_ids: tuple[int, ...]
    completion_ids: tuple[int, ...]
    completion_logprobs: tuple[float, ...]
    finish_reason: str
    routed_experts: Any | None = None
    routing_metadata: Mapping[str, Any] | None = None


class UpstreamResponseErrorCode(str, Enum):
    """Stable rollout-truth parse failures used by collection metrics."""

    MISSING_TOKEN_IDS = "missing_completion_ids"
    MISSING_LOGPROBS = "missing_logprobs"
    LOGPROB_MISMATCH = "logprob_length_mismatch"
    MALFORMED_LOGPROBS = "malformed_logprobs"
    MALFORMED_RESPONSE = "malformed_response"


class UpstreamResponseError(_CopyableCodedError, ValueError):
    """Provider response error carrying a stable collection category code."""

    def __init__(self, code: UpstreamResponseErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_vllm_response(response: Mapping[str, Any]) -> UpstreamGenerationData:
    """Extract exact token truth from the first OpenAI completion choice."""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise UpstreamResponseError(
            UpstreamResponseErrorCode.MALFORMED_RESPONSE,
            "upstream response must contain a completion choice",
        )
    choice = choices[0]
    assistant_message = choice.get("message")
    if not isinstance(assistant_message, Mapping):
        raise UpstreamResponseError(
            UpstreamResponseErrorCode.MALFORMED_RESPONSE,
            "upstream response is missing assistant message",
        )
    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str) or not finish_reason:
        raise UpstreamResponseError(
            UpstreamResponseErrorCode.MALFORMED_RESPONSE,
            "upstream response is missing finish reason",
        )

    prompt_ids = _int_tuple(response.get("prompt_token_ids"), "prompt token IDs")
    completion_ids = _int_tuple(choice.get("token_ids"), "completion token IDs")
    completion_logprobs = _logprob_tuple(choice.get("logprobs"))
    if len(completion_ids) != len(completion_logprobs):
        raise UpstreamResponseError(
            UpstreamResponseErrorCode.LOGPROB_MISMATCH,
            "completion token IDs and log-probabilities must align",
        )
    return UpstreamGenerationData(
        assistant_message=dict(assistant_message),
        prompt_ids=prompt_ids,
        completion_ids=completion_ids,
        completion_logprobs=completion_logprobs,
        finish_reason=finish_reason,
        routed_experts=choice.get("routed_experts", response.get("routed_experts")),
        routing_metadata=choice.get("routing_metadata", response.get("routing_metadata")),
    )


def _int_tuple(value: Any, field_name: str) -> tuple[int, ...]:
    if not isinstance(value, list) or any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise UpstreamResponseError(
            UpstreamResponseErrorCode.MISSING_TOKEN_IDS,
            f"upstream response has invalid {field_name}",
        )
    return tuple(value)


def _logprob_tuple(value: Any) -> tuple[float, ...]:
    content = value.get("content") if isinstance(value, Mapping) else None
    if not isinstance(content, list):
        raise UpstreamResponseError(
            UpstreamResponseErrorCode.MISSING_LOGPROBS,
            "upstream response is missing token log-probabilities",
        )
    logprobs: list[float] = []
    for item in content:
        logprob = item.get("logprob") if isinstance(item, Mapping) else None
        if isinstance(logprob, bool) or not isinstance(logprob, Real) or not math.isfinite(logprob):
            raise UpstreamResponseError(
                UpstreamResponseErrorCode.MALFORMED_LOGPROBS,
                "upstream response has invalid token log-probabilities",
            )
        logprobs.append(float(logprob))
    return tuple(logprobs)
