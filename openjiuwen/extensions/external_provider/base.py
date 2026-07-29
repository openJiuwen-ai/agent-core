# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ExternalAuthProvider(Protocol):
    """Common token-auth surface for external account providers.

    Provider login flows remain provider-specific. This protocol only covers
    the stable operations that callers can share after credentials exist.
    """

    def status(self) -> Any:
        """Return provider-specific auth status."""
        ...

    def logout(self) -> bool:
        """Remove provider credentials if present."""
        ...

    def resolve_access_token(self, *, force_refresh: bool = False) -> str:
        """Return a usable access token, refreshing it when needed."""
        ...


@runtime_checkable
class ProviderModelCatalog(Protocol):
    """Common model-discovery surface for external providers."""

    def list_model_ids(self, **kwargs: Any) -> list[str]:
        """Return available model IDs for this provider."""
        ...
