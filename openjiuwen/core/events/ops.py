# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Concrete builder and per-invocation view for event handler operations."""

from __future__ import annotations

import inspect
import keyword
from collections.abc import Mapping
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.events.types import OpsMethod, PackageMetadata, SessionContext


class EventHandlerOpsBuilder:
    """Declare an operations catalog and build isolated views."""

    def __init__(self) -> None:
        self._catalog: dict[str, dict[str, OpsMethod]] = {}

    def add_namespace(self, namespace: str) -> None:
        """Add an empty capability namespace."""
        self._validate_name(namespace, "namespace")
        if hasattr(EventHandlerOps, namespace):
            raise_error(StatusCode.EVENTS_OPS_INVALID, reason=f"namespace conflicts with ops attribute: {namespace}")
        if namespace in self._catalog:
            raise_error(StatusCode.EVENTS_OPS_INVALID, reason=f"duplicate namespace: {namespace}")
        self._catalog[namespace] = {}

    def add_method(
        self,
        namespace: str,
        method_name: str,
        method: OpsMethod,
    ) -> None:
        """Add one asynchronous method to an existing namespace."""
        self._validate_name(namespace, "namespace")
        self._validate_name(method_name, "method")
        if namespace not in self._catalog:
            raise_error(StatusCode.EVENTS_OPS_INVALID, reason=f"missing namespace: {namespace}")

        methods = self._catalog[namespace]
        if method_name in methods:
            raise_error(StatusCode.EVENTS_OPS_INVALID, reason=f"duplicate method: {namespace}.{method_name}")
        if not inspect.iscoroutinefunction(method):
            raise_error(StatusCode.EVENTS_OPS_INVALID, reason=f"method must be asynchronous: {namespace}.{method_name}")
        methods[method_name] = method

    def build(
        self,
        package_metadata: PackageMetadata | None = None,
        session_context: SessionContext | None = None,
    ) -> EventHandlerOps:
        """Build a new operations view over the catalog."""
        if package_metadata is not None and not isinstance(package_metadata, PackageMetadata):
            raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="package_metadata must be PackageMetadata or None")
        if session_context is not None and not isinstance(session_context, SessionContext):
            raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="session_context must be SessionContext or None")
        return EventHandlerOps(self._catalog, package_metadata, session_context)

    @staticmethod
    def _validate_name(name: str, kind: str) -> None:
        if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) or name.startswith("_"):
            raise_error(StatusCode.EVENTS_OPS_INVALID, reason=f"invalid {kind} name: {name!r}")


class EventHandlerOps:
    """Read-only invocation context with dynamic access to built capabilities."""

    def __init__(
        self,
        catalog: Mapping[str, Mapping[str, OpsMethod]],
        package_metadata: PackageMetadata | None,
        session_context: SessionContext | None,
    ) -> None:
        self._catalog = catalog
        self._package_metadata = package_metadata
        self._session_context = session_context

    @property
    def package_metadata(self) -> PackageMetadata | None:
        """Return package identity for this invocation."""
        return self._package_metadata

    @property
    def session_context(self) -> SessionContext | None:
        """Return session facts for this invocation."""
        return self._session_context

    def __getattr__(self, namespace: str) -> Any:
        if namespace not in self._catalog:
            raise_error(StatusCode.EVENTS_OPS_INVALID, reason=f"unknown namespace: {namespace}")
        return _OpsNamespace(self, self._catalog[namespace])


class _OpsNamespace:
    """Bind one method namespace to its current operations view."""

    def __init__(self, ops: EventHandlerOps, methods: Mapping[str, OpsMethod]) -> None:
        self._ops = ops
        self._methods = methods

    def __getattr__(self, method_name: str) -> Any:
        if method_name not in self._methods:
            raise_error(StatusCode.EVENTS_OPS_INVALID, reason=f"unknown method: {method_name}")
        method = self._methods[method_name]

        async def invoke(*args: object, **kwargs: object) -> object:
            return await method(self._ops, *args, **kwargs)

        return invoke
