# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""In-process catalog of harness-element descriptors.

The ``harness_element`` declaration is the single source of truth: it records a
serializable :class:`HarnessElementDescriptor` for a factory function (used as a
decorator) or a class builder (passed via ``builder=``). Registration into the
provider registries is driven separately from this catalog by
``openjiuwen.agent_teams.harness.manifest.registration.register_from_catalog``,
so metadata and registration never drift.
"""

from __future__ import annotations

from typing import Any, Callable

from openjiuwen.agent_teams.harness.manifest.inputs import (
    ConstructionInput,
    EmptyInput,
)
from openjiuwen.agent_teams.harness.manifest.introspect import (
    default_interface_methods,
    factory_ref,
)
from openjiuwen.agent_teams.harness.manifest.models import (
    ElementKind,
    HarnessElementDescriptor,
    InterfaceMethod,
)

# Ordered catalog of declared descriptors, keyed by element name.
_CATALOG: dict[str, HarnessElementDescriptor] = {}


def add_descriptor(descriptor: HarnessElementDescriptor) -> None:
    """Add *descriptor* to the catalog, rejecting duplicate names.

    Args:
        descriptor: The descriptor to record.

    Raises:
        ValueError: If an element with the same name is already declared.
    """
    if descriptor.name in _CATALOG:
        raise ValueError(f"Duplicate harness element name: {descriptor.name!r}")
    _CATALOG[descriptor.name] = descriptor


def get_catalog() -> dict[str, HarnessElementDescriptor]:
    """Return the live catalog mapping element name to descriptor."""
    return _CATALOG


def list_elements() -> list[dict[str, Any]]:
    """Return every descriptor as a JSON-serializable list.

    The "list all available harness elements with their schemas" entry point,
    suitable for introspection and future config authoring.

    Returns:
        One JSON-ready mapping per declared element.
    """
    return [descriptor.model_dump(mode="json") for descriptor in _CATALOG.values()]


def harness_element(
    *,
    kind: ElementKind,
    name: str,
    description: str,
    input_model: type[ConstructionInput] = EmptyInput,
    builder: Callable[..., Any] | type | None = None,
    interface_methods: list[InterfaceMethod] | None = None,
) -> Any:
    """Declare a serializable descriptor for one harness element.

    Used as a decorator on a factory function (``builder`` omitted), or called
    directly with ``builder=<class-or-callable>`` to declare a class rail. Pure
    metadata capture: the target is returned unchanged and NO provider
    registration happens here (see ``register_from_catalog``).

    Args:
        kind: The harness element kind.
        name: The unique element name (also the spec ``type`` / ``factory_name``).
        description: Human-readable summary of what the element provides.
        input_model: ConstructionInput model describing the real construction
            inputs (params and the needed context fields). Its JSON schema is
            stored on the descriptor.
        builder: The class or callable to describe. When ``None`` the call acts as
            a decorator and the wrapped function becomes the builder.
        interface_methods: Explicit interface methods. When ``None`` the kind's
            canonical interface is used.

    Returns:
        A decorator when ``builder`` is ``None``, otherwise *builder* unchanged.
    """

    def record(target: Callable[..., Any] | type) -> Callable[..., Any] | type:
        resolved_methods = interface_methods if interface_methods is not None else default_interface_methods(kind)
        input_model_ref = None if input_model is EmptyInput else factory_ref(input_model)
        add_descriptor(
            HarnessElementDescriptor(
                kind=kind,
                name=name,
                description=description,
                factory_ref=factory_ref(target),
                input_schema=input_model.model_json_schema(),
                input_model_ref=input_model_ref,
                interface_methods=resolved_methods,
            ),
        )
        return target

    if builder is not None:
        return record(builder)
    return record
