# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serializable descriptor models for harness elements.

A :class:`HarnessElementDescriptor` is the LLM-tool-style definition of one
harness element (tool / rail / sub-agent). It captures the element kind, name,
description, a reflective reference to its construction factory, the JSON schema
of its construction inputs, and the public interface methods the built instance
exposes. The whole model is plain pydantic, so it round-trips through JSON for
introspection and future config-file driven assembly.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class ElementKind(str, Enum):
    """Kind of harness element a descriptor describes.

    Values double as the registration target: ``TOOL`` ->
    ``register_tool_provider``, ``RAIL`` -> ``register_rail_provider`` (both
    factory rails and class rails), ``SUBAGENT`` -> ``register_subagent_provider``.
    """

    TOOL = "tool"
    RAIL = "rail"
    SUBAGENT = "subagent"


class InterfaceMethod(BaseModel):
    """One public method or property the built instance exposes."""

    name: str
    description: str = ""
    is_async: bool = False


class HarnessElementDescriptor(BaseModel):
    """Serializable, LLM-tool-style definition of one harness element.

    Attributes:
        kind: The element kind (tool / rail / sub-agent).
        name: The unique element name, doubling as the spec ``type`` /
            ``factory_name`` used by spec resolution.
        description: Human-readable summary of what the element provides.
        factory_ref: Entry-point-style ``"module:qualname"`` reference to the
            construction factory (function) or class, for reflective resolution.
        input_schema: JSON schema of the real construction inputs (params and the
            needed context fields), produced from the element's ConstructionInput
            model. Each property carries a ``source`` (params / context) tag.
        input_model_ref: ``"module:qualname"`` reference to the ConstructionInput
            model, for strong validation in the future config loader (``None``
            when the element takes no construction inputs).
        interface_methods: The public interface methods the built instance
            exposes, each with a one-line functionality description.
    """

    kind: ElementKind
    name: str
    description: str
    factory_ref: str
    input_schema: dict[str, Any] = {}
    input_model_ref: str | None = None
    interface_methods: list[InterfaceMethod] = []


__all__ = [
    "ElementKind",
    "HarnessElementDescriptor",
    "InterfaceMethod",
]
