# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unicode handling at the OpenAI-compatible request boundary."""

import re
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import llm_logger

_SURROGATES = re.compile(r"[\ud800-\udfff]")


def _replace_surrogates(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return _SURROGATES.subn("\ufffd", value)
    if isinstance(value, dict):
        result = {}
        count = 0
        for key, item in value.items():
            result[key], replaced = _replace_surrogates(item)
            count += replaced
        return result, count
    if isinstance(value, (list, tuple)):
        items = [_replace_surrogates(item) for item in value]
        values = [item for item, _ in items]
        return (tuple(values) if isinstance(value, tuple) else values), sum(count for _, count in items)
    return value, 0


def sanitize_request_messages(messages: list[dict]) -> list[dict]:
    """Replace surrogate code points without changing the caller's history.

    Normal emoji are Unicode scalar values in Python and remain unchanged.
    Only message values are repaired; invalid JSON keys/options still fail
    request construction with a parameter error.
    """
    sanitized = []
    for message in messages:
        try:
            replacement, count = _replace_surrogates(message)
        except RecursionError as exc:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg="Message data is cyclic or nested too deeply.",
                cause=exc,
            ) from exc
        if count:
            llm_logger.warning(
                "Replaced %s surrogate code points in model message: role=%s tool_call_id=%s",
                count,
                ascii(message.get("role")),
                ascii(message.get("tool_call_id")),
            )
        sanitized.append(replacement)
    return sanitized


def create_encoding_aware_openai_client(**kwargs: Any) -> Any:
    """Build an SDK client that distinguishes local request encoding failures.

    The SDK builds the HTTP request before sending it. Catch only at that
    boundary: response decoding, callbacks and output parsers may also raise
    ValueError/TypeError, but they are not request parameter errors. Keeping
    the override here covers SDK versions that serialize JSON themselves as
    well as versions that delegate serialization to httpx.
    """
    from openai import AsyncOpenAI

    class EncodingAwareAsyncOpenAI(AsyncOpenAI):
        """Preserve the SDK's client lifecycle and request/response behavior."""

        def _build_request(self, *args: Any, **request_kwargs: Any) -> Any:
            try:
                return super()._build_request(*args, **request_kwargs)
            except (TypeError, ValueError) as exc:
                # UnicodeEncodeError is a ValueError.
                raise build_error(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    error_msg=f"Cannot encode model request: {type(exc).__name__}: {exc}",
                    details={"stage": "request_encoding"},
                    cause=exc,
                ) from exc

    return EncodingAwareAsyncOpenAI(**kwargs)
