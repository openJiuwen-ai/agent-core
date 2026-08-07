# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Anthropic model client.

Talks directly to Anthropic-shape endpoints (``/v1/messages``) using the
``anthropic`` SDK. Works against:

  * ``https://api.anthropic.com``
  * ``https://openrouter.ai/api``

Promp caching layout:

  1. tools
  2. system
  3. messages
"""

import copy
import json
import re
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Tuple,
    Union,
)

import httpx

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import llm_logger, logger, LogEventType
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.foundation.llm.headers_helper import (
    PROTECTED_HEADERS,
    build_base_headers,
    merge_request_headers,
)
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.schema import (
    AudioGenerationResponse,
    ImageGenerationResponse,
    VideoGenerationResponse,
)
from openjiuwen.core.foundation.llm.schema.config import (
    LLMAuthMode,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    BaseMessage,
    UsageMetadata,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import LLMCallEvents

if TYPE_CHECKING:
    import anthropic


_ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY = "anthropic_content_blocks"
_ANTHROPIC_INTERNAL_CONTENT_BLOCKS_KEY = "__anthropic_content_blocks"
_ANTHROPIC_CACHEABLE_BLOCK_TYPES = frozenset({
    "text", "image", "document", "tool_use", "tool_result",
})
_IMAGE_DATA_URL_PATTERN = re.compile(
    r"^data:(image/(?:jpeg|png|gif|webp));base64,(.+)$",
    flags=re.IGNORECASE | re.DOTALL,
)


def _to_plain_data(value: Any) -> Any:
    """Convert Anthropic SDK models into JSON-compatible data."""
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, Mapping):
        return {key: _to_plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain_data(item) for item in value]
    return value


def _sanitize_replay_block(block: Any) -> Optional[dict]:
    """Keep only fields accepted by Anthropic's message input schema."""
    data = _to_plain_data(block)
    if not isinstance(data, Mapping):
        return None

    block_type = data.get("type")
    if block_type == "thinking":
        return {
            "type": "thinking",
            "thinking": str(data.get("thinking") or ""),
            "signature": str(data.get("signature") or ""),
        }
    if block_type == "redacted_thinking":
        opaque_data = data.get("data")
        if not opaque_data:
            return None
        return {"type": "redacted_thinking", "data": opaque_data}
    if block_type == "text":
        result = {"type": "text", "text": str(data.get("text") or "")}
        if data.get("citations") is not None:
            result["citations"] = copy.deepcopy(data["citations"])
        return result
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": str(data.get("id") or ""),
            "name": str(data.get("name") or ""),
            "input": copy.deepcopy(data.get("input") or {}),
        }
    return None


def _preserved_content_blocks(message: Mapping[str, Any]) -> List[dict]:
    raw_blocks = message.get(_ANTHROPIC_INTERNAL_CONTENT_BLOCKS_KEY)
    if raw_blocks is None:
        metadata = message.get("metadata")
        if isinstance(metadata, Mapping):
            raw_blocks = metadata.get(_ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY)
    if not isinstance(raw_blocks, list):
        return []
    blocks: List[dict] = []
    for block in raw_blocks:
        sanitized = _sanitize_replay_block(block)
        if sanitized is not None:
            blocks.append(sanitized)
    return blocks


def _copy_preserved_blocks_to_converted_messages(
        source_messages: Union[str, List[BaseMessage], List[dict]],
        converted_messages: List[dict],
) -> None:
    """Restore provider-private metadata stripped by the common converter."""
    if not isinstance(source_messages, list) or len(source_messages) != len(converted_messages):
        return
    for source, converted in zip(source_messages, converted_messages):
        metadata = source.get("metadata") if isinstance(source, dict) else getattr(source, "metadata", None)
        if not isinstance(metadata, Mapping):
            continue
        raw_blocks = metadata.get(_ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY)
        if isinstance(raw_blocks, list):
            converted[_ANTHROPIC_INTERNAL_CONTENT_BLOCKS_KEY] = copy.deepcopy(raw_blocks)


def _stream_blocks_metadata(block_acc: Mapping[int, dict]) -> dict[str, Any]:
    blocks = []
    for _, block in sorted(block_acc.items()):
        sanitized = _sanitize_replay_block(block)
        if sanitized is not None:
            blocks.append(sanitized)
    if not blocks:
        return {}
    return {_ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY: blocks}


def _convert_tool_choice(tool_choice: Any) -> Optional[dict]:
    """Translate common/OpenAI tool-choice shapes into Anthropic's shape."""
    if tool_choice is None or tool_choice == "auto":
        return None
    if isinstance(tool_choice, str):
        mapped = {"required": "any", "none": "none", "any": "any"}.get(tool_choice)
        return {"type": mapped} if mapped else None
    if not isinstance(tool_choice, Mapping):
        return None
    if tool_choice.get("type") == "function":
        function = tool_choice.get("function") or {}
        return {"type": "tool", "name": function.get("name", "")}
    if tool_choice.get("type") in {"auto", "any", "none", "tool"}:
        return copy.deepcopy(dict(tool_choice))
    return None


def _model_forbids_custom_sampling(model: str) -> bool:
    """Return whether a current Claude family accepts only default sampling."""
    normalized = str(model or "").lower().replace(".", "-")
    if not normalized.startswith("claude-"):
        return False
    if any(name in normalized for name in ("fable", "mythos")):
        return True
    if re.search(r"claude-(?:opus|sonnet)-5(?:-|$)", normalized):
        return True
    return bool(re.search(r"claude-opus-4-(?:7|8)(?:-|$)", normalized))


def _image_source_block(value: Any) -> dict:
    """Convert an OpenAI-style image value to an Anthropic image block."""
    if isinstance(value, Mapping):
        value = value.get("url")
    if not isinstance(value, str) or not value:
        raise ValueError("Anthropic image input requires a non-empty image URL.")

    data_url = _IMAGE_DATA_URL_PATTERN.fullmatch(value)
    if data_url:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": data_url.group(1).lower(),
                "data": data_url.group(2),
            },
        }
    if value.startswith("data:"):
        raise ValueError(
            "Anthropic image inputs support base64 JPEG, PNG, GIF, or WebP data URLs only."
        )
    return {
        "type": "image",
        "source": {"type": "url", "url": value},
    }


# ---------------------------------------------------------------------------
# Shape converters: openJiuwen BaseMessage list  <->  Anthropic Messages API payload
# ---------------------------------------------------------------------------

def _content_to_blocks(content: Any) -> List[dict]:
    """Normalize OJ ``content`` (str | list[str|dict]) to Anthropic block list."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        blocks2: List[dict] = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                image_value = None
                if item_type in {"image_url", "input_image"}:
                    image_value = item.get("image_url")
                elif item_type == "image" and "source" not in item:
                    image_value = item.get("data_url", item.get("image"))
                if image_value is not None or item_type in {"image_url", "input_image"}:
                    blocks2.append(_image_source_block(image_value))
                    continue
                blocks2.append(dict(item))
            elif isinstance(item, str):
                if item:
                    blocks2.append({"type": "text", "text": item})
            else:
                blocks2.append({"type": "text", "text": str(item)})
        return blocks2
    return [{"type": "text", "text": str(content)}]


def _mark_cache_control(blocks: List[dict], ttl: str) -> None:
    """Attach ``cache_control`` (5m or 1h ephemeral) to the LAST block.

    Anthropic caches the prefix up to and including the marked block; only the
    final block in a message needs the marker to anchor the prefix there.
    """
    marker: dict = {"type": "ephemeral"}
    if ttl == "1h":
        marker["ttl"] = "1h"
    for block in reversed(blocks):
        if block.get("type") in _ANTHROPIC_CACHEABLE_BLOCK_TYPES:
            block["cache_control"] = marker
            return


def _convert_message_schemas(
        messages: List[dict],
) -> tuple[Optional[List[dict]], List[dict]]:
    """Split an OpenAI-shape message list into (system_blocks, anthropic_messages).

    The Messages API expects ``system`` as a top-level parameter and the
    remaining messages alternating between ``user`` and ``assistant`` roles.
    OpenAI-style ``tool_calls`` on an assistant message become ``tool_use``
    blocks; OpenAI-style ``role: "tool"`` messages become ``user`` messages
    carrying ``tool_result`` blocks.
    """
    system_blocks: List[dict] = []
    out: List[dict] = []
    pending_tool_results: List[dict] = []

    def _flush_tool_results():
        if pending_tool_results:
            out.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for msg in messages:
        role = msg.get("role")

        if role == "system":
            system_blocks.extend(_content_to_blocks(msg.get("content", "")))
            continue

        if role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            result_content = msg.get("content", "")
            result_blocks = _content_to_blocks(result_content)
            if not result_blocks:
                # Anthropic requires non-empty content for tool_result; pad.
                result_blocks = [{"type": "text", "text": ""}]
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": result_blocks,
            })
            continue

        _flush_tool_results()

        if role == "assistant":
            preserved_blocks = _preserved_content_blocks(msg)
            if preserved_blocks:
                out.append({"role": "assistant", "content": preserved_blocks})
                continue

            blocks = _content_to_blocks(msg.get("content", ""))
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                args_str = fn.get("arguments", "{}") or "{}"
                try:
                    args_obj = json.loads(args_str) if isinstance(args_str, str) else args_str
                except Exception:
                    args_obj = {"_raw_arguments": args_str}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": args_obj,
                })
            if not blocks:
                blocks = [{"type": "text", "text": ""}]
            out.append({"role": "assistant", "content": blocks})
            continue

        # user role (or anything else we don't recognize -> treat as user)
        blocks = _content_to_blocks(msg.get("content", ""))
        if not blocks:
            blocks = [{"type": "text", "text": ""}]
        out.append({"role": "user", "content": blocks})

    _flush_tool_results()

    return (system_blocks or None), out


def _convert_tool_schemas(tools: Optional[List[dict]]) -> Optional[List[dict]]:
    """Translate OpenAI tool schema to Anthropic tool schema."""
    if not tools:
        return None
    out: List[dict] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") or {}
        out.append({
            "name": fn.get("name") or tool.get("name", ""),
            "description": fn.get("description") or tool.get("description", ""),
            "input_schema": fn.get("parameters") or tool.get("input_schema") or {"type": "object", "properties": {}},
        })
    return out


def _apply_static_cache_breakpoints(
        system_blocks: Optional[List[dict]],
        tools: Optional[List[dict]],
) -> None:
    if tools:
        tools[-1]["cache_control"] = {"type": "ephemeral"}
    if system_blocks:
        _mark_cache_control(system_blocks, "5m")


def _last_input_is_transient(messages: Any) -> bool:
    """True when the final input message is flagged ``metadata['transient']``.

    Transient messages (e.g. a per-call runtime-budget reminder) are appended at
    the tail and stripped after the call. They must sit *after* the last cache
    breakpoint: otherwise the volatile tail anchors the prefix and every turn
    misses cache. ``metadata`` is dropped by ``_convert_messages_to_dict``, so we
    read it from the original message list here, before conversion.
    """
    if not isinstance(messages, list) or not messages:
        return False
    last = messages[-1]
    if isinstance(last, dict):
        meta = last.get("metadata") or {}
    else:
        meta = getattr(last, "metadata", None) or {}
    return bool(meta.get("transient"))


def _apply_messages_cache_breakpoint(
        anthropic_messages: List[dict],
        *,
        exclude_tail: bool,
) -> None:
    """Anchor the conversation cache prefix on the last *stable* message.

    Replaces top-level automatic caching (which always targets the very last
    block). When the tail is a transient message, anchor on the message before
    it so the transient suffix stays uncached and the stable, monotonically
    growing prefix keeps hitting cache across turns.
    """
    if not anthropic_messages:
        return
    idx = len(anthropic_messages) - 1
    if exclude_tail and idx >= 1:
        idx -= 1
    blocks = anthropic_messages[idx].get("content")
    if isinstance(blocks, list):
        _mark_cache_control(blocks, "5m")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class AnthropicModelClient(BaseModelClient):
    """Anthropic Messages API client."""

    __client_name__ = [ProviderType.Anthropic.value]
    _PROTECTED_HEADERS = PROTECTED_HEADERS

    # Process-wide cache of long-lived ``AsyncAnthropic`` clients, bucketed by
    # tenant/connection config (mirrors OpenAIModelClient). Each cached client
    # keeps its own httpx keep-alive connection pool alive so cache hits reuse
    # connections. This cache is independent from OpenAIModelClient's (different
    # SDK client type).
    _client_cache: Dict[Tuple, "anthropic.AsyncAnthropic"] = {}

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)
        self._base_headers = build_base_headers(custom_headers=model_client_config.custom_headers)

    def _validate_config(self):
        super()._validate_config()
        if not str(self.model_client_config.api_key or "").strip():
            raise build_error(
                StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                error_msg="model client config api_key is required for Anthropic client.",
            )

    def _get_client_name(self) -> str:
        return "Anthropic client"

    def _use_shared_client(self) -> bool:
        """Whether to reuse the process-wide cached client (default True).

        Emergency kill-switch: set ``use_shared_llm_http_client=False`` to fall
        back to per-request clients.
        """
        return bool(getattr(self.model_client_config, "use_shared_llm_http_client", True))

    @classmethod
    def connection_key(cls, model_client_config: ModelClientConfig) -> Tuple:
        """Connection identity used to bucket/reuse cached clients.

        Uses the normalized base_url (what the SDK actually talks to) and
        includes api_key so tenants stay isolated. ``api_base`` already
        determines the proxy, so proxy is not part of the key. Exposed as a
        classmethod so callers (e.g. config hot-reload reconciliation) can
        compute the same key to select connections to close via
        :meth:`aclose_connections`.
        """
        cfg = model_client_config
        auth_mode = cfg.auth_mode.value if isinstance(cfg.auth_mode, LLMAuthMode) else cfg.auth_mode
        return (
            auth_mode,
            cfg.api_key,
            cls._normalize_base_url(cfg.api_base),
            cfg.verify_ssl,
            cfg.ssl_cert,
        )

    def _client_cache_key(self) -> Tuple:
        return self.connection_key(self.model_client_config)

    @classmethod
    def _build_request_headers(
            cls,
            base_headers: Optional[Mapping[str, Any]],
            request_headers: Optional[Mapping[str, Any]],
    ) -> dict[str, str]:
        return merge_request_headers(base_headers, request_headers)

    @staticmethod
    def _normalize_base_url(api_base: Optional[str]) -> Optional[str]:
        """Normalize ``api_base`` for the Anthropic SDK.

        The Anthropic SDK appends ``/v1/messages`` to ``base_url`` for the
        messages endpoint. Callers commonly pass an api_base shaped for the
        OpenAI client (``https://openrouter.ai/api/v1``), which would produce
        a double ``/v1/v1/messages``. Strip a trailing ``/v1`` to land on
        ``https://openrouter.ai/api/v1/messages``.
        """
        if not api_base:
            return None
        b = api_base.rstrip("/")
        if b.endswith("/v1/messages"):
            b = b[:-12]
        elif b.endswith("/v1"):
            b = b[:-3]
        return b or None

    def _create_async_anthropic_client(self, timeout: Optional[float] = None) -> "anthropic.AsyncAnthropic":
        """Acquire an ``AsyncAnthropic`` client for a request.

        Default (shared) path returns a long-lived, cached client whose httpx
        keep-alive pool reuses established connections; the caller MUST NOT close
        it on the hot path. Emergency fallback (``use_shared_llm_http_client=False``)
        builds a fresh per-request client that the caller owns and must close.

        ``timeout`` is only baked into the client in the fallback path; the
        shared path applies it per request via ``create(..., timeout=...)``.
        """
        if not self._use_shared_client():
            return self._build_async_anthropic_client(timeout=timeout)

        # Shared path: build once per tenant/connection identity and reuse.
        # Building is fully synchronous (no ``await``), so under a single-threaded
        # asyncio event loop the get/build/set below is atomic and needs no lock.
        key = self._client_cache_key()
        client = self._client_cache.get(key)
        if client is None:
            client = self._build_async_anthropic_client()
            self._client_cache[key] = client
            llm_logger.info(
                "Created shared long-lived AsyncAnthropic client.",
                event_type=LogEventType.LLM_CALL_START,
                timeout=self.model_client_config.timeout,
                max_retries=self.model_client_config.max_retries,
                metadata={"base_url": self._normalize_base_url(self.model_client_config.api_base)},
            )
        return client

    def _build_async_anthropic_client(self, timeout: Optional[float] = None) -> "anthropic.AsyncAnthropic":
        """Build a fresh ``AsyncAnthropic`` client with its own httpx connection pool."""
        from anthropic import AsyncAnthropic

        ssl_verify, ssl_cert = self.model_client_config.verify_ssl, self.model_client_config.ssl_cert
        verify = SslUtils.create_strict_ssl_context(ssl_cert) if ssl_verify else ssl_verify

        # httpx defaults keepalive_expiry to 5s, which drops idle keep-alive
        # connections between calls spaced >5s apart, forcing a rebuild. Bump to
        # 60s to keep connections warm across typical inter-request gaps while
        # staying at/under common upstream/LB idle timeouts (avoids reusing a
        # server-closed "dead" connection).
        http_client = httpx.AsyncClient(
            proxy=UrlUtils.get_global_proxy_url(self.model_client_config.api_base),
            verify=verify,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=60.0,
            ),
        )

        final_timeout = timeout if timeout is not None else self.model_client_config.timeout
        base_url = self._normalize_base_url(self.model_client_config.api_base)
        llm_logger.info(
            "Before create anthropic client, model client config params ready.",
            event_type=LogEventType.LLM_CALL_START,
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries,
            metadata={"base_url": base_url},
        )

        return AsyncAnthropic(
            api_key=self.model_client_config.api_key,
            base_url=base_url,
            http_client=http_client,
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries,
        )

    @classmethod
    async def aclose(cls) -> None:
        """Close all cached clients and their underlying connection pools.

        Intended for agent/process teardown only. NEVER call this on the request
        hot path: it tears down the shared client other in-flight calls rely on.
        """
        clients = list(cls._client_cache.values())
        cls._client_cache.clear()

        for client in clients:
            try:
                await client.close()
            except Exception as e:  # pragma: no cover - defensive cleanup
                logger.warning(f"Error closing cached AsyncAnthropic client: {e}")

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
            except Exception as e:
                logger.warning(f"Error closing AsyncAnthropic client: {e}")
        if closed:
            logger.info(f"Closed {closed} AsyncAnthropic client(s) for removed/updated model config")

    def _build_anthropic_params(
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
            **kwargs,
    ) -> dict:
        openai_params = super()._build_request_params(
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

        oai_messages: List[dict] = openai_params.get("messages") or []
        oai_tools: Optional[List[dict]] = openai_params.get("tools")
        _copy_preserved_blocks_to_converted_messages(messages, oai_messages)

        system_blocks, anthropic_messages = _convert_message_schemas(oai_messages)
        anthropic_tools = _convert_tool_schemas(oai_tools)

        _apply_static_cache_breakpoints(system_blocks, anthropic_tools)
        _apply_messages_cache_breakpoint(
            anthropic_messages,
            exclude_tail=_last_input_is_transient(messages),
        )
        # Anthropic API requires max_tokens; default to a sane upper bound.
        effective_max_tokens = openai_params.get("max_tokens") or 8192

        params: dict = {
            "model": openai_params["model"],
            "messages": anthropic_messages,
            "max_tokens": effective_max_tokens,
        }
        if system_blocks:
            params["system"] = system_blocks
        if anthropic_tools:
            params["tools"] = anthropic_tools

        # Forward Anthropic-native controls that the common OpenAI-shaped
        # builder intentionally treats as extras.
        for key in (
                "thinking", "output_config", "metadata", "service_tier", "top_k",
        ):
            if key in openai_params:
                params[key] = openai_params[key]

        # Compatible gateways may add fields that are not keyword parameters
        # in the Anthropic SDK. Send them through extra_body so the SDK merges
        # them into JSON instead of raising TypeError before the HTTP request.
        extra_body = copy.deepcopy(openai_params.get("extra_body") or {})
        for key in (
                "reasoning_effort", "thinking_budget", "thinking_strategy",
                "enable_thinking",
        ):
            if key in openai_params:
                extra_body[key] = openai_params[key]
        if extra_body:
            params["extra_body"] = extra_body
        anthropic_tool_choice = _convert_tool_choice(openai_params.get("tool_choice"))
        if anthropic_tool_choice is not None:
            params["tool_choice"] = anthropic_tool_choice

        # ModelRequestConfig carries OpenAI-oriented defaults. Treat those as
        # "unset" for Anthropic unless the caller explicitly configured them;
        # current Claude families reject non-default sampling with HTTP 400.
        temperature_explicit = temperature is not None or "temperature" in self.model_config.model_fields_set
        top_p_explicit = top_p is not None or "top_p" in self.model_config.model_fields_set
        temperature = openai_params.get("temperature")
        top_p = openai_params.get("top_p")
        thinking_payload = params.get("thinking")
        thinking_type = (
            (thinking_payload or {}).get("type")
            if isinstance(thinking_payload, Mapping)
            else None
        )
        sampling_forbidden = _model_forbids_custom_sampling(params["model"])
        thinking_restricts_sampling = thinking_type in {"enabled", "adaptive"}

        if sampling_forbidden or thinking_restricts_sampling:
            if temperature_explicit or top_p_explicit:
                llm_logger.debug(
                    "Anthropic: dropping sampling overrides that are incompatible "
                    "with this model/thinking mode."
                )
        elif temperature_explicit and temperature is not None:
            params["temperature"] = temperature
            if top_p_explicit and top_p is not None:
                llm_logger.debug(
                    "Anthropic: dropping top_p because temperature is set "
                    "(the API forbids specifying both)."
                )
        elif top_p_explicit and top_p is not None and top_p != 1.0:
            # top_p=1.0 is the default (no nucleus truncation); skip it so we
            # send the API only meaningful overrides.
            params["top_p"] = top_p
        if openai_params.get("stop"):
            stop_val = openai_params["stop"]
            params["stop_sequences"] = stop_val if isinstance(stop_val, list) else [stop_val]

        # NOTE: do not set a top-level ``cache_control`` here. Automatic caching
        # anchors the breakpoint on the very last block, which would be the
        # transient runtime-budget message and break incremental history
        # caching. The explicit messages breakpoint above anchors on the last
        # stable message instead (see _apply_messages_cache_breakpoint).

        return params

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
            **kwargs,
    ) -> AssistantMessage:
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        request_custom_headers = kwargs.pop("custom_headers", None)

        params = self._build_anthropic_params(
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            stop=stop,
            max_tokens=max_tokens,
            stream=False,
            **kwargs,
        )

        effective_headers = self._build_request_headers(self._base_headers, request_custom_headers)
        if effective_headers:
            params["extra_headers"] = effective_headers

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
            )

            async_client = self._create_async_anthropic_client(timeout=timeout)

            # Per-request timeout override; cached shared client is never rebuilt
            # just to change the timeout.
            if timeout is not None:
                params["timeout"] = timeout

            response = await async_client.messages.create(**params)

            llm_logger.info(
                "Anthropic API response received.",
                event_type=LogEventType.LLM_CALL_END,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=False,
            )

            assistant_message = await self._parse_response(response, output_parser)

            if tracer_record_data:
                await tracer_record_data(llm_response=assistant_message)

            await trigger(
                LLMCallEvents.LLM_OUTPUT,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                response=assistant_message.content,
                usage=assistant_message.usage_metadata,
                tool_calls=assistant_message.tool_calls,
            )
            return assistant_message

        except Exception as e:
            await trigger(
                LLMCallEvents.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=False,
                error=e,
            )
            llm_logger.error(
                "Anthropic API async invoke error.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=False,
                exception=str(e),
            )
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"Anthropic API async invoke error: {str(e)}",
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
            **kwargs,
    ) -> AsyncIterator[AssistantMessageChunk]:
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        request_custom_headers = kwargs.pop("custom_headers", None)

        params = self._build_anthropic_params(
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            stop=stop,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )

        effective_headers = self._build_request_headers(self._base_headers, request_custom_headers)
        if effective_headers:
            params["extra_headers"] = effective_headers

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
                is_stream=True,
            )

            async_client = self._create_async_anthropic_client(timeout=timeout)

            # Per-request timeout override; cached shared client is never rebuilt
            # just to change the timeout.
            if timeout is not None:
                params["timeout"] = timeout

            # Accumulator state across the stream
            current_text = ""
            # Ordered content-block state. Besides assembling tool arguments it
            # retains thinking signatures so the next agent iteration can replay
            # the assistant turn exactly as Anthropic returned it.
            tool_use_acc: dict[int, dict] = {}
            last_usage: Optional[UsageMetadata] = None
            final_stop_reason: Optional[str] = None

            async with async_client.messages.stream(**params) as response_stream:
                async for event in response_stream:
                    chunk = self._event_to_chunk(event, tool_use_acc)
                    if chunk is None:
                        continue
                    if chunk.usage_metadata is not None:
                        last_usage = chunk.usage_metadata
                    if chunk.finish_reason and chunk.finish_reason != "null":
                        final_stop_reason = chunk.finish_reason
                    if chunk.content:
                        current_text += chunk.content
                    await trigger(
                        LLMCallEvents.LLM_RESPONSE_RECEIVED,
                        model_name=params.get("model"),
                        model_provider=self.model_client_config.client_provider,
                    )
                    yield chunk

            # Emit a trailing chunk with usage if it landed late (defensive).
            if last_usage is not None:
                yield AssistantMessageChunk(
                    content="",
                    reasoning_content=None,
                    tool_calls=None,
                    usage_metadata=last_usage,
                    finish_reason=final_stop_reason or "null",
                )

        except Exception as e:
            await trigger(
                LLMCallEvents.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=True,
                error=e,
            )
            llm_logger.error(
                "Anthropic API async stream error.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=True,
                exception=str(e),
            )
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"Anthropic API async stream error: {str(e)}",
            ) from e
        finally:
            # Only close clients we own (fallback path). Shared/pooled clients
            # are long-lived; closing them on the hot path would tear down the
            # shared transport.
            if async_client is not None and not self._use_shared_client():
                await async_client.close()

    # ------------------------------------------------------------------
    # response parsing
    # ------------------------------------------------------------------

    async def _parse_response(
            self,
            response: Any,
            parser: Optional[BaseOutputParser] = None,
    ) -> AssistantMessage:
        """Convert an Anthropic ``Message`` response into ``AssistantMessage``."""
        content_blocks = list(getattr(response, "content", []) or [])
        text_parts: List[str] = []
        reasoning_parts: List[str] = []
        replay_blocks: List[dict] = []
        tool_calls: List[ToolCall] = []
        for idx, block in enumerate(content_blocks):
            btype = getattr(block, "type", None)
            replay_block = _sanitize_replay_block(block)
            if replay_block is not None:
                replay_blocks.append(replay_block)
            if btype == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif btype == "thinking":
                reasoning_parts.append(getattr(block, "thinking", "") or "")
            elif btype == "redacted_thinking":
                # The encrypted data is retained in metadata for replay, but it
                # has no displayable reasoning text.
                continue
            elif btype == "tool_use":
                input_obj = getattr(block, "input", None) or {}
                args_str = json.dumps(input_obj) if not isinstance(input_obj, str) else input_obj
                tool_calls.append(ToolCall(
                    id=getattr(block, "id", "") or "",
                    type="function",
                    name=getattr(block, "name", "") or "",
                    arguments=args_str,
                    index=idx,
                ))

        content = "".join(text_parts)
        reasoning_content = "".join(reasoning_parts) or None
        if reasoning_content is None:
            # A few Anthropic-compatible gateways expose an OpenAI-style extra
            # response field. It is safe to normalize for display, but it must
            # never replace signed Anthropic thinking blocks during replay.
            extra_reasoning = getattr(response, "reasoning_content", None)
            if extra_reasoning is None:
                model_extra = getattr(response, "model_extra", None)
                if isinstance(model_extra, Mapping):
                    extra_reasoning = model_extra.get("reasoning_content")
            if isinstance(extra_reasoning, str) and extra_reasoning:
                reasoning_content = extra_reasoning

        usage_metadata = self._usage_from_anthropic(getattr(response, "usage", None))

        parser_content = None
        if parser and content:
            try:
                parser_content = await parser.parse(content)
            except Exception as e:
                llm_logger.warning(
                    "Anthropic parser parse error.",
                    event_type=LogEventType.LLM_CALL_ERROR,
                    model_name=self.model_config.model_name,
                    model_provider=self.model_client_config.client_provider,
                    is_stream=False,
                    exception=str(e),
                )

        stop_reason = getattr(response, "stop_reason", None) or ""
        finish_reason = "tool_calls" if tool_calls else (
            "stop" if stop_reason in ("end_turn", "stop_sequence", "max_tokens") else (stop_reason or "stop")
        )

        return AssistantMessage(
            content=content,
            tool_calls=tool_calls if tool_calls else None,
            usage_metadata=usage_metadata,
            finish_reason=finish_reason,
            parser_content=parser_content,
            reasoning_content=reasoning_content,
            metadata=(
                {_ANTHROPIC_CONTENT_BLOCKS_METADATA_KEY: replay_blocks}
                if replay_blocks else {}
            ),
        )

    def _usage_from_anthropic(self, usage: Any) -> Optional[UsageMetadata]:
        """Build ``UsageMetadata`` from Anthropic's usage object.

        OJ's ``input_tokens`` field is treated as the total prompt seen by the
        model (uncached + cache-read + cache-write). ``cache_tokens`` is the
        read count (the cheap part).
        """
        if usage is None:
            return None
        u = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage.__dict__)
        uncached = int(u.get("input_tokens") or 0)
        cache_read = int(u.get("cache_read_input_tokens") or 0)
        cache_write = int(u.get("cache_creation_input_tokens") or 0)
        output = int(u.get("output_tokens") or 0)
        total_input = uncached + cache_read + cache_write

        # Best-effort cost extraction: Anthropic doesn't return $; rely on OJ's
        # base helper (which knows OpenRouter-style ``cost`` fields). If neither
        # is available, leave zeros -- the postrun script applies pricing.
        input_cost, output_cost, total_cost = self._extract_cost_info(usage)

        return UsageMetadata(
            model_name=self.model_config.model_name,
            input_tokens=total_input,
            output_tokens=output,
            total_tokens=total_input + output,
            cache_tokens=cache_read,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
        )

    # ------------------------------------------------------------------
    # stream event -> chunk
    # ------------------------------------------------------------------

    def _event_to_chunk(
            self,
            event: Any,
            tool_use_acc: dict[int, dict],
    ) -> Optional[AssistantMessageChunk]:
        """Map an Anthropic SSE event to ``AssistantMessageChunk``.

        Anthropic streaming emits a sequence of: ``message_start``,
        ``content_block_start``, ``content_block_delta`` (one or more),
        ``content_block_stop``, ``message_delta`` (carrying stop_reason +
        usage), ``message_stop``.
        """
        etype = getattr(event, "type", None)

        if etype == "message_start":
            msg = getattr(event, "message", None)
            usage = getattr(msg, "usage", None) if msg is not None else None
            usage_metadata = self._usage_from_anthropic(usage)
            return AssistantMessageChunk(
                content="",
                reasoning_content=None,
                tool_calls=None,
                usage_metadata=usage_metadata,
                finish_reason="null",
            )

        if etype == "content_block_start":
            block = getattr(event, "content_block", None)
            idx = getattr(event, "index", None)
            if block is None or idx is None:
                return None
            block_type = getattr(block, "type", None)
            if block_type == "tool_use":
                tool_use_acc[idx] = {
                    "type": "tool_use",
                    "id": getattr(block, "id", "") or "",
                    "name": getattr(block, "name", "") or "",
                    "input": getattr(block, "input", None) or {},
                    "args_str": "",
                }
            elif block_type == "thinking":
                tool_use_acc[idx] = {
                    "type": "thinking",
                    "thinking": getattr(block, "thinking", "") or "",
                    "signature": getattr(block, "signature", "") or "",
                }
            elif block_type == "redacted_thinking":
                tool_use_acc[idx] = {
                    "type": "redacted_thinking",
                    "data": getattr(block, "data", "") or "",
                }
                return AssistantMessageChunk(
                    content="",
                    metadata=_stream_blocks_metadata(tool_use_acc),
                    finish_reason="null",
                )
            elif block_type == "text":
                tool_use_acc[idx] = {
                    "type": "text",
                    "text": getattr(block, "text", "") or "",
                }
            return None

        if etype == "content_block_delta":
            delta = getattr(event, "delta", None)
            idx = getattr(event, "index", None)
            if delta is None:
                return None
            dtype = getattr(delta, "type", None)
            if dtype == "text_delta":
                text = getattr(delta, "text", "") or ""
                if not text:
                    return None
                if idx is not None:
                    state = tool_use_acc.setdefault(idx, {"type": "text", "text": ""})
                    state["text"] = (state.get("text") or "") + text
                return AssistantMessageChunk(
                    content=text,
                    reasoning_content=None,
                    metadata=_stream_blocks_metadata(tool_use_acc),
                    tool_calls=None,
                    usage_metadata=None,
                    finish_reason="null",
                )
            if dtype == "thinking_delta":
                thinking = getattr(delta, "thinking", "") or ""
                if not thinking:
                    return None
                if idx is not None:
                    state = tool_use_acc.setdefault(
                        idx, {"type": "thinking", "thinking": "", "signature": ""}
                    )
                    state["thinking"] = (state.get("thinking") or "") + thinking
                return AssistantMessageChunk(
                    content="",
                    reasoning_content=thinking,
                    metadata=_stream_blocks_metadata(tool_use_acc),
                    tool_calls=None,
                    usage_metadata=None,
                    finish_reason="null",
                )
            if dtype == "signature_delta":
                signature = getattr(delta, "signature", "") or ""
                if idx is None or not signature:
                    return None
                state = tool_use_acc.setdefault(
                    idx, {"type": "thinking", "thinking": "", "signature": ""}
                )
                state["signature"] = (state.get("signature") or "") + signature
                return AssistantMessageChunk(
                    content="",
                    metadata=_stream_blocks_metadata(tool_use_acc),
                    finish_reason="null",
                )
            if self._is_tool_input_json_delta(dtype, idx, tool_use_acc):
                tool_use_acc[idx]["args_str"] += getattr(delta, "partial_json", "") or ""
                return None
            return None

        if etype == "content_block_stop":
            idx = getattr(event, "index", None)
            if idx is None or idx not in tool_use_acc:
                return None
            block = tool_use_acc[idx]
            if block.get("type") != "tool_use":
                return AssistantMessageChunk(
                    content="",
                    metadata=_stream_blocks_metadata(tool_use_acc),
                    finish_reason="null",
                )
            args_str = block.get("args_str") or "{}"
            try:
                block["input"] = json.loads(args_str)
            except (TypeError, ValueError):
                block["input"] = {"_raw_arguments": args_str}
            return AssistantMessageChunk(
                content="",
                reasoning_content=None,
                tool_calls=[ToolCall(
                    id=block["id"],
                    type="function",
                    name=block["name"],
                    arguments=args_str,
                    index=idx,
                )],
                metadata=_stream_blocks_metadata(tool_use_acc),
                usage_metadata=None,
                finish_reason="null",
            )

        if etype == "message_delta":
            delta = getattr(event, "delta", None)
            usage = getattr(event, "usage", None)
            stop_reason = getattr(delta, "stop_reason", None) if delta is not None else None
            finish_reason = "stop"
            if stop_reason == "tool_use":
                finish_reason = "tool_calls"
            elif stop_reason in ("end_turn", "stop_sequence", "max_tokens"):
                finish_reason = "stop"
            elif stop_reason:
                finish_reason = stop_reason
            usage_metadata = self._usage_from_anthropic(usage) if usage is not None else None
            return AssistantMessageChunk(
                content="",
                reasoning_content=None,
                tool_calls=None,
                metadata=_stream_blocks_metadata(tool_use_acc),
                usage_metadata=usage_metadata,
                finish_reason=finish_reason,
            )

        if etype == "message_stop":
            return None

        return None

    @staticmethod
    def _is_tool_input_json_delta(
            dtype: str,
            idx: Optional[int],
            tool_use_acc: Mapping[int, dict],
    ) -> bool:
        return (
            dtype == "input_json_delta"
            and idx is not None
            and idx in tool_use_acc
            and tool_use_acc[idx].get("type") == "tool_use"
        )

    # ------------------------------------------------------------------
    # unsupported media methods (mirror OpenAI client's stub style)
    # ------------------------------------------------------------------

    async def generate_image(self, messages: List[UserMessage], **kwargs) -> ImageGenerationResponse:
        raise build_error(
            StatusCode.MODEL_CALL_FAILED,
            error_msg="generate_image is not supported by AnthropicModelClient",
        )

    async def generate_speech(self, messages: List[UserMessage], **kwargs) -> AudioGenerationResponse:
        raise build_error(
            StatusCode.MODEL_CALL_FAILED,
            error_msg="generate_speech is not supported by AnthropicModelClient",
        )

    async def generate_video(self, messages: List[UserMessage], **kwargs) -> VideoGenerationResponse:
        raise build_error(
            StatusCode.MODEL_CALL_FAILED,
            error_msg="generate_video is not supported by AnthropicModelClient",
        )
