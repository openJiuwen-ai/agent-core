# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unicode handling at the OpenAI-compatible request boundary."""

from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error


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
