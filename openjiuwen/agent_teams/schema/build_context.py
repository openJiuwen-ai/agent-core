# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime carrier threaded into ``spec.build()`` for provider-based assembly.

``BuildContext`` holds the live, non-serializable handles a capability
provider needs to materialize a rail / tool / sub-agent (resolved workspace,
member identity, and platform-specific handles attached by subclasses).  It
is deliberately NOT a Pydantic model: like ``TeamRuntimeContext`` it is a
Spec -> Runtime boundary object and never participates in JSON serialization.

Platforms subclass it to add typed handles (preferred); the generic
``extras`` mapping is an escape hatch for stacking multiple platforms.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

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
        extras: Escape-hatch mapping for platform handles when subclassing is
            not convenient.
    """

    language: str = "cn"
    member_name: Optional[str] = None
    role: Optional[str] = None
    workspace: Optional["Workspace"] = None
    member_card_id: Optional[str] = None
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


__all__ = ["BuildContext"]
