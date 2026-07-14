# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from copy import deepcopy
from typing import Any, Mapping, Optional, Union

from openjiuwen.core.common.logging import llm_logger
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.message import BaseMessage
from openjiuwen.core.foundation.llm.schema.config import ProviderType
from openjiuwen.core.foundation.tool import ToolInfo


OPENROUTER_ATTRIBUTION_HEADER_KEYS = frozenset({
    "http-referer",
    "x-openrouter-title",
    "x-openrouter-categories",
})
OPENROUTER_EXPLICIT_PROMPT_CACHING_PROVIDERS = frozenset({
    "anthropic",
    "qwen",
})
OPENROUTER_1H_PROMPT_CACHE_TTL_PROVIDERS = frozenset({
    "anthropic",
})


def _openrouter_model_provider(model: Optional[str]) -> Optional[str]:
    if not model or "/" not in model:
        return None
    return model.split("/", 1)[0].lstrip("~").lower()


def _normalize_openrouter_provider_set(value: Any, default: frozenset[str]) -> frozenset[str]:
    if value is None:
        return default
    if isinstance(value, str):
        values = value.split(",")
    else:
        values = value
    try:
        return frozenset(str(provider).strip().lower() for provider in values if str(provider).strip())
    except TypeError:
        return default


def _supports_openrouter_explicit_prompt_caching(
        model: Optional[str],
        supported_providers: frozenset[str] = OPENROUTER_EXPLICIT_PROMPT_CACHING_PROVIDERS,
) -> bool:
    provider = _openrouter_model_provider(model)
    return provider in supported_providers


def _supports_openrouter_1h_prompt_cache_ttl(
        model: Optional[str],
        supported_providers: frozenset[str] = OPENROUTER_1H_PROMPT_CACHE_TTL_PROVIDERS,
) -> bool:
    return _openrouter_model_provider(model) in supported_providers


def _without_cache_control(value: Any) -> Any:
    """Remove cache_control and normalize marked text blocks for comparison.

    OpenRouter prompt-cache markers can turn plain text content into a typed text block. 
    For the purpose of prefix matching & caching, these are equivalent and normalized:

    ``{"role": "user", "content": "text content"}``
    ``{"role": "user", "content": [{"type": "text", "text": "text content"}]}``

    Only used for longest prefix comparison
    """
    if isinstance(value, dict):
        normalized = {
            key: _without_cache_control(item)
            for key, item in value.items()
            if key != "cache_control"
        }
        if normalized.get("type") == "text" and set(normalized) <= {"type", "text"}:
            return normalized.get("text", "")
        content = normalized.get("content")
        if isinstance(content, list) and len(content) == 1 and isinstance(content[0], str):
            normalized["content"] = content[0]
        return normalized
    if isinstance(value, list):
        return [_without_cache_control(item) for item in value]
    return value


def _contains_cache_control(value: Any) -> bool:
    if isinstance(value, dict):
        if "cache_control" in value:
            return True
        return any(_contains_cache_control(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_cache_control(item) for item in value)
    return False


def _build_cache_control_marker(enable_1h_ttl: bool = False) -> dict:
    marker = {"type": "ephemeral"}
    if enable_1h_ttl:
        marker["ttl"] = "1h"
    return marker


def _add_cache_control_marker(block: dict, enable_1h_ttl: bool = False) -> dict:
    block.setdefault("cache_control", _build_cache_control_marker(enable_1h_ttl))
    return block


def _mark_message_with_cache_control(message: dict, enable_1h_ttl: bool = False) -> None:
    """Attach OpenRouter prompt-cache metadata to the final content block."""
    if _contains_cache_control(message):
        return

    content = message.get("content")
    if isinstance(content, list):
        if not content:
            return

        last_index = len(content) - 1
        last_block = content[last_index]
        if isinstance(last_block, dict):
            _add_cache_control_marker(last_block, enable_1h_ttl)
        else:
            content[last_index] = _add_cache_control_marker({
                "type": "text",
                "text": last_block if isinstance(last_block, str) else str(last_block),
            }, enable_1h_ttl)
        return

    message["content"] = [_add_cache_control_marker({
        "type": "text",
        "text": content if isinstance(content, str) else ("" if content is None else str(content)),
    }, enable_1h_ttl)]


def _longest_prefix_overlap_index(previous_messages: Optional[list], current_messages: list) -> Optional[int]:
    if not previous_messages:
        return None

    overlap = 0
    for previous, current in zip(previous_messages, current_messages):
        if _without_cache_control(previous) != _without_cache_control(current):
            break
        overlap += 1

    if overlap == 0:
        return None
    return overlap - 1


def _apply_openrouter_prompt_cache_control(
        params: dict,
        previous_messages: Optional[list],
        *,
        enable_1h_ttl: bool = False,
) -> None:
    tools = params.get("tools")
    if isinstance(tools, list) and tools and isinstance(tools[-1], dict):
        _add_cache_control_marker(tools[-1], enable_1h_ttl)

    messages = params.get("messages")
    if not isinstance(messages, list) or not messages:
        return

    prefix_index = _longest_prefix_overlap_index(previous_messages, messages)

    if isinstance(messages[0], dict):
        _mark_message_with_cache_control(messages[0], enable_1h_ttl)

    if prefix_index is not None and isinstance(messages[prefix_index], dict):
        _mark_message_with_cache_control(messages[prefix_index], enable_1h_ttl)

    if isinstance(messages[-1], dict):
        _mark_message_with_cache_control(messages[-1], enable_1h_ttl)


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

    def __init__(
            self,
            model_config,
            model_client_config,
    ):
        super().__init__(model_config, model_client_config)
        extra = model_client_config.__pydantic_extra__ or {}
        self._enable_explicit_caching = extra.get(
            "openrouter_enable_explicit_prompt_caching",
            True,
        )
        # Prefix matching stores previous messages on this client instance, so
        # parallel calls on one shared client can compare against another call's
        # messages and miss the intended prefix. Disable it in that case.
        # Note that Anthropic and Qwen providers allow up to 4 cache_control flags,
        # with no penalty for using extra flags. Hence, there's no reason not
        # to try to add a 4th flag in a reasonable place.
        self._enable_prompt_cache_prefix_matching = extra.get(
            "openrouter_enable_prompt_cache_prefix_matching",
            True,
        )
        self._enable_1h_prompt_cache_ttl = extra.get(
            "openrouter_enable_1h_prompt_cache_ttl",
            False,
        )
        self._explicit_prompt_cache_providers = _normalize_openrouter_provider_set(
            extra.get("openrouter_explicit_prompt_cache_providers"),
            OPENROUTER_EXPLICIT_PROMPT_CACHING_PROVIDERS,
        )
        self._prompt_cache_1h_ttl_providers = _normalize_openrouter_provider_set(
            extra.get("openrouter_prompt_cache_1h_ttl_providers"),
            OPENROUTER_1H_PROMPT_CACHE_TTL_PROVIDERS,
        )
        self._previous_prompt_cache_messages: Optional[list] = None

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

    def _build_request_params(
            self,
            *,
            messages: Union[str, list[BaseMessage], list[dict]],
            tools: Union[list[ToolInfo], list[dict], None],
            temperature: Optional[float],
            top_p: Optional[float],
            model: Optional[str],
            stop: Union[Optional[str], None],
            max_tokens: Optional[int],
            stream: bool,
            **kwargs
    ) -> dict:
        """Build params and add OpenRouter cache breakpoints.

        For supported models, explicit prompt caching marks the final tool, the
        first message, and the final message. When configured, the client also
        marks the longest message-prefix overlap with the previous request.
        """
        params = super()._build_request_params(
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            stop=stop,
            max_tokens=max_tokens,
            stream=stream,
            **kwargs,
        )

        model_name = params.get("model")
        if not self._enable_explicit_caching:
            self._previous_prompt_cache_messages = None
            return params

        if not _supports_openrouter_explicit_prompt_caching(
                model_name,
                self._explicit_prompt_cache_providers,
        ):
            llm_logger.warning(
                "OpenRouter explicit prompt caching is enabled but unsupported for model %s; "
                "skipping cache_control markers.",
                model_name,
            )
            if self._enable_1h_prompt_cache_ttl:
                llm_logger.warning(
                    "OpenRouter 1h prompt-cache TTL is enabled but unsupported for model %s; "
                    "the ttl flag will not be added.",
                    model_name,
                )
            self._previous_prompt_cache_messages = None
            return params

        current_messages = params.get("messages")
        if self._enable_prompt_cache_prefix_matching:
            previous_messages = self._previous_prompt_cache_messages
            self._previous_prompt_cache_messages = (
                deepcopy(current_messages) if isinstance(current_messages, list) else None
            )
        else:
            previous_messages = None
            self._previous_prompt_cache_messages = None

        if isinstance(current_messages, list):
            params["messages"] = deepcopy(current_messages)
        if isinstance(params.get("tools"), list):
            params["tools"] = deepcopy(params["tools"])

        enable_1h_ttl = (
            self._enable_1h_prompt_cache_ttl
            and _supports_openrouter_1h_prompt_cache_ttl(
                model_name,
                self._prompt_cache_1h_ttl_providers,
            )
        )
        if self._enable_1h_prompt_cache_ttl and not enable_1h_ttl:
            llm_logger.warning(
                "OpenRouter 1h prompt-cache TTL is enabled but unsupported for model %s; "
                "using default ephemeral cache_control markers.",
                model_name,
            )
        _apply_openrouter_prompt_cache_control(
            params,
            previous_messages,
            enable_1h_ttl=enable_1h_ttl,
        )
        return params

    @staticmethod
    def _extract_reasoning_content(msg_or_delta: Any) -> Optional[str]:
        return getattr(msg_or_delta, 'reasoning', None) or getattr(msg_or_delta, 'reasoning_content', None)
