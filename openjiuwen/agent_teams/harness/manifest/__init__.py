# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serializable manifest of harness elements.

Public surface for declaring, introspecting, and registering harness elements
(tools / rails / sub-agents) via serializable descriptors. See
:func:`openjiuwen.agent_teams.harness.manifest.catalog.harness_element` for the
declaration entry point and :func:`list_elements` for JSON introspection.

FUTURE: a config-file -> harness loader will read a declarative element list
(name + params per tool / rail / sub-agent), validate each ``params`` block
against the descriptor's input model, and emit the matching
``BuiltinToolSpec`` / ``RailSpec`` / ``SubAgentSpec`` into a ``DeepAgentSpec``.
Planned entry point (intentionally NOT implemented yet)::

    def load_deep_agent_spec(config: dict[str, Any]) -> DeepAgentSpec:
        # for each entry, look up descriptor = get_catalog()[entry["name"]],
        # validate entry["params"] via resolve_factory(descriptor.input_model_ref),
        # then emit:
        #   TOOL     -> BuiltinToolSpec(type=name, params=validated)
        #   RAIL     -> RailSpec(type=name, params=validated)
        #   SUBAGENT -> SubAgentSpec(factory_name=name, factory_kwargs=validated, ...)
        ...

The descriptor model already carries everything this needs (``name`` equals the
spec ``type`` / ``factory_name``, and ``input_model_ref`` enables validation),
so the loader can be added without touching any element declaration.
"""

from __future__ import annotations

from openjiuwen.agent_teams.harness.manifest.catalog import (
    add_descriptor,
    get_catalog,
    harness_element,
    list_elements,
)
from openjiuwen.agent_teams.harness.manifest.inputs import (
    ConstructionInput,
    EmptyInput,
    InputSource,
    context_field,
    param_field,
)
from openjiuwen.agent_teams.harness.manifest.introspect import (
    factory_ref,
    resolve_factory,
)
from openjiuwen.agent_teams.harness.manifest.models import (
    ElementKind,
    HarnessElementDescriptor,
    InterfaceMethod,
)
from openjiuwen.agent_teams.harness.manifest.registration import register_from_catalog

__all__ = [
    "ElementKind",
    "InterfaceMethod",
    "HarnessElementDescriptor",
    "ConstructionInput",
    "EmptyInput",
    "InputSource",
    "param_field",
    "context_field",
    "harness_element",
    "add_descriptor",
    "get_catalog",
    "list_elements",
    "factory_ref",
    "resolve_factory",
    "register_from_catalog",
]
