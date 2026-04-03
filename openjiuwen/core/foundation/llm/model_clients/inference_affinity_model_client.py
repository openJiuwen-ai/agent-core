# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import asyncio
from typing import List, Dict, Any, Optional, AsyncIterator, Union
from contextlib import asynccontextmanager
import aiohttp

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import llm_logger, LogEventType
from openjiuwen.core.foundation.llm.schema import ImageGenerationResponse, VideoGenerationResponse, \
    AudioGenerationResponse
from openjiuwen.core.foundation.llm.schema.message import (
    BaseMessage,
    AssistantMessage,
    UserMessage,
    UsageMetadata
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import LLMCallEvents


class InferenceAffinityModelClient(BaseModelClient):
    """Inference Affinity (vLLM) API client with cache release support"""
    __client_name__ = "inference_affinity"

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)

    def _get_client_name(self) -> str:
        """Get client name for error messages"""
        return "InferenceAffinity client"

    def _build_and_sanitize_params(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            model: str = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            stream: bool = False,
            session_id: str = None,
            enable_cache_sharing: bool = False,
            **kwargs
    ) -> Dict[str, Any]:
        """Build and sanitize request parameters"""
        params = self._build_request_params(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            max_tokens=max_tokens,
            stream=stream,
            **kwargs
        )
        # Sanitize tool_calls in messages
        params["messages"] = self._sanitize_tool_calls(params["messages"])
        if enable_cache_sharing and session_id:
            params["cache_sharing"] = True
            params["cache_salt"] = session_id
        return params

    @asynccontextmanager
    async def _create_session(self, timeout: Optional[float] = None):
        """Create a new aiohttp session for each request

        Args:
            timeout: Optional timeout override for this specific request
        """
        final_timeout = timeout if timeout is not None else self.model_client_config.timeout
        timeout_obj = aiohttp.ClientTimeout(
            total=final_timeout,
            connect=getattr(self.model_client_config, 'connect_timeout', 30),
            sock_read=final_timeout
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
            session_id: str = None,
            enable_cache_sharing: bool = False,
            **kwargs
    ) -> AssistantMessage:
        """Async invoke InferenceAffinity API

        Args:
            messages: Input messages
            tools: Available tools
            temperature: Sampling temperature
            top_p: Nucleus sampling parameter
            model: Model name override
            max_tokens: Maximum tokens to generate
            stop: Stop sequences
            output_parser: Optional output parser
            timeout: Request timeout in seconds
            session_id: session id for cache sharing
            enable_cache_sharing: enable cache sharing
            **kwargs: Additional parameters

        Returns:
            AssistantMessage: Model response
        """
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        params = self._build_and_sanitize_params(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            max_tokens=max_tokens,
            stream=False,
            session_id=session_id,
            enable_cache_sharing=enable_cache_sharing,
            **kwargs
        )
        if tracer_record_data:
            await tracer_record_data(llm_params=params)

        llm_logger.info(
            "LLM request params ready.",
            event_type=LogEventType.LLM_CALL_START,
            model_name=params.get("model"),
            model_provider=self.model_client_config.client_provider,
            messages=params.get("messages"),
            tools=params.get("tools"),
            temperature=params.get("temperature"),
            top_p=params.get("top_p"),
            max_tokens=params.get("max_tokens"),
            is_stream=False
        )

        try:
            await trigger(
                LLMCallEvents.LLM_INPUT,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                temperature=params.get("temperature"),
                top_p=params.get("top_p"),
                max_tokens=params.get("max_tokens"))

            response_data = await self._make_async_request(params, timeout=timeout)

            llm_logger.info(
                "InferenceAffinity API response received.",
                event_type=LogEventType.LLM_CALL_END,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                temperature=params.get("temperature"),
                top_p=params.get("top_p"),
                max_tokens=params.get("max_tokens"),
                is_stream=False,
                metadata={"response": response_data}
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
            assistant_message = await self._parse_response(response_data, output_parser)

            await trigger(
                LLMCallEvents.LLM_OUTPUT,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                response=assistant_message.content,
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
                "InferenceAffinity API async invoke error.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                temperature=params.get("temperature"),
                top_p=params.get("top_p"),
                max_tokens=params.get("max_tokens"),
                is_stream=False,
                exception=str(e)
            )
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"InferenceAffinity API async invoke error: {str(e)}"
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
            timeout: Optional[float] = None,
            session_id: str = None,
            enable_cache_sharing: bool = False,
            **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Async streaming invoke InferenceAffinity API

        Args:
            messages: Input messages
            tools: Available tools
            temperature: Sampling temperature
            top_p: Nucleus sampling parameter
            model: Model name override
            max_tokens: Maximum tokens to generate
            stop: Stop sequences
            output_parser: Optional output parser
            timeout: Request timeout in seconds
            session_id: session id for cache sharing
            enable_cache_sharing: enable cache sharing
            **kwargs: Additional parameters

        Yields:
            AssistantMessageChunk: Streaming response chunk
        """
        tracer_record_data = kwargs.pop("tracer_record_data", None)
        params = self._build_and_sanitize_params(
            messages=messages,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            model=model,
            stop=stop,
            max_tokens=max_tokens,
            stream=True,
            session_id=session_id,
            enable_cache_sharing=enable_cache_sharing,
            **kwargs
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
                is_stream=True)

            if output_parser:
                # Use streaming parser
                async for parsed_result in self._astream_with_parser(params, output_parser, timeout=timeout):
                    await trigger(
                        LLMCallEvents.LLM_OUTPUT,
                        model_name=params.get("model"),
                        model_provider=self.model_client_config.client_provider,
                        result=parsed_result,
                        is_stream=True)
                    yield parsed_result
            else:
                # Direct return without parser
                async for chunk in self._stream_response(params, timeout=timeout):
                    if chunk:
                        await trigger(
                            LLMCallEvents.LLM_OUTPUT,
                            model_name=params.get("model"),
                            model_provider=self.model_client_config.client_provider,
                            result=chunk,
                            is_stream=True)
                        yield chunk

        except Exception as e:
            await trigger(
                LLMCallEvents.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                is_stream=True,
                error=e)
            llm_logger.error(
                "InferenceAffinity API async stream error.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=params.get("model"),
                model_provider=self.model_client_config.client_provider,
                messages=params.get("messages"),
                tools=params.get("tools"),
                temperature=params.get("temperature"),
                top_p=params.get("top_p"),
                max_tokens=params.get("max_tokens"),
                is_stream=True,
                exception=str(e)
            )
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"InferenceAffinity API async stream error: {str(e)}"
            ) from e

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

    async def release(
            self,
            session_id: str,
            messages: List,
            messages_released_index: int,
            *,
            model: Optional[str] = None,
            tools: Optional[List] = None,
            tools_released_index: Optional[int] = None
    ) -> bool:
        """Release model cache or resources

        Args:
            session_id: Cache salt value to identify specific cache
            messages: Message list
            messages_released_index: Message release index (0-based)
            model: Model name (defaults to config model_name)
            tools: Tool list
            tools_released_index: Tool release index (0-based)

        Returns:
            bool: Whether release was successful

        Raises:
            BaseError: If release request fails
        """
        try:
            messages_dict = self._convert_messages_to_dict(messages)
            tools_dict = self._convert_tools_to_dict(tools)
            sanitized_messages = self._sanitize_tool_calls(messages_dict)

            release_params = {
                "model": model if model else self.model_config.model_name,
                "cache_salt": session_id,
                "cache_sharing": True,
                "messages": sanitized_messages,
                "messages_released_index": messages_released_index,
            }

            if tools_dict:
                release_params["tools"] = tools_dict

            if tools_released_index is not None:
                release_params["tools_released_index"] = tools_released_index

            client_name = self._get_client_name()
            llm_logger.info(
                "Before release KV cache, release request params ready.",
                event_type=LogEventType.LLM_CALL_START,
                model_name=model if model else self.model_config.model_name,
                model_provider=self.model_client_config.client_provider,
                metadata={
                    "client_name": client_name,
                    "session_id": session_id,
                    "messages_released_index": messages_released_index,
                    "tools_released_index": tools_released_index,
                }
            )

            # Call vLLM release API
            url = f"{self.model_client_config.api_base.rstrip('/')}/release_kv_cache"
            headers = {"Content-Type": "application/json"}

            async with self._create_session() as http_session:
                async with http_session.post(url, headers=headers, json=release_params) as response:
                    response_text = await response.text()

                    if response.status == 200:
                        try:
                            result = json.loads(response_text)
                            llm_logger.info(
                                "KV cache release successful.",
                                event_type=LogEventType.LLM_CALL_END,
                                model_name=model if model else self.model_config.model_name,
                                model_provider=self.model_client_config.client_provider,
                                metadata={
                                    "client_name": client_name,
                                    "session_id": session_id,
                                    "response": result
                                }
                            )
                            return True
                        except json.JSONDecodeError:
                            llm_logger.info(
                                "KV cache release successful (non-JSON response).",
                                event_type=LogEventType.LLM_CALL_END,
                                model_name=model if model else self.model_config.model_name,
                                model_provider=self.model_client_config.client_provider,
                                metadata={
                                    "client_name": client_name,
                                    "session_id": session_id,
                                    "response_text": response_text
                                }
                            )
                            return True
                    else:
                        llm_logger.error(
                            f"KV cache release failed with status {response.status}.",
                            event_type=LogEventType.LLM_CALL_ERROR,
                            model_name=model if model else self.model_config.model_name,
                            model_provider=self.model_client_config.client_provider,
                            metadata={
                                "client_name": client_name,
                                "session_id": session_id,
                                "status_code": response.status,
                                "response_body": response_text
                            }
                        )
                        return False

        except ValueError as ve:
            # Log validation errors before re-raising
            llm_logger.warning(
                "KV cache release validation error.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=model if model else self.model_config.model_name,
                model_provider=self.model_client_config.client_provider,
                metadata={
                    "client_name": self._get_client_name(),
                    "session_id": session_id,
                    "error": str(ve)
                },
                exc_info=True
            )
            raise  # Preserve original traceback
        except Exception as e:
            client_name = self._get_client_name()
            llm_logger.error(
                f"KV cache release error: {str(e)}",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=model if model else self.model_config.model_name,
                model_provider=self.model_client_config.client_provider,
                metadata={
                    "client_name": client_name,
                    "session_id": session_id,
                    "error": str(e)
                },
                exc_info=True
            )
            raise build_error(
                error_code=StatusCode.MODEL_CALL_FAILED,
                error_msg=f"Release error: {str(e)}",
                status=StatusCode.ERROR
            ) from e

    async def _make_async_request(self, params: Dict, timeout: Optional[float] = None) -> Dict:
        """Make async HTTP request with retry logic

        Args:
            params: Request parameters
            timeout: Optional timeout override for this specific request
        """
        url = f"{self.model_client_config.api_base.rstrip('/')}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}

        last_error = None
        for attempt in range(self.model_client_config.max_retries):
            try:
                llm_logger.debug(
                    f"Non-stream request (attempt {attempt + 1}/{self.model_client_config.max_retries})",
                    event_type=LogEventType.LLM_CALL_START,
                    model_name=params.get("model"),
                    model_provider=self.model_client_config.client_provider,
                    metadata={"attempt": attempt + 1}
                )

                async with self._create_session(timeout=timeout) as http_session:
                    async with http_session.post(url, headers=headers, json=params) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            raise Exception(f"API returned error {response.status}: {error_text}")

                        return await response.json()

            except Exception as e:
                last_error = e
                if isinstance(e, asyncio.TimeoutError):
                    llm_logger.warning(
                        f"Request timeout: {str(e)}",
                        event_type=LogEventType.LLM_CALL_ERROR,
                        model_name=params.get("model"),
                        model_provider=self.model_client_config.client_provider,
                        metadata={"attempt": attempt + 1}
                    )
                else:
                    llm_logger.error(
                        f"Request failed: {str(e)}",
                        event_type=LogEventType.LLM_CALL_ERROR,
                        model_name=params.get("model"),
                        model_provider=self.model_client_config.client_provider,
                        metadata={"attempt": attempt + 1},
                        exception=str(e)
                    )

                if attempt < self.model_client_config.max_retries - 1:
                    wait_time = 2 ** attempt
                    llm_logger.info(
                        f"Retrying in {wait_time} seconds...",
                        event_type=LogEventType.LLM_CALL_START,
                        model_name=params.get("model"),
                        model_provider=self.model_client_config.client_provider,
                        metadata={"wait_time": wait_time, "next_attempt": attempt + 2}
                    )
                    await asyncio.sleep(wait_time)

        raise Exception(f"Request failed after {self.model_client_config.max_retries} attempts: {str(last_error)}")

    async def _parse_response(
            self,
            response: Any,
            parser: Optional[BaseOutputParser] = None
    ) -> AssistantMessage:
        """Parse InferenceAffinity API response

        Args:
            response: API response object (dict from JSON)
            parser: Optional output parser, only parses content field

        Returns:
            AssistantMessage: Parsed assistant message
        """
        if not response.get("choices"):
            raise ValueError("API did not return a valid response")

        choice = response.get("choices", [{}])[0]
        message = choice.get("message", {})

        # Get content
        content = "" if message.get("content") is None else message.get("content")

        # Get reasoning_content (if exists)
        reasoning_content = message.get("reasoning_content", None)

        # Parse tool_calls
        tool_calls = []
        if message.get("tool_calls"):
            for idx, tc in enumerate(message.get("tool_calls", [])):
                function = tc.get("function", {})
                tool_call = ToolCall(
                    id=tc.get("id", "") or "",
                    type="function",
                    name=function.get("name", "") or "",
                    arguments=function.get("arguments", "") or "",
                    index=tc.get("index", idx)
                )
                tool_calls.append(tool_call)

        # Build UsageMetadata
        usage_metadata = None
        usage = response.get("usage")
        if usage:
            input_tokens = usage.get("prompt_tokens", 0) or 0
            output_tokens = usage.get("completion_tokens", 0) or 0
            total_tokens = usage.get("total_tokens", 0) or 0

            cache_tokens = 0
            prompt_tokens_details = usage.get("prompt_tokens_details")
            if prompt_tokens_details:
                cache_tokens = prompt_tokens_details.get("cached_tokens", 0) or 0

            usage_metadata = UsageMetadata(
                model_name=self.model_config.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cache_tokens=cache_tokens,
            )

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

        return AssistantMessage(
            content=content,
            tool_calls=tool_calls if tool_calls else None,
            usage_metadata=usage_metadata,
            finish_reason="tool_calls" if tool_calls else "stop",
            reasoning_content=reasoning_content,
            parser_content=parser_content
        )

    async def _astream_with_parser(
            self,
            params: Dict,
            output_parser: BaseOutputParser,
            timeout: Optional[float] = None
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

        async for chunk_item in self._stream_response(params, timeout=timeout):
            if chunk_item:
                # Accumulate content
                if chunk_item.content:
                    accumulated_content += chunk_item.content

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

                # Create new chunk with original content and parser_content
                chunk_with_parser = AssistantMessageChunk(
                    content=chunk_item.content,
                    reasoning_content=chunk_item.reasoning_content,
                    tool_calls=chunk_item.tool_calls,
                    usage_metadata=chunk_item.usage_metadata,
                    finish_reason=chunk_item.finish_reason,
                    parser_content=parser_content
                )

                yield chunk_with_parser

    async def _stream_response(self, params: Dict, timeout: Optional[float] = None) -> AsyncIterator[
        AssistantMessageChunk]:
        """Stream response from API

        Args:
            params: Request parameters
            timeout: Optional timeout override for this specific request
        """
        url = f"{self.model_client_config.api_base.rstrip('/')}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}

        async with self._create_session(timeout=timeout) as http_session:
            async with http_session.post(url, headers=headers, json=params) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"API returned error {response.status}: {error_text}")

                async for line in response.content:
                    line_str = line.decode('utf-8').strip()
                    if not line_str:
                        continue

                    chunk = self._parse_stream_chunk(line_str)
                    if chunk:
                        yield chunk

    def _parse_stream_chunk(self, line: str) -> Optional[AssistantMessageChunk]:
        """Parse streaming response line

        Args:
            line: SSE format single line data (e.g., "data: {...}")

        Returns:
            AssistantMessageChunk or None
        """
        if not line.startswith("data: "):
            return None

        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            return None

        try:
            chunk_data = json.loads(data_str)

            if "choices" in chunk_data and chunk_data["choices"]:
                choice = (chunk_data.get("choices") or [{}])[0]
                delta = choice.get("delta", {})

                # Extract content
                content = delta.get("content", None) or ""
                reasoning_content = delta.get("reasoning_content", None)

                # Parse tool_calls delta
                tool_calls = []
                tool_calls_delta = delta.get("tool_calls")
                if tool_calls_delta:
                    for tc_delta in tool_calls_delta:
                        index = tc_delta.get("index", 0)
                        tool_call_id = tc_delta.get("id", "")
                        function_delta = tc_delta.get("function", {})
                        name_delta = function_delta.get("name", "")
                        args_delta = function_delta.get("arguments", "")

                        tool_call = ToolCall(
                            id=tool_call_id or "",
                            type="function",
                            name=name_delta or "",
                            arguments=args_delta,
                            index=index
                        )
                        tool_calls.append(tool_call)

                # Build usage_metadata (usually only in the last chunk)
                usage_metadata = None
                usage = chunk_data.get("usage")
                if usage:
                    usage_metadata = UsageMetadata(
                        model_name=self.model_config.model_name,
                        input_tokens=usage.get("prompt_tokens", 0) or 0,
                        output_tokens=usage.get("completion_tokens", 0) or 0,
                        total_tokens=usage.get("total_tokens", 0) or 0,
                    )

                # Skip empty chunks
                if not content and not reasoning_content and not tool_calls:
                    return None

                return AssistantMessageChunk(
                    content=content,
                    reasoning_content=reasoning_content,
                    tool_calls=tool_calls if tool_calls else None,
                    usage_metadata=usage_metadata,
                    finish_reason=choice.get("finish_reason") or "null"
                )
            return None

        except (json.JSONDecodeError, KeyError, IndexError, AttributeError) as e:
            llm_logger.warning(
                "Error parsing stream chunk.",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=self.model_config.model_name,
                model_provider=self.model_client_config.client_provider,
                is_stream=True,
                line_content=line[:200] if len(line) <= 200 else f"{line[:200]}...",
                exception=str(e)
            )
            return None

    def _sanitize_tool_calls(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sanitize tool_calls in messages, keep OpenAI standard fields:
        id, type, function.name, function.arguments
        Force type to "function"

        Args:
            messages: List of message dictionaries

        Returns:
            Sanitized message list
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
                func = tc.get("function", {})
                cleaned.append({
                    "id": tc.get("id", ""),
                    "type": "function",
                    "index": tc.get("index"),
                    "function": {
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", "")
                    }
                })
            msg["tool_calls"] = cleaned
        return messages