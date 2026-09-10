# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compile resolved model selections into ``Model`` runtime configuration."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError as PydanticValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.foundation.llm.reasoning import _provider_key

from .schema import (
    CompiledModelSelection,
    ResolvedModel,
    ResolvedModelGroup,
    ResolvedRoute,
    ResolvedRoutingConfig,
    ResolvedSelection,
)

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm.model import Model

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


@dataclass(frozen=True)
class ModelGroupModelCandidate:
    """A callable model candidate resolved from one enabled model-group route."""

    model: "Model"
    route_id: str
    model_id: str
    model_name: str
    client_provider: str
    provider_key: str
    fallback_tag: str | None = None
    model_description: str | None = None


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


def get_model_group_models(model_group: ResolvedModelGroup) -> list[ModelGroupModelCandidate]:
    """Build callable model candidates from enabled routes in a resolved model group."""
    if not isinstance(model_group, ResolvedModelGroup):
        raise TypeError("model_group must be a ResolvedModelGroup")

    enabled_routes = _enabled_routes(model_group)
    from openjiuwen.core.foundation.llm.model import Model

    candidates: list[ModelGroupModelCandidate] = []
    for route in enabled_routes:
        client_config, request_config = _compile_route_as_model(route, model_group.request_config)
        candidates.append(
            ModelGroupModelCandidate(
                model=Model(
                    model_client_config=client_config,
                    model_config=request_config,
                ),
                route_id=route.route_id,
                model_id=route.model.model_id,
                model_name=route.model.model_name,
                client_provider=route.model.provider,
                provider_key=_provider_key(client_config),
                fallback_tag=route.model.fallback_tag,
                model_description=route.model.model_description,
            )
        )
    return candidates


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
    enabled_routes = _enabled_routes(model_group)
    _ensure_no_routing_strategy(model_group.routing)
    return _compile_route_as_model(enabled_routes[0], model_group.request_config)


def _enabled_routes(model_group: ResolvedModelGroup) -> list[ResolvedRoute]:
    enabled_routes = [route for route in model_group.routes if route.enabled]
    if not enabled_routes:
        raise build_error(
            StatusCode.MODEL_GROUP_NO_AVAILABLE_ROUTE,
            error_msg=f"model_group_id={model_group.model_group_id} has no enabled routes",
        )
    return enabled_routes


def _compile_route_as_model(
    route: ResolvedRoute,
    group_request_config: Mapping[str, Any],
) -> tuple[ModelClientConfig, ModelRequestConfig]:
    request_defaults = _merge_request_defaults(
        route.model.request_defaults,
        group_request_config,
        route.request_overrides,
    )
    selected_model = route.model.model_copy(
        update={"request_defaults": request_defaults}
    )
    return _compile_model(selected_model)


def _ensure_no_routing_strategy(
    routing_config: ResolvedRoutingConfig | Mapping[str, Any] | None,
) -> None:
    routing = _routing_config_mapping(routing_config)
    raw_strategy = routing.pop("strategy", None)
    if raw_strategy is None:
        return
    strategy = str(getattr(raw_strategy, "value", raw_strategy))
    raise build_error(
        StatusCode.MODEL_GROUP_INVALID,
        error_msg=(
            f"routing.strategy={strategy!r} is not supported yet. "
            "Omit routing.strategy or set it to None to use the first enabled route."
        ),
    )


def _routing_config_mapping(
    routing_config: ResolvedRoutingConfig | Mapping[str, Any] | None,
) -> dict[str, Any]:
    if routing_config is None:
        return {}
    if isinstance(routing_config, ResolvedRoutingConfig):
        return routing_config.model_dump(mode="python")
    return dict(routing_config)


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
