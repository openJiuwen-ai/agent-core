# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import time
from typing import Union, List, Optional, AsyncIterator, Type, Dict

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import llm_logger, LogEventType
from openjiuwen.core.foundation.llm.call_scope import LlmCallScope
from openjiuwen.core.foundation.llm.model_clients import create_model_client
from openjiuwen.core.foundation.llm.schema.message import BaseMessage, AssistantMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.schema.generation_response import (
    ImageGenerationResponse,
    AudioGenerationResponse,
    VideoGenerationResponse
)
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.model_clients.inference_affinity_model_client import InferenceAffinityModelClient
from openjiuwen.core.runner.callback import trigger


class Model:
    """Unified LLM invocation entry point

    Responsibilities:
    1. Get/create ModelClient instance based on client_id or configuration
    2. Delegate to ModelClient to execute actual LLM calls
    3. Provide unified interface (ainvoke, astream)

    Usage:

    Method 1: Dynamic creation (pass configuration)
        model = Model(model_config, client_config)
        response = await model.ainvoke("Hello")
    """

    def __init__(
            self,
            model_client_config: Optional[ModelClientConfig],
            model_config: ModelRequestConfig = None,
    ):
        """Initialize Model instance

        Args:
            model_config: Model parameter configuration
            model_client_config: Client configuration
        """
        self.model_config = model_config
        self.model_client_config = model_client_config
        self._client: Optional[BaseModelClient] = None

        if model_client_config is not None:
            self._client = create_model_client(client_config=model_client_config, model_config=self.model_config)
        else:
            raise build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                              error_msg="model client config is none")

        from openjiuwen.core.runner import Runner
        from openjiuwen.core.runner.callback.events import LLMCallEvents
        _fw = Runner.callback_framework
        _extra = {
            "model_config": model_config,
            "model_client_config": model_client_config,
        }

        fn = self._client.invoke
        fn = _fw.emit_before(LLMCallEvents.LLM_INVOKE_INPUT, extra_kwargs=_extra)(fn)
        fn = _fw.transform_io(
            input_event=LLMCallEvents.LLM_INVOKE_INPUT,
            output_event=LLMCallEvents.LLM_INVOKE_OUTPUT,
        )(fn)
        fn = _fw.emit_after(LLMCallEvents.LLM_INVOKE_OUTPUT, extra_kwargs=_extra)(fn)
        self._client.invoke = fn

        fn = self._client.stream
        fn = _fw.emit_before(LLMCallEvents.LLM_STREAM_INPUT, extra_kwargs=_extra)(fn)
        fn = _fw.transform_io(
            input_event=LLMCallEvents.LLM_STREAM_INPUT,
            output_event=LLMCallEvents.LLM_STREAM_OUTPUT,
        )(fn)
        fn = _fw.emit_after(LLMCallEvents.LLM_STREAM_OUTPUT, item_key="result",
                            extra_kwargs=_extra)(fn)
        self._client.stream = fn

    def _resolve_stream_timeout(self, name: str) -> Optional[float]:
        """Resolve a stream timeout option from the client config."""
        return getattr(self.model_client_config, name, None) if self.model_client_config is not None else None

    async def invoke(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            model: str = None,
            output_parser: Optional[BaseOutputParser] = None,
            timeout: float = None,
            **kwargs
    ) -> AssistantMessage:
        """Asynchronous LLM invocation

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
            **kwargs: Other parameters

        Returns:
            AssistantMessage
        """
        # Identify this request for the whole callback chain (input event,
        # the client's own LLM_OUTPUT trigger, output event, error event) so
        # observers can attribute what they receive to this call and not to
        # another one running concurrently. See ``call_scope``.
        with LlmCallScope():
            return await self._client.invoke(
                messages=messages,
                stop=stop,
                model=model,
                tools=tools,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                output_parser=output_parser,
                timeout=timeout,
                **kwargs
            )

    async def stream(
            self,
            messages: Union[str, List[BaseMessage], List[dict]],
            *,
            tools: Union[List[ToolInfo], List[dict], None] = None,
            temperature: Optional[float] = None,
            top_p: Optional[float] = None,
            max_tokens: Optional[int] = None,
            stop: Union[Optional[str], None] = None,
            model: str = None,
            output_parser: Optional[BaseOutputParser] = None,
            timeout: float = None,
            **kwargs
    ) -> AsyncIterator[AssistantMessageChunk]:
        """Asynchronous streaming LLM invocation

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
            **kwargs: Other parameters

        Yields:
            AssistantMessageChunk
        """
        first_chunk_timeout = self._resolve_stream_timeout("stream_first_chunk_timeout")
        idle_timeout = self._resolve_stream_timeout("stream_idle_timeout")

        # The scope is opened here, in the frame that drives the stream, and
        # not further down inside the callback handlers: every ``__anext__``
        # below runs in its own ``asyncio.wait_for`` task, and such a task
        # copies the context, so an id bound inside a chunk callback would be
        # gone by the time the next chunk arrives. See ``call_scope``.
        with LlmCallScope():
            # Capture the dispatch instant before the client opens the
            # connection: the first ``__anext__`` blocks until response
            # headers arrive, so a timestamp taken after ``stream()`` returns
            # would already have lost the connect + time-to-first-byte leg of
            # TTFT. ``time.perf_counter()`` (not ``monotonic``) because these
            # are sub-second latency measurements -- Windows' monotonic clock
            # ticks at 64Hz and would zero short streams -- and only
            # differences within one process are meaningful here.
            dispatch_at = time.perf_counter()
            stream_iterable = self._client.stream(
                messages=messages,
                stop=stop,
                model=model,
                tools=tools,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                output_parser=output_parser,
                timeout=timeout,
                **kwargs
            )
            stream_iterator = stream_iterable.__aiter__()
            started_at = time.monotonic()
            last_chunk_at = started_at
            chunk_count = 0
            effective_model_name = model or getattr(self.model_config, "model_name", None)

            # Stream timing collection (mirrors the Java CallbackIterator):
            # first/last output-bearing chunk, first/last reasoning fragment,
            # and the latest usage object, backfilled onto that usage when the
            # stream ends so every downstream consumer -- ReActAgent, the
            # tracer decorator, the observability rail -- reads the values off
            # the response it already holds.
            first_token_at = None
            last_token_at = None
            reasoning_first_at = None
            reasoning_last_at = None
            last_usage = None
            latency_backfilled = False

            def _backfill_latency() -> None:
                """Stamp TTFT, inter-token latency, total latency and reasoning
                duration onto the last observed usage object.

                Usage normally arrives only on the final chunk, so the token
                time metrics can only be computed once the stream completes.
                Missing measurements stay ``None`` instead of being written as
                zero: zero is itself a legitimate measurement, and conflating
                the two would corrupt downstream aggregation.
                """
                nonlocal latency_backfilled
                if latency_backfilled:
                    return
                latency_backfilled = True
                end_at = time.perf_counter()
                if last_usage is None:
                    return
                # Total latency covers the whole call and is measurable even
                # when no output chunk ever arrived (an early finish with
                # usage only), so it is not gated on first_token_at.
                if last_usage.total_latency <= 0.0:
                    last_usage.total_latency = round(end_at - dispatch_at, 6)
                if (reasoning_first_at is not None and reasoning_last_at is not None
                        and last_usage.reasoning_duration_ms is None):
                    last_usage.reasoning_duration_ms = round(
                        (reasoning_last_at - reasoning_first_at) * 1000, 3)
                if first_token_at is None:
                    return
                if last_usage.ttft_seconds is None:
                    last_usage.ttft_seconds = round(first_token_at - dispatch_at, 6)
                # Denominator is the provider-reported output token count, not
                # the chunk count: a single chunk may carry several tokens.
                if (last_usage.inter_token_latency_ms is None
                        and last_token_at is not None and last_token_at > first_token_at
                        and last_usage.output_tokens > 1):
                    last_usage.inter_token_latency_ms = round(
                        (last_token_at - first_token_at) / (last_usage.output_tokens - 1) * 1000, 3)

            try:
                while True:
                    stage = "first_chunk" if chunk_count == 0 else "idle_chunk"
                    next_timeout = first_chunk_timeout if chunk_count == 0 else idle_timeout

                    try:
                        if next_timeout is None:
                            chunk = await stream_iterator.__anext__()
                        else:
                            chunk = await asyncio.wait_for(stream_iterator.__anext__(), timeout=next_timeout)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError as exc:
                        close = getattr(stream_iterator, "aclose", None) or getattr(stream_iterable, "aclose", None)
                        if callable(close):
                            await close()

                        now = time.monotonic()
                        total_elapsed_seconds = now - started_at
                        wait_elapsed_seconds = now - (started_at if chunk_count == 0 else last_chunk_at)
                        timeout_seconds = next_timeout if next_timeout is not None else 0
                        model_provider = getattr(self.model_client_config, "client_provider", None)
                        elapsed_label = "first_chunk_elapsed" if chunk_count == 0 else "idle_elapsed"
                        error_detail = (
                            f"stream frame timeout: stage={stage}, timeout={timeout_seconds}s, "
                            f"chunk_count={chunk_count}, {elapsed_label}={wait_elapsed_seconds:.2f}s, "
                            f"total_elapsed={total_elapsed_seconds:.2f}s, "
                            f"model={effective_model_name or ''}"
                        )
                        from openjiuwen.core.runner.callback.events import LLMCallEvents
                        await trigger(
                            LLMCallEvents.LLM_CALL_ERROR,
                            model_name=effective_model_name,
                            model_provider=model_provider,
                            is_stream=True,
                            error=exc)
                        llm_logger.error(
                            "LLM stream timeout.",
                            event_type=LogEventType.LLM_CALL_ERROR,
                            model_name=effective_model_name,
                            model_provider=model_provider,
                            tools=tools,
                            temperature=temperature,
                            top_p=top_p,
                            max_tokens=max_tokens,
                            is_stream=True,
                            exception=error_detail
                        )
                        raise build_error(
                            StatusCode.MODEL_CALL_FAILED,
                            error_msg=f"LLM stream timeout: {error_detail}"
                        ) from exc

                    chunk_count += 1
                    last_chunk_at = time.monotonic()
                    # Timing marks are stamped before the yield: a consumer
                    # that abandons the stream mid-way still leaves the last
                    # observed chunk accounted for via the finally backfill.
                    if chunk.usage_metadata is not None:
                        last_usage = chunk.usage_metadata
                    now = time.perf_counter()
                    if chunk.carries_output_token():
                        if first_token_at is None:
                            first_token_at = now
                        last_token_at = now
                    if chunk.reasoning_content:
                        if reasoning_first_at is None:
                            reasoning_first_at = now
                        reasoning_last_at = now
                    yield chunk
            finally:
                _backfill_latency()

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
        """Release model cache/resources if the underlying client supports it."""
        release_fn = getattr(self._client, "release", None)
        if release_fn is None:
            return False
        return await release_fn(
            session_id=session_id,
            messages=messages,
            messages_released_index=messages_released_index,
            model=model,
            tools=tools,
            tools_released_index=tools_released_index,
        )

    def supports_kv_cache_release(self) -> bool:
        """Whether underlying client supports KV cache release."""
        return callable(getattr(self._client, "release", None))

    def supports_kv_cache_affinity(self) -> bool:
        """Whether underlying client supports Ascend KV cache affinity actions."""
        supports_fn = getattr(self._client, "supports_kv_cache_affinity", None)
        if callable(supports_fn):
            return bool(supports_fn())
        return all(
            callable(getattr(self._client, name, None))
            for name in ("evict_kvc", "offload_kvc", "prefetch_kvc")
        )

    def build_kv_cache_invoke_kwargs(
            self,
            *,
            session: object = None,
            enable_kv_cache_release: bool = False,
    ) -> dict:
        """Build extra kwargs for invoke/stream related to KV cache behavior.

        For InferenceAffinity (vLLM):
          - session_id: use session.get_session_id() if provided
          - enable_cache_sharing: follow enable_kv_cache_release
        """
        if not isinstance(self._client, InferenceAffinityModelClient):
            return {}

        extra: dict = {}
        if session is not None and hasattr(session, "get_session_id"):
            extra["session_id"] = session.get_session_id()
        if enable_kv_cache_release:
            extra["enable_cache_sharing"] = True
        return extra

    def build_kv_cache_affinity_invoke_kwargs(
            self,
            *,
            session: object = None,
            session_id: Optional[str] = None,
            parent_session_id: Optional[str] = None,
            enable_kv_cache_affinity: bool = False,
    ) -> dict:
        """Build AscendAffinity agent_hint kwargs for normal invoke/stream."""
        build_fn = getattr(self._client, "build_kv_cache_affinity_invoke_kwargs", None)
        if not callable(build_fn):
            return {}
        return build_fn(
            session=session,
            session_id=session_id,
            parent_session_id=parent_session_id,
            enable_kv_cache_affinity=enable_kv_cache_affinity,
        )

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
        """Evict KV cache through the underlying affinity-capable client."""
        evict_fn = getattr(self._client, "evict_kvc", None)
        if not callable(evict_fn):
            return False
        return bool(await evict_fn(
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
        ))

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
        """Offload KV cache through the underlying affinity-capable client."""
        offload_fn = getattr(self._client, "offload_kvc", None)
        if not callable(offload_fn):
            return False
        return bool(await offload_fn(
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
        ))

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
        """Prefetch KV cache through the underlying affinity-capable client."""
        prefetch_fn = getattr(self._client, "prefetch_kvc", None)
        if not callable(prefetch_fn):
            return False
        return bool(await prefetch_fn(
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
        ))

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
        """Generate image from text prompt (text-to-image or text+image-to-image)

        Args:
            messages: List of UserMessage containing text descriptions and optional image URLs
            model: Model to use for generation
            size: Size of the generated image (e.g., "1664*928", "1024*1024")
            negative_prompt: Optional negative prompt to guide what not to generate
            n: Number of images to generate
            prompt_extend: Whether to automatically extend/enhance the prompt
            watermark: Whether to add watermark to generated images
            seed: Random seed for reproducible generation (0 for random)
            **kwargs: Additional parameters

        Returns:
            ImageGenerationResponse: Generated image response
        """
        return await self._client.generate_image(
            messages=messages,
            model=model,
            negative_prompt=negative_prompt,
            size=size,
            n=n,
            prompt_extend=prompt_extend,
            watermark=watermark,
            seed=seed,
            **kwargs
        )

    async def generate_speech(
            self,
            messages: List[UserMessage],
            *,
            model: Optional[str] = None,
            voice: Optional[str] = "Cherry",
            language_type: Optional[str] = "Auto",
            **kwargs
    ) -> AudioGenerationResponse:
        """Generate speech audio from text

        Args:
            messages: List of UserMessage containing text to convert to speech
            model: Model to use for generation
            voice: Voice to use for speech synthesis (required), refer to supported voices
            language_type: Language type for synthesized audio, defaults to "Auto" for automatic detection
            **kwargs: Additional parameters

        Returns:
            AudioGenerationResponse: Generated audio response
        """
        return await self._client.generate_speech(
            messages=messages,
            model=model,
            voice=voice,
            language_type=language_type,
            **kwargs
        )

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
        """Generate video from text prompt (text-to-video or image-to-video)

        Args:
            messages: List of UserMessage containing text description of the video to generate
            img_url: Optional URL/path of the first frame image for image-to-video generation.
                     Supports: public URL, local file path (file:// prefix), or base64 encoded image
            audio_url: Optional URL of audio to add to the video
            model: Model to use for generation
            size: Video size (e.g., "1280*720"). Use '*' as separator.
            resolution: Video resolution (e.g., "720P", "1080P")
            duration: Duration of the video in seconds (default: 5)
            prompt_extend: Whether to automatically extend/enhance the prompt (default: True)
            watermark: Whether to add watermark to generated video (default: False)
            negative_prompt: Negative prompt to guide what not to generate
            seed: Random seed for reproducible generation
            **kwargs: Additional parameters

        Returns:
            VideoGenerationResponse: Generated video response
        """
        return await self._client.generate_video(
            messages=messages,
            img_url=img_url,
            audio_url=audio_url,
            model=model,
            size=size,
            resolution=resolution,
            duration=duration,
            prompt_extend=prompt_extend,
            watermark=watermark,
            negative_prompt=negative_prompt,
            seed=seed,
            **kwargs
        )


def init_model(
        provider: str,
        model_name: str,
        api_key: str,
        api_base: str,
        *,
        temperature: float = 0.95,
        top_p: float = 0.1,
        max_tokens: Optional[int] = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        verify_ssl: bool = False,
        custom_headers: Optional[dict[str, str]] = None,
) -> Model:
    """Convenience factory to create a Model instance.

    Args:
        provider: Model provider name (e.g. "OpenAI").
        model_name: Model name (e.g. "gpt-4").
        api_key: API key.
        api_base: API base URL.
        temperature: Sampling temperature.
        top_p: Top-p sampling parameter.
        max_tokens: Maximum tokens to generate.
        timeout: Request timeout in seconds.
        max_retries: Maximum number of retries.
        verify_ssl: Whether to verify SSL certificates.
        custom_headers: Additional headers sent with each model request.

    Returns:
        Configured Model instance.
    """
    client_config = ModelClientConfig(
        client_provider=provider,
        api_key=api_key,
        api_base=api_base,
        timeout=timeout,
        max_retries=max_retries,
        verify_ssl=verify_ssl,
        custom_headers=custom_headers,
    )
    request_config = ModelRequestConfig(
        model=model_name,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )
    return Model(
        model_client_config=client_config,
        model_config=request_config,
    )
