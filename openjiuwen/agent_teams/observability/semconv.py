# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Semantic convention constants for OpenTelemetry attributes.

Standard LLM attributes follow OpenLLMetry / GenAI semantic conventions
(`gen_ai.*`). Team collaboration attributes use the project-specific
`agentteam.*` namespace; DeepAgent task-loop attributes use `deepagent.*`.

Keeping all attribute keys here avoids typo drift between handlers.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# OpenLLMetry / GenAI standard attributes
# ---------------------------------------------------------------------------

GEN_AI_SYSTEM = "gen_ai.system"

GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"

GEN_AI_PROMPT = "gen_ai.prompt"
GEN_AI_COMPLETION = "gen_ai.completion"

GEN_AI_USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
GEN_AI_USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
GEN_AI_RESPONSE_FINISH_REASON = "gen_ai.response.finish_reason"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_TTFT_MS = "gen_ai.response.time_to_first_token_ms"

GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_INPUT = "gen_ai.tool.input"
GEN_AI_TOOL_OUTPUT = "gen_ai.tool.output"


# ---------------------------------------------------------------------------
# agentteam.* — Team-level collaboration attributes (Monitor handler)
# ---------------------------------------------------------------------------

AT_TEAM_NAME = "agentteam.team.name"
AT_TEAM_DISPLAY_NAME = "agentteam.team.display_name"
AT_EVENT_TYPE = "agentteam.event_type"

AT_AGENT_ID = "agentteam.agent.id"
AT_AGENT_ROLE = "agentteam.agent.role"
AT_AGENT_INPUT = "agentteam.agent.input"
AT_AGENT_OUTPUT = "agentteam.agent.output"

AT_MEMBER_NAME = "agentteam.member.name"
AT_MEMBER_STATUS_OLD = "agentteam.member.status.old"
AT_MEMBER_STATUS_NEW = "agentteam.member.status.new"
AT_MEMBER_RESTART_REASON = "agentteam.member.restart_reason"
AT_MEMBER_RESTART_COUNT = "agentteam.member.restart_count"
AT_MEMBER_SHUTDOWN_FORCE = "agentteam.member.shutdown_force"

AT_MESSAGE_ID = "agentteam.message.id"
AT_MESSAGE_FROM = "agentteam.message.from"
AT_MESSAGE_TO = "agentteam.message.to"
AT_MESSAGE_BROADCAST = "agentteam.message.broadcast"

AT_TASK_ID = "agentteam.task.id"
AT_TASK_STATUS = "agentteam.task.status"
AT_TASK_ASSIGNEE = "agentteam.task.assignee"


# ---------------------------------------------------------------------------
# deepagent.* — DeepAgent task-loop attributes (Rail)
# ---------------------------------------------------------------------------

DA_TASK_ITERATION = "deepagent.task.iteration"
DA_TASK_IS_FOLLOW_UP = "deepagent.task.is_follow_up"
