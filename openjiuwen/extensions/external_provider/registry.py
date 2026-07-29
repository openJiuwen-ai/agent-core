# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from openjiuwen.extensions.external_provider.base import ExternalAuthProvider, ProviderModelCatalog


AuthProviderFactory = Callable[..., ExternalAuthProvider]
ModelCatalogFactory = Callable[..., ProviderModelCatalog]
_Factory = TypeVar("_Factory", AuthProviderFactory, ModelCatalogFactory)

_AUTH_PROVIDER_FACTORIES: dict[str, AuthProviderFactory] = {}
_AUTH_PROVIDER_NAMES: dict[str, str] = {}
_MODEL_CATALOG_FACTORIES: dict[str, ModelCatalogFactory] = {}
_MODEL_CATALOG_NAMES: dict[str, str] = {}
_BUILTIN_PROVIDERS_REGISTERED = False


class ExternalProviderRegistryError(ValueError):
    """Raised when an external provider is missing or registered incorrectly."""


def register_auth_provider(
    provider: str,
    factory: AuthProviderFactory,
    *,
    replace: bool = False,
) -> None:
    """Register an auth provider factory."""
    _register_factory(
        registry=_AUTH_PROVIDER_FACTORIES,
        names=_AUTH_PROVIDER_NAMES,
        provider=provider,
        factory=factory,
        kind="auth provider",
        replace=replace,
    )


def register_model_catalog(
    provider: str,
    factory: ModelCatalogFactory,
    *,
    replace: bool = False,
) -> None:
    """Register a model catalog factory."""
    _register_factory(
        registry=_MODEL_CATALOG_FACTORIES,
        names=_MODEL_CATALOG_NAMES,
        provider=provider,
        factory=factory,
        kind="model catalog",
        replace=replace,
    )


def get_auth_provider_factory(provider: str) -> AuthProviderFactory:
    """Return the auth provider factory for a provider name."""
    _ensure_builtin_providers()
    return _get_factory(
        registry=_AUTH_PROVIDER_FACTORIES,
        names=_AUTH_PROVIDER_NAMES,
        provider=provider,
        kind="auth provider",
    )


def get_model_catalog_factory(provider: str) -> ModelCatalogFactory:
    """Return the model catalog factory for a provider name."""
    _ensure_builtin_providers()
    return _get_factory(
        registry=_MODEL_CATALOG_FACTORIES,
        names=_MODEL_CATALOG_NAMES,
        provider=provider,
        kind="model catalog",
    )


def create_auth_provider(provider: str, **kwargs: Any) -> ExternalAuthProvider:
    """Create a new auth provider instance."""
    return get_auth_provider_factory(provider)(**kwargs)


def create_model_catalog(provider: str, **kwargs: Any) -> ProviderModelCatalog:
    """Create a new model catalog instance."""
    return get_model_catalog_factory(provider)(**kwargs)


def list_auth_providers() -> list[str]:
    """Return registered auth provider names."""
    _ensure_builtin_providers()
    return sorted(_AUTH_PROVIDER_NAMES.values())


def list_model_catalogs() -> list[str]:
    """Return registered model catalog provider names."""
    _ensure_builtin_providers()
    return sorted(_MODEL_CATALOG_NAMES.values())


def _register_factory(
    *,
    registry: dict[str, _Factory],
    names: dict[str, str],
    provider: str,
    factory: _Factory,
    kind: str,
    replace: bool,
) -> None:
    key = _provider_key(provider)
    if not callable(factory):
        raise ExternalProviderRegistryError(f"External {kind} factory for {provider!r} is not callable.")
    if key in registry and not replace:
        raise ExternalProviderRegistryError(f"External {kind} for {names[key]!r} is already registered.")
    registry[key] = factory
    names[key] = provider.strip()


def _get_factory(
    *,
    registry: dict[str, _Factory],
    names: dict[str, str],
    provider: str,
    kind: str,
) -> _Factory:
    key = _provider_key(provider)
    try:
        return registry[key]
    except KeyError as exc:
        available = ", ".join(sorted(names.values())) or "none"
        raise ExternalProviderRegistryError(
            f"Unknown external {kind}: {provider!r}. Available providers: {available}."
        ) from exc


def _provider_key(provider: str) -> str:
    if not isinstance(provider, str):
        raise ExternalProviderRegistryError("External provider name must be a string.")
    key = provider.strip().lower()
    if not key:
        raise ExternalProviderRegistryError("External provider name cannot be empty.")
    return key


def _ensure_builtin_providers() -> None:
    global _BUILTIN_PROVIDERS_REGISTERED
    if _BUILTIN_PROVIDERS_REGISTERED:
        return
    _register_builtin_providers()
    _BUILTIN_PROVIDERS_REGISTERED = True


def _register_builtin_providers() -> None:
    from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import (
        OPENAI_ACCOUNT_PROVIDER,
        OpenAIAccountAuthManager,
    )
    from openjiuwen.extensions.external_provider.openai_auth.openai_account_models import (
        OpenAIAccountModelCatalog,
    )

    provider_key = _provider_key(OPENAI_ACCOUNT_PROVIDER)
    if provider_key not in _AUTH_PROVIDER_FACTORIES:
        register_auth_provider(OPENAI_ACCOUNT_PROVIDER, OpenAIAccountAuthManager)
    if provider_key not in _MODEL_CATALOG_FACTORIES:
        register_model_catalog(OPENAI_ACCOUNT_PROVIDER, OpenAIAccountModelCatalog)
