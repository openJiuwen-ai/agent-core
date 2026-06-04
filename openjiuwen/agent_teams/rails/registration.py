# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Register openjiuwen's built-in harness elements into the provider registries.

``ensure_harness_elements_registered`` imports the built-in element
declarations (populating the manifest catalog) and drives provider registration
from it. It is the single entry the spec build path (``RailSpec.build`` /
``BuiltinToolSpec.build``) calls before resolving a ``type`` to a provider, now
that the class registries are gone. Idempotent: only the first call does work.
"""

from __future__ import annotations

_REGISTERED = False


def ensure_harness_elements_registered() -> None:
    """Register the built-in rails / tools with the provider registries.

    Imports ``builtin_elements`` — its module-level ``harness_element`` calls
    populate the manifest catalog — then runs ``register_from_catalog`` to wire
    every catalog descriptor (built-in plus any already-declared platform
    elements) into the provider registries. Safe to call repeatedly.
    """
    global _REGISTERED
    if _REGISTERED:
        return
    from openjiuwen.agent_teams.harness.manifest import register_from_catalog

    # Importing the modules runs their ``harness_element`` declarations.
    import openjiuwen.agent_teams.rails.builtin_elements  # noqa: F401
    import openjiuwen.agent_teams.rails.elements  # noqa: F401

    register_from_catalog()
    _REGISTERED = True
