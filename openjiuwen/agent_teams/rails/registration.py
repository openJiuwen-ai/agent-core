# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Composition entry that registers team providers and harness builtins.

``ensure_harness_elements_registered`` is the single entry the team spec build
path (``RailSpec.build`` / ``BuiltinToolSpec.build``) used to call before
resolving a ``type`` to a provider. It is retained as the team-side
**composition entry**: it first delegates to
``ensure_builtin_elements_registered`` for ``core.*`` builtins, then imports
the team element declarations (populating the shared catalog), then
explicitly runs ``register_from_catalog`` so team descriptors reach the
provider registries even if the harness ensure already short-circuited on
its own ``_REGISTERED`` guard.

This split preserves the original public name (so swarm / agent_teams
callers keep their import) and the idempotent semantics: only the first call
does work. Subsequent calls short-circuit on the ``_REGISTERED`` guard.
"""

from __future__ import annotations

_REGISTERED = False


def ensure_harness_elements_registered() -> None:
    """Register the team rails / tools / sub-agents and harness builtins.

    Calls the harness-internal ``ensure_builtin_elements_registered`` first
    (``core.*`` builtins; may no-op if already registered), then imports the
    team element modules — their module-level ``harness_element`` calls
    populate the shared catalog — then explicitly runs
    ``register_from_catalog`` over the union catalog (team + ``core.*``).

    The trailing ``register_from_catalog`` is required under the harness
    ``_REGISTERED`` guard convention: team descriptors are added after
    harness ensure may already have short-circuited.

    Idempotent: only the first call does work. The ``_REGISTERED`` guard
    protects the team import side effect and the follow-up catalog sync, so
    repeated calls from ``RailSpec.build`` / ``SubAgentSpec.build`` /
    ``AgentConfigurator`` do not re-execute.
    """
    global _REGISTERED
    if _REGISTERED:
        return
    from openjiuwen.harness.manifest import (
        ensure_builtin_elements_registered,
        register_from_catalog,
    )

    # Pull in the harness builtins (``core.*``) first; may short-circuit if
    # leaf build already ran harness ensure.
    ensure_builtin_elements_registered()

    # Importing the modules runs their ``harness_element`` declarations and
    # records team-specific descriptors in the shared catalog.
    import openjiuwen.agent_teams.rails.elements  # noqa: F401
    import openjiuwen.agent_teams.rails.subagent_elements  # noqa: F401

    # Sync the full catalog (team + core) into the provider registries.
    # Required under the harness ``_REGISTERED`` guard: team descriptors
    # were added after harness ensure may already have short-circuited.
    register_from_catalog()
    _REGISTERED = True


__all__ = ["ensure_harness_elements_registered"]
