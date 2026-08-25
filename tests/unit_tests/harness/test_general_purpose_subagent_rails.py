# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for general-purpose subagent rail inheritance filtering.

Locks down the ``inherit_to_subagents`` opt-out in
``factory._inject_general_purpose_subagent``: a rail that binds parent-specific
state in ``init`` (e.g. ``TaskExecutionRail._deep_agent = agent``) must NOT be
shared by reference into the general-purpose subagent, otherwise the child's
``_ensure_initialized`` re-runs ``init`` against the subagent and silently
rebinds the shared instance — breaking the parent (todo.json resolved under
the child's empty workspace → task.start stops firing for later stages).
"""

from __future__ import annotations

import pytest

from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.factory import _inject_general_purpose_subagent
from openjiuwen.harness.rails.subagent.subagent_rail import SubagentRail
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.schema.config import SubAgentConfig


# ---------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------

class _RailNoFlag(AgentRail):
    """Old rail subclass without the ``inherit_to_subagents`` attribute.

    ``getattr(..., "inherit_to_subagents", True)`` must yield True so this is
    inherited — guarding backward compatibility for rails written before the
    flag existed.
    """


class _RailInheritTrue(AgentRail):
    """Rail that explicitly opts in to subagent inheritance."""

    inherit_to_subagents = True


class _RailInheritFalse(AgentRail):
    """Rail that explicitly opts out of subagent inheritance."""

    inherit_to_subagents = False


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _inject(rails):
    """Run the general-purpose injector with the given rails and return the gp spec."""
    result = _inject_general_purpose_subagent(
        [],
        add_general_purpose_agent=True,
        resolved_language="cn",
        rails=rails,
        system_prompt="",
        tools=None,
        mcps=None,
        model=None,
        skills=None,
    )
    assert len(result) == 1, f"expected exactly one injected spec, got {len(result)}"
    spec = result[0]
    assert isinstance(spec, SubAgentConfig)
    assert spec.agent_card.name == "general-purpose"
    return spec


def _rail_types(spec) -> set[type]:
    return {type(r) for r in (spec.rails or [])}


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------

def test_rail_with_inherit_true_is_included():
    """A rail with ``inherit_to_subagents = True`` must reach gp_rails."""
    rail = _RailInheritTrue()
    spec = _inject([rail])
    assert rail in (spec.rails or [])


def test_rail_with_inherit_false_is_excluded():
    """A rail with ``inherit_to_subagents = False`` must NOT reach gp_rails.

    This is the core guard: TaskExecutionRail opts out via this flag so the
    subagent never runs ``init`` against the shared instance.
    """
    spec = _inject([_RailInheritFalse()])
    assert _RailInheritFalse not in _rail_types(spec)


def test_rail_without_attribute_is_included():
    """A rail lacking the attribute defaults to inherited (backward compat)."""
    rail = _RailNoFlag()
    spec = _inject([rail])
    assert rail in (spec.rails or [])
    # also confirms getattr default path: no AttributeError, no surprise drop
    assert getattr(_RailNoFlag(), "inherit_to_subagents", None) is True


def test_subagent_rail_is_excluded():
    """``SubagentRail`` must still be excluded regardless of the flag."""
    rail = SubagentRail()
    spec = _inject([rail])
    assert SubagentRail not in _rail_types(spec)


def test_sysoperation_rail_auto_injected_when_missing():
    """When no SysOperationRail is supplied, one is prepended to gp_rails."""
    spec = _inject([_RailInheritTrue()])
    rails = spec.rails or []
    assert any(isinstance(r, SysOperationRail) for r in rails)
    # prepended (first), so the inherited rails keep their relative order after
    assert isinstance(rails[0], SysOperationRail)


def test_filtered_rails_propagate_to_subagent_config():
    """The exact filtered list (minus excluded, plus auto SysOperationRail) lands
    on ``SubAgentConfig.rails`` — verifying the filtered rails reach the spec.
    """
    keep = _RailInheritTrue()
    drop = _RailInheritFalse()
    spec = _inject([keep, drop, SubagentRail()])
    rails = spec.rails or []
    # keep is present, drop is absent, SubagentRail is absent, SysOperationRail
    # auto-injected at head
    assert keep in rails
    assert drop not in rails
    assert not any(isinstance(r, SubagentRail) for r in rails)
    assert any(isinstance(r, SysOperationRail) for r in rails)
