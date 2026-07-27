# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Scheduled-dispatch runtime (F_62) — the leader-side decision engine.

Sits parallel to ``agent/coordination``: coordination wakes agents and never
decides; this package decides (start tasks, dispatch reviews, settle votes,
escalate) and never touches another member's round directly — every handoff
is a leader-identity mailbox message.
"""

from openjiuwen.agent_teams.agent.scheduling.scheduler import SchedulerHost, TeamScheduler

__all__ = ["SchedulerHost", "TeamScheduler"]
