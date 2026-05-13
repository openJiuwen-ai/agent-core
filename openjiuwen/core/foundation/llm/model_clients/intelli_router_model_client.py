# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
IntelliRouter Model Client - uses intelli_router's ReliableRouter as the underlying implementation
"""
import hashlib
import json
from dataclasses import dataclass, field
from threading import Lock
from typing import List, Optional, AsyncIterator, Union, Dict, Any

from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.schema.message import (
    BaseMessage, AssistantMessage, UserMessage
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.config import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import llm_logger, LogEventType
from openjiuwen.core.common.security.user_config import UserConfig

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
        )


# Module-level router cache: same router config → same ReliableRouter instance
# Key = md5 hash of (deployments + strategy + strategy_kwargs + ...)
_router_cache: dict[str, "ReliableRouter"] = {}
_router_cache_lock: Lock = Lock()


class IntelliRouterModelClient(BaseModelClient):
    """
    IntelliRouter ModelClient using intelli_router's ReliableRouter as the underlying implementation

    Features:
    - API pooling: one model name maps to multiple deployment endpoints
    - Smart routing: supports multiple routing strategies (random, lowest-latency, tag-based, adaptive, etc.)
    - Auto retry: automatically switches to another deployment on failure
    - State management: tracks deployment health status and latency statistics
    - Streaming support: supports streaming responses
    - Router sharing: clients with identical deployment configs share the same router instance
    """
    __client_name__ = "intelli_router"

    def __init__(
        self,
        model_config: ModelRequestConfig,
        model_client_config: ModelClientConfig,
        router: Optional["ReliableRouter"] = None,
    ):
        """
        Initialize IntelliRouterModelClient

        Args:
            model_config: Model request config
            model_client_config: Client config; extra fields (intelli_router_*) extracted from __pydantic_extra__
            router: Optional pre-built ReliableRouter. When provided, skips cache lookup
                    and uses this router directly. Useful for advanced scenarios where
                    router lifecycle is managed externally.
        """
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
            f"{config.verify_ssl}"
        )
        return hashlib.md5(raw.encode()).hexdigest()

    @classmethod
    def _get_or_create_router(cls, config: IntelliRouterClientConfig) -> "ReliableRouter":
        """Get a router from cache or create & cache a new one."""
        key = cls._make_router_key(config)
        if key not in _router_cache:
            with _router_cache_lock:
                if key not in _router_cache:
                    _router_cache[key] = cls._create_router(config)
        return _router_cache[key]

    @classmethod
    def _create_router(cls, config: IntelliRouterClientConfig):
        """Create a ReliableRouter from IntelliRouterClientConfig"""
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
                tpm=dep_cfg.get("tpm", 100000),
                rpm=dep_cfg.get("rpm", 60),
                tags=dep_cfg.get("tags", []),
                timeout=dep_cfg.get("timeout", 30.0),
                verify_ssl=dep_cfg.get("verify_ssl", config.verify_ssl),
            )
            deployments.append(dep)

        router_kwargs = config.strategy_kwargs
        return ReliableRouter(
            deployments=deployments,
            strategy=config.strategy,
            num_retries=config.num_retries,
            timeout=config.timeout,
            **router_kwargs
        )

    def _validate_config(self):
        """Override config validation — intelli_router does not require api_key or api_base"""
        pass

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
        """
        Async LLM invocation

        Args:
            messages: List of messages
            tools: List of tools
            temperature: Temperature parameter
            top_p: Top-p sampling parameter
            max_tokens: Maximum number of tokens to generate
            stop: Stop sequence
            model: Model name
            output_parser: Output parser
            timeout: Timeout in seconds
            **kwargs: Additional parameters

        Returns:
            AssistantMessage: Assistant response message
        """
        converted_messages = self._convert_messages_to_dict(messages)

        model_name = model or self.model_config.model_name

        request_params = self._build_request_params(
            messages=converted_messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
            tools=tools,
            model=model_name,
            stream=False,
            timeout=timeout,
            **kwargs
        )

        response = await self._router.completion(
            model=model_name,
            messages=converted_messages,
            **request_params
        )

        return self._convert_response(response, output_parser)

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
        """
        Async streaming LLM invocation

        Args:
            messages: List of messages
            tools: List of tools
            temperature: Temperature parameter
            top_p: Top-p sampling parameter
            max_tokens: Maximum number of tokens to generate
            stop: Stop sequence
            model: Model name
            output_parser: Output parser
            timeout: Timeout in seconds
            **kwargs: Additional parameters

        Yields:
            AssistantMessageChunk: Streaming response chunk
        """
        converted_messages = self._convert_messages_to_dict(messages)

        model_name = model or self.model_config.model_name

        request_params = self._build_request_params(
            messages=converted_messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
            tools=tools,
            model=model_name,
            stream=True,
            timeout=timeout,
            **kwargs
        )

        async for chunk in self._router.stream_completion(
            model=model_name,
            messages=converted_messages,
            **request_params
        ):
            yield self._convert_chunk(chunk)

    def _build_request_params(
        self,
        *,
        messages: Union[str, List[dict]],
        tools: Union[List[ToolInfo], List[dict], None] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        model: str = None,
        stop: Union[Optional[str], None] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        timeout: Optional[float] = None,
        **kwargs
    ) -> Dict[str, Any]:
        params = {}

        final_temperature = temperature if temperature is not None else self.model_config.temperature
        final_top_p = top_p if top_p is not None else self.model_config.top_p
        final_max_tokens = max_tokens if max_tokens is not None else self.model_config.max_tokens

        if final_temperature is not None:
            params["temperature"] = final_temperature
        if final_top_p is not None:
            params["top_p"] = final_top_p
        if final_max_tokens is not None:
            params["max_tokens"] = final_max_tokens
        if stop is not None:
            params["stop"] = stop
        if timeout is not None:
            params["timeout"] = timeout

        tools_dict = self._convert_tools_to_dict(tools)
        if tools_dict:
            params["tools"] = tools_dict

        params.update(kwargs)

        client_name = self._get_client_name()
        final_model = model if model else self.model_config.model_name
        if UserConfig.is_sensitive():
            llm_logger.info(
                "Before request chat model, LLM request params ready.",
                event_type=LogEventType.LLM_CALL_START,
                model_name=final_model,
                model_provider=self.model_client_config.client_provider,
                temperature=final_temperature,
                top_p=final_top_p,
                max_tokens=final_max_tokens,
                is_stream=stream,
                stop=stop,
                metadata={"client_name": client_name},
            )
        else:
            llm_logger.info(
                "Before request chat model, LLM request params ready.",
                event_type=LogEventType.LLM_CALL_START,
                model_name=final_model,
                model_provider=self.model_client_config.client_provider,
                messages=messages,
                tools=tools_dict,
                temperature=final_temperature,
                top_p=final_top_p,
                max_tokens=final_max_tokens,
                is_stream=stream,
                metadata={"client_name": client_name},
            )

        return params

    def _convert_response(
        self,
        response: Dict[str, Any],
        output_parser: Optional[BaseOutputParser] = None
    ) -> AssistantMessage:
        """
        Convert an intelli_router response to AssistantMessage

        Args:
            response: intelli_router response
            output_parser: Output parser

        Returns:
            AssistantMessage: Assistant response message
        """
        choices = response.get("choices", [])
        if not choices:
            content = ""
        else:
            content = choices[0].get("message", {}).get("content", "")

        if output_parser and content:
            try:
                parsed = output_parser.parse(content)
                if isinstance(parsed, str):
                    content = parsed
                else:
                    content = str(parsed)
            except Exception as e:
                llm_logger.warning(
                    "Output parser failed to parse content, using raw content as fallback.",
                    event_type=LogEventType.LLM_CALL_ERROR,
                    model_name=self.model_config.model_name,
                    model_provider=self.model_client_config.client_provider,
                    exception=str(e),
                )

        return AssistantMessage(content=content)

    def _convert_chunk(self, chunk: Dict[str, Any]) -> AssistantMessageChunk:
        """
        Convert a streaming response chunk to AssistantMessageChunk

        Args:
            chunk: Streaming response chunk

        Returns:
            AssistantMessageChunk: Assistant message chunk
        """
        choices = chunk.get("choices", [])
        if not choices:
            content = ""
        else:
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")

        return AssistantMessageChunk(content=content)

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
    ):
        """IntelliRouter does not support image generation"""
        raise build_error(
            StatusCode.MODEL_INVOKE_CALL_FAILED,
            error_msg="IntelliRouter does not support image generation"
        )

    async def generate_speech(
        self,
        messages: List[UserMessage],
        *,
        model: Optional[str] = None,
        voice: Optional[str] = "Cherry",
        language_type: Optional[str] = "Auto",
        **kwargs
    ):
        """IntelliRouter does not support speech generation"""
        raise build_error(
            StatusCode.MODEL_INVOKE_CALL_FAILED,
            error_msg="IntelliRouter does not support speech generation"
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
    ):
        """IntelliRouter does not support video generation"""
        raise build_error(
            StatusCode.MODEL_INVOKE_CALL_FAILED,
            error_msg="IntelliRouter does not support video generation"
        )