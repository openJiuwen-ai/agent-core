# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Optional, AsyncIterator, Union, Dict, Any
from contextlib import asynccontextmanager
import aiohttp

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.foundation.llm.schema.message import (
    BaseMessage,
    AssistantMessage,
    UserMessage,
    SystemMessage,
    ToolMessage,
    UsageMetadata
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig


class SiliconFlowModelClient(BaseModelClient):
    """SiliconFlow API client supporting GPT models and OpenAI-compatible services."""

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)

    def _get_client_name(self) -> str:
        """Get client name for error messages"""
        return "SiliconFlow client"

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
            **kwargs
    ) -> Dict[str, Any]:
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
        return params

    @asynccontextmanager
    async def _apost(self, params: Dict[str, Any], timeout: Optional[float] = None):
        """Create a POST request context for SiliconFlow API.
        
        Args:
            params: Request parameters
            timeout: Optional timeout override for this specific request
        """
        # Validate API base URL
        UrlUtils.check_url_is_valid(self.model_client_config.api_base)

        # Build complete API URL - auto-append /chat/completions if not present
        api_url = self.model_client_config.api_base.rstrip('/')
        if not api_url.endswith('/chat/completions'):
            api_url = f"{api_url}/chat/completions"

        ssl_verify, ssl_cert = self.model_client_config.verify_ssl, self.model_client_config.ssl_cert
        if ssl_verify:
            ssl_context = SslUtils.create_strict_ssl_context(ssl_cert)
            connector = aiohttp.TCPConnector(ssl=ssl_context)
        else:
            connector = aiohttp.TCPConnector(ssl=False)

        # Use method-level timeout if provided, otherwise use config timeout
        final_timeout = timeout if timeout is not None else self.model_client_config.timeout
        timeout_obj = aiohttp.ClientTimeout(total=final_timeout)

        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                    url=api_url,
                    proxy=UrlUtils.get_global_proxy_url(api_url),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.model_client_config.api_key}"
                    },
                    json=params,
                    allow_redirects=False,
                    timeout=timeout_obj
            ) as response:
                response.raise_for_status()
                yield response

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
        """Async invoke SiliconFlow API
        
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
        params = self._build_and_sanitize_params(
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
        logger.info(f"Request params: {params}")

        try:
            async with self._apost(params, timeout=timeout) as response:
                data = await response.json()
                logger.info(f"SiliconFlow API response: {data}")

                # Parse response and apply output parser
                logger.info(f"Before parse response with output parser, output_parser: {output_parser}")
                assistant_message = await self._parse_response(data, output_parser)

                return assistant_message
                    
        except Exception as e:
            logger.error(f"SiliconFlow API async invoke error: {e}")
            raise JiuWenBaseException(
                error_code=StatusCode.MODEL_CALL_FAILED.code,
                message=StatusCode.MODEL_CALL_FAILED.errmsg.format(
                    error_msg=f"siliconFlow API async invoke error: {str(e)}"
                )
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
            **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Async streaming invoke silicon flow API
        
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
        params = self._build_and_sanitize_params(
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

        try:
            async with self._apost(params, timeout=timeout) as response:
                if output_parser:
                    # Use streaming parser
                    async for parsed_result in self._astream_with_parser(response, output_parser):
                        yield parsed_result
                else:
                    # Direct return without parser
                    async for line in response.content:
                        if line:
                            parsed_chunk = self._parse_stream_chunk(line)
                            if parsed_chunk:
                                yield parsed_chunk
                                    
        except Exception as e:
            logger.error(f"SiliconFlow API async stream error: {e}")
            raise JiuWenBaseException(
                error_code=StatusCode.MODEL_CALL_FAILED.code,
                message=StatusCode.MODEL_CALL_FAILED.errmsg.format(
                    error_msg=f"siliconFlow API async stream error: {str(e)}"
                )
            ) from e

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
        
        async for line in response_stream.content:
            if line:
                parsed_chunk = self._parse_stream_chunk(line)
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
                            logger.debug(f"Stream parser attempt: {e}")
                            parser_content = None
                    
                    # Create new chunk with original content and parser_content
                    chunk_with_parser = AssistantMessageChunk(
                        content=parsed_chunk.content,  # Keep original content increment unchanged
                        reasoning_content=parsed_chunk.reasoning_content,
                        tool_calls=parsed_chunk.tool_calls,
                        usage_metadata=parsed_chunk.usage_metadata,
                        finish_reason=parsed_chunk.finish_reason,
                        parser_content=parser_content  # Has value when parsing succeeds, otherwise None
                    )
                    
                    yield chunk_with_parser

    async def _parse_response(
            self,
            response: Any,
            parser: Optional[BaseOutputParser] = None
    ) -> AssistantMessage:
        """Parse SiliconFlow API response

        Args:
            response: SiliconFlow API response object (dict from JSON)
            parser: Optional output parser, only parses content field
            
        Returns:
            AssistantMessage: Parsed assistant message
            
        Note:
            Non-streaming finish_reason can only be "stop" or "tool_calls":
            - stop: Model generation completed without tool calls
            - tool_calls: Model generation completed with tool calls
        """
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
            # Extract basic token information
            input_tokens = usage.get("prompt_tokens", 0) or 0
            output_tokens = usage.get("completion_tokens", 0) or 0
            total_tokens = usage.get("total_tokens", 0) or 0
            
            # Extract cached token information
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
        logger.info(f"Before parse content with parser, content: {content}")
        logger.info(f"Before parse content with parser, parser: {parser}")
        if parser and content:
            try:
                parser_content = await parser.parse(content)
                logger.info(f"Parser parse success, parsed content: {parser_content}")
            except Exception as e:
                logger.warning(f"Parser parse error: {e}")
                parser_content = None
        
        return AssistantMessage(
            content=content,
            tool_calls=tool_calls if tool_calls else None,
            usage_metadata=usage_metadata,
            finish_reason="tool_calls" if tool_calls else "stop",
            reasoning_content=reasoning_content,
            parser_content=parser_content
        )

    def _parse_stream_chunk(self, chunk: Any) -> Optional[AssistantMessageChunk]:
        """Parse SiliconFlow streaming response chunk

        Args:
            chunk: SiliconFlow streaming response chunk (bytes)
            
        Returns:
            AssistantMessageChunk or None
        """
        import json
        
        # Handle SSE format: data: {...}
        if chunk.startswith(b"data: "):
            chunk = chunk[6:]
        
        # Handle [DONE] marker
        if chunk.strip() == b"[DONE]":
            return None
        
        try:
            data = json.loads(chunk.decode("utf-8"))
            choice = data.get("choices", [{}])[0]
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
            usage = data.get("usage")
            if usage:
                finish_reason = choice.get("finish_reason")
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
        except json.JSONDecodeError:
            return None
        except Exception as e:
            logger.warning(f"Error parsing stream chunk: {e}")
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
                # Extract only valid fields
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
