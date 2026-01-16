# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import List, Optional, AsyncIterator, Union, Dict, Any

import httpx
import openai

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


class OpenAIModelClient(BaseModelClient):
    """OpenAI API client supporting GPT models and OpenAI-compatible services."""

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)

    def _get_client_name(self) -> str:
        """Get client name."""
        return "OpenAI client"

    def _create_async_openai_client(self, timeout: Optional[float] = None) -> openai.AsyncOpenAI:
        """
        Create an OpenAI Async client with configured SSL/proxy/http client settings.
        
        Args:
            timeout: Optional timeout override for this specific request
        """
        ssl_verify, ssl_cert = self.model_client_config.verify_ssl, self.model_client_config.ssl_cert
        verify = SslUtils.create_strict_ssl_context(ssl_cert) if ssl_verify else ssl_verify

        http_client = httpx.AsyncClient(
            proxy=UrlUtils.get_global_proxy_url(self.model_client_config.api_base),
            verify=verify
        )

        # Use method-level timeout if provided, otherwise use config timeout
        final_timeout = timeout if timeout is not None else self.model_client_config.timeout

        return openai.AsyncOpenAI(
            api_key=self.model_client_config.api_key,
            base_url=self.model_client_config.api_base,
            http_client=http_client,
            timeout=final_timeout,
            max_retries=self.model_client_config.max_retries
        )

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

        async_client = None
        try:
            async_client = self._create_async_openai_client(timeout=timeout)

            # Call API
            response = await async_client.chat.completions.create(**params)
            logger.info(f"OpenAI API: {response}")

            # Parse response and apply output parser
            logger.info(f"Before parse response with output parser, output_parser: {output_parser}")
            assistant_message = await self._parse_response(response, output_parser)

            return assistant_message

        except Exception as e:
            logger.error(f"OpenAI API async invoke error: {e}")
            raise JiuWenBaseException(
                error_code=StatusCode.MODEL_CALL_FAILED.code,
                message=StatusCode.MODEL_CALL_FAILED.errmsg.format(
                    error_msg=f"openAI API async invoke error: {str(e)}"
                )
            ) from e
        finally:
            if async_client is not None:
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

        async_client = None
        try:
            async_client = self._create_async_openai_client(timeout=timeout)

            # Call API with streaming
            response_stream = await async_client.chat.completions.create(**params)

            if output_parser:
                # Use streaming parser
                async for parsed_result in self._astream_with_parser(response_stream, output_parser):
                    yield parsed_result
            else:
                async for chunk in response_stream:
                    parsed_chunk = self._parse_stream_chunk(chunk)
                    if parsed_chunk:
                        yield parsed_chunk

        except Exception as e:
            logger.error(f"OpenAI API async stream error: {e}")
            raise JiuWenBaseException(
                error_code=StatusCode.MODEL_CALL_FAILED.code,
                message=StatusCode.MODEL_CALL_FAILED.errmsg.format(
                    error_msg=f"openAI API async stream error: {str(e)}"
                )
            ) from e
        finally:
            if async_client is not None:
                await async_client.close()

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
                        logger.debug(f"Stream parser attempt: {e}")
                        parser_content = None
                
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
        """Parse OpenAI API response
        
        Args:
            response: OpenAI API response object
            parser: Optional output parser, only parses content field
            
        Returns:
            AssistantMessage: Parsed assistant message
            
        Note:
            Non-streaming finish_reason can only be "stop" or "tool_calls":
            - stop: Model generation completed without tool calls
            - tool_calls: Model generation completed with tool calls
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

        # Get reasoning_content (if exists)
        reasoning_content = getattr(message, 'reasoning_content', None)

        # Build UsageMetadata, use returned data to populate UsageMetadata attribute fields as much as possible
        usage_metadata = None
        if response.usage:
            # Extract basic token information
            input_tokens = getattr(response.usage, 'prompt_tokens', 0) or 0
            output_tokens = getattr(response.usage, 'completion_tokens', 0) or 0
            total_tokens = getattr(response.usage, 'total_tokens', 0) or 0

            # Extract cached token information (OpenAI API may return in prompt_tokens_details)
            cache_tokens = 0
            prompt_tokens_details = getattr(response.usage, 'prompt_tokens_details', None)
            if prompt_tokens_details:
                cache_tokens = getattr(prompt_tokens_details, 'cached_tokens', 0) or 0

            usage_metadata = UsageMetadata(
                model_name=self.model_config.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cache_tokens=cache_tokens,
            )

        # Get content
        content = message.content or ""

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
        """Parse OpenAI streaming response chunk
        
        Args:
            chunk: OpenAI streaming response chunk
            
        Returns:
            AssistantMessageChunk or None
        """
        if not chunk.choices:
            return None

        choice = chunk.choices[0]
        delta = choice.delta

        # Extract content
        content = getattr(delta, 'content', None) or ""
        reasoning_content = getattr(delta, 'reasoning_content', None)

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

        # Build usage_metadata (usually only in the last chunk)
        usage_metadata = None
        if hasattr(chunk, 'usage') and chunk.usage:
            usage_metadata = UsageMetadata(
                model_name=self.model_config.model_name,
                input_tokens=chunk.usage.prompt_tokens if hasattr(chunk.usage, 'prompt_tokens') else 0,
                output_tokens=chunk.usage.completion_tokens if hasattr(chunk.usage, 'completion_tokens') else 0,
                total_tokens=chunk.usage.total_tokens if hasattr(chunk.usage, 'total_tokens') else 0,
            )

        return AssistantMessageChunk(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls if tool_calls else None,
            usage_metadata=usage_metadata,
            finish_reason=choice.finish_reason or "null"
        )
