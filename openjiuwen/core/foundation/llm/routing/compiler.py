# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compile resolved model selections into ``Model`` runtime configuration."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.schema.config import (
    IntelliRouterConfig,
    IntelliRouterDeploymentConfig,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)

from .schema import (
    CompiledModelSelection,
    ResolvedModel,
    ResolvedModelGroup,
    ResolvedRoute,
    ResolvedSelection,
)

_CLIENT_CONFIG_FIELDS = set(ModelClientConfig.model_fields)
_REQUEST_MODEL_KEYS = (set(ModelRequestConfig.model_fields) & {"model_name"}) | {"model"}
_INTERNAL_REQUEST_KEYS = {
    "api_key",
    "api_base",
    "client_provider",
    "deployment",
    "messages",
    "provider",
    "stream",
}
_ROUTER_PROVIDER_BY_CORE_PROVIDER = {
    ProviderType.OpenAI.value.lower(): "openai",
    ProviderType.OpenAIAccount.value.lower(): "openai",
    ProviderType.OpenRouter.value.lower(): "openai",
    ProviderType.Anthropic.value.lower(): "anthropic",
    ProviderType.SiliconFlow.value.lower(): "siliconflow",
    ProviderType.DashScope.value.lower(): "dashscope",
    ProviderType.DeepSeek.value.lower(): "deepseek",
    ProviderType.Zhipu.value.lower(): "zhipu",
    ProviderType.InferenceAffinity.value.lower(): "inference-affinity",
}
_ROUTER_PROVIDER_BY_ENDPOINT_PROFILE = {
    "anthropic": "anthropic",
    "aws-bedrock": "aws-bedrock",
    "bedrock": "aws-bedrock",
    "dashscope": "dashscope",
    "deepseek": "deepseek",
    "google-gemini": "google-gemini",
    "gemini": "google-gemini",
    "inference-affinity": "inference-affinity",
    "siliconflow": "siliconflow",
    "zhipu": "zhipu",
}


def compile_model_selection(selection: ResolvedSelection | Mapping[str, Any]) -> CompiledModelSelection:
    """Compile a resolved model or model group into runtime LLM configs.

    Upper layers remain responsible for permissions, ownership, and persistence.
    This function only validates the resolved payload and converts it to the
    existing ``ModelClientConfig`` / ``ModelRequestConfig`` pair.
    """
    resolved = _coerce_selection(selection)
    if isinstance(resolved, ResolvedModel):
        client_config, request_config = _compile_model(resolved)
        return CompiledModelSelection(
            model_client_config=client_config,
            model_request_config=request_config,
            selected_type="model",
            selected_id=resolved.model_id,
        )
    client_config, request_config = _compile_model_group(resolved)
    return CompiledModelSelection(
        model_client_config=client_config,
        model_request_config=request_config,
        selected_type="model_group",
        selected_id=resolved.model_group_id,
    )


def _coerce_selection(selection: ResolvedSelection | Mapping[str, Any]) -> ResolvedSelection:
    if isinstance(selection, (ResolvedModel, ResolvedModelGroup)):
        return selection
    if not isinstance(selection, Mapping):
        raise build_error(
            StatusCode.MODEL_REQUEST_CONFIG_INVALID,
            error_msg="model selection payload must be a mapping or resolved model object",
        )

    try:
        if "routes" in selection:
            return ResolvedModelGroup.model_validate(selection)
        return ResolvedModel.model_validate(selection)
    except PydanticValidationError as exc:
        raise build_error(
            StatusCode.MODEL_REQUEST_CONFIG_INVALID,
            error_msg=str(exc),
        ) from exc


def _compile_model(model: ResolvedModel) -> tuple[ModelClientConfig, ModelRequestConfig]:
    _validate_model(model)
    client_data = _client_config_data(model)
    request_defaults = _request_config_data(model.request_defaults)
    try:
        return (
            ModelClientConfig(**client_data),
            ModelRequestConfig(model=model.model_name, **request_defaults),
        )
    except Exception as exc:
        raise build_error(
            StatusCode.MODEL_REQUEST_CONFIG_INVALID,
            error_msg=str(exc),
        ) from exc


def _compile_model_group(
    model_group: ResolvedModelGroup,
) -> tuple[ModelClientConfig, ModelRequestConfig]:
    enabled_routes = [route for route in model_group.routes if route.enabled]
    if not enabled_routes:
        raise build_error(
            StatusCode.MODEL_GROUP_NO_AVAILABLE_ROUTE,
            error_msg=f"model_group_id={model_group.model_group_id} has no enabled routes",
        )

    deployments = [_deployment_data(route, model_group.request_config) for route in enabled_routes]
    strategy, num_retries, strategy_kwargs, router_options = _routing_config_data(model_group.routing)
    if num_retries is None:
        num_retries = max(len(deployments) - 1, 0)

    try:
        router_config = IntelliRouterConfig(
            model_group_id=model_group.model_group_id,
            deployments=[
                IntelliRouterDeploymentConfig(**deployment)
                for deployment in deployments
            ],
            strategy=strategy,
            num_retries=num_retries,
            strategy_kwargs=strategy_kwargs,
            **router_options,
        )
        client_config = ModelClientConfig(
            client_provider=ProviderType.IntelliRouter.value,
            intelli_router=router_config,
        )
        request_config = ModelRequestConfig()
        return client_config, request_config
    except Exception as exc:
        raise build_error(
            StatusCode.MODEL_GROUP_INVALID,
            error_msg=str(exc),
        ) from exc


def _routing_config_data(
    routing_config: Mapping[str, Any] | None,
) -> tuple[str, int | None, dict[str, Any], dict[str, Any]]:
    routing = dict(routing_config or {})
    strategy = str(routing.pop("strategy", "ordered-failover") or "ordered-failover")
    num_retries = routing.pop("num_retries", None)
    nested_kwargs = routing.pop("strategy_kwargs", {})
    if nested_kwargs is None:
        nested_kwargs = {}
    if not isinstance(nested_kwargs, Mapping):
        raise build_error(
            StatusCode.MODEL_GROUP_INVALID,
            error_msg="routing.strategy_kwargs must be a mapping",
        )

    strategy_kwargs = {key: deepcopy(value) for key, value in nested_kwargs.items()}
    if strategy == "tag-filtered":
        fallback_tag = strategy_kwargs.get("fallback_tag")
        if fallback_tag is not None and not isinstance(fallback_tag, str):
            raise build_error(
                StatusCode.MODEL_GROUP_INVALID,
                error_msg="routing.strategy_kwargs.fallback_tag must be a string",
            )
    router_options = _router_options_data(routing_config)
    return strategy, num_retries, strategy_kwargs, router_options


def _router_options_data(routing_config: Mapping[str, Any] | None) -> dict[str, Any]:
    routing = dict(routing_config or {})
    field_map = {
        "timeout_seconds": "timeout",
        "enable_health_check": "enable_health_check",
        "health_check_interval_seconds": "health_check_interval",
        "enable_observability": "enable_observability",
    }
    return {
        target_key: deepcopy(routing[source_key])
        for source_key, target_key in field_map.items()
        if source_key in routing and routing[source_key] is not None
    }


def _validate_model(model: ResolvedModel) -> None:
    if not str(model.model_name or "").strip():
        raise build_error(
            StatusCode.MODEL_REQUEST_CONFIG_INVALID,
            error_msg=f"model_id={model.model_id} has empty model_name",
        )
    if not str(model.provider or "").strip():
        raise build_error(
            StatusCode.MODEL_REQUEST_CONFIG_INVALID,
            error_msg=f"model_id={model.model_id} has empty provider",
        )


def _deployment_data(route: ResolvedRoute, group_request_config: Mapping[str, Any]) -> dict[str, Any]:
    _validate_model(route.model)
    request_defaults = _merge_request_defaults(
        route.model.request_defaults,
        group_request_config,
        route.request_overrides,
    )
    client_options = dict(route.model.client_options or {})
    data = {
        "route_id": route.route_id,
        "model_id": route.model.model_id,
        "model_name": route.model.model_name,
        "api_key": route.model.api_key,
        "api_base": route.model.api_base,
        "provider": _router_provider(route.model),
        "request_defaults": request_defaults,
    }
    if route.model.fallback_tag is not None:
        data["fallback_tag"] = route.model.fallback_tag
    if route.model.model_description is not None:
        data["model_description"] = route.model.model_description
    for key in ("tpm", "rpm", "timeout"):
        value = getattr(route, key)
        if value is not None:
            data[key] = value
    if route.model.endpoint_profile is not None:
        data["endpoint_profile"] = route.model.endpoint_profile
    for key in ("endpoint_profile", "custom_headers", "verify_ssl"):
        if key == "endpoint_profile" and "endpoint_profile" in data:
            continue
        if key in client_options and client_options[key] is not None:
            data[key] = deepcopy(client_options[key])
    return data


def _client_config_data(model: ResolvedModel) -> dict[str, Any]:
    data = {
        "client_provider": model.provider,
        "api_key": model.api_key,
        "api_base": model.api_base,
    }
    if model.endpoint_profile is not None:
        data["endpoint_profile"] = model.endpoint_profile
    for key, value in (model.client_options or {}).items():
        if key in {"client_provider", "api_key", "api_base"}:
            continue
        if key == "endpoint_profile" and "endpoint_profile" in data:
            continue
        data[key] = deepcopy(value)
    return data


def _request_config_data(values: Mapping[str, Any] | None) -> dict[str, Any]:
    result = {}
    for key, value in (values or {}).items():
        normalized_key = "model" if key == "model_name" else key
        if value is None or key in _INTERNAL_REQUEST_KEYS or normalized_key in _REQUEST_MODEL_KEYS:
            continue
        if normalized_key in _CLIENT_CONFIG_FIELDS:
            continue
        result[normalized_key] = deepcopy(value)
    return result


def _merge_request_defaults(*sources: Mapping[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in sources:
        for key, value in _request_config_data(source).items():
            if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
                merged[key] = _deep_merge_dicts(merged[key], value)
            else:
                merged[key] = value
    return merged


def _deep_merge_dicts(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = {key: deepcopy(value) for key, value in left.items()}
    for key, value in right.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge_dicts(merged[key], value)
        elif value is not None:
            merged[key] = deepcopy(value)
    return merged


def _router_provider(model: ResolvedModel) -> str:
    options = dict(model.client_options or {})
    explicit = options.get("intelli_router_provider")
    if explicit:
        return _normalize_provider(explicit)

    profile = _normalize_provider(model.endpoint_profile or options.get("endpoint_profile"))
    if profile in _ROUTER_PROVIDER_BY_ENDPOINT_PROFILE:
        return _ROUTER_PROVIDER_BY_ENDPOINT_PROFILE[profile]

    provider = _normalize_provider(model.provider)
    return _ROUTER_PROVIDER_BY_CORE_PROVIDER.get(provider, provider)


def _normalize_provider(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")
