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

from openjiuwen.harness.manifest.catalog import get_catalog
from openjiuwen.harness.manifest.introspect import (
    class_rail_adapter,
    class_tool_adapter,
    resolve_factory,
)
from openjiuwen.harness.manifest.models import ElementKind
from openjiuwen.harness.schema.deep_agent_spec import (
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
            builder = target if not inspect.isclass(target) else class_tool_adapter(target)
            register_tool_provider(descriptor.name, builder)
        elif descriptor.kind is ElementKind.RAIL:
            builder = target if not inspect.isclass(target) else class_rail_adapter(target)
            register_rail_provider(descriptor.name, builder)
        elif descriptor.kind is ElementKind.SUBAGENT:
            register_subagent_provider(descriptor.name, target)


# Convention: ``ensure_builtin_elements_registered`` owns a process-level
# ``_REGISTERED`` guard (import builtins once + one catalog→registry sync).
# Upper layers that populate *additional* catalog descriptors after this may
# already have run (team / swarm) MUST call ``register_from_catalog()``
# themselves after their own imports — otherwise those declarations stay in
# the catalog and never reach the provider registries. See
# ``agent_teams.rails.registration.ensure_harness_elements_registered`` and
# ``jiuwenswarm...registry.register_swarm_providers``.
_REGISTERED = False


def ensure_builtin_elements_registered() -> None:
    """Import harness builtin + unique + meta declarations and sync registries.

    Imports ``builtin_elements``, ``harness_elements``, and ``meta_elements`` so
    their module-level ``harness_element`` calls populate the catalog, then runs
    ``register_from_catalog`` to wire every catalog descriptor into the provider
    registries. Idempotent via ``_REGISTERED``: only the first call does work.

    Only loads harness-owned declarations; it never imports
    ``openjiuwen.agent_teams.*``. Team / swarm composition entries that add
    more catalog descriptors after this may have already run must call
    ``register_from_catalog()`` explicitly (see module convention note above).
    """
    global _REGISTERED
    if _REGISTERED:
        return

    import openjiuwen.harness.manifest.builtin_elements
    import openjiuwen.harness.manifest.harness_elements
    import openjiuwen.harness.manifest.meta_elements

    register_from_catalog()
    _REGISTERED = True


__all__ = [
    "register_from_catalog",
    "ensure_builtin_elements_registered",
]
