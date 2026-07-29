# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Semantic convention constants for trajectory traces.

This module intentionally lives under ``agent_evolving.trajectory`` instead of
the optional telemetry extension package. Trajectory trace data is durable
evolution data and must not depend on optional runtime telemetry boundaries.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Schema / scope constants
# ---------------------------------------------------------------------------

TRAJECTORY_SCHEMA_VERSION = "0.2"
TRAJECTORY_SCOPE_NAME = "openjiuwen.agent_evolving.trajectory"


# ---------------------------------------------------------------------------
# GenAI standard attributes
# ---------------------------------------------------------------------------

GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_SYSTEM_INSTRUCTIONS = "gen_ai.system_instructions"
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"
GEN_AI_TOOL_DEFINITIONS = "gen_ai.tool.definitions"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
GEN_AI_TOOL_CALL_RESULT = "gen_ai.tool.call.result"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"


# ---------------------------------------------------------------------------
# openjiuwen.agent.* custom attributes
# ---------------------------------------------------------------------------

OJ_AGENT_INVOKE_TYPE = "openjiuwen.agent.invoke_type"
OJ_AGENT_NAME = "openjiuwen.agent.name"
OJ_AGENT_INPUTS = "openjiuwen.agent.inputs"
OJ_AGENT_OUTPUTS = "openjiuwen.agent.outputs"


# ---------------------------------------------------------------------------
# openjiuwen.workflow.* custom attributes
# ---------------------------------------------------------------------------

OJ_WORKFLOW_ID = "openjiuwen.workflow.id"
OJ_WORKFLOW_NAME = "openjiuwen.workflow.name"
OJ_WORKFLOW_VERSION = "openjiuwen.workflow.version"
OJ_WORKFLOW_COMPONENT_ID = "openjiuwen.workflow.component.id"
OJ_WORKFLOW_COMPONENT_TYPE = "openjiuwen.workflow.component.type"
OJ_WORKFLOW_COMPONENT_NAME = "openjiuwen.workflow.component.name"
OJ_WORKFLOW_INPUTS = "openjiuwen.workflow.inputs"
OJ_WORKFLOW_OUTPUTS = "openjiuwen.workflow.outputs"


# ---------------------------------------------------------------------------
# openjiuwen.* base trace/span attributes
# ---------------------------------------------------------------------------

OJ_SESSION_ID = "openjiuwen.session_id"
OLD_OJ_SESSION_ID = "openjiuwen.session.id"
OJ_TRACE_ID = "openjiuwen.trace.id"
OJ_INVOKE_ID = "openjiuwen.invoke_id"
OJ_PARENT_INVOKE_ID = "openjiuwen.parent_invoke_id"
OJ_PARENT_NODE_ID = "openjiuwen.parent_node_id"
OJ_SOURCE_IDS = "openjiuwen.source_ids"
OJ_STATUS = "openjiuwen.status"
OJ_ERROR = "openjiuwen.error"
OJ_INNER_ERROR = "openjiuwen.inner_error"
OJ_CHILD_INVOKE_IDS = "openjiuwen.child_invoke_ids"
OJ_META_DATA = "openjiuwen.meta_data"


# ---------------------------------------------------------------------------
# openjiuwen.trajectory.* trace attributes
# ---------------------------------------------------------------------------

TRAJECTORY_ID = "openjiuwen.trajectory_id"
OLD_TRAJECTORY_ID = "openjiuwen.trajectory.id"
TRAJECTORY_SCHEMA_VERSION_ATTR = "openjiuwen.trajectory.schema_version"
TRAJECTORY_SOURCE = "openjiuwen.trajectory.source"
TRAJECTORY_END_REASON = "openjiuwen.trajectory.end_reason"
TRAJECTORY_PARENT_ID = "openjiuwen.trajectory.parent_id"
TRAJECTORY_TASK_HASH = "openjiuwen.trajectory.task_hash"
TRAJECTORY_INCOMPLETE = "openjiuwen.trajectory.incomplete"
TRAJECTORY_INVOKE_TYPE = "openjiuwen.trajectory.invoke_type"
TRAJECTORY_STEP_KIND = "openjiuwen.trajectory.step.kind"
TRAJECTORY_TRACE_ID = "openjiuwen.trajectory.trace_id"


# ---------------------------------------------------------------------------
# openjiuwen.team/member and RL attributes
# ---------------------------------------------------------------------------

OJ_TEAM_ID = "openjiuwen.team.id"
OJ_TEAM_NAME = "openjiuwen.team.name"
OJ_MEMBER_ID = "openjiuwen.member.id"
OJ_MEMBER_NAME = "openjiuwen.member.name"
OJ_MEMBER_ROLE = "openjiuwen.member.role"

OJ_RL_FINAL_REWARD = "openjiuwen.rl.final_reward"
OJ_RL_REWARD_SOURCE = "openjiuwen.rl.reward_source"
OJ_RL_ROLLOUT_ID = "openjiuwen.rl.rollout_id"
OJ_RL_ATTEMPT_SEQ = "openjiuwen.rl.attempt_seq"
OJ_RL_REWARD = "openjiuwen.rl.reward"
OJ_RL_PROMPT_TOKEN_IDS = "openjiuwen.rl.prompt_token_ids"
OJ_RL_COMPLETION_TOKEN_IDS = "openjiuwen.rl.completion_token_ids"
OJ_RL_LOGPROBS = "openjiuwen.rl.logprobs"
OJ_RL_TOKEN_SOURCE = "openjiuwen.rl.token_source"


# ---------------------------------------------------------------------------
# openjiuwen.legacy.* compatibility projection attributes
# ---------------------------------------------------------------------------

CASE_ID = "case_id"
LEGACY_OPERATOR_ID = "openjiuwen.legacy.operator_id"
LEGACY_PARENT_LLM_CALL = "openjiuwen.legacy.parent_llm_call"
LEGACY_STEP_META = "openjiuwen.legacy.step.meta"
