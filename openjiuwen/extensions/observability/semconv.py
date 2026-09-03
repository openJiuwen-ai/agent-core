# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OpenJiuwen semantic-convention facade for observability attributes.

Standard GenAI attributes are re-exported from the replaceable generated
``gen_ai_semconv`` module. Team collaboration attributes use the project-specific
``agentteam.*`` namespace; DeepAgent task-loop attributes use ``deepagent.*``.

Only standard GenAI/core attributes and ``openjiuwen.*`` / ``agentteam.*``
project extensions live here. Any backend-specific projection (the Langfuse
namespace included) belongs exclusively to the exporter adapters under
``exporters/``.

Keeping project-owned attribute keys here avoids typo drift between handlers.
"""

from __future__ import annotations

from openjiuwen.extensions.observability.gen_ai_semconv import *  # noqa: F403


# ---------------------------------------------------------------------------
# openjiuwen.* — additive trajectory and correlation attributes
# ---------------------------------------------------------------------------

OJ_TRACE_ROOT = "openjiuwen.trace.root"
OJ_TRACE_SCHEMA_VERSION = "openjiuwen.trace.schema_version"
OJ_TRACE_COMPLETE = "openjiuwen.trace.complete"
OJ_TRACE_FORCED_CLOSE = "openjiuwen.trace.forced_close"
OJ_SPAN_FORCED_CLOSE = "openjiuwen.span.forced_close"
OJ_SPAN_FORCED_CLOSE_REASON = "openjiuwen.span.forced_close.reason"
# Backend-neutral span input/output for records that have no dedicated
# standard carrier (team/task/event/agent-tier spans). Kept as JSON strings
# and governed by the same redaction and truncation policies as prompts.
OJ_SPAN_INPUT = "openjiuwen.span.input"
OJ_SPAN_OUTPUT = "openjiuwen.span.output"
OJ_SESSION_ID = "openjiuwen.session.id"
OJ_REQUEST_ID = "openjiuwen.request.id"
OJ_REQUEST_MESSAGE_COUNT = "openjiuwen.request.message_count"
OJ_REQUEST_PREVIOUS_MESSAGE_COUNT_PREFIX = "openjiuwen.request.previous_message_count."
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
OJ_GEN_AI_REASONING_DURATION_MS = "openjiuwen.gen_ai.reasoning.duration_ms"
OJ_GEN_AI_REASONING_TIMING = "openjiuwen.gen_ai.reasoning.timing"
OJ_GEN_AI_REASONING_TIMING_UNMEASURED = "unmeasured: non-streaming call"

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
OJ_TOOL_PROTOCOL = "openjiuwen.tool.protocol"
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
