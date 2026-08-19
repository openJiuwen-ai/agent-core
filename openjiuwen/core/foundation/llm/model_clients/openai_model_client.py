# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, Iterable, List, Mapping, Optional, Tuple, Union

import httpx

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ModelError, build_error
from openjiuwen.core.common.logging import llm_logger, logger, LogEventType
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.foundation.llm.schema import ImageGenerationResponse, VideoGenerationResponse, \
    AudioGenerationResponse
from openjiuwen.core.foundation.llm.schema.message import (
    BaseMessage,
    AssistantMessage,
    UsageMetadata,
    UserMessage
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.headers_helper import (
    PROTECTED_HEADERS,
    build_base_headers,
    merge_request_headers,
)
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.config import (
    LLMAuthMode,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.utils.endpoint_profiles import apply_message_transforms
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import LLMCallEvents

if TYPE_CHECKING:
    import openai


@dataclass(frozen=True)
class ModelParamRule:
    name: str
    predicate: Callable[[str], bool]
    extra_body_fields: Mapping[str, object]


_DEFAULT_MODEL_PARAM_RULES: tuple[ModelParamRule, ...] = (
    ModelParamRule(
        name="minimax_reasoning_split",
        predicate=lambda m: m.startswith("MiniMax-M"),
        extra_body_fields={"reasoning_split": True},
    ),
)

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
DASHSCOPE_VOICE = frozenset({
    "Cherry", "Serena", "Ethan", "Chelsie", "Momo", "Vivian", "Moon", "Maia", "Kai", "Nofish",
    "Bella", "Jennifer", "Ryan", "Katerina", "Aiden", "Eldric Sage", "Mia", "Mochi", "Bellona",
    "Vincent", "Bunny", "Neil", "Elias", "Arthur", "Nini", "Ebona", "Seren", "Pip", "Stella", "Bodega",
    "Sonrisa", "Alek", "Dolce", "Sohee", "Ono Anna", "Lenn", "Emilien", "Andre", "Radio Gol", "Jada",
    "Dylan", "Li", "Marcus", "Roy", "Peter", "Sunny", "Eric", "Rocky", "Kiki",
})
DASHSCOPE_LANGUAGE_TYPE = frozenset({
    "Auto", "Chinese", "English", "German", "Italian", "Portuguese",
    "Spanish", "Japanese", "Korean", "French", "Russian",
})
_KV_ACTIONS = {"evict", "offload", "prefetch"}
_KV_TARGETS = {"messages", "tools", "session"}
_OPENAI_EXTRA_BODY_EXTENSION_FIELDS = {
    "agent_hint",
    "cache_salt",
    "cache_sharing",
    "return_token_ids",
}


def _openrouter_model_provider(model: Optional[str]) -> Optional[str]:
    if not model or "/" not in model:
        return None
    return model.split("/", 1)[0].lstrip("~").lower()


def _normalize_openrouter_provider_set(value: Any, default: frozenset[str]) -> frozenset[str]:
    if value is None:
        return default
    values = value.split(",") if isinstance(value, str) else value
    try:
        return frozenset(str(provider).strip().lower() for provider in values if str(provider).strip())
    except TypeError:
        return default


def _supports_openrouter_explicit_prompt_caching(
        model: Optional[str],
        supported_providers: frozenset[str] = OPENROUTER_EXPLICIT_PROMPT_CACHING_PROVIDERS,
) -> bool:
    return _openrouter_model_provider(model) in supported_providers


def _supports_openrouter_1h_prompt_cache_ttl(
        model: Optional[str],
        supported_providers: frozenset[str] = OPENROUTER_1H_PROMPT_CACHE_TTL_PROVIDERS,
) -> bool:
    return _openrouter_model_provider(model) in supported_providers


def _without_cache_control(value: Any) -> Any:
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


def _format_exception_detail(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _should_omit_authorization(model_client_config: ModelClientConfig) -> bool:
    auth_mode = getattr(model_client_config, "auth_mode", LLMAuthMode.ApiKey.value)
    if auth_mode in (LLMAuthMode.NoneAuth, LLMAuthMode.NoneAuth.value):
        return True
    if auth_mode in (LLMAuthMode.CustomHeaders, LLMAuthMode.CustomHeaders.value):
        return not str(getattr(model_client_config, "api_key", "") or "").strip()
    return False


def _normalize_openai_base_url(api_base: str) -> str:
    """Return an OpenAI SDK base_url without inventing a ``/v1`` suffix.

    Caller-provided ``api_base`` is passed through after trimming whitespace
    and a trailing slash. If the value is a full chat-completions URL, strip
    that path so the SDK (or affinity HTTP client) can append endpoint paths.
    """
    base = str(api_base or "").strip().rstrip("/")
    if not base:
        return base
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")].rstrip("/")
    return base


def _chat_completions_url(api_base: str) -> str:
    return f"{_normalize_openai_base_url(api_base)}/chat/completions"


def _resolved_api_key_for_config(model_client_config: ModelClientConfig) -> str:
    if _should_omit_authorization(model_client_config):
        return "EMPTY"
    return model_client_config.api_key


def _gateway_nested_error_message(payload: Any) -> Optional[str]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    message = payload.get("message")
    if isinstance(message, str) and message:
        return message
    return None


def _parse_gateway_stream_line(line: str) -> Optional[AssistantMessageChunk]:
    """Parse one SSE or JSON line from an OpenAI-compatible / affinity gateway."""
    raw = (line or "").strip()
    if not raw or raw == "data: [DONE]" or raw == "data:[DONE]":
        return None
    if raw.startswith("data:"):
        raw = raw[5:].lstrip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    nested_error = _gateway_nested_error_message(payload.get("message"))
    if nested_error:
        raise ValueError(nested_error)
    top_error = _gateway_nested_error_message(payload)
    if top_error and not payload.get("choices"):
        raise ValueError(top_error)

    choices = payload.get("choices") or []
    if not choices:
        return None
    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = (
        delta.get("content")
        or message.get("content")
        or message.get("token_text")
        or delta.get("token_text")
        or ""
    )
    reasoning_content = (
        delta.get("reasoning_content")
        or message.get("reasoning_content")
        or message.get("reasoning_token_text")
        or delta.get("reasoning_token_text")
    )
    finish_reason = choice.get("finish_reason") or "null"
    return AssistantMessageChunk(
        content=content or "",
        reasoning_content=reasoning_content,
        finish_reason=finish_reason,
    )


class OpenAIModelClient(BaseModelClient):
    """OpenAI API client supporting GPT models and OpenAI-compatible services."""
    __client_name__ = [ProviderType.OpenAI.value]
    _PROTECTED_HEADERS = PROTECTED_HEADERS
    _MODEL_PARAM_RULES: tuple[ModelParamRule, ...] = _DEFAULT_MODEL_PARAM_RULES

    # Process-wide cache of long-lived ``AsyncOpenAI`` clients, bucketed by
    # tenant/connection config so different api_key/api_base never share a
    # client. Each cached client keeps its own httpx keep-alive connection pool
    # alive, so cache hits reuse established connections (no per-request
    # build/close). Shared across subclasses (OpenRouter/DashScope/DeepSeek) on
    # purpose: one cache per process.
    _client_cache: Dict[Tuple, "openai.AsyncOpenAI"] = {}

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)
        self._base_headers = build_base_headers(
            custom_headers=model_client_config.custom_headers,
        )
        extra = model_client_config.__pydantic_extra__ or {}
        self._enable_openrouter_explicit_caching = extra.get(
            "openrouter_enable_explicit_prompt_caching",
            True,
        )
        self._enable_openrouter_prompt_cache_prefix_matching = extra.get(
            "openrouter_enable_prompt_cache_prefix_matching",
            True,
        )
        self._enable_openrouter_1h_prompt_cache_ttl = extra.get(
            "openrouter_enable_1h_prompt_cache_ttl",
            False,
        )
        self._openrouter_explicit_prompt_cache_providers = _normalize_openrouter_provider_set(
            extra.get("openrouter_explicit_prompt_cache_providers"),
            OPENROUTER_EXPLICIT_PROMPT_CACHING_PROVIDERS,
        )
        self._openrouter_prompt_cache_1h_ttl_providers = _normalize_openrouter_provider_set(
            extra.get("openrouter_prompt_cache_1h_ttl_providers"),
            OPENROUTER_1H_PROMPT_CACHE_TTL_PROVIDERS,
        )
        self._previous_openrouter_prompt_cache_messages: Optional[list] = None

    def _use_shared_client(self) -> bool:
        """Whether to reuse the process-wide cached client (default True).

        Emergency kill-switch: set ``use_shared_llm_http_client=False`` to fall
        back to per-request clients.
        """
        return bool(getattr(self.model_client_config, "use_shared_llm_http_client", True))

    @classmethod
    def connection_key(cls, model_client_config: ModelClientConfig) -> Tuple:
        """Connection identity used to bucket/reuse cached clients.

        Includes ``api_key``/``api_base`` so different tenants never share a
        client. ``api_base`` already determines the proxy, so proxy is not part
        of the key. Exposed as a classmethod so callers (e.g. config hot-reload
        reconciliation) can compute the same key to select connections to close
        via :meth:`aclose_connections`.
        """
        cfg = model_client_config
        # Omit-auth is encoded as a None api_key slot so it stays distinct from
        # a literal "EMPTY" key, without adding auth_mode/omit as extra fields.
        return (
            None if _should_omit_authorization(cfg) else cfg.api_key,
            _normalize_openai_base_url(cfg.api_base),
            cfg.verify_ssl,
            cfg.ssl_cert,
        )

    def _apply_model_specific_params(self, model: Optional[str], params: dict) -> None:
        """Apply provider-specific ``extra_body`` fields based on model name.

        Mutates ``params`` in place: for each matching ``ModelParamRule`` the
        rule's ``extra_body_fields`` are merged into ``params['extra_body']``.
        Existing caller-provided fields are preserved; later rules override
        earlier ones on key collision.
        """
        if not model:
            return
        for rule in self._MODEL_PARAM_RULES:
            if not rule.predicate(model) or not rule.extra_body_fields:
                continue
            extra_body = dict(params.get("extra_body") or {})
            extra_body.update(rule.extra_body_fields)
            params["extra_body"] = extra_body

    def _client_cache_key(self) -> Tuple:
        return self.connection_key(self.model_client_config)

    def _resolved_api_key(self) -> str:
        return _resolved_api_key_for_config(self.model_client_config)

    def _get_client_name(self) -> str:
        """Get client name."""
        return "OpenAI client"

    @classmethod
    def _build_request_headers(
            cls,
            base_headers: Optional[Mapping[str, Any]],
            request_headers: Optional[Mapping[str, Any]],
    ) -> dict[str, str]:
        """Merge request-level headers with prebuilt config-level headers (request wins)."""
        filtered_request_headers = request_headers
        if request_headers:
            filtered_request_headers = {
                key: value
                for key, value in request_headers.items()
                if key.lower() not in OPENROUTER_ATTRIBUTION_HEADER_KEYS
            }
        return merge_request_headers(base_headers, filtered_request_headers)

    def _endpoint_profile_name(self) -> str:
        return str(getattr(self.model_client_config, "endpoint_profile", "") or "").strip().lower()

    def _kv_cache_config(self):
        extensions = getattr(self.model_client_config, "extensions", None)
        return getattr(extensions, "kv_cache", None) if extensions is not None else None

    def _kv_cache_mode(self) -> str:
        kv_cache = self._kv_cache_config()
        mode = getattr(kv_cache, "mode", "none")
        return mode.value if hasattr(mode, "value") else str(mode or "none")

    def _uses_affinity_gateway(self) -> bool:
        return self._kv_cache_mode() == "affinity"

    def _affinity_http_headers(self, *, stream: bool) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if stream:
            headers["Accept"] = "text/event-stream"
        api_key = str(self.model_client_config.api_key or "").strip()
        if api_key and not _should_omit_authorization(self.model_client_config):
            headers["Authorization"] = f"Bearer {api_key}"
        custom = self.model_client_config.custom_headers or {}
        for key, value in custom.items():
            headers[str(key)] = str(value)
        return headers

    def _affinity_request_body(self, params: dict) -> dict:
        body = dict(params)
        extra_body = body.pop("extra_body", None) or {}
        if isinstance(extra_body, dict):
            body.update(extra_body)
        body.pop("timeout", None)
        body.pop("extra_headers", None)
        return body

    async def _iter_affinity_gateway_stream(
            self,
            params: dict,
            *,
            timeout: Optional[float] = None,
    ) -> AsyncIterator[AssistantMessageChunk]:
        url = _chat_completions_url(self.model_client_config.api_base)
        headers = self._affinity_http_headers(stream=True)
        body = self._affinity_request_body(params)
        verify = (
            SslUtils.create_strict_ssl_context(self.model_client_config.ssl_cert)
            if self.model_client_config.verify_ssl
            else False
        )
        raw_samples: list[str] = []
        parsed_chunks = 0
        has_model_output = False
        final_timeout = timeout if timeout is not None else self.model_client_config.timeout

        async with httpx.AsyncClient(
                proxy=UrlUtils.get_global_proxy_url(url),
                verify=verify,
                timeout=final_timeout,
        ) as http_client:
            async with http_client.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code != 200:
                    error_text = (await response.aread()).decode("utf-8", errors="replace")
                    raise ValueError(f"API returned error {response.status_code}: {error_text}")
                content_type = str(response.headers.get("Content-Type", "")).lower()
                if "text/event-stream" in content_type:
                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()
                        if not line:
                            continue
                        if len(raw_samples) < 4:
                            raw_samples.append(line[:300])
                        chunk = _parse_gateway_stream_line(line)
                        if chunk is None:
                            continue
                        parsed_chunks += 1
                        has_model_output = has_model_output or bool(
                            chunk.content or chunk.reasoning_content or chunk.tool_calls
                        )
                        yield chunk
                else:
                    raw = (await response.aread()).decode("utf-8", errors="replace")
                    stripped = raw.strip()
                    if stripped:
                        raw_samples.append(stripped[:300])
                    chunk = _parse_gateway_stream_line(stripped)
                    if chunk is not None:
                        parsed_chunks += 1
                        has_model_output = bool(
                            chunk.content or chunk.reasoning_content or chunk.tool_calls
                        )
                        yield chunk
                    else:
                        for line in stripped.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            parsed = _parse_gateway_stream_line(line)
                            if parsed is None:
                                continue
                            parsed_chunks += 1
                            has_model_output = has_model_output or bool(
                                parsed.content or parsed.reasoning_content or parsed.tool_calls
                            )
                            yield parsed

        if parsed_chunks == 0 or not has_model_output:
            raise ValueError(
                "affinity stream completed without model output, "
                f"raw_samples={raw_samples!r}"
            )

    def supports_kv_cache_release(self) -> bool:
        return self._kv_cache_mode() == "release"

    def supports_kv_cache_affinity(self) -> bool:
        return self._kv_cache_mode() == "affinity"

    @staticmethod
    def _sanitize_tool_calls(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue

            cleaned = []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                func = tc.get("function", {})
                cleaned.append({
                    "id": tc.get("id", ""),
                    "type": "function",
                    "index": tc.get("index"),
                    "function": {
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", ""),
                    },
                })
            msg["tool_calls"] = cleaned
        return messages

    @staticmethod
    def _raise_kv_cache_error(message: str):
        raise build_error(
            StatusCode.MODEL_CONFIG_ERROR,
            error_msg=f"[OpenAIModelClient kv_cache] {message}",
        )

    @classmethod
    def _validate_kv_action_target(cls, action: str, target: str) -> None:
        if action not in _KV_ACTIONS:
            cls._raise_kv_cache_error(f"unsupported KV affinity action: {action}")
        if target not in _KV_TARGETS:
            cls._raise_kv_cache_error(f"unsupported KV affinity target: {target}")

    @classmethod
    def _kv_range_edit(
            cls,
            *,
            action: str,
            target: str,
            start: Optional[int] = None,
            end: Optional[int] = None,
    ) -> dict[str, Any]:
        if start is None or end is None:
            cls._raise_kv_cache_error(f"target={target} requires both start and end")
        if not isinstance(start, int) or isinstance(start, bool):
            cls._raise_kv_cache_error(f"target={target} start must be an integer")
        if not isinstance(end, int) or isinstance(end, bool):
            cls._raise_kv_cache_error(f"target={target} end must be an integer")
        if start < 0 or end < 0:
            cls._raise_kv_cache_error(f"target={target} range must be non-negative")
        if start >= end:
            cls._raise_kv_cache_error(f"target={target} half-open range requires start < end")
        return {"type": action, "target": target, "start": start, "end": end}

    @staticmethod
    def _has_any_kv_range(**ranges: Optional[int]) -> bool:
        return any(value is not None for value in ranges.values())

    @classmethod
    def _build_kv_target_edits(
            cls,
            *,
            action: str,
            target: str,
            msg_start: Optional[int] = None,
            msg_end: Optional[int] = None,
            tools_start: Optional[int] = None,
            tools_end: Optional[int] = None,
            include_tools: bool = False,
    ) -> list[dict[str, Any]]:
        cls._validate_kv_action_target(action, target)

        if target == "session":
            if cls._has_any_kv_range(
                    msg_start=msg_start,
                    msg_end=msg_end,
                    tools_start=tools_start,
                    tools_end=tools_end,
            ):
                cls._raise_kv_cache_error("target=session does not accept message/tool ranges")
            if include_tools:
                cls._raise_kv_cache_error("target=session does not accept include_tools=True")
            return [{"type": action, "target": "session"}]

        if target == "messages":
            edits = [
                cls._kv_range_edit(action=action, target="messages", start=msg_start, end=msg_end)
            ]
            if include_tools:
                edits.append(
                    cls._kv_range_edit(action=action, target="tools", start=tools_start, end=tools_end)
                )
            elif cls._has_any_kv_range(tools_start=tools_start, tools_end=tools_end):
                cls._raise_kv_cache_error("tools range requires include_tools=True or target=tools")
            return edits

        if include_tools:
            cls._raise_kv_cache_error("target=tools should not also set include_tools=True")
        if cls._has_any_kv_range(msg_start=msg_start, msg_end=msg_end):
            cls._raise_kv_cache_error("messages range is invalid for target=tools")
        return [
            cls._kv_range_edit(action=action, target="tools", start=tools_start, end=tools_end)
        ]

    @classmethod
    def _build_agent_hint(
            cls,
            *,
            session_id: Optional[str] = None,
            parent_session_id: Optional[str] = None,
            action: Optional[str] = None,
            target: str = "session",
            manage_request: Optional[bool] = None,
            msg_start: Optional[int] = None,
            msg_end: Optional[int] = None,
            tools_start: Optional[int] = None,
            tools_end: Optional[int] = None,
            include_tools: bool = False,
    ) -> dict[str, Any]:
        if not session_id:
            cls._raise_kv_cache_error("session_id is required")
        if not parent_session_id:
            cls._raise_kv_cache_error("parent_session_id is required")

        hint: dict[str, Any] = {
            "session_id": session_id,
            "parent_session_id": parent_session_id,
        }

        if action is None:
            if manage_request is not None:
                cls._raise_kv_cache_error("manage_request is only valid when kv_action is set")
            return hint

        if not isinstance(manage_request, bool):
            cls._raise_kv_cache_error("manage_request must be explicitly set when kv_action is set")

        hint["context_management"] = {
            "manage_request": manage_request,
            "edits": cls._build_kv_target_edits(
                action=action,
                target=target,
                msg_start=msg_start,
                msg_end=msg_end,
                tools_start=tools_start,
                tools_end=tools_end,
                include_tools=include_tools,
            ),
        }
        return hint

    def build_kv_cache_affinity_invoke_kwargs(
            self,
            *,
            session: object = None,
            session_id: Optional[str] = None,
            parent_session_id: Optional[str] = None,
            enable_kv_cache_affinity: bool = False,
            **_: Any,
    ) -> dict:
        if not enable_kv_cache_affinity or not self.supports_kv_cache_affinity():
            return {}
        cache_id = session_id
        if cache_id is None and session is not None and hasattr(session, "get_session_id"):
            cache_id = session.get_session_id()
        if not cache_id:
            self._raise_kv_cache_error("session_id is required when KV cache affinity is enabled")
        return {
            "session_id": cache_id,
            "parent_session_id": parent_session_id or cache_id,
        }

    def build_kv_cache_invoke_kwargs(
            self,
            *,
            session: object = None,
            enable_kv_cache_release: bool = False,
            **_: Any,
    ) -> dict:
        if not enable_kv_cache_release or not self.supports_kv_cache_release():
            return {}
        extra: dict = {}
        if session is not None and hasattr(session, "get_session_id"):
            extra["session_id"] = session.get_session_id()
        extra["enable_cache_sharing"] = True
        return extra

    async def release(
            self,
            session_id: str,
            messages: List,
            messages_released_index: int,
            *,
            model: Optional[str] = None,
            tools: Optional[List] = None,
            tools_released_index: Optional[int] = None,
    ) -> bool:
        if not self.supports_kv_cache_release():
            return False

        kv_cache = self._kv_cache_config()
        messages_dict = self._convert_messages_to_dict(messages)
        tools_dict = self._convert_tools_to_dict(tools)
        sanitized_messages = self._sanitize_tool_calls(messages_dict)
        release_params = {
            "model": model if model else self.model_config.model_name,
            getattr(kv_cache, "session_field", "cache_salt"): session_id,
            getattr(kv_cache, "enable_cache_sharing_field", "cache_sharing"): True,
            "messages": sanitized_messages,
            "messages_released_index": messages_released_index,
        }
        if tools_dict:
            release_params["tools"] = tools_dict
        if tools_released_index is not None:
            release_params["tools_released_index"] = tools_released_index

        url = (
            f"{self.model_client_config.api_base.rstrip('/')}"
            f"{getattr(kv_cache, 'release_endpoint', '/release_kv_cache')}"
        )
        verify = (
            SslUtils.create_strict_ssl_context(self.model_client_config.ssl_cert)
            if self.model_client_config.verify_ssl
            else False
        )
        headers = {"Content-Type": "application/json"}
        async with httpx.AsyncClient(
                proxy=UrlUtils.get_global_proxy_url(url),
                verify=verify,
                timeout=self.model_client_config.timeout,
        ) as http_client:
            response = await http_client.post(url, headers=headers, json=release_params)
        if 200 <= response.status_code < 300:
            return True
        raise build_error(
            StatusCode.MODEL_CALL_FAILED,
            error_msg=(
                f"OpenAI-compatible KV cache release failed: "
                f"{response.status_code} {response.text}"
            ),
        )

    async def evict_kvc(self, **kwargs) -> bool:
        return await self._invoke_kv_cache_affinity_action("evict", **kwargs)

    async def offload_kvc(self, **kwargs) -> bool:
        return await self._invoke_kv_cache_affinity_action("offload", **kwargs)

    async def prefetch_kvc(self, **kwargs) -> bool:
        return await self._invoke_kv_cache_affinity_action("prefetch", **kwargs)

    async def _invoke_kv_cache_affinity_action(
            self,
            action: str,
            *,
            session_id: str,
            parent_session_id: Optional[str] = None,
            target: str = "session",
            model: Optional[str] = None,
            msg_start: Optional[int] = None,
            msg_end: Optional[int] = None,
            tools_start: Optional[int] = None,
            tools_end: Optional[int] = None,
            include_tools: bool = False,
            timeout: Optional[float] = None,
            max_attempts: Optional[int] = None,
            **kwargs,
    ) -> bool:
        if not self.supports_kv_cache_affinity():
            return False

        params = self._build_request_params(
            messages=[{"role": "user", "content": ""}],
            tools=None,
            temperature=None,
            top_p=None,
            model=model,
            stop=None,
            max_tokens=None,
            stream=False,
            session_id=session_id,
            parent_session_id=parent_session_id or session_id,
            kv_action=action,
            target=target,
            manage_request=True,
            msg_start=msg_start,
            msg_end=msg_end,
            tools_start=tools_start,
            tools_end=tools_end,
            include_tools=include_tools,
            **kwargs,
        )
        self._move_openai_extra_body_extensions(params)

        attempts = self.model_client_config.max_retries if max_attempts is None else max(1, int(max_attempts))
        last_error = None
        for attempt in range(attempts):
            async_client = None
            try:
                async_client = self._create_async_openai_client(timeout=timeout)
                if timeout is not None:
                    params["timeout"] = timeout
                await async_client.chat.completions.create(**params)
                return True
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    continue
            finally:
                if async_client is not None and not self._use_shared_client():
                    await async_client.close()

        raise build_error(
            StatusCode.MODEL_CALL_FAILED,
            error_msg=f"OpenAI-compatible KV cache {action} failed: {last_error}",
        )

    def _build_request_params(
            self,
            *,
            messages: Union[str, List[BaseMessage], List[dict]],
            tools: Union[List[ToolInfo], List[dict], None],
            temperature: Optional[float],
            top_p: Optional[float],
            model: Optional[str],
            stop: Union[Optional[str], None],
            max_tokens: Optional[int],
            stream: bool,
            **kwargs
    ) -> dict:
        """
        Build request params with OpenAI-specific adjustments.

        Custom rule:
            For api_base containing "openai.com", keep only one of temperature/top_p:
            - temperature has higher priority than top_p
            - if temperature is present, drop top_p
            - if temperature is not present but top_p is, keep top_p
        """
        session_id = kwargs.pop("session_id", None)
        enable_cache_sharing = bool(kwargs.pop("enable_cache_sharing", False))
        parent_session_id = kwargs.pop("parent_session_id", None)
        kv_action = kwargs.pop("kv_action", None)
        kv_target = kwargs.pop("target", "session")
        manage_request = kwargs.pop("manage_request", None)
        msg_start = kwargs.pop("msg_start", None)
        msg_end = kwargs.pop("msg_end", None)
        tools_start = kwargs.pop("tools_start", None)
        tools_end = kwargs.pop("tools_end", None)
        include_tools = bool(kwargs.pop("include_tools", False))

        is_session_manage_request = bool(
            kv_action and manage_request is True and kv_target == "session"
        )
        build_messages = (
            [{"role": "user", "content": ""}]
            if is_session_manage_request
            else messages
        )

        # First, use the base implementation to build standard OpenAI-compatible params
        params = super()._build_request_params(
            messages=build_messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            stop=stop,
            max_tokens=max_tokens,
            stream=stream,
            **kwargs
        )

        api_base = (self.model_client_config.api_base or "").lower()
        if "openai.com" in api_base:
            has_temperature = "temperature" in params and params["temperature"] is not None
            has_top_p = "top_p" in params and params["top_p"] is not None

            # If both exist, keep temperature and remove top_p
            if has_temperature and has_top_p:
                params.pop("top_p", None)
            # If only one exists, keep as-is

        params["messages"] = apply_message_transforms(
            self.model_client_config,
            params["messages"],
        )

        profile_name = self._endpoint_profile_name()
        kv_mode = self._kv_cache_mode()
        if profile_name == "siliconflow" or kv_mode in {"release", "affinity"}:
            params["messages"] = self._sanitize_tool_calls(params["messages"])

        if is_session_manage_request:
            params["messages"] = []
            params.pop("tools", None)
            params.pop("tool_choice", None)
            params.pop("max_tokens", None)

        if kv_mode == "release" and enable_cache_sharing and session_id:
            kv_cache = self._kv_cache_config()
            params[getattr(kv_cache, "enable_cache_sharing_field", "cache_sharing")] = True
            params[getattr(kv_cache, "session_field", "cache_salt")] = session_id

        if kv_mode == "affinity" and session_id:
            kv_cache = self._kv_cache_config()
            params[getattr(kv_cache, "affinity_field", "agent_hint")] = self._build_agent_hint(
                session_id=session_id,
                parent_session_id=parent_session_id or session_id,
                action=kv_action,
                target=kv_target,
                manage_request=manage_request,
                msg_start=msg_start,
                msg_end=msg_end,
                tools_start=tools_start,
                tools_end=tools_end,
                include_tools=include_tools,
            )

        self._apply_openrouter_profile(params)

        return params

    def _apply_openrouter_profile(self, params: dict) -> None:
        if self._endpoint_profile_name() != "openrouter":
            return

        model_name = params.get("model")
        if not self._enable_openrouter_explicit_caching:
            self._previous_openrouter_prompt_cache_messages = None
            return

        if not _supports_openrouter_explicit_prompt_caching(
                model_name,
                self._openrouter_explicit_prompt_cache_providers,
        ):
            llm_logger.warning(
                "OpenRouter explicit prompt caching is enabled but unsupported for model %s; "
                "skipping cache_control markers.",
                model_name,
            )
            if self._enable_openrouter_1h_prompt_cache_ttl:
                llm_logger.warning(
                    "OpenRouter 1h prompt-cache TTL is enabled but unsupported for model %s; "
                    "the ttl flag will not be added.",
                    model_name,
                )
            self._previous_openrouter_prompt_cache_messages = None
            return

        current_messages = params.get("messages")
        if self._enable_openrouter_prompt_cache_prefix_matching:
            previous_messages = self._previous_openrouter_prompt_cache_messages
            self._previous_openrouter_prompt_cache_messages = (
                deepcopy(current_messages) if isinstance(current_messages, list) else None
            )
        else:
            previous_messages = None
            self._previous_openrouter_prompt_cache_messages = None

        if isinstance(current_messages, list):
            params["messages"] = deepcopy(current_messages)
        if isinstance(params.get("tools"), list):
            params["tools"] = deepcopy(params["tools"])

        enable_1h_ttl = (
            self._enable_openrouter_1h_prompt_cache_ttl
            and _supports_openrouter_1h_prompt_cache_ttl(
                model_name,
                self._openrouter_prompt_cache_1h_ttl_providers,
            )
        )
        if self._enable_openrouter_1h_prompt_cache_ttl and not enable_1h_ttl:
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

    @staticmethod
    def _move_openai_extra_body_extensions(params: dict) -> None:
        extra_body = dict(params.get("extra_body") or {})
        for key in list(_OPENAI_EXTRA_BODY_EXTENSION_FIELDS):
            if key in params:
                extra_body[key] = params.pop(key)
        if extra_body:
            params["extra_body"] = extra_body

    def _create_async_openai_client(self, timeout: Optional[float] = None) -> "openai.AsyncOpenAI":
        """Acquire an ``AsyncOpenAI`` client for a request.

        Default (shared) path returns a long-lived, cached client whose httpx
        keep-alive pool reuses established connections. The caller MUST NOT close
        it on the hot path.

        Emergency fallback path (``use_shared_llm_http_client=False``) builds a
        fresh per-request client that the caller owns and must close.

        Args:
            timeout: Optional per-request timeout. Only baked into the client in
                the fallback path; in the shared path it is applied per request
                via ``create(..., timeout=...)`` so the cached client is never
                rebuilt just to change the timeout.
        """
        if not self._use_shared_client():
            return self._build_async_openai_client(timeout=timeout)

        # Shared path: build once per tenant/connection identity and reuse.
        # Building is fully synchronous (no ``await``), so under a single-threaded
        # asyncio event loop the get/build/set below is atomic and needs no lock.
        key = self._client_cache_key()
        client = self._client_cache.get(key)
        if client is None:
            client = self._build_async_openai_client()
            self._client_cache[key] = client
            llm_logger.info(
                "Created shared long-lived AsyncOpenAI client.",
                event_type=LogEventType.LLM_CALL_START,
                timeout=self.model_client_config.timeout,
                max_retries=self.model_client_config.max_retries,
            )
        return client

    def _build_async_openai_client(self, timeout: Optional[float] = None) -> "openai.AsyncOpenAI":
        """Build a fresh ``AsyncOpenAI`` client with its own httpx connection pool."""
        from openai import AsyncOpenAI

        ssl_verify, ssl_cert = self.model_client_config.verify_ssl, self.model_client_config.ssl_cert
        verify = SslUtils.create_strict_ssl_context(ssl_cert) if ssl_verify else ssl_verify

        # httpx defaults keepalive_expiry to 5s, which drops idle keep-alive
        # connections between calls spaced >5s apart, forcing a rebuild. Bump to
        # 60s to keep connections warm across typical inter-request gaps while
        # staying at/under common upstream/LB idle timeouts (avoids reusing a
        # server-closed "dead" connection).
        event_hooks = None
        if _should_omit_authorization(self.model_client_config):
            async def _strip_authorization(request: httpx.Request) -> None:
                request.headers.pop("Authorization", None)

            event_hooks = {"request": [_strip_authorization]}

        http_client = httpx.AsyncClient(
            proxy=UrlUtils.get_global_proxy_url(self.model_client_config.api_base),
            verify=verify,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=60.0,
            ),
            event_hooks=event_hooks,
        )

        # Use method-level timeout if provided, otherwise use config timeout
        final_timeout = timeout if timeout is not None else self.model_client_config.timeout
        llm_logger.info(
            "Before create openai client, model client config params ready.",
            event_type=LogEventType.LLM_CALL_START,
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries
        )

        return AsyncOpenAI(
            api_key=self._resolved_api_key(),
            base_url=_normalize_openai_base_url(self.model_client_config.api_base),
            http_client=http_client,
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries
        )

    @classmethod
    async def aclose(cls) -> None:
        """Close all cached clients and their underlying connection pools.

        Intended for agent/process teardown only. NEVER call this on the request
        hot path: it tears down the shared client that other in-flight calls
        rely on.
        """
        clients = list(cls._client_cache.values())
        cls._client_cache.clear()

        for client in clients:
            try:
                await client.close()
            except Exception as e:  # pragma: no cover - defensive cleanup
                logger.warning(f"Error closing cached AsyncOpenAI client: {e}")

    @classmethod
    async def aclose_connections(cls, configs: Iterable[ModelClientConfig]) -> None:
        """Close and drop cached clients for exactly the given connection identities.

        Only the connections whose identity matches one of ``configs`` are
        closed; everything else is left untouched. Intended for delta-based
        eviction on config hot-reload (close just the credentials that were
        removed/changed), so unrelated cached clients (used by other components
        sharing this process-wide cache) are never disturbed.

        Closing is immediate even if a call is in flight: a model the user
        removed should stop consuming tokens at once. An in-flight request on a
        closed client surfaces as a normal model-call failure (not retried by
        LLMRetryRail, which only retries repetition/stream-timeout markers).
        """
        keys = {cls.connection_key(cfg) for cfg in configs}
        closed = 0
        for key in keys:
            client = cls._client_cache.pop(key, None)
            if client is None:
                continue
            try:
                await client.close()
                closed += 1
            except Exception as e:  # pragma: no cover - defensive cleanup
                logger.warning(f"Error closing AsyncOpenAI client: {e}")
        if closed:
            logger.info(f"Closed {closed} AsyncOpenAI client(s) for removed/updated model config")

    async def invoke(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            model: str = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            output_parser: Optional[BaseOutputParser] = None,
            timeout: float = None,
            **kwargs
    ) -> AssistantMessage:
        """Async invoke OpenAI API
        
        Args:
            :param output_parser:
            :param model:
            :param stop:
            :param temperature:
            :param tools:
            :param messages:
            :param top_p:
            :param max_tokens:
            :param timeout:
            **kwargs: Additional parameters
            
        Returns:
            AssistantMessage: Model response
        """
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        request_custom_headers = kwargs.pop("custom_headers", None)

        # Build request parameters
        params = self._build_request_params(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            max_tokens=max_tokens,
            stream=False,
            **kwargs
        )

        effective_headers = self._build_request_headers(
            self._base_headers,
            request_custom_headers,
        )
        if effective_headers:
            params["extra_headers"] = effective_headers

        self._apply_model_specific_params(model, params)
        self._move_openai_extra_body_extensions(params)
        if tracer_record_data:
            await tracer_record_data(llm_params=params)

        async_client = None
        try:
            await trigger(
                LLMCallEvents.LLM_INPUT,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                temperature=params.get("temperature"),
                top_p=params.get("top_p"),
                max_tokens=params.get("max_tokens"),
                frequency_penalty=params.get("frequency_penalty"),
                presence_penalty=params.get("presence_penalty"),
                stop=params.get("stop"))

            async_client = self._create_async_openai_client(timeout=timeout)

            # Per-request timeout override; cached shared client is never rebuilt
            # just to change the timeout.
            if timeout is not None:
                params["timeout"] = timeout

            # Call API
            response = await async_client.chat.completions.create(**params)
            llm_logger.info(
                "OpenAI API response received.",
                event_type=LogEventType.LLM_CALL_END,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                temperature=params.get("temperature"),
                top_p=params.get("top_p"),
                max_tokens=params.get("max_tokens"),
                is_stream=False,
                metadata={"response": str(response)}
            )

            # Parse response and apply output parser
            llm_logger.info(
                "Before parse response with output parser.",
                event_type=LogEventType.LLM_CALL_END,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=False,
                metadata={"output_parser": str(output_parser)}
            )
            assistant_message = await self._parse_response(response, output_parser)

            if tracer_record_data:
                await tracer_record_data(llm_response=assistant_message)

            await trigger(
                LLMCallEvents.LLM_OUTPUT,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                response=assistant_message.content,
                reasoning_content=assistant_message.reasoning_content,
                usage=assistant_message.usage_metadata,
                tool_calls=assistant_message.tool_calls)

            return assistant_message

        except Exception as e:
            await trigger(
                LLMCallEvents.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=False,
                error=e)
            llm_logger.error(
                "OpenAI API async invoke error.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                temperature=params.get("temperature"),
                top_p=params.get("top_p"),
                max_tokens=params.get("max_tokens"),
                is_stream=False,
                exception=_format_exception_detail(e)
            )
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"openAI API async invoke error: {_format_exception_detail(e)}"
            ) from e
        finally:
            # Only close clients we own (fallback path). Shared/pooled clients
            # are long-lived; closing them on the hot path would tear down the
            # shared transport.
            if async_client is not None and not self._use_shared_client():
                await async_client.close()

    async def stream(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            model: str = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            output_parser: Optional[BaseOutputParser] = None,
            timeout: float = None,
            **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Async streaming invoke OpenAI API
        
        Args:
            :param output_parser:
            :param model:
            :param stop:
            :param temperature:
            :param tools:
            :param messages:
            :param top_p:
            :param max_tokens:
            :param timeout:
            **kwargs: Additional parameters
            
        Yields:
            AssistantMessageChunk: Streaming response chunk
        """
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        request_custom_headers = kwargs.pop("custom_headers", None)

        # Build request parameters
        params = self._build_request_params(
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            stop=stop,
            max_tokens=max_tokens,
            stream=True,
            **kwargs
        )

        # OpenAI-compatible streaming responses only include usage on the final
        # chunk when include_usage is explicitly requested.
        stream_options = params.get("stream_options")
        if isinstance(stream_options, dict):
            stream_options.setdefault("include_usage", True)
        elif stream_options is None:
            params["stream_options"] = {"include_usage": True}

        effective_headers = self._build_request_headers(
            self._base_headers,
            request_custom_headers,
        )
        if effective_headers:
            params["extra_headers"] = effective_headers

        self._apply_model_specific_params(model, params)
        self._move_openai_extra_body_extensions(params)

        if tracer_record_data:
            await tracer_record_data(llm_params=params)

        async_client = None
        try:
            await trigger(
                LLMCallEvents.LLM_INPUT,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                temperature=params.get("temperature"),
                top_p=params.get("top_p"),
                max_tokens=params.get("max_tokens"),
                frequency_penalty=params.get("frequency_penalty"),
                presence_penalty=params.get("presence_penalty"),
                stop=params.get("stop"),
                is_stream=True)

            async_client = self._create_async_openai_client(timeout=timeout)

            # Per-request timeout override; cached shared client is never rebuilt
            # just to change the timeout.
            if timeout is not None:
                params["timeout"] = timeout

            final_message = None
            if self._uses_affinity_gateway() and not output_parser:
                async for parsed_chunk in self._iter_affinity_gateway_stream(
                        params,
                        timeout=timeout,
                ):
                    await trigger(
                        LLMCallEvents.LLM_RESPONSE_RECEIVED,
                        model_name=params.get("model"),
                        model_provider=self.model_client_config.client_provider)
                    if final_message:
                        final_message = final_message + parsed_chunk
                    else:
                        final_message = parsed_chunk
                    yield parsed_chunk
            else:
                response_stream = await async_client.chat.completions.create(**params)
                if output_parser:
                    async for parsed_result in self._astream_with_parser(response_stream, output_parser):
                        await trigger(
                            LLMCallEvents.LLM_RESPONSE_RECEIVED,
                            model_name=params.get("model"),
                            model_provider=self.model_client_config.client_provider)
                        if final_message:
                            final_message = final_message + parsed_result
                        else:
                            final_message = parsed_result
                        yield parsed_result
                else:
                    async for chunk in response_stream:
                        parsed_chunk = self._parse_stream_chunk(chunk)
                        if parsed_chunk:
                            await trigger(
                                LLMCallEvents.LLM_RESPONSE_RECEIVED,
                                model_name=params.get("model"),
                                model_provider=self.model_client_config.client_provider)
                            if final_message:
                                final_message = final_message + parsed_chunk
                            else:
                                final_message = parsed_chunk
                            yield parsed_chunk

            if tracer_record_data:
                await tracer_record_data(llm_response=final_message)

            await trigger(
                LLMCallEvents.LLM_OUTPUT,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=True,
                response=final_message.content if final_message else None,
                reasoning_content=final_message.reasoning_content if final_message else None,
                usage=final_message.usage_metadata if final_message else None,
                tool_calls=final_message.tool_calls if final_message else None)

        except Exception as e:
            # Many stream-layer exceptions (httpx.RemoteProtocolError,
            # APIConnectionError wrappers, asyncio.CancelledError) return an
            # empty str(), which leaves the error log unactionable. Always
            # surface the exception type so the cause is identifiable.
            error_detail = _format_exception_detail(e)
            await trigger(
                LLMCallEvents.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=True,
                error=e)
            llm_logger.error(
                "OpenAI API async stream error.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                temperature=params.get("temperature"),
                top_p=params.get("top_p"),
                max_tokens=params.get("max_tokens"),
                is_stream=True,
                exception=error_detail
            )
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"openAI API async stream error: {error_detail}"
            ) from e
        finally:
            # Only close clients we own (fallback path). Shared/pooled clients
            # are long-lived; closing them on the hot path would tear down the
            # shared transport.
            if async_client is not None and not self._use_shared_client():
                await async_client.close()

    async def generate_image(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            size: Optional[str] = "1664*928",
            negative_prompt: Optional[str] = None,
            n: Optional[int] = 1,
            prompt_extend: bool = True,
            watermark: bool = False,
            seed: int = 0,
            **kwargs
    ) -> ImageGenerationResponse:
        self._require_dashscope_media_profile("generate_image")

        try:
            content_list = self._dashscope_image_content(messages)
            import dashscope
            from dashscope import MultiModalConversation

            api_params = {
                "api_key": self.model_client_config.api_key,
                "model": model or self.model_config.model_name,
                "messages": [{"role": "user", "content": content_list}],
                "result_format": "message",
                "stream": False,
                "size": size,
                "n": n,
                "prompt_extend": prompt_extend,
                "watermark": watermark,
            }
            if negative_prompt:
                api_params["negative_prompt"] = negative_prompt
            if seed is not None:
                api_params["seed"] = seed
            api_params.update(kwargs)

            dashscope.base_http_api_url = self.model_client_config.api_base
            response = MultiModalConversation.call(**api_params)
            self._raise_for_dashscope_response(response, "image generation")

            image_urls = []
            for choice in (getattr(response, "output", None) or {}).get("choices", []):
                content = (choice.get("message") or {}).get("content") or []
                for content_item in content:
                    if isinstance(content_item, dict) and content_item.get("image"):
                        image_urls.append(content_item["image"])
            if not image_urls:
                raise build_error(
                    StatusCode.MODEL_CALL_FAILED,
                    error_msg="No images returned from DashScope API.",
                )
            return ImageGenerationResponse(
                model=api_params["model"],
                images=image_urls,
                created=None,
            )
        except Exception as exc:
            self._raise_dashscope_model_error("image generation", exc)

    async def generate_video(
            self,
            messages: List[UserMessage],
            *,
            img_url: Optional[str] = None,
            audio_url: Optional[str] = None,
            model: Optional[str] = None,
            size: Optional[str] = None,
            resolution: Optional[str] = None,
            duration: Optional[int] = 5,
            prompt_extend: bool = True,
            watermark: bool = False,
            negative_prompt: Optional[str] = None,
            seed: Optional[int] = None,
            **kwargs
    ) -> VideoGenerationResponse:
        self._require_dashscope_media_profile("generate_video")

        try:
            prompt = self._single_user_text(messages, "Video generation")
            if not prompt.strip():
                raise build_error(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    error_msg="Video generation requires non-empty text content.",
                )
            self._validate_dashscope_video_params(img_url=img_url, size=size, resolution=resolution)
            import dashscope
            from dashscope import VideoSynthesis

            api_params = {
                "api_key": self.model_client_config.api_key,
                "model": model or self.model_config.model_name,
                "prompt": prompt,
                "duration": duration,
                "prompt_extend": prompt_extend,
                "watermark": watermark,
            }
            if img_url:
                api_params["img_url"] = img_url
            if audio_url:
                api_params["audio_url"] = audio_url
            if size:
                api_params["size"] = size
            if resolution:
                api_params["resolution"] = resolution
            if negative_prompt:
                api_params["negative_prompt"] = negative_prompt
            if seed is not None:
                api_params["seed"] = seed
            api_params.update(kwargs)

            dashscope.base_http_api_url = self.model_client_config.api_base
            response = VideoSynthesis.call(**api_params)
            self._raise_for_dashscope_response(response, "video generation")
            output = getattr(response, "output", None) or {}
            video_url = self._get_mapping_or_attr(output, "video_url") or self._get_mapping_or_attr(output, "url")
            if not video_url:
                raise build_error(
                    StatusCode.MODEL_CALL_FAILED,
                    error_msg="No video URL returned from DashScope API.",
                )
            usage = getattr(response, "usage", None) or {}
            video_duration = (
                self._get_mapping_or_attr(usage, "duration")
                or self._get_mapping_or_attr(usage, "output_video_duration")
                or duration
            )
            video_resolution = self._get_mapping_or_attr(usage, "size") or resolution or size
            return VideoGenerationResponse(
                model=api_params["model"],
                video_url=video_url,
                duration=video_duration,
                resolution=video_resolution,
                format="mp4",
            )
        except Exception as exc:
            self._raise_dashscope_model_error("video generation", exc)

    async def generate_speech(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            voice: Optional[str] = "Cherry",
            language_type: Optional[str] = "Auto",
            **kwargs
    ) -> AudioGenerationResponse:
        self._require_dashscope_media_profile("generate_speech")

        try:
            text = self._single_user_text(messages, "Speech generation")
            if not text.strip():
                raise build_error(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    error_msg="Speech generation requires non-empty text content.",
                )
            self._validate_dashscope_speech_params(voice=voice, language_type=language_type)
            import dashscope
            from dashscope import MultiModalConversation

            api_params = {
                "api_key": self.model_client_config.api_key,
                "model": model or self.model_config.model_name,
                "text": text,
                "voice": voice,
                "language_type": language_type,
            }
            api_params.update(kwargs)

            dashscope.base_http_api_url = self.model_client_config.api_base
            response = MultiModalConversation.call(**api_params)
            self._raise_for_dashscope_response(response, "speech generation")

            audio_url, audio_data, audio_format = self._extract_dashscope_audio(response)
            if not audio_url and not audio_data:
                raise build_error(
                    StatusCode.MODEL_CALL_FAILED,
                    error_msg="No audio URL or data returned from DashScope API.",
                )
            return AudioGenerationResponse(
                model=api_params["model"],
                audio_url=audio_url,
                audio_data=audio_data,
                format=audio_format,
            )
        except Exception as exc:
            self._raise_dashscope_model_error("speech generation", exc)

    def _require_dashscope_media_profile(self, operation: str) -> None:
        if self._endpoint_profile_name() == "dashscope":
            return
        raise build_error(
            StatusCode.MODEL_CALL_FAILED,
            error_msg=f"{operation} is not supported by OpenAIModelClient for this endpoint_profile.",
        )

    @staticmethod
    def _raise_dashscope_model_error(operation: str, exc: Exception):
        if isinstance(exc, ModelError):
            raise exc
        error_msg = f"Unexpected error during DashScope {operation}: {str(exc)}"
        logger.error(error_msg, exc_info=True)
        raise ModelError(
            StatusCode.MODEL_CALL_FAILED,
            msg=error_msg,
            cause=exc,
        ) from exc

    @staticmethod
    def _validate_dashscope_speech_params(*, voice: Optional[str], language_type: Optional[str]) -> None:
        if voice not in DASHSCOPE_VOICE:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg=f"Unsupported DashScope voice: {voice}.",
            )
        if language_type not in DASHSCOPE_LANGUAGE_TYPE:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg=f"Unsupported DashScope language_type: {language_type}.",
            )

    @staticmethod
    def _validate_dashscope_video_params(
            *,
            img_url: Optional[str],
            size: Optional[str],
            resolution: Optional[str],
    ) -> None:
        if img_url:
            if size:
                raise build_error(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    error_msg="Image-to-video generation uses resolution; do not pass size.",
                )
            return
        if resolution:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg="Text-to-video generation uses size; do not pass resolution.",
            )

    @staticmethod
    def _single_user_message(messages: List[UserMessage], operation: str) -> UserMessage:
        if not messages or len(messages) != 1:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg=f"{operation} requires exactly one UserMessage.",
            )
        message = messages[0]
        if not isinstance(message, UserMessage):
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg=f"{operation} requires a UserMessage.",
            )
        return message

    @classmethod
    def _single_user_text(cls, messages: List[UserMessage], operation: str) -> str:
        message = cls._single_user_message(messages, operation)
        content = message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                elif isinstance(item, dict) and item.get("text"):
                    text_parts.append(str(item["text"]))
            return "\n".join(text_parts)
        return str(content)

    @classmethod
    def _dashscope_image_content(cls, messages: List[UserMessage]) -> list[dict]:
        message = cls._single_user_message(messages, "Image generation")
        content = message.content
        content_list: list[dict] = []
        image_count = 0
        text_count = 0

        if isinstance(content, str):
            content_list.append({"text": content})
            text_count += 1
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    content_list.append({"text": item})
                    text_count += 1
                    continue
                if not isinstance(item, dict):
                    raise build_error(
                        StatusCode.MODEL_INVOKE_PARAM_ERROR,
                        error_msg=f"Content item must be string or dict, but got {type(item).__name__}.",
                    )
                if cls._dashscope_is_text_item(item):
                    content_list.append({"text": item["text"]})
                    text_count += 1
                    continue
                image_value = cls._dashscope_image_value(item)
                if image_value:
                    content_list.append({"image": image_value})
                    image_count += 1
                    continue
                raise build_error(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    error_msg=(
                        "Content dict must contain a non-empty 'text', 'image', or "
                        f"'image_url' value, but got: {list(item.keys())}"
                    ),
                )
        else:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg=f"Message content must be string or list, but got {type(content).__name__}.",
            )

        if text_count == 0:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg="Image generation requires at least one text prompt.",
            )
        if image_count > 3:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg=f"Image generation supports at most 3 input images, but got {image_count}.",
            )
        return content_list

    @staticmethod
    def _dashscope_is_text_item(item: dict) -> bool:
        keys = set(item)
        if keys == {"text"}:
            return True
        if keys == {"type", "text"} and item.get("type") == "text":
            return True
        if "text" in item:
            raise build_error(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                error_msg=f"Content dict with 'text' must not contain extra keys, but got: {list(item.keys())}",
            )
        return False

    @staticmethod
    def _dashscope_image_value(item: dict) -> Optional[str]:
        keys = set(item)
        image_value = item.get("image")
        if "image" in item:
            if keys != {"image"}:
                raise build_error(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    error_msg=f"Content dict with 'image' must not contain extra keys, but got: {list(item.keys())}",
                )
            if isinstance(image_value, str) and image_value.strip():
                return image_value
            return None

        image_url = item.get("image_url")
        if "image_url" in item:
            allowed_keys = {"image_url"} if "type" not in item else {"type", "image_url"}
            if keys != allowed_keys:
                raise build_error(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    error_msg=(
                        "Content dict with 'image_url' must not contain extra keys, "
                        f"but got: {list(item.keys())}"
                    ),
                )
            if "type" in item and item.get("type") not in {"image_url", "input_image", "image"}:
                return None
            if isinstance(image_url, str) and image_url.strip():
                return image_url
            if isinstance(image_url, dict):
                url = image_url.get("url")
                if isinstance(url, str) and url.strip():
                    return url
            return None

        if item.get("type") in {"image", "input_image"}:
            if keys != {"type", "url"}:
                raise build_error(
                    StatusCode.MODEL_INVOKE_PARAM_ERROR,
                    error_msg=(
                        "Content dict with image type must contain only 'type' and 'url', "
                        f"but got: {list(item.keys())}"
                    ),
                )
            url = item.get("url")
            if isinstance(url, str) and url.strip():
                return url
        return None

    @classmethod
    def _extract_dashscope_audio(cls, response: Any) -> tuple[Optional[str], Any, Optional[str]]:
        audio_url = None
        audio_data = None
        audio_format = None
        output = getattr(response, "output", None) or {}

        audio = cls._get_mapping_or_attr(output, "audio")
        if audio:
            audio_url = cls._get_mapping_or_attr(audio, "url")
            audio_data = cls._get_mapping_or_attr(audio, "data")
            if isinstance(audio_data, str):
                audio_data = audio_data.encode("utf-8")
            if audio_url:
                lower_url = str(audio_url).lower()
                if lower_url.endswith(".wav"):
                    audio_format = "wav"
                elif lower_url.endswith(".mp3"):
                    audio_format = "mp3"
                elif lower_url.endswith(".pcm"):
                    audio_format = "pcm"

        choices = cls._get_mapping_or_attr(output, "choices") or []
        for choice in choices:
            content = (cls._get_mapping_or_attr(cls._get_mapping_or_attr(choice, "message") or {}, "content") or [])
            for content_item in content:
                if not isinstance(content_item, dict):
                    continue
                audio_url = content_item.get("audio") or content_item.get("audio_url") or audio_url
                audio_data = content_item.get("audio_data") or audio_data
                audio_format = content_item.get("format") or audio_format
        return audio_url, audio_data, audio_format

    @staticmethod
    def _get_mapping_or_attr(value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    @staticmethod
    def _raise_for_dashscope_response(response: Any, operation: str) -> None:
        status_code = getattr(response, "status_code", None)
        if status_code == 200:
            return
        raise build_error(
            StatusCode.MODEL_CALL_FAILED,
            error_msg=(
                f"DashScope {operation} failed. "
                f"HTTP status: {status_code}, "
                f"Error code: {getattr(response, 'code', None)}, "
                f"Error message: {getattr(response, 'message', None)}"
            ),
        )

    async def _astream_with_parser(
            self,
            response_stream,
            output_parser: BaseOutputParser
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Process streaming response with output parser
        
        Strategy:
        1. Immediately yield each raw chunk, maintaining streaming characteristics (content is incremental)
        2. Accumulate all content
        3. **Attempt to parse accumulated content every time a new chunk is received**
        4. When parsing succeeds, output parser_content and clear buffer (implementing incremental output)
        5. When parsing fails, parser_content is None, continue accumulating
        """
        accumulated_content = ""

        async for chunk_item in response_stream:
            parsed_chunk = self._parse_stream_chunk(chunk_item)
            if parsed_chunk:
                # Accumulate content
                if parsed_chunk.content:
                    accumulated_content += parsed_chunk.content

                # Attempt to parse accumulated content every time
                parser_content = None
                if accumulated_content and output_parser:
                    try:
                        current_parsed_result = await output_parser.parse(accumulated_content)
                        # When parsing succeeds, output result and clear buffer
                        if current_parsed_result is not None:
                            parser_content = current_parsed_result
                            accumulated_content = ""  # Clear buffer to implement incremental output
                    except Exception as e:
                        llm_logger.debug(
                            "Stream parser attempt error.",
                            event_type=LogEventType.LLM_CALL_ERROR,
                            model_name=self.model_config.model_name,
                            model_provider=self.model_client_config.client_provider,
                            is_stream=True,
                            exception=str(e)
                        )
                        parser_content = None

                chunk_with_parser = AssistantMessageChunk(
                    content=parsed_chunk.content,  # Keep original content increment unchanged
                    reasoning_content=parsed_chunk.reasoning_content,
                    tool_calls=parsed_chunk.tool_calls,
                    usage_metadata=parsed_chunk.usage_metadata,
                    finish_reason=parsed_chunk.finish_reason,
                    parser_content=parser_content,  # Has value when parsing succeeds, otherwise None
                    prompt_token_ids=parsed_chunk.prompt_token_ids,
                    completion_token_ids=parsed_chunk.completion_token_ids,
                    logprobs=parsed_chunk.logprobs,
                )

                yield chunk_with_parser

    @staticmethod
    def _extract_reasoning_content(msg_or_delta: Any) -> Optional[str]:
        reasoning_details = getattr(msg_or_delta, "reasoning_details", None)
        if isinstance(reasoning_details, list) and reasoning_details:
            first = reasoning_details[0]
            if isinstance(first, dict):
                text = first.get("text")
                if text:
                    return text
        for attr in ("reasoning_content", "reasoning", "reasoning_token_text"):
            value = getattr(msg_or_delta, attr, None)
            if isinstance(value, str) and value:
                return value
        return None

    async def _parse_response(
            self,
            response: Any,
            parser: Optional[BaseOutputParser] = None
    ) -> AssistantMessage:
        """Parse OpenAI API response
        
        Args:
            response: OpenAI API response object
            parser: Optional output parser, only parses content field
            
        Returns:
            AssistantMessage: Parsed assistant message
            
        Note:
            Non-streaming finish_reason is normalized as follows:
            - If the provider returns a value, it is preserved as-is (e.g. "stop",
              "tool_calls", "length", "content_filter", etc.).
            - If the provider returns None or an empty string, it defaults to
              "tool_calls" when tool_calls are present, otherwise "stop".
        """
        choice = response.choices[0]
        message = choice.message

        # Parse tool_calls
        tool_calls = []
        if hasattr(message, 'tool_calls') and message.tool_calls:
            for idx, tc in enumerate(message.tool_calls):
                function_name = getattr(getattr(tc, 'function', None), 'name', None) or ""
                function_arguments = getattr(getattr(tc, 'function', None), 'arguments', None) or ""
                tool_call = ToolCall(
                    id=getattr(tc, 'id', '') or "",
                    type="function",
                    name=function_name,
                    arguments=function_arguments,
                    index=getattr(tc, 'index', idx)
                )
                tool_calls.append(tool_call)

        reasoning_content = self._extract_reasoning_content(message)

        # Build UsageMetadata, use returned data to populate UsageMetadata attribute fields as much as possible
        usage_metadata = None
        if response.usage:
            # Extract basic token information
            input_tokens = getattr(response.usage, 'prompt_tokens', 0) or 0
            output_tokens = getattr(response.usage, 'completion_tokens', 0) or 0
            total_tokens = getattr(response.usage, 'total_tokens', 0) or 0

            # Extract cost information if available
            input_cost, output_cost, total_cost = self._extract_cost_info(response.usage)

            usage_metadata = UsageMetadata(
                model_name=self.model_config.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cache_tokens=self._extract_cache_tokens(response.usage),
                reasoning_tokens=self._extract_reasoning_tokens(response.usage),
                input_cost=input_cost,
                output_cost=output_cost,
                total_cost=total_cost,
            )

        # Get content
        content = message.content or ""

        # Apply output parser (only parse content field)
        parser_content = None
        llm_logger.info(
            "Before parse content with parser.",
            event_type=LogEventType.LLM_CALL_END,
            model_name=self.model_config.model_name,
            model_provider=self.model_client_config.client_provider,
            response_content=content,
            is_stream=False
        )
        llm_logger.info(
            "Before parse content with parser config.",
            event_type=LogEventType.LLM_CALL_END,
            model_name=self.model_config.model_name,
            model_provider=self.model_client_config.client_provider,
            is_stream=False,
            metadata={"parser": str(parser)}
        )
        if parser and content:
            try:
                parser_content = await parser.parse(content)
                llm_logger.info(
                    "Parser parse success.",
                    event_type=LogEventType.LLM_CALL_END,
                    model_name=self.model_config.model_name,
                    model_provider=self.model_client_config.client_provider,
                    is_stream=False,
                    metadata={"parser_content": parser_content}
                )
            except Exception as e:
                llm_logger.warning(
                    "Parser parse error.",
                    event_type=LogEventType.LLM_CALL_ERROR,
                    model_name=self.model_config.model_name,
                    model_provider=self.model_client_config.client_provider,
                    is_stream=False,
                    exception=str(e)
                )
                parser_content = None
        
        prompt_token_ids = getattr(response, 'prompt_token_ids', None) or None
        completion_token_ids = getattr(choice, 'token_ids', None) or None
        logprobs = self._normalize_logprobs(getattr(choice, 'logprobs', None))
        finish_reason = getattr(choice, 'finish_reason', None) or None
        if not finish_reason:
            finish_reason = "tool_calls" if tool_calls else "stop"
        return AssistantMessage(
            content=content,
            tool_calls=tool_calls if tool_calls else None,
            usage_metadata=usage_metadata,
            finish_reason=finish_reason,
            reasoning_content=reasoning_content,
            parser_content=parser_content,
            prompt_token_ids=prompt_token_ids,
            completion_token_ids=completion_token_ids,
            logprobs=logprobs,
        )

    @staticmethod
    def _normalize_logprobs(logprobs_obj: Any) -> Optional[Any]:
        """Convert provider logprobs object to a JSON-serializable form.

        Returns None when the provider did not include logprobs.
        """
        if not logprobs_obj:
            return None
        if hasattr(logprobs_obj, 'model_dump'):
            return logprobs_obj.model_dump()
        if hasattr(logprobs_obj, '__dict__'):
            return vars(logprobs_obj)
        return logprobs_obj

    def _parse_stream_chunk(self, chunk: Any) -> Optional[AssistantMessageChunk]:
        """Parse OpenAI streaming response chunk
        
        Args:
            chunk: OpenAI streaming response chunk
            
        Returns:
            AssistantMessageChunk or None
        """
        # Some OpenAI-compatible providers send a final usage-only chunk with no
        # choices. Keep that chunk so usage_metadata can propagate to the final
        # accumulated AssistantMessage.
        usage_metadata = None
        if hasattr(chunk, 'usage') and chunk.usage:
            input_cost, output_cost, total_cost = self._extract_cost_info(chunk.usage)
            usage_metadata = UsageMetadata(
                model_name=self.model_config.model_name,
                input_tokens=getattr(chunk.usage, 'prompt_tokens', 0) or 0,
                output_tokens=getattr(chunk.usage, 'completion_tokens', 0) or 0,
                total_tokens=getattr(chunk.usage, 'total_tokens', 0) or 0,
                cache_tokens=self._extract_cache_tokens(chunk.usage),
                reasoning_tokens=self._extract_reasoning_tokens(chunk.usage),
                input_cost=input_cost,
                output_cost=output_cost,
                total_cost=total_cost,
            )

        # vLLM's return_token_ids streams prompt_token_ids only on the first
        # chunk at the top level; surface it whether or not choices is empty.
        prompt_token_ids = getattr(chunk, 'prompt_token_ids', None) or None

        if not chunk.choices:
            if usage_metadata or prompt_token_ids:
                return AssistantMessageChunk(
                    content="",
                    reasoning_content=None,
                    tool_calls=None,
                    usage_metadata=usage_metadata,
                    finish_reason="null",
                    prompt_token_ids=prompt_token_ids,
                )
            return None

        choice = chunk.choices[0]
        delta = getattr(choice, "delta", None)
        message = getattr(choice, "message", None)

        # Extract content. Only accept real strings so MagicMock / non-text
        # provider fields cannot leak into AssistantMessageChunk.
        content = _first_text(
            getattr(delta, "content", None),
            getattr(message, "content", None),
            getattr(message, "token_text", None),
            getattr(delta, "token_text", None),
        ) or ""
        reasoning_content = (
            self._extract_reasoning_content(delta)
            or self._extract_reasoning_content(message)
        )

        # Parse tool_calls delta
        tool_calls = []
        if hasattr(delta, 'tool_calls') and delta.tool_calls:
            for tc_delta in delta.tool_calls:
                if hasattr(tc_delta, 'function') and tc_delta.function:
                    index = getattr(tc_delta, 'index', None)
                    function_name = getattr(tc_delta.function, 'name', None) or ""
                    function_arguments = getattr(tc_delta.function, 'arguments', None) or ""

                    tool_call = ToolCall(
                        id=getattr(tc_delta, 'id', '') or "",
                        type="function",
                        name=function_name,
                        arguments=function_arguments,
                        index=index
                    )
                    tool_calls.append(tool_call)

        # vLLM emits delta token IDs and per-chunk logprobs alongside content;
        # accumulate via AssistantMessageChunk.__add__ so the final message
        # carries the full sequences.
        completion_token_ids = (
            getattr(choice, 'token_ids', None) or getattr(delta, 'token_ids', None) or None
        )
        logprobs = self._normalize_logprobs(getattr(choice, 'logprobs', None))

        return AssistantMessageChunk(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls if tool_calls else None,
            usage_metadata=usage_metadata,
            finish_reason=choice.finish_reason or "null",
            prompt_token_ids=prompt_token_ids,
            completion_token_ids=completion_token_ids,
            logprobs=logprobs,
        )
