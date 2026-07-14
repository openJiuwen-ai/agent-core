# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
IntelliRouter Model Client — wraps intelli_router.ReliableRouter.
"""
import atexit
import hashlib
import json
from dataclasses import dataclass, field
from threading import Lock
from typing import List, Optional, AsyncIterator, Union, Dict, Any

from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.message import (
    BaseMessage, AssistantMessage, UserMessage, UsageMetadata
)
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, ModelError, ValidationError
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.generation_response import (
    ImageGenerationResponse,
    AudioGenerationResponse,
    VideoGenerationResponse,
)

try:
    from intelli_router import ReliableRouter, Deployment
except ImportError:
    ReliableRouter = None
    Deployment = None


@dataclass
class IntelliRouterClientConfig:
    """Typed config extracted from ModelClientConfig"""
    deployments: list[dict[str, Any]] = field(default_factory=list)
    strategy: str = "simple-shuffle"
    num_retries: int = 3
    timeout: float = 30.0
    strategy_kwargs: dict[str, Any] = field(default_factory=dict)
    enable_health_check: bool = False
    health_check_interval: float = 300.0
    verify_ssl: bool = True
    enable_observability: bool = False
    web_dashboard_port: int = 0

    @classmethod
    def from_model_client_config(cls, config: ModelClientConfig) -> "IntelliRouterClientConfig":
        """Extract IntelliRouter config from ModelClientConfig."""
        extra = config.__pydantic_extra__ or {}
        return cls(
            deployments=extra.get("intelli_router_deployments", []),
            strategy=extra.get("intelli_router_strategy", "simple-shuffle"),
            num_retries=extra.get("intelli_router_num_retries", 3),
            timeout=extra.get("intelli_router_timeout", 30.0),
            strategy_kwargs=extra.get("intelli_router_strategy_kwargs", {}),
            enable_health_check=extra.get("intelli_router_enable_health_check", False),
            health_check_interval=extra.get("intelli_router_health_check_interval", 300.0),
            verify_ssl=config.verify_ssl,
            enable_observability=extra.get("intelli_router_enable_observability", False),
            web_dashboard_port=extra.get("intelli_router_web_dashboard_port", 0),
        )


# Module-level router cache: same config -> same ReliableRouter instance
_router_cache: dict[str, "ReliableRouter"] = {}
_router_cache_lock: Lock = Lock()

# Track web servers for lifecycle management (keyed by router cache key)
_web_servers: dict[str, Any] = {}


def _shutdown_web_servers():
    """Stop all active MetricsWebServer instances on process exit."""
    for server in _web_servers.values():
        try:
            server.stop()
        except Exception as e:
            logger.error(f"shutdown web server failed: {e}")
    _web_servers.clear()


atexit.register(_shutdown_web_servers)

# Map each provider to the generation APIs it supports
_GENERATION_SUPPORT: Dict[str, set] = {
    "dashscope": {"image", "speech", "video"},
}


class IntelliRouterModelClient(BaseModelClient):
    """
    IntelliRouter Model Client — wraps intelli_router.ReliableRouter.

    Provides API pooling, smart routing, auto retry, streaming support,
    and multimodal generation (image/speech/video).
    """
    __client_name__ = "intelli_router"

    def __init__(
        self,
        model_config: ModelRequestConfig,
        model_client_config: ModelClientConfig,
        router: Optional["ReliableRouter"] = None,
    ):
        super().__init__(model_config, model_client_config)
        if router is not None:
            self._router = router
        else:
            router_config = IntelliRouterClientConfig.from_model_client_config(model_client_config)
            self._router = self._get_or_create_router(router_config)

    @staticmethod
    def _make_router_key(config: IntelliRouterClientConfig) -> str:
        """Generate a deterministic cache key from router config."""
        deployments_json = json.dumps(config.deployments, sort_keys=True)
        kwargs_json = json.dumps(config.strategy_kwargs, sort_keys=True)
        raw = (
            f"{deployments_json}|{config.strategy}|{kwargs_json}|"
            f"{config.num_retries}|{config.timeout}|"
            f"{config.enable_health_check}|{config.health_check_interval}|"
            f"{config.verify_ssl}|{config.enable_observability}|"
            f"{config.web_dashboard_port}"
        )
        return hashlib.md5(raw.encode()).hexdigest()

    @classmethod
    def _get_or_create_router(cls, config: IntelliRouterClientConfig) -> "ReliableRouter":
        """Get a ReliableRouter from cache or create & cache a new one."""
        key = cls._make_router_key(config)
        if key not in _router_cache:
            with _router_cache_lock:
                if key not in _router_cache:
                    _router_cache[key] = cls._create_router(config, cache_key=key)
        return _router_cache[key]

    @classmethod
    def _create_router(cls, config: IntelliRouterClientConfig, cache_key: str = ""):
        """Create a ReliableRouter from IntelliRouterClientConfig."""
        if ReliableRouter is None or Deployment is None:
            raise build_error(
                StatusCode.MODEL_SERVICE_CONFIG_ERROR,
                error_msg="intelli_router package is not installed. Please install it with: pip install intelli-router"
            )

        deployments = []
        for dep_cfg in config.deployments:
            dep = Deployment(
                id=dep_cfg.get("id"),
                model_name=dep_cfg.get("model_name"),
                api_key=dep_cfg.get("api_key"),
                api_base=dep_cfg.get("api_base"),
                provider=dep_cfg.get("provider", "openai"),
                tpm=dep_cfg.get("tpm"),
                rpm=dep_cfg.get("rpm"),
                tags=dep_cfg.get("tags", []),
                timeout=dep_cfg.get("timeout"),
                verify_ssl=dep_cfg.get("verify_ssl", config.verify_ssl),
            )
            deployments.append(dep)

        event_bus = None
        metrics_collector = None
        if config.enable_observability:
            try:
                from intelli_router import EventBus, LoggingHook, MetricsCollector
                event_bus = EventBus()
                event_bus.register(LoggingHook(format="text"))
                metrics_collector = MetricsCollector()
                event_bus.register(metrics_collector)
            except ImportError:
                logger.warning("intelli_router observability modules not available, skipping")
                if config.web_dashboard_port > 0:
                    logger.warning("web dashboard will also be skipped due to missing observability modules")

        if config.web_dashboard_port > 0 and not config.enable_observability:
            logger.warning(
                "intelli_router_web_dashboard_port is set but enable_observability is False, "
                "web dashboard requires observability enabled"
            )

        router = ReliableRouter(
            deployments=deployments,
            strategy=config.strategy,
            num_retries=config.num_retries,
            timeout=config.timeout,
            enable_health_check=config.enable_health_check,
            health_check_interval=config.health_check_interval,
            event_bus=event_bus,
            **config.strategy_kwargs,
        )

        if config.web_dashboard_port > 0 and metrics_collector is not None:
            try:
                from intelli_router import MetricsWebServer
                web_server = MetricsWebServer(metrics=metrics_collector, port=config.web_dashboard_port)
                web_server.start()
                if cache_key:
                    _web_servers[cache_key] = web_server
                logger.info("IntelliRouter web dashboard started at %s", web_server.url)
            except ImportError:
                logger.warning("intelli_router MetricsWebServer not available, skipping web dashboard")
            except OSError as e:
                logger.warning("Failed to start web dashboard on port %d: %s", config.web_dashboard_port, e)

        return router

    def _validate_config(self):
        """Override — intelli_router does not require api_key or api_base at top level."""
        pass

    # ------------------------------------------------------------------
    # Chat completion
    # ------------------------------------------------------------------

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
        converted_messages = self._convert_messages_to_dict(messages)
        model_name = model or self.model_config.model_name or "*"

        result = await self._router.invoke(
            messages=converted_messages,
            tools=self._convert_tools_to_dict(tools),
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
            model=model_name,
            **kwargs
        )

        # Map intelli_router types -> openjiuwen types
        return await self._to_ow_assistant_message(result, output_parser)

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
        converted_messages = self._convert_messages_to_dict(messages)
        model_name = model or self.model_config.model_name or "*"

        async for chunk in self._router.stream(
            messages=converted_messages,
            tools=self._convert_tools_to_dict(tools),
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
            model=model_name,
            **kwargs
        ):
            yield self._to_ow_chunk(chunk)

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
        provider = self._resolve_generation_provider(model)
        if "image" not in _GENERATION_SUPPORT.get(provider, set()):
            raise NotImplementedError(
                f"Provider '{provider}' does not support image generation. "
                f"Supported: {_GENERATION_SUPPORT}"
            )
        return await self._generate_image_dashscope(
            messages, model=model, size=size,
            negative_prompt=negative_prompt, n=n,
            prompt_extend=prompt_extend, watermark=watermark,
            seed=seed, **kwargs,
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
        provider = self._resolve_generation_provider(model)
        if "speech" not in _GENERATION_SUPPORT.get(provider, set()):
            raise NotImplementedError(
                f"Provider '{provider}' does not support speech generation."
                f"Supported: {_GENERATION_SUPPORT}"
            )
        return await self._generate_speech_dashscope(
            messages, model=model, voice=voice,
            language_type=language_type, **kwargs,
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
        provider = self._resolve_generation_provider(model)
        if "video" not in _GENERATION_SUPPORT.get(provider, set()):
            raise NotImplementedError(
                f"Provider '{provider}' does not support video generation."
                f"Supported: {_GENERATION_SUPPORT}"
            )
        return await self._generate_video_dashscope(
            messages, img_url=img_url, audio_url=audio_url,
            model=model, size=size, resolution=resolution,
            duration=duration, prompt_extend=prompt_extend,
            watermark=watermark, negative_prompt=negative_prompt,
            seed=seed, **kwargs,
        )

    # ------------------------------------------------------------------
    # Internal: type conversion helpers
    # ------------------------------------------------------------------

    async def _to_ow_assistant_message(
        self,
        msg: Any,
        output_parser: Optional[BaseOutputParser] = None,
    ) -> AssistantMessage:
        """Convert intelli_router AssistantMessage -> openjiuwen AssistantMessage."""
        content = msg.content or ""

        # Apply output parser (openjiuwen's parser)
        if output_parser and content:
            try:
                parsed = await output_parser.parse(content)
                if isinstance(parsed, str):
                    content = parsed
                elif parsed is not None:
                    content = str(parsed)
            except Exception as e:
                logger.error(f"parse content failed: {e}")

        # Convert tool_calls
        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                ToolCall(id=tc.id, type=tc.type, name=tc.name, arguments=tc.arguments, index=tc.index)
                for tc in msg.tool_calls
            ]

        # Convert usage metadata
        usage_metadata = None
        if msg.usage_metadata:
            usage_metadata = UsageMetadata(
                input_tokens=msg.usage_metadata.input_tokens,
                output_tokens=msg.usage_metadata.output_tokens,
                total_tokens=msg.usage_metadata.total_tokens,
                cache_tokens=msg.usage_metadata.cache_tokens,
                model_name=msg.usage_metadata.model_name or "",
            )

        return AssistantMessage(
            content=content,
            tool_calls=tool_calls,
            usage_metadata=usage_metadata,
            finish_reason=msg.finish_reason or "stop",
            reasoning_content=msg.reasoning_content,
        )

    @staticmethod
    def _to_ow_chunk(chunk: Any) -> AssistantMessageChunk:
        """Convert intelli_router AssistantMessageChunk -> openjiuwen AssistantMessageChunk."""
        tool_calls = None
        if chunk.tool_calls:
            tool_calls = [
                ToolCall(id=tc.id, type=tc.type, name=tc.name, arguments=tc.arguments, index=tc.index)
                for tc in chunk.tool_calls
            ]
        return AssistantMessageChunk(
            content=chunk.content or "",
            tool_calls=tool_calls,
            finish_reason=chunk.finish_reason or "null",
            reasoning_content=chunk.reasoning_content,
        )

    # ------------------------------------------------------------------
    # Internal: generation helpers
    # ------------------------------------------------------------------

    def _resolve_generation_provider(self, model: Optional[str] = None) -> str:
        """Resolve the provider name used for generation APIs."""
        for dep in self._router.deployments:
            if model and dep.model_name != model:
                continue
            return dep.provider
        return "unknown"

    def _get_api_key_for_provider(self, provider: str) -> Optional[str]:
        """Return the api_key of the first deployment matching the provider."""
        for dep in self._router.deployments:
            if dep.provider == provider:
                return dep.api_key
        return None

    def _get_api_base_for_provider(self, provider: str) -> Optional[str]:
        """Return the api_base of the first deployment matching the provider."""
        for dep in self._router.deployments:
            if dep.provider == provider:
                return dep.api_base
        return None

    # ------------------------------------------------------------------
    # DashScope generation implementations
    # ------------------------------------------------------------------

    async def _generate_image_dashscope(
        self,
        messages: list,
        *,
        model: Optional[str] = None,
        size: Optional[str] = "1664*928",
        negative_prompt: Optional[str] = None,
        n: Optional[int] = 1,
        prompt_extend: bool = True,
        watermark: bool = False,
        seed: int = 0,
        **kwargs,
    ) -> ImageGenerationResponse:
        try:
            import dashscope
            from dashscope import MultiModalConversation
        except ImportError as e:
            raise ImportError(
                "dashscope package is required for image generation. "
                "Install it with: pip install dashscope"
            ) from e

        if not messages or len(messages) != 1:
            raise ValidationError(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                msg=f"Image generation requires exactly one message, but got {len(messages) if messages else 0}."
            )

        api_base = self._get_api_base_for_provider("dashscope")
        api_key = self._get_api_key_for_provider("dashscope")

        if api_base:
            dashscope.base_http_api_url = api_base

        if model is None:
            model = "qwen-image-max"

        # Convert messages to DashScope format
        msg = messages[0]
        content_list = []

        if isinstance(msg, UserMessage):
            if isinstance(msg.content, str):
                content_list.append({"text": msg.content})
            elif isinstance(msg.content, list):
                for item in msg.content:
                    if isinstance(item, str):
                        content_list.append({"text": item})
                    elif isinstance(item, dict):
                        if "text" in item:
                            content_list.append({"text": item["text"]})
                        elif "image" in item:
                            content_list.append({"image": item["image"]})
        elif isinstance(msg, dict):
            if "text" in msg:
                content_list.append({"text": msg["text"]})
            elif "image" in msg:
                content_list.append({"image": msg["image"]})
            elif "content" in msg:
                content_list.append({"text": msg["content"]})

        if not content_list:
            raise ValidationError(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                msg="Image generation requires non-empty content."
            )

        dashscope_messages = [{"role": "user", "content": content_list}]

        api_params = {
            "api_key": api_key,
            "model": model,
            "messages": dashscope_messages,
            "result_format": "message",
            "stream": False,
            "watermark": watermark,
            "prompt_extend": prompt_extend,
            "size": size,
            "n": n,
        }
        if negative_prompt:
            api_params["negative_prompt"] = negative_prompt
        if seed:
            api_params["seed"] = seed
        api_params.update(kwargs)

        logger.info(f"Calling DashScope image generation API with model: {model}, size: {size}")

        response = MultiModalConversation.call(**api_params)

        if response.status_code != 200:
            error_msg = (
                f"DashScope image generation failed: {response.message} "
                f"(code={response.code}, status={response.status_code})"
            )
            logger.error(error_msg)
            raise ModelError(StatusCode.MODEL_CALL_FAILED, msg=error_msg)

        # Extract image URLs
        image_urls = []
        if response.output and response.output.get("choices"):
            for choice in response.output["choices"]:
                if choice.get("message") and choice["message"].get("content"):
                    for content_item in choice["message"]["content"]:
                        if isinstance(content_item, dict) and "image" in content_item:
                            image_urls.append(content_item["image"])

        if not image_urls:
            raise ModelError(StatusCode.MODEL_CALL_FAILED, msg="No images returned from DashScope API.")

        logger.info(f"DashScope image generation succeeded. Generated {len(image_urls)} image(s).")
        return ImageGenerationResponse(model=model, images=image_urls)

    async def _generate_speech_dashscope(
        self,
        messages: list,
        *,
        model: Optional[str] = None,
        voice: Optional[str] = "Cherry",
        language_type: Optional[str] = "Auto",
        **kwargs,
    ) -> AudioGenerationResponse:
        try:
            import dashscope
            from dashscope import MultiModalConversation
        except ImportError as e:
            raise ImportError(
                "dashscope package is required for speech generation."
            ) from e

        if not messages or len(messages) != 1:
            raise ValidationError(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                msg=f"Speech generation requires exactly one message, but got {len(messages) if messages else 0}."
            )

        api_base = self._get_api_base_for_provider("dashscope")
        api_key = self._get_api_key_for_provider("dashscope")

        if api_base:
            dashscope.base_http_api_url = api_base

        if model is None:
            model = "cosyvoice-v1"

        # Extract text from message
        msg = messages[0]
        text = ""
        if isinstance(msg, UserMessage):
            text = msg.content if isinstance(msg.content, str) else ""
        elif isinstance(msg, dict):
            text = msg.get("content", msg.get("text", ""))

        if not text or not text.strip():
            raise ValidationError(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                msg="Speech generation requires non-empty text content."
            )

        api_params = {
            "api_key": api_key,
            "model": model,
            "text": text,
            "voice": voice,
            "language_type": language_type,
        }
        api_params.update(kwargs)

        logger.info(f"Calling DashScope speech generation API with model: {model}, voice: {voice}")

        response = MultiModalConversation.call(**api_params)

        if response.status_code != 200:
            error_msg = (
                f"DashScope speech generation failed: {response.message} "
                f"(code={response.code}, status={response.status_code})"
            )
            logger.error(error_msg)
            raise ModelError(StatusCode.MODEL_CALL_FAILED, msg=error_msg)

        # Extract audio info
        audio_url = None
        audio_data = None
        audio_format = None

        if response.output and response.output.get("audio"):
            audio_info = response.output["audio"]
            audio_url = audio_info.get("url")
            data_str = audio_info.get("data")
            if data_str:
                audio_data = data_str.encode("utf-8") if isinstance(data_str, str) else data_str
            if audio_url:
                if audio_url.endswith(".wav"):
                    audio_format = "wav"
                elif audio_url.endswith(".mp3"):
                    audio_format = "mp3"
                elif audio_url.endswith(".pcm"):
                    audio_format = "pcm"

        if not audio_url and not audio_data:
            raise ModelError(StatusCode.MODEL_CALL_FAILED, msg="No audio returned from DashScope API.")

        logger.info(f"DashScope speech generation succeeded. Format: {audio_format or 'unknown'}")
        return AudioGenerationResponse(
            model=model,
            audio_url=audio_url,
            audio_data=audio_data,
            format=audio_format,
        )

    async def _generate_video_dashscope(
        self,
        messages: list,
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
        **kwargs,
    ) -> VideoGenerationResponse:
        try:
            import dashscope
            from dashscope import VideoSynthesis
        except ImportError as e:
            raise ImportError(
                "dashscope package is required for video generation"
            ) from e

        if not messages or len(messages) != 1:
            raise ValidationError(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                msg=f"Video generation requires exactly one message, but got {len(messages) if messages else 0}."
            )

        api_base = self._get_api_base_for_provider("dashscope")
        api_key = self._get_api_key_for_provider("dashscope")

        if api_base:
            dashscope.base_http_api_url = api_base

        if model is None:
            model = "wan2.6-t2v"

        # Extract prompt from message
        msg = messages[0]
        prompt = ""
        if isinstance(msg, UserMessage):
            prompt = msg.content if isinstance(msg.content, str) else ""
        elif isinstance(msg, dict):
            prompt = msg.get("content", msg.get("text", ""))

        if not prompt or not prompt.strip():
            raise ValidationError(
                StatusCode.MODEL_INVOKE_PARAM_ERROR,
                msg="Video generation requires non-empty text content."
            )

        api_params = {
            "api_key": api_key,
            "model": model,
            "prompt": prompt,
            "prompt_extend": prompt_extend,
            "watermark": watermark,
        }
        if duration is not None:
            api_params["duration"] = duration
        if negative_prompt:
            api_params["negative_prompt"] = negative_prompt
        if seed is not None:
            api_params["seed"] = seed
        if audio_url:
            api_params["audio_url"] = audio_url
        if img_url:
            api_params["img_url"] = img_url
            if resolution:
                api_params["resolution"] = resolution
        else:
            if size:
                api_params["size"] = size
        api_params.update(kwargs)

        logger.info(f"Calling DashScope video generation API with model: {model}, duration: {duration}")

        response = VideoSynthesis.call(**api_params)

        if response.status_code != 200:
            error_msg = (
                f"DashScope video generation failed: {response.message} "
                f"(code={response.code}, status={response.status_code})"
            )
            logger.error(error_msg)
            raise ModelError(StatusCode.MODEL_CALL_FAILED, msg=error_msg)

        video_url = None
        video_duration = None
        video_resolution = None

        if response.output:
            video_url = getattr(response.output, "video_url", None)

        if response.usage:
            video_duration = response.usage.get('duration') or response.usage.get('output_video_duration')
            video_resolution = response.usage.get('size')

        if not video_url:
            raise ModelError(StatusCode.MODEL_CALL_FAILED, msg="No video URL returned from DashScope API.")

        logger.info(f"DashScope video generation succeeded. Video URL: {video_url[:100]}...")
        return VideoGenerationResponse(
            model=model,
            video_url=video_url,
            duration=video_duration,
            resolution=video_resolution,
            format="mp4",
        )
