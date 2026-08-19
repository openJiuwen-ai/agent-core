# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trajectory-owned OTLP envelope and RL attribute names.

Observability fields (GenAI, tool and Team context attributes) intentionally
remain owned by :mod:`openjiuwen.extensions.observability.semconv`.  This
module only owns fields that identify a trajectory or are added by the
evolution/RL projection.
"""

from __future__ import annotations

# These values mirror the Team observability semantic conventions.  Keep the
# data-only trajectory schema independent from the agent_teams runtime import
# graph; alignment is protected by a focused unit test.
MEMBER_ID = "agentteam.member.id"
SESSION_ID = "agentteam.session.id"
TEAM_ID = "agentteam.team.id"

# The schema version is intentionally unchanged during the S_004 migration.
TRAJECTORY_SCHEMA_VERSION = "0.2"
TRAJECTORY_SCOPE_NAME = "openjiuwen.agent_evolving.trajectory"

TRAJECTORY_ID = "openjiuwen.trajectory_id"
TRAJECTORY_SCHEMA_VERSION_ATTR = "openjiuwen.trajectory.schema_version"
TRAJECTORY_SOURCE = "openjiuwen.trajectory.source"
CASE_ID = "case_id"

# RL enrichment fields are trajectory-owned and are not produced by
# extensions.observability.  Keep the public names aligned with S_004.
RL_PROMPT_TOKEN_IDS = "evolution.rl.prompt_token_ids"
RL_COMPLETION_TOKEN_IDS = "evolution.rl.completion_token_ids"
RL_LOGPROBS = "evolution.rl.logprobs"
RL_REWARD = "evolution.rl.reward"
RL_FINAL_REWARD = "evolution.rl.final_reward"
RL_REWARD_SOURCE = "evolution.rl.reward_source"
RL_ROLLOUT_ID = "evolution.rl.rollout_id"
RL_ATTEMPT_SEQ = "evolution.rl.attempt_seq"
RL_TOKEN_SOURCE = "evolution.rl.token_source"

# Short descriptive names used by trajectory projectors.
PROMPT_TOKEN_IDS = RL_PROMPT_TOKEN_IDS
COMPLETION_TOKEN_IDS = RL_COMPLETION_TOKEN_IDS
LOGPROBS = RL_LOGPROBS

__all__ = [
    "CASE_ID",
    "COMPLETION_TOKEN_IDS",
    "LOGPROBS",
    "MEMBER_ID",
    "PROMPT_TOKEN_IDS",
    "RL_ATTEMPT_SEQ",
    "RL_COMPLETION_TOKEN_IDS",
    "RL_FINAL_REWARD",
    "RL_LOGPROBS",
    "RL_PROMPT_TOKEN_IDS",
    "RL_REWARD",
    "RL_REWARD_SOURCE",
    "RL_ROLLOUT_ID",
    "RL_TOKEN_SOURCE",
    "SESSION_ID",
    "TEAM_ID",
    "TRAJECTORY_ID",
    "TRAJECTORY_SCHEMA_VERSION",
    "TRAJECTORY_SCHEMA_VERSION_ATTR",
    "TRAJECTORY_SCOPE_NAME",
    "TRAJECTORY_SOURCE",
]
