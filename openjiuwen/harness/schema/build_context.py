# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime carrier threaded into ``spec.build()`` for provider-based assembly.

``BuildContext`` holds the live, non-serializable handles a capability
provider needs to materialize a rail / tool / sub-agent (resolved workspace,
member identity, and platform-specific handles attached by subclasses).  It is
deliberately NOT a Pydantic model: like ``TeamRuntimeContext`` it is a
Spec -> Runtime boundary object and never participates in JSON serialization.

Platforms subclass it to add typed handles (preferred); the generic
``extras`` mapping is an escape hatch for stacking multiple platforms.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from openjiuwen.harness.workspace.workspace import Workspace


@dataclass
class BuildContext:
    """Opaque runtime carrier handed to capability providers at build time.

    Attributes:
        language: Resolved language code (e.g. "cn" / "en").
        member_name: Team member name, when building a team member.
        role: Team role value (e.g. "leader" / "teammate"), when applicable.
        workspace: Live workspace, filled by ``DeepAgentSpec.build`` before
            rails / tools / sub-agents are materialized.
        member_card_id: Resolved agent card id, used to namespace tool ids.
        project_dir: Resolved project root directory, when members are built
            against a code project (e.g. the ``lsp`` rail roots here).
        extras: Escape-hatch mapping for platform handles when subclassing is
            not convenient.
    """

    language: str = "cn"
    member_name: Optional[str] = None
    role: Optional[str] = None
    workspace: Optional["Workspace"] = None
    member_card_id: Optional[str] = None
    project_dir: Optional[str] = None
    extras: dict[str, Any] = field(default_factory=dict)

    def derive(self, **overrides: Any) -> "BuildContext":
        """Return a shallow per-member copy with ``overrides`` applied.

        Uses ``copy.copy`` so the concrete subclass and all platform fields are
        preserved; only the named fields are overridden.  The original context
        is left unmutated, so a per-team base can fan out to many members.
        """
        clone = copy.copy(self)
        for key, value in overrides.items():
            setattr(clone, key, value)
        return clone


# Process-wide factory that rebuilds a ``BuildContext`` from a serializable
# seed. ``build_context`` is non-serializable and excluded from JSON, so a
# member rebuilt across a serialization boundary (spawned teammate process,
# distributed remote, cold recovery) loses it. The platform registers a factory
# here; the spec carries a serializable seed; the receiving side rebuilds a
# live context from the seed using local handles (config, registries).
_BUILD_CONTEXT_FACTORY: Optional[Callable[[dict[str, Any]], Optional["BuildContext"]]] = None


def register_build_context_factory(
    factory: Callable[[dict[str, Any]], Optional["BuildContext"]],
) -> None:
    """Register the platform factory that rebuilds a context from a seed.

    The factory maps the serializable seed carried on
    ``TeamAgentSpec.build_context_seed`` back into a live ``BuildContext`` on
    the receiving side of a serialization boundary. Only one factory is active
    per process; the last registration wins. Registration is expected to be
    idempotent.

    Args:
        factory: Callable mapping a seed mapping to a ``BuildContext`` (or None
            when it cannot rebuild from the given seed).
    """
    global _BUILD_CONTEXT_FACTORY
    _BUILD_CONTEXT_FACTORY = factory


def build_context_from_seed(seed: Optional[dict[str, Any]]) -> Optional["BuildContext"]:
    """Rebuild a context from a serializable seed via the registered factory.

    Returns None when no factory is registered or the seed is empty, so callers
    fall back to the live / legacy path unchanged.

    Args:
        seed: The serializable seed mapping (from ``build_context_seed``).

    Returns:
        A live ``BuildContext`` subclass instance, or None.
    """
    if _BUILD_CONTEXT_FACTORY is None or not seed:
        return None
    return _BUILD_CONTEXT_FACTORY(seed)


__all__ = [
    "BuildContext",
    "register_build_context_factory",
    "build_context_from_seed",
]
