# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""External-agent access surface for agent teams.

This package lets an agent that lives outside the team process — a
third-party CLI (claudecode / codex / openclaw / hermes ...) or an
independent service — act as a first-class team member by talking
directly to the shared team database and messager.

Public surface:
    TeamJoinDescriptor / TEAM_JOIN_ENV — the connection descriptor a team
        hands to an external agent (db + transport + identity).
    ExternalTeamClient — opens db + messager from a descriptor and exposes
        the collaboration operations (send / view / claim / ... + inbox).
    openjiuwen.harness_protocol — provider-neutral Python SPI for third-party
        agent harnesses; ``openjiuwen.harness_providers`` implements it for
        DeepAgent, Claude Code, Codex and DSH.
    ExternalHarnessMemberRuntime — composes ``HarnessIOAdapter`` with the team
        session, reliability and observability layers so any protocol harness
        acts as an AgentTeam MemberRuntime (Claude Code / Codex members use it).
"""

from openjiuwen.agent_teams.external.client import ExternalTeamClient
from openjiuwen.agent_teams.external.descriptor import (
    TEAM_JOIN_ENV,
    TeamJoinDescriptor,
)
from openjiuwen.agent_teams.external.member_runtime import ExternalHarnessMemberRuntime

__all__ = [
    "TEAM_JOIN_ENV",
    "ExternalTeamClient",
    "ExternalHarnessMemberRuntime",
    "TeamJoinDescriptor",
]
