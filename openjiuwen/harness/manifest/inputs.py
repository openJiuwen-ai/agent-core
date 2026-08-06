# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Source-tagged construction-input models for harness elements.

A harness element's real construction inputs come from two namespaces: the spec
``params`` dict (caller / config supplied) and the runtime ``BuildContext``
(language, workspace, config-derived values, ...). A :class:`ConstructionInput`
subclass declares each input as a field tagged with its source via
:func:`param_field` / :func:`context_field`, and :meth:`ConstructionInput.resolve`
extracts the values from ``params + context`` and validates them. The per-field
source metadata is serializable, so the descriptor's ``input_schema`` truthfully
describes what it takes to build the element.

Runtime-only handles (``ctx.extras`` objects, registries), globals, and env vars
are intentionally NOT modeled here: they are runtime/deployment plumbing rather
than construction parameters, so the factories keep reading them directly.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Self

from pydantic import BaseModel, Field

from openjiuwen.harness.manifest.introspect import (
    factory_ref,
    resolve_factory,
)


class InputSource(str, Enum):
    """Where a construction input is sourced from at build time."""

    PARAMS = "params"
    CONTEXT = "context"


def param_field(
    *,
    default: Any = ...,
    default_factory: Callable[[], Any] | None = None,
    description: str = "",
) -> Any:
    """Declare an input sourced from the spec ``params`` dict (key == field name).

    Args:
        default: Field default used when the params key is absent.
        default_factory: Factory for a mutable default (e.g. list), mutually
            exclusive with ``default``.
        description: Human-readable field description.

    Returns:
        A pydantic ``Field`` tagged with ``source=params``.
    """
    extra = {"source": InputSource.PARAMS.value}
    if default_factory is not None:
        return Field(
            default_factory=default_factory,
            description=description,
            json_schema_extra=extra,
        )
    return Field(default, description=description, json_schema_extra=extra)


def context_field(
    *,
    attr: str | None = None,
    resolver: Callable[[Any], Any] | None = None,
    default: Any = None,
    description: str = "",
) -> Any:
    """Declare an input sourced from the build context.

    Provide exactly one of ``attr`` (a direct ``getattr(context, attr)``) or
    ``resolver`` (a ``(context) -> value`` callable, e.g. a config-derived value).
    The serializable schema records ``source`` plus ``context_attr`` /
    ``resolver_ref`` (an entry-point dotted path) so the field stays reflectable.

    Args:
        attr: Context attribute name to read directly.
        resolver: Callable computing the value from the context.
        default: Field default used when the resolved value is ``None``.
        description: Human-readable field description.

    Returns:
        A pydantic ``Field`` tagged with ``source=context``.
    """
    extra: dict[str, Any] = {"source": InputSource.CONTEXT.value}
    if attr is not None:
        extra["context_attr"] = attr
    if resolver is not None:
        extra["resolver_ref"] = factory_ref(resolver)
    return Field(default, description=description, json_schema_extra=extra)


class ConstructionInput(BaseModel):
    """Base for an element's source-tagged construction-input model."""

    @classmethod
    def resolve(cls, params: dict[str, Any] | None, context: Any) -> Self:
        """Extract each field from ``params`` / ``context`` and validate.

        Params-sourced fields read ``params[name]`` (falling back to the field
        default when absent). Context-sourced fields read the declared attribute
        or call the declared resolver. ``None`` results are dropped so the field
        default applies, preserving the factories' tolerance of missing handles.

        Args:
            params: The spec ``params`` / ``factory_kwargs`` dict.
            context: The runtime build context.

        Returns:
            A validated instance of the input model.
        """
        values: dict[str, Any] = {}
        for name, field in cls.model_fields.items():
            extra = field.json_schema_extra or {}
            source = extra.get("source")
            if source == InputSource.PARAMS.value:
                if params and name in params:
                    values[name] = params[name]
            elif "resolver_ref" in extra:
                values[name] = resolve_factory(extra["resolver_ref"])(context)
            elif "context_attr" in extra:
                values[name] = getattr(context, extra["context_attr"], None)
        return cls(**{name: value for name, value in values.items() if value is not None})


class EmptyInput(ConstructionInput):
    """Input model for elements that take no construction inputs."""


__all__ = [
    "InputSource",
    "param_field",
    "context_field",
    "ConstructionInput",
    "EmptyInput",
]
