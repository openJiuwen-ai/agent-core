# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Privacy-aware edge/cloud routing model client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, List, Literal, NoReturn, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType
from openjiuwen.core.foundation.llm.schema.generation_response import (
    AudioGenerationResponse,
    ImageGenerationResponse,
    VideoGenerationResponse,
)
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, BaseMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.tool import ToolInfo


_DEPLOYMENT_NAMES = (
    "local_fast",
    "local_medium",
    "cloud_complex",
    "cloud_research",
    "cloud_reasoning",
)
_LEVEL_DEPLOYMENTS = {
    "SIMPLE": "local_fast",
    "MEDIUM": "local_medium",
    "COMPLEX": "cloud_complex",
    "RESEARCH": "cloud_research",
    "REASONING": "cloud_reasoning",
}
_CLOUD_ALLOWED_KWARGS = {
    "frequency_penalty",
    "include_reasoning_encrypted_content",
    "logprobs",
    "parallel_tool_calls",
    "presence_penalty",
    "reasoning",
    "reasoning_effort",
    "response_format",
    "return_token_ids",
    "seed",
    "tool_choice",
    "top_logprobs",
}
_TRANSPORT_OWNED_REQUEST_FIELDS = {
    "custom_headers",
    "extra_body",
    "extra_headers",
    "messages",
    "stream",
    "timeout",
    "tools",
}


class _ChildConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_client_config: ModelClientConfig
    model_request_config: ModelRequestConfig = Field(default_factory=ModelRequestConfig)


class _ComplexityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["llm", "heuristic"] = "llm"
    privacy_scope: Literal["local"] = "local"
    model_client_config: ModelClientConfig | None = None
    model_request_config: ModelRequestConfig | None = None
    classifier_preview_chars: int = Field(default=6000, ge=256)


class _PrivacyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class _DeploymentConfig(_ChildConfig):
    privacy_scope: Literal["local", "cloud"]


class _DeploymentsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_fast: _DeploymentConfig
    local_medium: _DeploymentConfig
    cloud_complex: _DeploymentConfig
    cloud_research: _DeploymentConfig
    cloud_reasoning: _DeploymentConfig

    @model_validator(mode="after")
    def validate_privacy_scopes(self) -> "_DeploymentsConfig":
        for name in _DEPLOYMENT_NAMES:
            expected_scope = "local" if name.startswith("local_") else "cloud"
            if getattr(self, name).privacy_scope != expected_scope:
                raise ValueError(f"{name} privacy_scope must be {expected_scope}")
        return self


class _EdgeCloudRouterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    privacy: _PrivacyConfig = Field(default_factory=_PrivacyConfig)
    complexity: _ComplexityConfig = Field(default_factory=_ComplexityConfig)
    deployments: _DeploymentsConfig

    @model_validator(mode="after")
    def validate_routes(self) -> "_EdgeCloudRouterConfig":
        if self.complexity.mode == "llm":
            if self.complexity.model_client_config is None or self.complexity.model_request_config is None:
                raise ValueError("llm complexity mode requires classifier client and request configuration")
            if not self.complexity.model_request_config.model_name.strip():
                raise ValueError("classifier model name cannot be empty")

        deployments = [getattr(self.deployments, name) for name in _DEPLOYMENT_NAMES]
        if any(not deployment.model_request_config.model_name.strip() for deployment in deployments):
            raise ValueError("every router deployment requires a model name")

        for name in _DEPLOYMENT_NAMES:
            deployment = getattr(self.deployments, name)
            if deployment.privacy_scope != "cloud":
                continue
            request_extras = deployment.model_request_config.__pydantic_extra__ or {}
            reserved_fields = sorted(_TRANSPORT_OWNED_REQUEST_FIELDS & request_extras.keys())
            if reserved_fields:
                raise ValueError(
                    f"{name} model request configuration cannot override router-owned fields: "
                    f"{', '.join(reserved_fields)}"
                )

        child_configs = [deployment.model_client_config for deployment in deployments]
        if self.complexity.model_client_config is not None:
            child_configs.append(self.complexity.model_client_config)
        if any(
            _provider_value(config.client_provider) == ProviderType.EdgeCloudRouter.value for config in child_configs
        ):
            raise ValueError("EdgeCloudRouter cannot contain another EdgeCloudRouter child")
        return self

    @classmethod
    def from_model_client_config(cls, config: ModelClientConfig) -> "_EdgeCloudRouterConfig":
        extra = config.__pydantic_extra__ or {}
        raw = extra.get("edge_cloud_router")
        if not isinstance(raw, dict):
            _raise_config_error("edge_cloud_router configuration is required")
        try:
            parsed = cls.model_validate(raw)
        except ValidationError:
            _raise_config_error("edge_cloud_router configuration is invalid")
        return parsed


def _raise_config_error(message: str) -> NoReturn:
    raise build_error(StatusCode.MODEL_SERVICE_CONFIG_ERROR, error_msg=message)


def _provider_value(provider: ProviderType | str) -> str:
    return provider.value if isinstance(provider, ProviderType) else str(provider)


def _load_agent_xrouter() -> Any:
    try:
        import agent_xrouter as edge_router
    except ImportError:
        _raise_config_error(
            "agent-xrouter package is missing or incompatible; install agent-xrouter to use EdgeCloudRouter"
        )
    required_symbols = (
        "ComplexityLevel",
        "ComplexityMode",
        "EdgeRouterEngine",
        "PrivacyTier",
        "RoutePlan",
        "RouteTarget",
        "RouterPolicy",
        "RouterRequest",
    )
    if any(not hasattr(edge_router, symbol) for symbol in required_symbols):
        _raise_config_error(
            "agent-xrouter package is missing or incompatible; install agent-xrouter to use EdgeCloudRouter"
        )
    return edge_router


@dataclass(frozen=True, slots=True)
class _RouteSelection:
    deployment_name: str
    # BaseModelClient.stream is typed as a coroutine although implementations
    # are async iterators, so keep the selected child transport-neutral here.
    child: Any
    messages: Any
    tools: Any
    model: str
    cloud: bool


@dataclass(frozen=True, slots=True)
class _AnswerOptions:
    temperature: float | None
    top_p: float | None
    max_tokens: int | None
    stop: str | None
    output_parser: BaseOutputParser | None
    timeout: float | None
    kwargs: dict[str, Any]


class _AgentCoreComplexityBackend:
    def __init__(self, client: BaseModelClient, model: str) -> None:
        self._client = client
        self._model = model

    async def classify(self, request: Any) -> str:
        response = await self._client.invoke(
            request.prompt,
            model=self._model,
            tools=None,
            output_parser=None,
        )
        if isinstance(response.content, str):
            return response.content
        if isinstance(response.content, list):
            parts: list[str] = []
            for part in response.content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
            return "".join(parts)
        return ""


class EdgeCloudRouterModelClient(BaseModelClient):
    """A built-in model client backed by the external ``agent-xrouter`` policy engine."""

    __client_name__ = ProviderType.EdgeCloudRouter.value

    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        self._edge = _load_agent_xrouter()
        super().__init__(model_config, model_client_config)
        self._config = _EdgeCloudRouterConfig.from_model_client_config(model_client_config)

        self._deployment_clients = {
            name: self._create_child(getattr(self._config.deployments, name)) for name in _DEPLOYMENT_NAMES
        }
        self._classifier_client: BaseModelClient | None = None
        self._classifier_model: str | None = None
        if self._config.complexity.mode == "llm":
            classifier_config = _ChildConfig(
                model_client_config=self._config.complexity.model_client_config,
                model_request_config=self._classifier_model_config(),
            )
            self._classifier_client = self._create_child(classifier_config)
            self._classifier_model = classifier_config.model_request_config.model_name

        policy = self._edge.RouterPolicy(
            privacy_enabled=self._config.privacy.enabled,
            complexity_mode=self._edge.ComplexityMode(self._config.complexity.mode),
            classifier_preview_chars=self._config.complexity.classifier_preview_chars,
        )
        self._engine = self._edge.EdgeRouterEngine(policy)

    def _validate_config(self) -> None:
        """The wrapper has child credentials, not top-level credentials."""

        if not isinstance(self.model_client_config.verify_ssl, bool):
            _raise_config_error("model client config verify_ssl must be a boolean type")

    @staticmethod
    def _create_child(config: _ChildConfig) -> BaseModelClient:
        from openjiuwen.core.foundation.llm.model_clients import create_model_client

        return create_model_client(config.model_client_config, config.model_request_config)

    def _classifier_model_config(self) -> ModelRequestConfig:
        config = self._config.complexity.model_request_config
        if config is None:
            _raise_config_error("classifier model configuration is required")
        updates: dict[str, Any] = {}
        if "temperature" not in config.model_fields_set:
            updates["temperature"] = 0.0
        if "max_tokens" not in config.model_fields_set:
            updates["max_tokens"] = 16
        return config.model_copy(update=updates)

    async def _prepare_route(self, messages, tools):
        classifier_backend = None
        if self._classifier_client is not None and self._classifier_model is not None:
            classifier_backend = _AgentCoreComplexityBackend(self._classifier_client, self._classifier_model)
        try:
            canonical_messages = self._convert_messages_to_dict(messages)
            canonical_tools = self._convert_tools_to_dict(tools)
            request = self._edge.RouterRequest.from_data(canonical_messages, canonical_tools)
            plan = await self._engine.route(request, classifier=classifier_backend)
            if not isinstance(plan, self._edge.RoutePlan):
                raise TypeError("agent-xrouter returned an invalid route plan")
        except Exception:
            plan = self._edge.RoutePlan(
                target=self._edge.RouteTarget.LOCAL,
                privacy_tier=self._edge.PrivacyTier.INDETERMINATE,
                complexity_level=None,
                complexity_source=None,
                reason_code="router_failed",
            )
        return plan

    def _resolve_answer_arg(self, name: str, call_value: Any) -> Any:
        if call_value is not None:
            return call_value
        if name in self.model_config.model_fields_set:
            return getattr(self.model_config, name)
        return None

    @staticmethod
    def _child_kwargs(kwargs: dict[str, Any], *, cloud: bool) -> dict[str, Any]:
        if not cloud:
            return dict(kwargs)
        return {key: value for key, value in kwargs.items() if key in _CLOUD_ALLOWED_KWARGS}

    def _select_route(self, plan, original_messages, original_tools) -> _RouteSelection:
        if plan.target is self._edge.RouteTarget.CLOUD:
            messages, tools = plan.cloud_request.to_data()
            deployment_name = _LEVEL_DEPLOYMENTS[plan.complexity_level.name]
        else:
            messages, tools = original_messages, original_tools
            deployment_name = (
                "local_fast" if plan.complexity_level is self._edge.ComplexityLevel.SIMPLE else "local_medium"
            )
        deployment = getattr(self._config.deployments, deployment_name)
        cloud = deployment.privacy_scope == "cloud"
        return _RouteSelection(
            deployment_name=deployment_name,
            child=self._deployment_clients[deployment_name],
            messages=messages,
            tools=tools,
            model=deployment.model_request_config.model_name,
            cloud=cloud,
        )

    def _select_local_fallback(self, messages, tools) -> _RouteSelection:
        deployment_name = "local_medium"
        deployment = self._config.deployments.local_medium
        return _RouteSelection(
            deployment_name=deployment_name,
            child=self._deployment_clients[deployment_name],
            messages=messages,
            tools=tools,
            model=deployment.model_request_config.model_name,
            cloud=False,
        )

    def _metadata(
        self,
        plan,
        *,
        deployment_name: str,
        cloud: bool,
        model: str,
        fallback_reason: str | None,
    ) -> dict[str, Any]:
        child_config = getattr(self._config.deployments, deployment_name)
        target = "cloud" if cloud else "local"
        return {
            "target": target,
            "privacy_enabled": self._config.privacy.enabled,
            "privacy_tier": plan.privacy_tier.value,
            "complexity_level": plan.complexity_level.name if plan.complexity_level is not None else None,
            "complexity_source": plan.complexity_source,
            "selected_deployment": deployment_name,
            "selected_provider": _provider_value(child_config.model_client_config.client_provider),
            "selected_model": model,
            "route_reason": plan.reason_code,
            "fallback_reason": fallback_reason,
            "policy_origin": target,
            "classifier_model": self._classifier_model,
        }

    @staticmethod
    def _with_message_metadata(message: AssistantMessage, metadata: dict[str, Any]) -> AssistantMessage:
        merged = dict(message.metadata)
        merged["edge_cloud_router"] = metadata
        return message.model_copy(update={"metadata": merged})

    @staticmethod
    def _with_chunk_metadata(chunk: AssistantMessageChunk, metadata: dict[str, Any]) -> AssistantMessageChunk:
        merged = dict(chunk.metadata)
        merged["edge_cloud_router"] = metadata
        return chunk.model_copy(update={"metadata": merged})

    async def _invoke_child(
        self,
        selection: _RouteSelection,
        options: _AnswerOptions,
    ) -> AssistantMessage:
        return await selection.child.invoke(
            selection.messages,
            tools=selection.tools,
            temperature=options.temperature,
            top_p=options.top_p,
            model=selection.model,
            max_tokens=options.max_tokens,
            stop=options.stop,
            output_parser=options.output_parser,
            timeout=options.timeout,
            **self._child_kwargs(options.kwargs, cloud=selection.cloud),
        )

    async def invoke(
        self,
        messages: Union[str, List[BaseMessage], List[dict]],
        *,
        tools: Union[List[ToolInfo], List[dict], None] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop: Optional[str] = None,
        model: str = None,
        output_parser: Optional[BaseOutputParser] = None,
        timeout: float = None,
        **kwargs,
    ) -> AssistantMessage:
        plan = await self._prepare_route(messages, tools)
        selection = self._select_route(plan, messages, tools)
        options = _AnswerOptions(
            temperature=self._resolve_answer_arg("temperature", temperature),
            top_p=self._resolve_answer_arg("top_p", top_p),
            max_tokens=self._resolve_answer_arg("max_tokens", max_tokens),
            stop=self._resolve_answer_arg("stop", stop),
            output_parser=output_parser,
            timeout=timeout,
            kwargs=kwargs,
        )
        try:
            response = await self._invoke_child(selection, options)
            metadata = self._metadata(
                plan,
                deployment_name=selection.deployment_name,
                cloud=selection.cloud,
                model=selection.model,
                fallback_reason=None,
            )
            return self._with_message_metadata(response, metadata)
        except Exception:
            if not selection.cloud:
                raise

        selection = self._select_local_fallback(messages, tools)
        response = await self._invoke_child(selection, options)
        metadata = self._metadata(
            plan,
            deployment_name=selection.deployment_name,
            cloud=False,
            model=selection.model,
            fallback_reason="cloud_invoke_failed",
        )
        return self._with_message_metadata(response, metadata)

    async def stream(
        self,
        messages: Union[str, List[BaseMessage], List[dict]],
        *,
        tools: Union[List[ToolInfo], List[dict], None] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop: Optional[str] = None,
        model: str = None,
        output_parser: Optional[BaseOutputParser] = None,
        timeout: float = None,
        **kwargs,
    ) -> AsyncIterator[AssistantMessageChunk]:
        plan = await self._prepare_route(messages, tools)
        selection = self._select_route(plan, messages, tools)
        options = _AnswerOptions(
            temperature=self._resolve_answer_arg("temperature", temperature),
            top_p=self._resolve_answer_arg("top_p", top_p),
            max_tokens=self._resolve_answer_arg("max_tokens", max_tokens),
            stop=self._resolve_answer_arg("stop", stop),
            output_parser=output_parser,
            timeout=timeout,
            kwargs=kwargs,
        )
        emitted = False
        try:
            async for chunk in selection.child.stream(
                selection.messages,
                tools=selection.tools,
                temperature=options.temperature,
                top_p=options.top_p,
                model=selection.model,
                max_tokens=options.max_tokens,
                stop=options.stop,
                output_parser=options.output_parser,
                timeout=options.timeout,
                **self._child_kwargs(options.kwargs, cloud=selection.cloud),
            ):
                if not emitted:
                    metadata = self._metadata(
                        plan,
                        deployment_name=selection.deployment_name,
                        cloud=selection.cloud,
                        model=selection.model,
                        fallback_reason=None,
                    )
                    chunk = self._with_chunk_metadata(chunk, metadata)
                emitted = True
                yield chunk
        except Exception:
            if not selection.cloud or emitted:
                raise
        else:
            if emitted:
                return
            if not selection.cloud:
                raise build_error(StatusCode.MODEL_CALL_FAILED, error_msg="local model returned no stream chunks")

        selection = self._select_local_fallback(messages, tools)
        local_emitted = False
        async for chunk in selection.child.stream(
            selection.messages,
            tools=selection.tools,
            temperature=options.temperature,
            top_p=options.top_p,
            model=selection.model,
            max_tokens=options.max_tokens,
            stop=options.stop,
            output_parser=options.output_parser,
            timeout=options.timeout,
            **self._child_kwargs(options.kwargs, cloud=False),
        ):
            if not local_emitted:
                metadata = self._metadata(
                    plan,
                    deployment_name=selection.deployment_name,
                    cloud=False,
                    model=selection.model,
                    fallback_reason="cloud_stream_failed_before_first_chunk",
                )
                chunk = self._with_chunk_metadata(chunk, metadata)
            local_emitted = True
            yield chunk
        if not local_emitted:
            raise build_error(StatusCode.MODEL_CALL_FAILED, error_msg="local fallback returned no stream chunks")

    @staticmethod
    def _unsupported_media() -> NoReturn:
        raise build_error(StatusCode.MODEL_CALL_FAILED, error_msg="EdgeCloudRouter supports chat requests only")

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
        **kwargs,
    ) -> ImageGenerationResponse:
        self._unsupported_media()

    async def generate_speech(
        self,
        messages: List[UserMessage],
        *,
        model: Optional[str] = None,
        voice: Optional[str] = "Cherry",
        language_type: Optional[str] = "Auto",
        **kwargs,
    ) -> AudioGenerationResponse:
        self._unsupported_media()

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
        **kwargs,
    ) -> VideoGenerationResponse:
        self._unsupported_media()
