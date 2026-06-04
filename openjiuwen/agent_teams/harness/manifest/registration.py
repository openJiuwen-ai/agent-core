# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Drive provider registration from the manifest catalog.

This is the single registration path for catalog-declared harness elements: it
iterates the declared descriptors and calls the matching ``register_*`` function.
Class rails (declared with a class ``builder``) are unified with parameterized
factory rails by wrapping the class in :func:`class_rail_adapter`, so every rail
registers through ``register_rail_provider``.
"""

from __future__ import annotations

import inspect

from openjiuwen.agent_teams.harness.manifest.catalog import get_catalog
from openjiuwen.agent_teams.harness.manifest.introspect import (
    class_rail_adapter,
    resolve_factory,
)
from openjiuwen.agent_teams.harness.manifest.models import ElementKind
from openjiuwen.agent_teams.schema.deep_agent_spec import (
    register_rail_provider,
    register_subagent_provider,
    register_tool_provider,
)


def register_from_catalog() -> None:
    """Register every catalog descriptor with the provider registries (idempotent).

    Each ``register_*`` is a name-keyed overwrite, so repeated calls are safe.
    Rails whose builder is a class are adapted to the ``(params, context) -> rail``
    provider contract before registration.
    """
    for descriptor in get_catalog().values():
        target = resolve_factory(descriptor.factory_ref)
        if descriptor.kind is ElementKind.TOOL:
            register_tool_provider(descriptor.name, target)
        elif descriptor.kind is ElementKind.RAIL:
            builder = target if not inspect.isclass(target) else class_rail_adapter(target)
            register_rail_provider(descriptor.name, builder)
        elif descriptor.kind is ElementKind.SUBAGENT:
            register_subagent_provider(descriptor.name, target)
