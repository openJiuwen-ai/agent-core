# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Any, Mapping, Optional

from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ProviderType


OPENROUTER_ATTRIBUTION_HEADER_KEYS = frozenset({
    "http-referer",
    "x-openrouter-title",
    "x-openrouter-categories",
})


class OpenRouterModelClient(OpenAIModelClient):
    """OpenRouter-specific model client with configurable App Attribution headers.

    This client does NOT provide default attribution header values. The calling
    application (e.g. JiuwenSwarm) is responsible for injecting attribution
    headers via ``ModelClientConfig.custom_headers``.

    This client provides a protection mechanism: once attribution headers are
    configured at the config level, they cannot be overridden by request-level
    headers, following the OpenRouter App Attribution specification.
    """
    __client_name__ = [ProviderType.OpenRouter.value]

    _ATTRIBUTION_PROTECTED_KEYS: frozenset[str] = OPENROUTER_ATTRIBUTION_HEADER_KEYS

    @classmethod
    def _build_request_headers(
            cls,
            base_headers: Optional[Mapping[str, Any]],
            request_headers: Optional[Mapping[str, Any]],
    ) -> dict[str, str]:
        """Merge request-level headers but protect attribution keys from override."""
        effective = dict(base_headers or {})
        if request_headers:
            protected_lower = cls._ATTRIBUTION_PROTECTED_KEYS
            for key, value in request_headers.items():
                if key.lower() in protected_lower:
                    continue
                effective[key] = str(value)
        return effective

    @staticmethod
    def _extract_reasoning_content(msg_or_delta: Any) -> Optional[str]:
        return getattr(msg_or_delta, 'reasoning', None) or getattr(msg_or_delta, 'reasoning_content', None)
