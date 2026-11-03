# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team rules delivery routing for agent teams (F_130).

A per-member supervisor decides HOW a new mailbox message reaches a busy (or
idle) member: interrupt and replan, steer, append, deliver now, or record only.
It never changes what the message says or what the member does with it.

``TeamRulesConfig`` is the user-facing entry point (hang it off
``TeamAgentSpec.team_rules``). The router, scheduler and handlers are wired by the
coordination dispatcher at assembly time.
"""

from openjiuwen.agent_teams.team_rules.config import TeamRulesConfig

__all__ = ["TeamRulesConfig"]
