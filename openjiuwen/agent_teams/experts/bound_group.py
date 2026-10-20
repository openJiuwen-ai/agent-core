# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The AgentGroup a team was assembled from, as recorded in its own spec.

When a host assembles a team from an AgentGroup it writes, per non-leader
member, the capability snapshot into ``agents[<member>]`` (or the launch
config into an ``ExternalCliMemberSpec``) and the persona into the predefined
member's ``prompt``. ``spawn_expert`` spawns further instances of such a
member from exactly that data — not from the package on disk — so every
instance matches the predefined one even if the package was upgraded or
removed after the team was built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from openjiuwen.agent_teams.schema.team import ExternalCliAgentSpec


@dataclass(frozen=True)
class BoundGroupMember:
    """One non-leader member of the bound AgentGroup, as assembled into the team."""

    name: str
    display_name: str
    desc: str
    persona: str
    """The predefined member's ``prompt`` (rendered persona)."""
    snapshot: dict[str, Any] | None = None
    """``agents[<member>].agent_template_spec`` of a native member."""
    external_cli: "ExternalCliAgentSpec | None" = None
    """Launch config of an external-runtime member."""


@dataclass(frozen=True)
class BoundAgentGroup:
    """The team's bound AgentGroup: its name and spawnable member templates."""

    name: str
    members: tuple[BoundGroupMember, ...] = ()

    def member(self, name: str) -> BoundGroupMember | None:
        """Template member by name."""
        return next((member for member in self.members if member.name == name), None)

    def member_names(self) -> list[str]:
        """Member names in roster order."""
        return [member.name for member in self.members]


def bound_agent_group(spec: "TeamAgentSpec") -> BoundAgentGroup | None:
    """Read the bound AgentGroup out of a team spec; ``None`` when none is bound.

    A predefined member counts as a template member when it is an external
    CLI member, or an ordinary teammate whose own ``agents[<member>]`` spec
    carries a capability snapshot.
    """
    from openjiuwen.agent_teams.schema.team import ExternalCliMemberSpec, TeamRole

    if not spec.agent_group_name:
        return None
    members: list[BoundGroupMember] = []
    for predefined in spec.predefined_members:
        if isinstance(predefined, ExternalCliMemberSpec):
            members.append(
                BoundGroupMember(
                    name=predefined.member_name,
                    display_name=predefined.display_name,
                    desc=predefined.desc,
                    persona=predefined.prompt,
                    external_cli=predefined.external_cli,
                )
            )
            continue
        agent_spec = spec.agents.get(predefined.member_name)
        if predefined.role_type != TeamRole.TEAMMATE or agent_spec is None or not agent_spec.agent_template_spec:
            continue
        members.append(
            BoundGroupMember(
                name=predefined.member_name,
                display_name=predefined.display_name,
                desc=predefined.desc,
                persona=predefined.prompt,
                snapshot=agent_spec.agent_template_spec,
            )
        )
    return BoundAgentGroup(name=spec.agent_group_name, members=tuple(members))


__all__ = ["BoundAgentGroup", "BoundGroupMember", "bound_agent_group"]
