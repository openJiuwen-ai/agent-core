# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Union

import aiohttp

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import LogEventType, llm_logger
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.schema import (
    AudioGenerationResponse,
    ImageGenerationResponse,
    VideoGenerationResponse,
)
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, BaseMessage, UsageMetadata, UserMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.kv_cache.kv_cache_config import (
    KVC_MANAGEMENT_MAX_ATTEMPTS,
    resolve_kvc_action_timeout,
)
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import LLMCallEvents


_KV_ACTIONS = {"evict", "offload", "prefetch"}
_KV_TARGETS = {"messages", "tools", "session"}


class AscendAffinityModelClient(BaseModelClient):
    """OpenAI-compatible client for Ascend KV-cache affinity.

    The client keeps the framework-facing behavior of ``BaseModelClient`` while
    using the aiohttp transport pattern already verified against the Ascend
    inference service. Normal inference and KV-cache management share the
    ``/v1/chat/completions`` endpoint; affinity intent is carried by the
    top-level ``agent_hint`` request field.
    """

    __client_name__ = ProviderType.AscendAffinity.value

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)

    def _get_client_name(self) -> str:
        return "AscendAffinity client"

    def supports_kv_cache_affinity(self) -> bool:
        return True

    @staticmethod
    def _raise_config_error(message: str):
        raise build_error(
            StatusCode.MODEL_CONFIG_ERROR,
            error_msg=f"[AscendAffinityModelClient] {message}"
        )

    @classmethod
    def _validate_action_target(cls, action: str, target: str) -> None:
        """Validate protocol-level KV action and target values."""
        if action not in _KV_ACTIONS:
            cls._raise_config_error(f"unsupported KV affinity action: {action}")
        if target not in _KV_TARGETS:
            cls._raise_config_error(f"unsupported KV affinity target: {target}")

    @classmethod
    def _range_edit(
            cls,
            *,
            action: str,
            target: str,
            start: Optional[int] = None,
            end: Optional[int] = None,
    ) -> dict[str, Any]:
        if start is None or end is None:
            cls._raise_config_error(
                f"target={target} requires both start and end"
            )
        if not isinstance(start, int) or isinstance(start, bool):
            cls._raise_config_error(f"target={target} start must be an integer")
        if not isinstance(end, int) or isinstance(end, bool):
            cls._raise_config_error(f"target={target} end must be an integer")
        if start < 0 or end < 0:
            cls._raise_config_error(f"target={target} range must be non-negative")
        if start >= end:
            cls._raise_config_error(
                f"target={target} half-open range requires start < end"
            )
        return {"type": action, "target": target, "start": start, "end": end}

    @staticmethod
    def _has_any_range(**ranges: Optional[int]) -> bool:
        return any(value is not None for value in ranges.values())

    @classmethod
    def _build_target_edits(
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
        """Build context-management edits for one protocol target.

        ``session`` operations are identity-scoped and therefore reject
        message/tool ranges. ``messages`` may optionally include a tools edit
        in the same management request.
        """
        cls._validate_action_target(action, target)

        if target == "session":
            if cls._has_any_range(
                    msg_start=msg_start,
                    msg_end=msg_end,
                    tools_start=tools_start,
                    tools_end=tools_end,
            ):
                cls._raise_config_error("target=session does not accept message/tool ranges")
            if include_tools:
                cls._raise_config_error("target=session does not accept include_tools=True")
            return [{"type": action, "target": "session"}]

        if target == "messages":
            edits = [
                cls._range_edit(action=action, target="messages", start=msg_start, end=msg_end)
            ]
            if include_tools:
                edits.append(
                    cls._range_edit(action=action, target="tools", start=tools_start, end=tools_end)
                )
            elif cls._has_any_range(tools_start=tools_start, tools_end=tools_end):
                cls._raise_config_error("tools range requires include_tools=True or target=tools")
            return edits

        if include_tools:
            cls._raise_config_error("target=tools should not also set include_tools=True")
        if cls._has_any_range(msg_start=msg_start, msg_end=msg_end):
            cls._raise_config_error("messages range is invalid for target=tools")
        return [
            cls._range_edit(action=action, target="tools", start=tools_start, end=tools_end)
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
        """Build the required Ascend affinity extension.

        Normal inference carries only ``session_id`` and
        ``parent_session_id``. When ``action`` is provided,
        ``context_management`` is added and ``manage_request`` must be supplied
        explicitly:

        - ``True``: execute a pure KV-cache management request;
        - ``False``: reserved for a dedicated inference-then-management API,
          such as a future ``invoke_then_evict_kvc`` implementation.

        The client deliberately provides no default for this distinction.
        Current ``evict_kvc``, ``offload_kvc``, and ``prefetch_kvc`` methods
        always use the pure-management path.
        """
        if not session_id:
            cls._raise_config_error("session_id is required")
        if not parent_session_id:
            cls._raise_config_error("parent_session_id is required")

        hint: dict[str, Any] = {
            "session_id": session_id,
            "parent_session_id": parent_session_id,
        }

        if action is None:
            if manage_request is not None:
                cls._raise_config_error(
                    "manage_request is only valid when kv_action is set"
                )
            return hint

        if not isinstance(manage_request, bool):
            cls._raise_config_error(
                "manage_request must be explicitly set when kv_action is set"
            )

        context_management: dict[str, Any] = {
            "manage_request": manage_request,
            "edits": cls._build_target_edits(
                action=action,
                target=target,
                msg_start=msg_start,
                msg_end=msg_end,
                tools_start=tools_start,
                tools_end=tools_end,
                include_tools=include_tools,
            ),
        }
        hint["context_management"] = context_management
        return hint

    def _build_ascend_affinity_request_params(
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
            session_id: str,
            parent_session_id: str,
            action: Optional[str] = None,
            target: str = "session",
            manage_request: Optional[bool] = None,
            msg_start: Optional[int] = None,
            msg_end: Optional[int] = None,
            tools_start: Optional[int] = None,
            tools_end: Optional[int] = None,
            include_tools: bool = False,
            **kwargs,
    ) -> Dict[str, Any]:
        """Build one Ascend-affinity Chat Completion request payload.

        This method owns protocol construction only. It validates affinity
        identity and management fields, delegates standard Chat Completion
        normalization to ``BaseModelClient``, and attaches the top-level
        ``agent_hint`` extension. It performs no network I/O.
        """
        if action is None and manage_request is not None:
            self._raise_config_error(
                "manage_request is only valid when kv_action is set"
            )
        if action is not None and not isinstance(manage_request, bool):
            self._raise_config_error(
                "manage_request must be explicitly set when kv_action is set"
            )
        if action is not None:
            if not session_id:
                self._raise_config_error("session_id is required")
            if not parent_session_id:
                self._raise_config_error("parent_session_id is required")
        elif parent_session_id and not session_id:
            self._raise_config_error(
                "session_id is required when parent_session_id is set"
            )

        # Lifecycle-level session management may not have access to the original
        # messages or tools. BaseModelClient rejects an empty message list, so a
        # temporary message is used only during normalization and then removed.
        is_session_manage_request = bool(
            action and manage_request is True and target == "session"
        )
        build_messages = (
            [{"role": "user", "content": ""}]
            if is_session_manage_request
            else messages
        )

        params = super()._build_request_params(
            messages=build_messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            stop=stop,
            max_tokens=max_tokens,
            stream=stream,
            **kwargs,
        )

        if isinstance(params.get("messages"), list):
            params["messages"] = self._sanitize_tool_calls(params["messages"])

        if is_session_manage_request:
            params["messages"] = []
            params.pop("tools", None)
            params.pop("tool_choice", None)

        if session_id:
            params["agent_hint"] = self._build_agent_hint(
                session_id=session_id,
                parent_session_id=parent_session_id or session_id,
                action=action,
                target=target,
                manage_request=manage_request,
                msg_start=msg_start,
                msg_end=msg_end,
                tools_start=tools_start,
                tools_end=tools_end,
                include_tools=include_tools,
            )
        return params

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
            **kwargs,
    ) -> Dict[str, Any]:
        """Compatibility adapter for framework code using the base method name."""
        session_id = kwargs.pop("session_id", None)
        parent_session_id = kwargs.pop("parent_session_id", None) or session_id
        return self._build_ascend_affinity_request_params(
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            stop=stop,
            max_tokens=max_tokens,
            stream=stream,
            session_id=session_id,
            parent_session_id=parent_session_id,
            action=kwargs.pop("kv_action", None),
            target=kwargs.pop("target", "session"),
            manage_request=kwargs.pop("manage_request", None),
            msg_start=kwargs.pop("msg_start", None),
            msg_end=kwargs.pop("msg_end", None),
            tools_start=kwargs.pop("tools_start", None),
            tools_end=kwargs.pop("tools_end", None),
            include_tools=bool(kwargs.pop("include_tools", False)),
            **kwargs,
        )

    def build_kv_cache_affinity_invoke_kwargs(
            self,
            *,
            session: object = None,
            session_id: Optional[str] = None,
            parent_session_id: Optional[str] = None,
            enable_kv_cache_affinity: bool = False,
            **_: Any,
    ) -> dict:
        if not enable_kv_cache_affinity:
            return {}
        cache_id = session_id
        if cache_id is None and session is not None and hasattr(session, "get_session_id"):
            cache_id = session.get_session_id()
        if not cache_id:
            self._raise_config_error(
                "session_id is required when KV cache affinity is enabled"
            )
        return {
            "session_id": cache_id,
            "parent_session_id": parent_session_id or cache_id,
        }

    @asynccontextmanager
    async def _create_session(self, timeout: Optional[float] = None):
        """Create a request-scoped aiohttp session.

        This transport shape intentionally follows the previously verified
        InferenceAffinityModelClient path used in the restricted intranet
        environment.
        """
        final_timeout = timeout if timeout is not None else self.model_client_config.timeout
        timeout_obj = aiohttp.ClientTimeout(
            total=final_timeout,
            connect=getattr(self.model_client_config, "connect_timeout", 30),
            sock_read=final_timeout,
        )
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            yield session

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
            timeout: Optional[float] = None,
            **kwargs
    ) -> AssistantMessage:
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        params = self._build_ascend_affinity_request_params(
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            stop=stop,
            max_tokens=max_tokens,
            stream=False,
            session_id=kwargs.pop("session_id", None),
            parent_session_id=kwargs.pop("parent_session_id", None),
            action=kwargs.pop("kv_action", None),
            target=kwargs.pop("target", "session"),
            manage_request=kwargs.pop("manage_request", None),
            msg_start=kwargs.pop("msg_start", None),
            msg_end=kwargs.pop("msg_end", None),
            tools_start=kwargs.pop("tools_start", None),
            tools_end=kwargs.pop("tools_end", None),
            include_tools=bool(kwargs.pop("include_tools", False)),
            **kwargs,
        )
        if tracer_record_data:
            await tracer_record_data(llm_params=params)

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
            response_data = await self._make_ascend_affinity_request(params, timeout=timeout)
            assistant_message = await self._parse_response(response_data, output_parser)
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
        except Exception as exc:
            await trigger(
                LLMCallEvents.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=False,
                error=exc,
            )
            llm_logger.error(
                "AscendAffinity API async invoke error.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                is_stream=False,
                exception=str(exc),
            )
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"AscendAffinity API async invoke error: {str(exc)}"
            ) from exc

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
            timeout: Optional[float] = None,
            **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        params = self._build_ascend_affinity_request_params(
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            stop=stop,
            max_tokens=max_tokens,
            stream=True,
            session_id=kwargs.pop("session_id", None),
            parent_session_id=kwargs.pop("parent_session_id", None),
            action=kwargs.pop("kv_action", None),
            target=kwargs.pop("target", "session"),
            manage_request=kwargs.pop("manage_request", None),
            msg_start=kwargs.pop("msg_start", None),
            msg_end=kwargs.pop("msg_end", None),
            tools_start=kwargs.pop("tools_start", None),
            tools_end=kwargs.pop("tools_end", None),
            include_tools=bool(kwargs.pop("include_tools", False)),
            **kwargs,
        )
        if tracer_record_data:
            await tracer_record_data(llm_params=params)

        final_message = None
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
            stream_iter = (
                self._astream_with_parser(params, output_parser, timeout=timeout)
                if output_parser
                else self._stream_response(params, timeout=timeout)
            )
            async for chunk in stream_iter:
                if chunk:
                    final_message = chunk if final_message is None else final_message + chunk
                    yield chunk
            if tracer_record_data:
                await tracer_record_data(llm_response=final_message)
            await trigger(
                LLMCallEvents.LLM_OUTPUT,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=True,
                response=final_message.content if final_message else None,
                usage=final_message.usage_metadata if final_message else None,
                tool_calls=final_message.tool_calls if final_message else None,
            )
        except Exception as exc:
            await trigger(
                LLMCallEvents.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=True,
                error=exc,
            )
            llm_logger.error(
                "AscendAffinity API async stream error.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                is_stream=True,
                exception=str(exc),
            )
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"AscendAffinity API async stream error: {str(exc)}"
            ) from exc

    async def _make_ascend_affinity_request(
            self,
            params: Dict[str, Any],
            *,
            timeout: Optional[float] = None,
            max_attempts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute one non-streaming Ascend-affinity protocol request.

        The method intentionally remains thin: protocol payloads are built by
        ``_build_ascend_affinity_request_params`` and raw HTTP transport is
        delegated to ``_make_async_request``.
        """
        return await self._make_async_request(
            params,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def _make_async_request(
            self,
            params: Dict[str, Any],
            timeout: Optional[float] = None,
            max_attempts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send raw JSON over aiohttp with bounded exponential-backoff retries."""
        url = f"{self.model_client_config.api_base.rstrip('/')}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        last_error = None

        attempts = (
            self.model_client_config.max_retries
            if max_attempts is None
            else max(1, int(max_attempts))
        )
        for attempt in range(attempts):
            try:
                async with self._create_session(timeout=timeout) as http_session:
                    async with http_session.post(url, headers=headers, json=params) as response:
                        response_text = await response.text()
                        if response.status != 200:
                            raise Exception(f"API returned error {response.status}: {response_text}")
                        try:
                            return json.loads(response_text)
                        except json.JSONDecodeError as exc:
                            raise Exception(f"API returned invalid JSON: {response_text}") from exc
            except Exception as exc:
                last_error = exc
                if isinstance(exc, asyncio.TimeoutError):
                    llm_logger.warning(
                        "AscendAffinity request timeout.",
                        event_type=LogEventType.LLM_CALL_ERROR,
                        model_name=params.get("model"),
                        model_provider=self.model_client_config.client_provider,
                        metadata={"attempt": attempt + 1},
                    )
                else:
                    llm_logger.error(
                        "AscendAffinity request failed.",
                        event_type=LogEventType.LLM_CALL_ERROR,
                        model_name=params.get("model"),
                        model_provider=self.model_client_config.client_provider,
                        metadata={"attempt": attempt + 1},
                        exception=str(exc),
                    )
                if attempt < attempts - 1:
                    await asyncio.sleep(2 ** attempt)

        raise Exception(f"Request failed after {attempts} attempts: {last_error}")

    async def _stream_response(
            self,
            params: Dict[str, Any],
            timeout: Optional[float] = None,
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Yield parsed chunks from an SSE-style chat-completions response."""
        url = f"{self.model_client_config.api_base.rstrip('/')}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        async with self._create_session(timeout=timeout) as http_session:
            async with http_session.post(url, headers=headers, json=params) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"API returned error {response.status}: {error_text}")
                async for line in response.content:
                    line_str = line.decode("utf-8").strip()
                    if not line_str:
                        continue
                    chunk = self._parse_stream_chunk(line_str)
                    if chunk:
                        yield chunk

    async def _parse_response(
            self,
            response: Any,
            parser: Optional[BaseOutputParser] = None,
    ) -> AssistantMessage:
        if not response.get("choices"):
            raise ValueError("API did not return a valid response")

        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = "" if message.get("content") is None else message.get("content")
        reasoning_content = message.get("reasoning_content", None)

        tool_calls = []
        for idx, tc in enumerate(message.get("tool_calls") or []):
            function = tc.get("function", {})
            tool_calls.append(ToolCall(
                id=tc.get("id", "") or "",
                type="function",
                name=function.get("name", "") or "",
                arguments=function.get("arguments", "") or "",
                index=tc.get("index", idx),
            ))

        usage_metadata = self._build_usage_metadata(response.get("usage"))
        parser_content = None
        if parser and content:
            try:
                parser_content = await parser.parse(content)
            except Exception as exc:
                llm_logger.warning(
                    "Parser parse error.",
                    event_type=LogEventType.LLM_CALL_ERROR,
                    model_name=self.model_config.model_name,
                    model_provider=self.model_client_config.client_provider,
                    is_stream=False,
                    exception=str(exc),
                )

        return AssistantMessage(
            content=content,
            tool_calls=tool_calls if tool_calls else None,
            usage_metadata=usage_metadata,
            finish_reason="tool_calls" if tool_calls else "stop",
            reasoning_content=reasoning_content,
            parser_content=parser_content,
        )

    def _build_usage_metadata(self, usage: Any) -> Optional[UsageMetadata]:
        if not usage:
            return None
        get_value = usage.get if isinstance(usage, dict) else lambda key, default=0: getattr(usage, key, default)
        input_cost, output_cost, total_cost = self._extract_cost_info(usage)
        return UsageMetadata(
            model_name=self.model_config.model_name,
            input_tokens=get_value("prompt_tokens", 0) or 0,
            output_tokens=get_value("completion_tokens", 0) or 0,
            total_tokens=get_value("total_tokens", 0) or 0,
            cache_tokens=self._extract_cache_tokens(usage),
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
        )

    async def _astream_with_parser(
            self,
            params: Dict[str, Any],
            output_parser: BaseOutputParser,
            timeout: Optional[float] = None,
    ) -> AsyncIterator[AssistantMessageChunk]:
        accumulated_content = ""
        async for chunk_item in self._stream_response(params, timeout=timeout):
            if chunk_item.content:
                accumulated_content += chunk_item.content
            parser_content = None
            if accumulated_content:
                try:
                    parsed = await output_parser.parse(accumulated_content)
                    if parsed is not None:
                        parser_content = parsed
                        accumulated_content = ""
                except Exception:
                    parser_content = None
            yield AssistantMessageChunk(
                content=chunk_item.content,
                reasoning_content=chunk_item.reasoning_content,
                tool_calls=chunk_item.tool_calls,
                usage_metadata=chunk_item.usage_metadata,
                finish_reason=chunk_item.finish_reason,
                parser_content=parser_content,
            )

    def _parse_stream_chunk(self, line: str) -> Optional[AssistantMessageChunk]:
        if not line.startswith("data: "):
            return None
        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            return None
        try:
            chunk_data = json.loads(data_str)
        except json.JSONDecodeError as exc:
            llm_logger.warning(
                "Error parsing stream chunk.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=self.model_config.model_name,
                model_provider=self.model_client_config.client_provider,
                is_stream=True,
                line_content=line[:200],
                exception=str(exc),
            )
            return None

        choices = chunk_data.get("choices") or []
        usage_metadata = self._build_usage_metadata(chunk_data.get("usage"))
        if not choices:
            if usage_metadata:
                return AssistantMessageChunk(
                    content="",
                    usage_metadata=usage_metadata,
                    finish_reason="null",
                )
            return None

        choice = choices[0]
        delta = choice.get("delta", {})
        tool_calls = []
        for tc_delta in delta.get("tool_calls") or []:
            function = tc_delta.get("function", {})
            tool_calls.append(ToolCall(
                id=tc_delta.get("id", "") or "",
                type="function",
                name=function.get("name", "") or "",
                arguments=function.get("arguments", "") or "",
                index=tc_delta.get("index", 0),
            ))

        content = delta.get("content", None) or ""
        reasoning_content = delta.get("reasoning_content", None)
        if not any((content, reasoning_content, tool_calls, usage_metadata)):
            return None
        return AssistantMessageChunk(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls if tool_calls else None,
            usage_metadata=usage_metadata,
            finish_reason=choice.get("finish_reason") or "null",
        )

    def _sanitize_tool_calls(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize assistant tool calls to the OpenAI-compatible wire schema.

        The list is normalized in place, matching the behavior of the verified
        InferenceAffinityModelClient implementation.
        """
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
                function = tc.get("function", {})
                cleaned.append({
                    "id": tc.get("id", ""),
                    "type": "function",
                    "index": tc.get("index"),
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": function.get("arguments", ""),
                    },
                })
            msg["tool_calls"] = cleaned
        return messages

    @staticmethod
    def _validate_management_response(response: Any) -> None:
        """Validate the agreed Chat Completion response envelope."""
        if not isinstance(response, dict) or not response.get("choices"):
            raise ValueError(
                "KV management request did not return a valid Chat Completion response"
            )

    async def _manage_kvc(
            self,
            action: str,
            *,
            session_id: str,
            parent_session_id: Optional[str] = None,
            target: str = "session",
            messages: Union[str, List[BaseMessage], List[dict], None] = None,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            model: Optional[str] = None,
            msg_start: Optional[int] = None,
            msg_end: Optional[int] = None,
            tools_start: Optional[int] = None,
            tools_end: Optional[int] = None,
            include_tools: bool = False,
            timeout: Optional[float] = None,
    ) -> bool:
        """Execute a pure KV-cache management request.

        Management requests are protocol peers of ``invoke`` rather than model
        inference calls. They share request construction and aiohttp transport,
        but do not emit normal LLM input/output callbacks or build an
        ``AssistantMessage`` that would immediately be discarded.
        """
        if not session_id:
            self._raise_config_error("session_id is required")
        if target == "messages" and messages is None:
            self._raise_config_error("messages is required for target=messages")
        if target == "tools" and messages is None:
            self._raise_config_error("messages is required for target=tools")
        if target == "tools" and tools is None:
            self._raise_config_error("tools is required for target=tools")
        if include_tools and tools is None:
            self._raise_config_error("tools is required when include_tools=True")

        resolved_parent_session_id = parent_session_id or session_id
        action_timeout = resolve_kvc_action_timeout(action, target, timeout)
        params = self._build_ascend_affinity_request_params(
            messages=[] if target == "session" else messages,
            tools=None if target == "session" else tools,
            temperature=None,
            top_p=None,
            model=model,
            stop=None,
            max_tokens=None,
            stream=False,
            session_id=session_id,
            parent_session_id=resolved_parent_session_id,
            action=action,
            target=target,
            manage_request=True,
            msg_start=msg_start,
            msg_end=msg_end,
            tools_start=tools_start,
            tools_end=tools_end,
            include_tools=include_tools,
        )

        try:
            llm_logger.info(
                "AscendAffinity KV management request started.",
                event_type=LogEventType.LLM_CALL_START,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                metadata={
                    "action": action,
                    "target": target,
                    "session_id": session_id,
                    "parent_session_id": resolved_parent_session_id,
                    "msg_start": msg_start,
                    "msg_end": msg_end,
                    "tools_start": tools_start,
                    "tools_end": tools_end,
                },
            )
            # The transport timeout is per attempt.  This outer deadline owns
            # the whole management action and therefore also bounds retries
            # and exponential backoff. wait_for cancels and reaps the request
            # coroutine on expiry, so no retry task is left running.
            response_data = await asyncio.wait_for(
                self._make_ascend_affinity_request(
                    params,
                    timeout=action_timeout,
                    max_attempts=KVC_MANAGEMENT_MAX_ATTEMPTS,
                ),
                timeout=action_timeout,
            )
            self._validate_management_response(response_data)
            llm_logger.info(
                "AscendAffinity KV management request completed.",
                event_type=LogEventType.LLM_CALL_END,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                metadata={
                    "action": action,
                    "target": target,
                    "session_id": session_id,
                    "parent_session_id": resolved_parent_session_id,
                },
            )
            return True
        except Exception as exc:
            llm_logger.error(
                "AscendAffinity KV management request failed.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                metadata={
                    "action": action,
                    "target": target,
                    "session_id": session_id,
                    "parent_session_id": resolved_parent_session_id,
                },
                exception=str(exc),
            )
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=(
                    "AscendAffinity KV management request failed: "
                    f"{str(exc)}"
                ),
            ) from exc

    async def evict_kvc(
            self,
            *,
            session_id: str,
            parent_session_id: Optional[str] = None,
            target: str = "session",
            messages: Union[str, List[BaseMessage], List[dict], None] = None,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            model: Optional[str] = None,
            msg_start: Optional[int] = None,
            msg_end: Optional[int] = None,
            tools_start: Optional[int] = None,
            tools_end: Optional[int] = None,
            include_tools: bool = False,
            timeout: Optional[float] = None,
    ) -> bool:
        return await self._manage_kvc(
            "evict",
            session_id=session_id,
            parent_session_id=parent_session_id,
            target=target,
            messages=messages,
            tools=tools,
            model=model,
            msg_start=msg_start,
            msg_end=msg_end,
            tools_start=tools_start,
            tools_end=tools_end,
            include_tools=include_tools,
            timeout=timeout,
        )

    async def offload_kvc(
            self,
            *,
            session_id: str,
            parent_session_id: Optional[str] = None,
            target: str = "session",
            messages: Union[str, List[BaseMessage], List[dict], None] = None,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            model: Optional[str] = None,
            msg_start: Optional[int] = None,
            msg_end: Optional[int] = None,
            tools_start: Optional[int] = None,
            tools_end: Optional[int] = None,
            include_tools: bool = False,
            timeout: Optional[float] = None,
    ) -> bool:
        return await self._manage_kvc(
            "offload",
            session_id=session_id,
            parent_session_id=parent_session_id,
            target=target,
            messages=messages,
            tools=tools,
            model=model,
            msg_start=msg_start,
            msg_end=msg_end,
            tools_start=tools_start,
            tools_end=tools_end,
            include_tools=include_tools,
            timeout=timeout,
        )

    async def prefetch_kvc(
            self,
            *,
            session_id: str,
            parent_session_id: Optional[str] = None,
            target: str = "session",
            messages: Union[str, List[BaseMessage], List[dict], None] = None,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            model: Optional[str] = None,
            msg_start: Optional[int] = None,
            msg_end: Optional[int] = None,
            tools_start: Optional[int] = None,
            tools_end: Optional[int] = None,
            include_tools: bool = False,
            timeout: Optional[float] = None,
    ) -> bool:
        return await self._manage_kvc(
            "prefetch",
            session_id=session_id,
            parent_session_id=parent_session_id,
            target=target,
            messages=messages,
            tools=tools,
            model=model,
            msg_start=msg_start,
            msg_end=msg_end,
            tools_start=tools_start,
            tools_end=tools_end,
            include_tools=include_tools,
            timeout=timeout,
        )

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
        pass

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
        pass

    async def generate_speech(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            voice: Optional[str] = "Cherry",
            language_type: Optional[str] = "Auto",
            **kwargs
    ) -> AudioGenerationResponse:
        pass
