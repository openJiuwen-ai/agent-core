# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Process-wide default rail factories.

Extensions register zero-argument factories here; every ``BaseAgent``
then attaches one rail per factory on its first callback execution
(see ``BaseAgent._ensure_default_rails``). This lets an extension
observe agents it never constructed — e.g. agents built inside
workflow or multi-agent orchestration, where the caller has no place
to call ``register_rail`` — without core importing the extension:
the dependency direction stays extension → core.
"""

from __future__ import annotations

from typing import Callable, List, Tuple

from openjiuwen.core.single_agent.rail.base import AgentRail

RailFactory = Callable[[], AgentRail]

_default_rail_factories: List[RailFactory] = []


def register_default_rail_factory(factory: RailFactory) -> None:
    """Register a zero-argument rail factory.

    Idempotent by callable identity: registering the same factory twice
    (e.g. ``init_otel_tracer`` re-run) does not grow the list.

    Args:
        factory: callable returning a new ``AgentRail`` per agent.
            Must be cheap and side-effect free — it is invoked once
            per agent creation until cleared.
    """
    if factory not in _default_rail_factories:
        _default_rail_factories.append(factory)


def clear_default_rail_factories() -> None:
    """Remove all default rail factories.

    Intended for test isolation; normal shutdown does not need it.
    """
    _default_rail_factories.clear()


def default_rail_factories() -> Tuple[RailFactory, ...]:
    """Snapshot the registered factories.

    Returns:
        Tuple of factories in registration order.
    """
    return tuple(_default_rail_factories)
