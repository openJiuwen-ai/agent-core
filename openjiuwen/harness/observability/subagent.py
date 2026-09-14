# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Give every dispatched sub-agent its own agent-tier span.

A sub-agent is built inside the SDK at dispatch time, so nothing in the parent
agent's spec reaches it: without a rail of its own it produces no
``agent.<type>.invoke`` span, and its llm/tool spans attach to the
**dispatching** agent's span — the sub-agent's whole run then reads as if the
parent had made those calls, with nothing under the ``task_tool`` span it
actually ran inside.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import logger

# Marker stamped on the ``create_subagent`` wrapper so a second install
# recognizes its own work and leaves it alone.
_SUBAGENT_HOOK_MARKER_ATTR = "openjiuwen_observability_hooked"


def attach_subagent_observability(subagent: Any) -> None:
    """Give *subagent* its own agent-tier span for the run that dispatches it.

    Attaching at build time is unreliable: the parent agent is constructed
    once, typically before observability is initialized, so the rail guard
    would return None. By dispatch time observability is up, and ``add_rail``
    still lands before the sub-agent's first ``_ensure_initialized()``
    registers its hooks.

    Idempotent, and a no-op when observability is off or *subagent* lacks the
    DeepAgent rail API. Best-effort: tracing must never break a run.

    Args:
        subagent: The freshly created sub-agent DeepAgent.
    """
    if subagent is None:
        return
    try:
        from openjiuwen.harness.observability.rail import (
            AgentObservabilityRail,
            maybe_agent_observability_rail,
        )

        rail = maybe_agent_observability_rail()
        if rail is None:
            return  # observability not initialized -> nothing to trace
        configured = subagent.configured_rails() if hasattr(subagent, "configured_rails") else []
        if any(isinstance(item, AgentObservabilityRail) for item in configured):
            return  # already attached — never add a second one
        if hasattr(subagent, "add_rail"):
            subagent.add_rail(rail)
    except Exception as exc:
        logger.debug("[AgentObservability] attach subagent rail failed: %s", exc)


def install_subagent_observability_hook() -> None:
    """Trace every sub-agent, whichever tool dispatched it.

    ``DeepAgent.create_subagent`` is the one point all dispatch paths share —
    the SDK's builtin ``task_tool``, a platform's custom agent tool, and
    background sub-agents. Wrapping it there is what makes tracing independent
    of the dispatcher; hooking a single tool covers only that tool.

    Idempotent — a second call sees the wrapper already installed. Best-effort:
    never raises, and a failure only costs sub-agent spans.
    """
    try:
        from openjiuwen.harness.deep_agent import DeepAgent
    except Exception as exc:  # pragma: no cover - import cycle guard
        logger.debug("[AgentObservability] subagent hook install skipped: %s", exc)
        return

    original = getattr(DeepAgent, "create_subagent", None)
    if original is None or getattr(original, _SUBAGENT_HOOK_MARKER_ATTR, False):
        return

    def create_subagent_with_observability(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Create the sub-agent, then give it its own observability rail."""
        subagent = original(self, *args, **kwargs)
        attach_subagent_observability(subagent)
        return subagent

    setattr(create_subagent_with_observability, _SUBAGENT_HOOK_MARKER_ATTR, True)
    DeepAgent.create_subagent = create_subagent_with_observability


__all__ = [
    "attach_subagent_observability",
    "install_subagent_observability_hook",
]
