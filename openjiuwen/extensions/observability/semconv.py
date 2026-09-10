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
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_AGENT_ID = "gen_ai.agent.id"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_AGENT_VERSION = "gen_ai.agent.version"
GEN_AI_AGENT_DESCRIPTION = "gen_ai.agent.description"
GEN_AI_SYSTEM_INSTRUCTIONS = "gen_ai.system_instructions"
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"

# Id of the LLM request this span belongs to, stamped so a trace can be read
# back against the framework's own correlation key when a span looks wrong.
GEN_AI_REQUEST_ID = "gen_ai.request.id"

GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_MESSAGE_COUNT = "gen_ai.request.message_count"
GEN_AI_REQUEST_STREAM = "gen_ai.request.stream"

# Per-member prev-message-count stored on the team span to make prompt
# delta tracking survive across iterations (each iteration opens/closes its
# own agent span, so a count stored there is lost). Keyed by agent_id
# (``{team}_{member}``), i.e. ``gen_ai.request.prev_message_count.<agent_id>``.
# Distinct prefix from the standard ``gen_ai.request.message_count`` to avoid
# collision with the per-span display count.
GEN_AI_REQUEST_MESSAGE_COUNT_PREFIX = "gen_ai.request.prev_message_count."

GEN_AI_USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
GEN_AI_USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
GEN_AI_USAGE_CACHE_TOKENS = "gen_ai.usage.cache_tokens"
GEN_AI_USAGE_REASONING_TOKENS = "gen_ai.usage.reasoning_tokens"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS = "gen_ai.usage.cache_read.input_tokens"
GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS = "gen_ai.usage.cache_creation.input_tokens"
GEN_AI_USAGE_REASONING_OUTPUT_TOKENS = "gen_ai.usage.reasoning.output_tokens"
GEN_AI_RESPONSE_FINISH_REASON = "gen_ai.response.finish_reason"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_TTFT_MS = "gen_ai.response.time_to_first_token_ms"
GEN_AI_RESPONSE_TTFC = "gen_ai.response.time_to_first_chunk"
GEN_AI_REASONING_DURATION_MS = "gen_ai.reasoning.duration_ms"
# Why a reasoning span carries no duration. Reasoning time is measured from the
# stream, so a non-streaming call has none to report — the attribute says so
# rather than leaving a bare zero-length span to read as instant thinking.
GEN_AI_REASONING_TIMING = "gen_ai.reasoning.timing"
REASONING_TIMING_UNMEASURED = "unmeasured: non-streaming call"

# Standard OpenLLMetry / GenAI keys
GEN_AI_PROMPT = "gen_ai.prompt"
GEN_AI_COMPLETION = "gen_ai.completion"
GEN_AI_TOOL_DEFINITIONS = "gen_ai.tool.definitions"

GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_INPUT = "gen_ai.tool.input"
GEN_AI_TOOL_OUTPUT = "gen_ai.tool.output"
GEN_AI_TOOL_ID = "gen_ai.tool.id"
GEN_AI_TOOL_CALLS = "gen_ai.tool_calls"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
GEN_AI_TOOL_CALL_RESULT = "gen_ai.tool.call.result"
GEN_AI_TOOL_TYPE = "gen_ai.tool.type"
GEN_AI_TOOL_DESCRIPTION = "gen_ai.tool.description"


# ---------------------------------------------------------------------------
# openjiuwen.* — additive trajectory and correlation attributes
# ---------------------------------------------------------------------------

OJ_TRACE_ROOT = "openjiuwen.trace.root"
OJ_TRACE_SCHEMA_VERSION = "openjiuwen.trace.schema_version"
OJ_TRACE_COMPLETE = "openjiuwen.trace.complete"
OJ_TRACE_FORCED_CLOSE = "openjiuwen.trace.forced_close"
OJ_SPAN_FORCED_CLOSE = "openjiuwen.span.forced_close"
OJ_SPAN_FORCED_CLOSE_REASON = "openjiuwen.span.forced_close.reason"
OJ_SESSION_ID = "openjiuwen.session.id"
OJ_REQUEST_ID = "openjiuwen.request.id"
OJ_RUN_ID = "openjiuwen.run.id"
OJ_TURN_ID = "openjiuwen.turn.id"
OJ_TURN_NUMBER = "openjiuwen.turn.number"
OJ_STEP_ID = "openjiuwen.step.id"
OJ_STEP_NUMBER = "openjiuwen.step.number"
OJ_INFERENCE_ID = "openjiuwen.inference.id"
OJ_REQUEST_NUMBER = "openjiuwen.request.number"
OJ_REQUEST_PURPOSE = "openjiuwen.request.purpose"
OJ_CONTEXT_OPERATION_ID = "openjiuwen.context.operation.id"
OJ_TRAJECTORY_RECORD_KIND = "openjiuwen.trajectory.record.kind"
OJ_TRAJECTORY_SCHEMA_VERSION = "openjiuwen.trajectory.schema_version"
OJ_TRAJECTORY_EVENT_ID = "openjiuwen.trajectory.event_id"
OJ_TRAJECTORY_EVENT_KIND = "openjiuwen.trajectory.event_kind"
OJ_TRAJECTORY_SUBJECT_ID = "openjiuwen.trajectory.subject_id"
OJ_TRAJECTORY_SEQUENCE_EPOCH = "openjiuwen.trajectory.sequence_epoch"
OJ_TRAJECTORY_SUBJECT_SEQUENCE = "openjiuwen.trajectory.subject_sequence"
OJ_TRAJECTORY_SESSION_ID = "openjiuwen.trajectory.session_id"
OJ_TRAJECTORY_TURN_ID = "openjiuwen.trajectory.turn_id"
OJ_TRAJECTORY_STEP_ID = "openjiuwen.trajectory.step_id"
OJ_TRAJECTORY_REQUEST_ID = "openjiuwen.trajectory.request_id"
OJ_TRAJECTORY_RECORDED_AT_UNIX_NANO = "openjiuwen.trajectory.recorded_at_unix_nano"
OJ_TRAJECTORY_PAYLOAD = "openjiuwen.trajectory.payload"
OJ_AGENT_MODE = "openjiuwen.agent.mode"
OJ_EXECUTION_SUBJECT_ID = "openjiuwen.execution.subject.id"
OJ_EXECUTION_SUBJECT_DISPLAY_NAME = "openjiuwen.execution.subject.display_name"
OJ_EXECUTION_SUBJECT_KIND = "openjiuwen.execution.subject.kind"
OJ_EXECUTION_SUBJECT_PARENT_ID = "openjiuwen.execution.subject.parent_id"
OJ_EXECUTION_SUBJECT_SESSION_ID = "openjiuwen.execution.subject.session_id"
OJ_EXECUTION_SUBJECT_REQUEST_NUMBER = "openjiuwen.execution.subject.request.number"

OJ_GEN_AI_USAGE_INPUT_COST = "openjiuwen.gen_ai.usage.input_cost"
OJ_GEN_AI_USAGE_OUTPUT_COST = "openjiuwen.gen_ai.usage.output_cost"
OJ_GEN_AI_USAGE_TOTAL_COST = "openjiuwen.gen_ai.usage.total_cost"
OJ_GEN_AI_RESPONSE_TOTAL_LATENCY_MS = "openjiuwen.gen_ai.response.total_latency_ms"
OJ_GEN_AI_RESPONSE_TPOT_MS = "openjiuwen.gen_ai.response.tpot_ms"
OJ_GEN_AI_RESPONSE_PROMPT_TOKEN_IDS = "openjiuwen.gen_ai.response.prompt_token_ids"
OJ_GEN_AI_RESPONSE_COMPLETION_TOKEN_IDS = "openjiuwen.gen_ai.response.completion_token_ids"
OJ_GEN_AI_RESPONSE_LOGPROBS = "openjiuwen.gen_ai.response.logprobs"
OJ_GEN_AI_RESPONSE_PARSER_RESULT = "openjiuwen.gen_ai.response.parser_result"
OJ_GEN_AI_RESPONSE_PROVIDER_METADATA = "openjiuwen.gen_ai.response.provider_metadata"
OJ_GEN_AI_RESPONSE_PROVIDER_CONTENT = "openjiuwen.gen_ai.response.provider_content"
OJ_GEN_AI_INPUT_MESSAGE_PROVENANCE = "openjiuwen.gen_ai.input.message_provenance"

OJ_EVENT_SEQUENCE = "openjiuwen.event.sequence"
OJ_TEAM_ID = "openjiuwen.team.id"
OJ_TEAM_NAME = "openjiuwen.team.name"
OJ_TEAM_SESSION_ID = "openjiuwen.team.session.id"
OJ_STREAM_KIND = "openjiuwen.stream.kind"
OJ_STREAM_TEXT = "openjiuwen.stream.text"
OJ_STREAM_TOOL_CALL_ID = "openjiuwen.stream.tool_call.id"
OJ_STREAM_TOOL_CALL_NAME = "openjiuwen.stream.tool_call.name"
OJ_STREAM_TOOL_CALL_ARGUMENTS_DELTA = "openjiuwen.stream.tool_call.arguments_delta"

OJ_TOOL_RESOURCE_ID = "openjiuwen.tool.resource_id"
OJ_TOOL_TYPE = "openjiuwen.tool.type"
OJ_TOOL_AUTHORITATIVE = "openjiuwen.tool.authoritative"

ERROR_TYPE = "error.type"

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

# Identifies which agent an agent-tier span belongs to, without assuming the
# agent is a team member. The rail reads it back off a leftover span to tell an
# own orphan from another agent's span inherited through a ContextVar snapshot,
# so it must stay independent of the team-only ``agentteam.*`` namespace.
DA_AGENT_NAME = "deepagent.agent.name"


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
