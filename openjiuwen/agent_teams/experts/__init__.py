# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Experts for Teams: locating protocol, AgentGroup packages, template → member assembly.

* ``catalog`` — ``ExpertCatalog``: the host answers which expert packages are
  installed and where (never what they contain);
* ``agent_group`` — the AgentGroup package format: loader (sync / async) and card;
* ``template_member`` — the single implementation turning an AgentTemplate into
  a Team member (persona + capability snapshot);
* ``bound_group`` — the AgentGroup a team was assembled from, read from its spec.
"""

from openjiuwen.agent_teams.experts.agent_group import (
    LEADER_MEMBER,
    AgentGroupCard,
    AgentGroupMemberCard,
    LoadedAgentGroupPackage,
    aload_agent_group_package,
    load_agent_group_card,
    group_capabilities,
    load_agent_group_package,
    load_agent_group_package_bundle,
    read_agent_group_card,
)
from openjiuwen.agent_teams.experts.bound_group import BoundAgentGroup, BoundGroupMember, bound_agent_group
from openjiuwen.agent_teams.experts.catalog import (
    ExpertCatalog,
    ExpertRef,
    PackageLocation,
    get_process_expert_catalog,
    set_process_expert_catalog,
)
from openjiuwen.agent_teams.experts.template_member import (
    TemplateMember,
    apply_template_snapshot,
    compose_template_member,
    render_template_persona,
    template_skill_names,
    template_snapshot,
)

__all__ = [
    "LEADER_MEMBER",
    "AgentGroupCard",
    "AgentGroupMemberCard",
    "BoundAgentGroup",
    "BoundGroupMember",
    "ExpertCatalog",
    "ExpertRef",
    "LoadedAgentGroupPackage",
    "PackageLocation",
    "TemplateMember",
    "aload_agent_group_package",
    "load_agent_group_card",
    "apply_template_snapshot",
    "bound_agent_group",
    "compose_template_member",
    "get_process_expert_catalog",
    "group_capabilities",
    "load_agent_group_package",
    "load_agent_group_package_bundle",
    "read_agent_group_card",
    "render_template_persona",
    "set_process_expert_catalog",
    "template_skill_names",
    "template_snapshot",
]
