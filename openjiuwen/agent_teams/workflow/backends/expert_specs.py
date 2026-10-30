# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarmflow expert table: AgentGroup members a script may pick via ``agent_template``.

An expert is an AgentGroup member the platform already assembled into the team
blueprint twice over — its capability snapshot sits in ``agents[member]`` and its
persona in ``predefined_members[member].prompt``. This module joins the two by
member name once at assembly time so the worker backend can answer "who runs
this call" with a plain dict lookup and no knowledge of how the platform laid
the group out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openjiuwen.agent_teams.schema.deep_agent_spec import ProgressiveToolSpec
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger

# Coordination roles never run as single-shot workers.
_NON_EXPERT_ROLES = frozenset({TeamRole.LEADER, TeamRole.HUMAN_AGENT, TeamRole.PASSIVE_HUMAN, TeamRole.BRIDGE_AGENT})


@dataclass(frozen=True)
class SwarmflowExpertEntry:
    """One pickable expert: identity for events, persona for the prompt, spec to build from."""

    member_name: str
    display_name: str
    persona: str
    spec: Any | None
    external_cli: Any | None

    @property
    def is_external(self) -> bool:
        return self.external_cli is not None


def derive_swarmflow_expert_specs(
    team_spec: Any,
    observability_rail_specs: list[Any],
) -> dict[str, SwarmflowExpertEntry]:
    """Join ``agents[m]`` with ``predefined_members[m]`` into worker-ready expert entries.

    The worker keeps the member's model / task-loop flags / rails
    (infrastructure the teammate path also keeps) and gains the observability
    rails every worker carries; its ``tools`` list is cleared so capabilities
    come from the template snapshot alone. The produced spec still carries the
    member's ``workspace``, but the worker path substitutes its own workspace
    (as it does for every worker), so the member's is never used there.
    External-runtime members are listed
    with ``spec=None`` so a script naming them gets a precise error instead of a
    silent native run. Ordinary predefined teammates carry no template snapshot
    and are therefore not experts: they are skipped, and a script naming one
    later gets the backend's "not a member" error.
    """
    if callable(observability_rail_specs) and not isinstance(observability_rail_specs, (list, tuple)):
        # The configurator imports the rail-spec *factory* under this very name;
        # passing it unevaluated only blows up once a team has an expert.
        raise TypeError(
            "derive_swarmflow_expert_specs expects the evaluated rail list, "
            "not the factory: pass observability_rail_specs()"
        )
    experts: dict[str, SwarmflowExpertEntry] = {}
    for member in team_spec.predefined_members:
        if getattr(member, "role_type", None) in _NON_EXPERT_ROLES:
            continue
        name = member.member_name
        persona = member.prompt or ""
        external_cli = getattr(member, "external_cli", None)
        if external_cli is not None:
            experts[name] = SwarmflowExpertEntry(
                member_name=name,
                display_name=member.display_name,
                persona=persona,
                spec=None,
                external_cli=external_cli,
            )
            continue
        base = team_spec.agents.get(name)
        if base is None:
            team_logger.debug("[swarmflow] expert skipped: member %r not in agents[]", name)
            continue
        if getattr(base, "agent_template_spec", None) is None:
            team_logger.debug("[swarmflow] expert skipped: member %r has no agent_template_spec snapshot", name)
            continue
        update: dict[str, Any] = {
            "tools": [],
            "rails": list(base.rails or []) + list(observability_rail_specs),
        }
        if team_spec.progressive_tool:
            update["progressive_tool"] = ProgressiveToolSpec(enabled=True)
        experts[name] = SwarmflowExpertEntry(
            member_name=name,
            display_name=member.display_name,
            persona=persona,
            spec=base.model_copy(update=update),
            external_cli=None,
        )
    return experts


__all__ = ["SwarmflowExpertEntry", "derive_swarmflow_expert_specs"]
