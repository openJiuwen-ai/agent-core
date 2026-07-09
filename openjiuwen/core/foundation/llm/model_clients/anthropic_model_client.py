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

from typing import TYPE_CHECKING, Any, AsyncIterator, List, Mapping, Optional, Union

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import llm_logger, LogEventType
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
    if not blocks:
        return
    marker: dict = {"type": "ephemeral"}
    if ttl == "1h":
        marker["ttl"] = "1h"
    blocks[-1]["cache_control"] = marker


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
            blocks = _content_to_blocks(msg.get("content", ""))
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                args_str = fn.get("arguments", "{}") or "{}"
                try:
                    import json
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

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)
        self._base_headers = build_base_headers(custom_headers=model_client_config.custom_headers)

    def _get_client_name(self) -> str:
        return "Anthropic client"

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
        if b.endswith("/v1"):
            b = b[:-3]
        return b or None

    async def _create_async_anthropic_client(self, timeout: Optional[float] = None) -> "anthropic.AsyncAnthropic":
        """Create an Anthropic Async client backed by the shared connection pool.

        Mirrors the OpenAI client: the underlying HTTPX transport is drawn from
        the global ``ConnectorPoolManager`` (keyed by api_base/ssl/proxy) so
        connections are reused across calls instead of being re-established per
        request. A per-request ``timeout`` is applied via ``with_options``, which
        shares the same transport — no new connections.

        The returned client is pooled and shared; callers must NOT close it.
        """
        from openjiuwen.core.common.clients.llm_client import create_async_anthropic_client

        final_timeout = timeout if timeout is not None else self.model_client_config.timeout
        base_url = self._normalize_base_url(self.model_client_config.api_base)
        llm_logger.info(
            "Before create anthropic client, model client config params ready.",
            event_type=LogEventType.LLM_CALL_START,
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries,
            metadata={"base_url": base_url},
        )

        client = await create_async_anthropic_client(self.model_client_config, base_url=base_url)
        if timeout is not None and timeout != self.model_client_config.timeout:
            return client.with_options(timeout=timeout)
        return client

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
        if openai_params.get("temperature") is not None:
            params["temperature"] = openai_params["temperature"]
        if openai_params.get("top_p") is not None:
            params["top_p"] = openai_params["top_p"]
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

            async_client = await self._create_async_anthropic_client(timeout=timeout)
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

            async_client = await self._create_async_anthropic_client(timeout=timeout)

            # Accumulator state across the stream
            current_text = ""
            tool_use_acc: dict[int, dict] = {}      # index -> {id, name, args_str}
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
        tool_calls: List[ToolCall] = []
        for idx, block in enumerate(content_blocks):
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", "") or "")
            elif btype == "tool_use":
                import json
                input_obj = getattr(block, "input", None) or {}
                args_str = json.dumps(input_obj) if not isinstance(input_obj, str) else input_obj
                tool_calls.append(ToolCall(
                    id=getattr(block, "id", "") or "",
                    type="function",
                    name=getattr(block, "name", "") or "",
                    arguments=args_str,
                    index=idx,
                ))
            # thinking / redacted_thinking blocks: ignored for now -- could be
            # surfaced as reasoning_content if/when OJ wants to display them.

        content = "".join(text_parts)

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
            if block is not None and getattr(block, "type", None) == "tool_use" and idx is not None:
                tool_use_acc[idx] = {
                    "id": getattr(block, "id", "") or "",
                    "name": getattr(block, "name", "") or "",
                    "args_str": "",
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
                return AssistantMessageChunk(
                    content=text,
                    reasoning_content=None,
                    tool_calls=None,
                    usage_metadata=None,
                    finish_reason="null",
                )
            if dtype == "input_json_delta" and idx is not None and idx in tool_use_acc:
                tool_use_acc[idx]["args_str"] += getattr(delta, "partial_json", "") or ""
                return None
            return None

        if etype == "content_block_stop":
            idx = getattr(event, "index", None)
            if idx is None or idx not in tool_use_acc:
                return None
            tu = tool_use_acc.pop(idx)
            return AssistantMessageChunk(
                content="",
                reasoning_content=None,
                tool_calls=[ToolCall(
                    id=tu["id"],
                    type="function",
                    name=tu["name"],
                    arguments=tu["args_str"] or "{}",
                    index=idx,
                )],
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
                usage_metadata=usage_metadata,
                finish_reason=finish_reason,
            )

        if etype == "message_stop":
            return None

        return None

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
