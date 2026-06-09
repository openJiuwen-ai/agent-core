# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reflection helpers for the harness-element manifest.

Provides the dotted-reference encoding used to serialize a construction factory
(``factory_ref`` / ``resolve_factory``), interface-method introspection for the
descriptor's interface surface, and the class-rail adapter that unifies
no-factory class rails with parameterized factory rails under one provider
contract.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Callable

from openjiuwen.agent_teams.harness.manifest.models import ElementKind, InterfaceMethod

# Internal rail plumbing that is not a user-facing lifecycle hook.
_RAIL_INTERNAL_METHODS = frozenset({"get_callbacks"})


def factory_ref(target: Callable[..., Any] | type) -> str:
    """Return the entry-point-style ``"module:qualname"`` reference for *target*.

    Args:
        target: The factory function or class to reference.

    Returns:
        A dotted reference resolvable by :func:`resolve_factory`.
    """
    return f"{target.__module__}:{target.__qualname__}"


def resolve_factory(ref: str) -> Any:
    """Resolve a ``"module:qualname"`` reference to the live callable or class.

    Args:
        ref: A dotted reference produced by :func:`factory_ref`.

    Returns:
        The imported factory function or class.
    """
    module_path, _, qualname = ref.partition(":")
    obj: Any = importlib.import_module(module_path)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def _first_doc_line(member: Any) -> str:
    """Return the first line of *member*'s docstring, or an empty string."""
    doc = inspect.getdoc(member) or ""
    lines = doc.splitlines()
    return lines[0] if lines else ""


def interface_methods_from_class(cls: type) -> list[InterfaceMethod]:
    """Introspect the public methods and properties of *cls*.

    Captures non-underscore methods and properties, using the first docstring
    line as the description and flagging coroutine methods as async. Internal
    rail plumbing (e.g. ``get_callbacks``) is excluded.

    Args:
        cls: The class whose public interface to describe.

    Returns:
        The interface-method descriptors, sorted by name.
    """
    methods: list[InterfaceMethod] = []
    for name, member in inspect.getmembers(cls, predicate=inspect.isfunction):
        if name.startswith("_") or name in _RAIL_INTERNAL_METHODS:
            continue
        methods.append(
            InterfaceMethod(
                name=name,
                description=_first_doc_line(member),
                is_async=inspect.iscoroutinefunction(member),
            ),
        )
    for name, member in inspect.getmembers(cls, lambda obj: isinstance(obj, property)):
        if name.startswith("_"):
            continue
        methods.append(InterfaceMethod(name=name, description=_first_doc_line(member)))
    methods.sort(key=lambda method: method.name)
    return methods


def default_interface_methods(kind: ElementKind) -> list[InterfaceMethod]:
    """Return the canonical interface methods for an element *kind*.

    Rails expose the ``AgentRail`` lifecycle hooks. Tools expose the ``Tool``
    invoke / stream / card surface. Sub-agents build a ``SubAgentConfig`` spec
    rather than an interface object, so they expose none. The base classes are
    imported lazily to keep manifest import light.

    Args:
        kind: The harness element kind.

    Returns:
        The default interface-method descriptors for the kind.
    """
    if kind is ElementKind.RAIL:
        from openjiuwen.core.single_agent.rail.base import AgentRail

        return interface_methods_from_class(AgentRail)
    if kind is ElementKind.TOOL:
        from openjiuwen.core.foundation.tool import Tool

        return interface_methods_from_class(Tool)
    return []


def class_rail_adapter(cls: type) -> Callable[..., Any]:
    """Adapt a no-factory rail class into a ``(params, context) -> rail`` factory.

    Replicates the class branch of ``RailSpec.build``: instantiate the class with
    the spec params, auto-injecting ``language`` from the build context when the
    constructor accepts it. This unifies class rails with parameterized factory
    rails under the single rail-provider registration path.

    Args:
        cls: The rail class to adapt.

    Returns:
        A provider factory that builds an instance of *cls*.
    """
    accepts_language = "language" in inspect.signature(cls.__init__).parameters

    def _build(params: dict[str, Any], context: Any) -> Any:
        kwargs = dict(params or {})
        if accepts_language and "language" not in kwargs and context is not None:
            language = getattr(context, "language", None)
            if language is not None:
                kwargs["language"] = language
        return cls(**kwargs)

    return _build


def class_tool_adapter(cls: type) -> Callable[..., Any]:
    """Adapt a no-factory tool class into a ``(params, context) -> tool`` factory.

    Replicates the class branch of ``BuiltinToolSpec.build``: instantiate the
    class with the spec params, auto-injecting ``language`` from the build
    context when the constructor accepts it. This unifies class tools with
    parameterized factory tools under the single tool-provider registration
    path (mirrors :func:`class_rail_adapter`).

    Args:
        cls: The tool class to adapt.

    Returns:
        A provider factory that builds an instance of *cls*.
    """
    accepts_language = "language" in inspect.signature(cls.__init__).parameters

    def _build(params: dict[str, Any], context: Any) -> Any:
        kwargs = dict(params or {})
        if accepts_language and "language" not in kwargs and context is not None:
            language = getattr(context, "language", None)
            if language is not None:
                kwargs["language"] = language
        return cls(**kwargs)

    return _build
