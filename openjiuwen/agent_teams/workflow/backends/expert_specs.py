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


class BoundGroupExpertLoader:
    """Loads members of the team's bound AgentGroup as swarmflow experts on demand.

    The expert table only holds bound members the host assembled into the
    team spec. A host may leave them out — a scheduled team bound to a group
    starts with an empty roster and spawns experts as tasks need them — so an
    ``agent_template`` pick that misses the table is loaded here through the
    host catalog and the core AgentGroup loader, assembled on the worker base
    spec exactly as ``derive_swarmflow_expert_specs`` assembles a table entry.
    """

    def __init__(self, *, catalog: Any, group: str, worker_base_spec: Any) -> None:
        """Bind the loader to one bound group.

        Args:
            catalog: The host ``ExpertCatalog``.
            group: The bound AgentGroup's name (``TeamAgentSpec.agent_group_name``).
            worker_base_spec: The swarmflow worker base spec (teammate spec plus
                observability rails), the base every expert worker derives from.
        """
        self._catalog = catalog
        self._worker_base_spec = worker_base_spec
        self.group = group

    async def load(self, member: str) -> SwarmflowExpertEntry | None:
        """Assemble one member as an expert entry.

        Returns:
            The entry, or ``None`` when ``member`` is the group's leader or not
            one of its members.

        Raises:
            ValueError: The group is not installed or cannot be loaded, or the
                member declares a runtime core cannot map.
        """
        from openjiuwen.agent_teams.experts.agent_group import LEADER_MEMBER, aload_agent_group_package
        from openjiuwen.agent_teams.experts.template_member import compose_template_member
        from openjiuwen.agent_teams.external.manifest import external_cli_agent_spec_from_template
        from openjiuwen.harness.resources import read_agent_template_card

        if member == LEADER_MEMBER:
            return None
        location = await self._catalog.locate_group(self.group)
        package = await aload_agent_group_package(location.path)
        template = package.templates.get(member)
        if template is None:
            return None
        base = self._worker_base_spec
        card = await read_agent_template_card(package.package_dir / "agents" / member / "manifest.json")
        display_name = card.display_label(base.language) or template.agent_card.name or member
        composed = compose_template_member(base, template)
        external_cli = external_cli_agent_spec_from_template(template)
        if external_cli is not None:
            return SwarmflowExpertEntry(
                member_name=member,
                display_name=display_name,
                persona=composed.persona,
                spec=None,
                external_cli=external_cli,
            )
        return SwarmflowExpertEntry(
            member_name=member,
            display_name=display_name,
            persona=composed.persona,
            spec=composed.spec.model_copy(update={"tools": []}),
            external_cli=None,
        )


def bound_group_expert_loader(context: Any, worker_base_spec: Any) -> BoundGroupExpertLoader | None:
    """The on-demand loader for the leader's bound AgentGroup, or ``None``.

    Present only when the host injected an expert catalog (the same signal
    the leader's expert tools ride), the team is bound to a group and a worker
    base spec exists to assemble on.
    """
    from openjiuwen.agent_teams.rails.team_context import get_expert_binding, get_expert_catalog

    catalog = get_expert_catalog(context)
    binding = get_expert_binding(context)
    bound = binding.bound_group if binding is not None else None
    if catalog is None or bound is None or worker_base_spec is None:
        return None
    return BoundGroupExpertLoader(catalog=catalog, group=bound.name, worker_base_spec=worker_base_spec)


__all__ = [
    "BoundGroupExpertLoader",
    "SwarmflowExpertEntry",
    "bound_group_expert_loader",
    "derive_swarmflow_expert_specs",
]
