# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, Iterable, List, Mapping, Optional, Tuple, Union

import httpx

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
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
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType
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
        return (
            cfg.api_key,
            cfg.api_base,
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
        return merge_request_headers(base_headers, request_headers)

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
        # First, use the base implementation to build standard OpenAI-compatible params
        params = super()._build_request_params(
            messages=messages,
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

        return params

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
        http_client = httpx.AsyncClient(
            proxy=UrlUtils.get_global_proxy_url(self.model_client_config.api_base),
            verify=verify,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=60.0,
            ),
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
            api_key=self.model_client_config.api_key,
            base_url=self.model_client_config.api_base,
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

        # OpenAI SDK drops unknown top-level create() args; vLLM needs return_token_ids in JSON body.
        if "return_token_ids" in params:
            extra_body = dict(params.get("extra_body") or {})
            extra_body["return_token_ids"] = params.pop("return_token_ids")
            params["extra_body"] = extra_body

        self._apply_model_specific_params(model, params)
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
                exception=str(e)
            )
            raise build_error(
                StatusCode.MODEL_CALL_FAILED,
                error_msg=f"openAI API async invoke error: {str(e)}"
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

        if "return_token_ids" in params:
            extra_body = dict(params.get("extra_body") or {})
            extra_body["return_token_ids"] = params.pop("return_token_ids")
            params["extra_body"] = extra_body
        self._apply_model_specific_params(model, params)

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

            # Call API with streaming
            response_stream = await async_client.chat.completions.create(**params)

            final_message = None
            if output_parser:
                # Use streaming parser
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
            error_detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
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
        for attr in ("reasoning_content", "reasoning"):
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
        delta = choice.delta

        # Extract content
        content = getattr(delta, 'content', None) or ""
        reasoning_content = self._extract_reasoning_content(delta)

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
