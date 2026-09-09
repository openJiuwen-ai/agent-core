# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team contributions to the agent-tier span.

The agent span itself — opening it, nesting it, closing it, draining orphans —
belongs to :class:`openjiuwen.harness.observability.rail.AgentObservabilityRail`
and is identical whether the agent runs alone or as a team member. This rail
adds only what is genuinely about teams, and does it **without subclassing or
re-opening the span**: it runs first in the hook chain (higher priority) and

* parks the ``agentteam.*`` identity block as an
  :class:`AgentSpanDecoration`, which the agent rail applies to the span it
  opens (and mirrors the redacted output into when it closes), and
* stamps the leader's round result as the Team trace's top-level output.

Both rails are mounted side by side (``core.observability`` +
``core.team.observability``); neither knows the other's internals, and the
handoff is the callback context they already share.

Span tree (the team.{name} root is opened by the Team runner, not here)::

  team.{name}
  ├── agent.{member}.task_iteration.1    [AGENT]   <- agent rail
  │     ├── llm.call                     [GENERATION]
  │     └── tool.xxx                     [TOOL]
  └── task.{id}                          [SPAN]    <- team monitor handler
"""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.observability.span_context import get_team_span
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.extensions.observability.redaction import redact_completion
from openjiuwen.extensions.observability.semconv import (
    AT_AGENT_ID,
    AT_AGENT_INPUT,
    AT_AGENT_NAME,
    AT_AGENT_OUTPUT,
    AT_AGENT_ROLE,
    AT_MEMBER_ID,
    AT_MEMBER_NAME,
    AT_SESSION_ID,
    AT_TEAM_ID,
    LANGFUSE_OBSERVATION_OUTPUT,
)
from openjiuwen.harness.observability.rail import (
    AgentObservabilityRail,
    AgentSpanDecoration,
)
from openjiuwen.harness.observability.span_context import current_session_id
from openjiuwen.harness.rails.base import DeepAgentRail


class TeamObservabilityRail(DeepAgentRail):
    """Contribute team identity to the agent span, and the trace's output."""

    # Above ``AgentObservabilityRail.priority`` (10) on purpose: higher runs
    # first in every hook chain, so the decoration is parked before the agent
    # rail opens the span, and the trace output is stamped while the round's
    # result is still the one in hand.
    priority: int = 12

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        try:
            self._build_decoration(ctx.agent).park(ctx)
        except Exception as exc:
            team_logger.warning("otel team rail before_task_iteration failed: {}", exc)

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        try:
            self._build_decoration(ctx.agent).park(ctx)
        except Exception as exc:
            team_logger.warning("otel team rail before_invoke failed: {}", exc)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Stamp the leader's round result as the Team trace's output.

        Only the leader answers for the team, so only its result belongs on the
        ``team.{name}`` root — a teammate's round output stays on its own agent
        span. Runs before the agent rail closes the agent span, which is
        harmless: this touches the team root only.
        """
        try:
            agent = getattr(ctx, "agent", None)
            if agent is None or getattr(agent, "role", None) != TeamRole.LEADER:
                return
            if not getattr(agent, "team_name", ""):
                return

            inputs = getattr(ctx, "inputs", None)
            output = getattr(inputs, "result", None) if inputs is not None else None
            if not output:
                return

            team_span = get_team_span()
            if team_span is None or not team_span.is_recording():
                return

            from openjiuwen.agent_teams.observability.setup import get_config

            config = get_config()
            output_str = str(output)
            redacted = redact_completion(output_str, config) if config else output_str
            team_span.set_attribute(LANGFUSE_OBSERVATION_OUTPUT, redacted)
        except Exception as exc:
            team_logger.warning("otel team rail after_task_iteration failed: {}", exc)

    @staticmethod
    def _resolve_role(agent: Any) -> str:
        """Return the role string for the ``AT_AGENT_ROLE`` attribute.

        Source priority (symmetric with ``AgentObservabilityRail.resolve_agent_name``):
          1. ``agent.role`` — ``TeamAgent`` property, returns a ``TeamRole``
             enum whose ``.value`` is the authoritative role string.
          2. ``agent.build_context.role`` — ``NativeHarness`` / ``DeepAgent``
             shells expose the build context set by the configurator from
             ``ctx.role.value`` (a plain role string such as ``"leader"`` or
             ``"human_agent"``).
          3. Falls back to ``""`` (caller substitutes member_name).
        """
        role_attr = getattr(agent, "role", None)
        if isinstance(role_attr, TeamRole):
            return role_attr.value
        build_ctx = getattr(agent, "build_context", None)
        bc_role = getattr(build_ctx, "role", None) if build_ctx else None
        if isinstance(bc_role, str) and bc_role:
            try:
                return TeamRole(bc_role).value
            except ValueError:
                pass
        return ""

    @staticmethod
    def _build_decoration(agent: Any) -> AgentSpanDecoration:
        """Build the ``agentteam.*`` block for the span this agent is about to open.

        The span name and generic attributes are the agent rail's; this is the
        team identity layered on top. ``member_name`` resolution is shared with
        the agent rail so the identity block can never disagree with the span
        name it annotates.

        Args:
            agent: The agent whose span is about to be opened.

        Returns:
            The contribution to park on the callback context. Every field is
            optional — a sub-agent with no team still gets its member/agent
            names, and an agent outside any session simply carries no session id.
        """
        member_name = AgentObservabilityRail.resolve_agent_name(agent)
        team_name = getattr(agent, "team_name", "") or ""
        session_id = current_session_id()

        attributes: dict[str, Any] = {}
        if team_name and member_name:
            attributes[AT_AGENT_ID] = f"{team_name}_{member_name}"
        elif member_name:
            attributes[AT_AGENT_ID] = member_name
        if member_name:
            attributes[AT_AGENT_NAME] = member_name
            attributes[AT_MEMBER_ID] = member_name
            attributes[AT_MEMBER_NAME] = member_name
        # AT_AGENT_ROLE carries the resolved role value (leader / teammate /
        # human_agent / ...) when the agent exposes one; sub-agents and shells
        # that expose neither ``agent.role`` nor a ``build_context.role`` fall
        # back to the member name so the attribute is never empty.
        attributes[AT_AGENT_ROLE] = TeamObservabilityRail._resolve_role(agent) or member_name or ""
        if team_name:
            attributes[AT_TEAM_ID] = team_name
        if session_id:
            attributes[AT_SESSION_ID] = session_id

        return AgentSpanDecoration(
            attributes=attributes,
            input_attribute_keys=(AT_AGENT_INPUT,),
            output_attribute_keys=(AT_AGENT_OUTPUT,),
        )


def maybe_team_observability_rail() -> TeamObservabilityRail | None:
    """Return a ``TeamObservabilityRail`` when observability is on, else None."""
    from openjiuwen.agent_teams.observability.setup import is_initialized

    if not is_initialized():
        return None
    return TeamObservabilityRail()


def maybe_observability_rails() -> list[DeepAgentRail]:
    """Return both rails a team *member* needs, in mount order, or an empty list.

    The agent tier and the team contribution are separate rails; mounting only
    one of them yields either a trace with no agent spans or agent spans with
    no team identity. This is the single source of truth for "mount team
    observability", so no call site has to remember the pair.

    Sub-agents are deliberately not covered by this: they are dispatched work,
    not members, so they mount the agent rail alone
    (``maybe_agent_observability_rail``) and inherit team attribution
    structurally, from the member span they nest under.
    """
    from openjiuwen.harness.observability.rail import maybe_agent_observability_rail

    team_rail = maybe_team_observability_rail()
    if team_rail is None:
        return []
    agent_rail = maybe_agent_observability_rail()
    if agent_rail is None:
        return []
    return [team_rail, agent_rail]


__all__ = [
    "TeamObservabilityRail",
    "maybe_observability_rails",
    "maybe_team_observability_rail",
]
