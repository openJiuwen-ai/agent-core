# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Semantic convention constants for OpenTelemetry attributes.

Standard LLM attributes follow OpenLLMetry / GenAI semantic conventions
(`gen_ai.*`). Team collaboration attributes use the project-specific
`agentteam.*` namespace; DeepAgent task-loop attributes use `deepagent.*`.

Langfuse-specific attributes (`langfuse.*`) are used for fields that
Langfuse's OTel ingestion processor maps to its observation model
(input, output, session_id, trace name, etc.).

Keeping all attribute keys here avoids typo drift between handlers.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# OpenLLMetry / GenAI standard attributes
# ---------------------------------------------------------------------------

GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"

GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_MESSAGE_COUNT = "gen_ai.request.message_count"

GEN_AI_USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
GEN_AI_USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
GEN_AI_RESPONSE_FINISH_REASON = "gen_ai.response.finish_reason"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_TTFT_MS = "gen_ai.response.time_to_first_token_ms"

# Standard OpenLLMetry / GenAI keys
GEN_AI_PROMPT = "gen_ai.prompt"
GEN_AI_COMPLETION = "gen_ai.completion"
GEN_AI_TOOL_DEFINITIONS = "gen_ai.tool.definitions"

GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_INPUT = "gen_ai.tool.input"
GEN_AI_TOOL_OUTPUT = "gen_ai.tool.output"
GEN_AI_TOOL_ID = "gen_ai.tool.id"
GEN_AI_TOOL_CALLS = "gen_ai.tool_calls"


# ---------------------------------------------------------------------------
# agentteam.* — Team-level collaboration attributes (Monitor handler)
# ---------------------------------------------------------------------------

AT_TEAM_ID = "agentteam.team.id"
AT_TEAM_NAME = "agentteam.team.name"
AT_TEAM_DISPLAY_NAME = "agentteam.team.display_name"
AT_TEAM_LEADER = "agentteam.team.leader"
AT_EVENT_TYPE = "agentteam.event_type"

AT_AGENT_ID = "agentteam.agent.id"
AT_AGENT_NAME = "agentteam.agent.name"
AT_AGENT_ROLE = "agentteam.agent.role"
AT_AGENT_INPUT = "agentteam.agent.input"
AT_AGENT_OUTPUT = "agentteam.agent.output"
AT_SESSION_ID = "agentteam.session.id"

AT_MEMBER_ID = "agentteam.member.id"
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

AT_PLAN_APPROVED = "agentteam.plan.approved"
AT_PLAN_SUBMITTED_BY = "agentteam.plan.submitted_by"

# ---------------------------------------------------------------------------
# deepagent.* — DeepAgent task-loop attributes (Rail)
# ---------------------------------------------------------------------------

DA_TASK_ITERATION = "deepagent.task.iteration"
DA_TASK_IS_FOLLOW_UP = "deepagent.task.is_follow_up"
DA_TASK_LOOP_EVENT = "deepagent.task.loop_event"


# ---------------------------------------------------------------------------
# langfuse.* — Langfuse OTel ingestion processor attributes
# ---------------------------------------------------------------------------
# These attribute keys are specifically recognized by Langfuse's
# OTel ingestion processor (both Python SDK and Langfuse backend) to
# populate trace/observation fields that aren't covered by standard
# gen_ai.* or custom agentteam.* attrs.
#
# CRITICAL: The Python SDK's LangfuseOtelSpanAttributes defines the
# canonical key names. Some differ from what one might expect:
#   - "session.id" (NOT "langfuse.session.id")
#   - "langfuse.trace.tags" ✓
#   - "langfuse.observation.input" ✓
#   - "langfuse.observation.output" ✓
#
# See: langfuse.LangfuseOtelSpanAttributes for the full list.

LANGFUSE_TRACE_NAME = "langfuse.trace.name"
LANGFUSE_TRACE_TAGS = "langfuse.trace.tags"
LANGFUSE_SESSION_ID = "session.id"

LANGFUSE_OBSERVATION_INPUT = "langfuse.observation.input"
LANGFUSE_OBSERVATION_OUTPUT = "langfuse.observation.output"
LANGFUSE_OBSERVATION_TYPE = "langfuse.observation.type"

# Langfuse-specific gen_ai mirror keys — avoid collision with standard
# gen_ai.prompt / gen_ai.completion which Langfuse's OTel processor
# maps differently (expects zero-based indices).
LANGFUSE_GEN_AI_PROMPT = "langfuse.gen_ai.prompt"
LANGFUSE_GEN_AI_COMPLETION = "langfuse.gen_ai.completion"
