# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed inputs and outputs of the team extension events.

The events framework types ``EventInput`` / ``EventOutput`` as plain object
aliases, so these models are self-contained frozen dataclasses: the bus hands
them to matchers and handlers as-is, and business entries validate the final
output type after the chain returns.

Two events are covered here: ``team.monitor_event`` (public monitor
notifications) and ``team.build_policy`` (policy section assembly). The
read-only config snapshot ``TeamPolicyContext`` is not an event input — the
build entry binds it per call and handlers read it through
``ops.team.policy_context()``.
"""

from __future__ import annotations

from dataclasses import dataclass

from openjiuwen.agent_teams.monitor.models import MonitorEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent.prompts.builder import PromptSection


@dataclass(frozen=True, slots=True)
class TeamMonitorEventInput:
    """One public monitor notification delivered to the extension chain.

    ``member_name`` mirrors ``monitor_event.member_name``: the member the
    notification is *about*, never the receiver and never inferred from a
    sender or recipient field. The internal ``EventMessage`` never crosses
    this boundary.
    """

    team_name: str
    member_name: str | None
    monitor_event: MonitorEvent


@dataclass(frozen=True, slots=True)
class TeamMonitorEventOutput:
    """Marker that the notification chain finished.

    Says nothing about which handlers ran — the chain may short-circuit, so
    completed output does not imply every subscriber observed the event.
    """


@dataclass(frozen=True, slots=True)
class TeamPolicyBuildInput:
    """The build target of one policy build.

    A matching key, not a config carrier: it names the team, the role and
    (when already determined) the member whose policy is being built.
    ``workspace_prompt_variant`` says which consumer the sections are for
    ("native" in-process members, "external" CLI members — the teammate
    role policy picks its template variant from it). The effective config
    the sections must reflect is bound separately as a
    :class:`TeamPolicyContext` at the build entry.
    """

    team_name: str
    role: TeamRole
    member_name: str | None = None
    workspace_prompt_variant: str = "native"


@dataclass(frozen=True, slots=True)
class TeamPolicyBuildOutput:
    """The assembled policy sections of one build.

    Sections carry stable names (the ``team_*`` section-name constants);
    the build entry validates structure, names and reserved boundaries
    before installing them. Installing the sections into a model context
    is the caller's business, not part of this output.
    """

    sections: tuple[PromptSection, ...]


@dataclass(frozen=True, slots=True)
class TeamPolicyContext:
    """Read-only snapshot of the effective config for one policy build.

    Captures *effective* values, not spec ceilings: ``hitt_enabled`` for
    instance comes from the running team backend, and ``base_prompt`` /
    ``swarmflow_enabled`` / ``expose_human_agents_to_teammates`` reflect
    what the built sections must actually say. The default builder and any
    extension handler read the same snapshot so both build from one truth;
    nothing here is mutable by a handler.
    """

    language: str = "cn"
    lifecycle: str = "temporary"
    team_mode: str = "default"
    dispatch_mode: str = "autonomous"
    teammate_mode: str = "build_mode"
    bootstrap_mode: str = "leader"
    member_messaging: bool = False
    member_subtasks: bool = False
    bound_group_name: str | None = None
    hitt_enabled: bool = False
    base_prompt: str | None = None
    swarmflow_enabled: bool = False
    expose_human_agents_to_teammates: bool = False


__all__ = [
    "TeamMonitorEventInput",
    "TeamMonitorEventOutput",
    "TeamPolicyBuildInput",
    "TeamPolicyBuildOutput",
    "TeamPolicyContext",
]
