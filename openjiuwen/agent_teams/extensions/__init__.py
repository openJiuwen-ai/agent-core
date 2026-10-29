# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team extension infrastructure: Ops queries, resolvers, and the bus channel.

This package hosts the team-side building blocks of the team extension
mechanism. The host registers its extension bus and the team Ops
namespace; per-event contracts (monitor notifications, policy builds)
arrive in follow-up changes on top of this base.
"""

from openjiuwen.agent_teams.extensions.ops import (
    MessagePage,
    TaskDependencyInfo,
    TeamReadView,
    TeamResourceResolver,
    bind_team_scope,
    current_team_name,
)
from openjiuwen.agent_teams.extensions.resolver import (
    BackendTeamReadView,
    GlobalRunnerPoolResolver,
    RuntimePoolResolver,
)

__all__ = [
    "BackendTeamReadView",
    "GlobalRunnerPoolResolver",
    "MessagePage",
    "RuntimePoolResolver",
    "TaskDependencyInfo",
    "TeamReadView",
    "TeamResourceResolver",
    "bind_team_scope",
    "current_team_name",
]
