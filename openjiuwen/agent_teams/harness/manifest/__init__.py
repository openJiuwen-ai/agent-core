# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Package-level re-export of the harness manifest surface.

The implementation lives in ``openjiuwen.harness.manifest``. This package
keeps the team import path
(``from openjiuwen.agent_teams.harness.manifest import harness_element`` and
friends) working with the same objects (``is``-identical). Submodules are not
mirrored here — deep imports should use ``openjiuwen.harness.manifest.*``.

NOTE: ``ensure_harness_elements_registered`` (the team composition entry in
``agent_teams.rails.registration``) is intentionally NOT re-exported from
here. Callers that need it should import it from there.
"""

from __future__ import annotations

from openjiuwen.harness.manifest import (  # noqa: F401
    ConstructionInput,
    ElementKind,
    EmptyInput,
    HarnessElementDescriptor,
    InputSource,
    InterfaceMethod,
    add_descriptor,
    context_field,
    factory_ref,
    get_catalog,
    harness_element,
    list_elements,
    param_field,
    register_from_catalog,
    resolve_factory,
)
from openjiuwen.harness.manifest.registration import (
    ensure_builtin_elements_registered,
)

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
    "ensure_builtin_elements_registered",
]
