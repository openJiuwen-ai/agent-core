# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Semantic convention constants for OpenTelemetry attributes.

Standard LLM attributes follow OpenLLMetry / GenAI semantic conventions
(`gen_ai.*`).  Workflow attributes use the project-specific
`openjiuwen.workflow.*` namespace.  Agent (non-LLM) attributes use
`openjiuwen.agent.*`.

Keeping all attribute keys here avoids typo drift between handlers.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# GenAI standard attributes (aligned with observability/semconv.py)
# LLM payload keys renamed gen_ai.prompt/completion → gen_ai.input.messages/
# gen_ai.output.messages to unify the naming with develop (issue #1833).
# ---------------------------------------------------------------------------

GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_SYSTEM_VALUE = "openjiuwen"
# Standard registry key for the same fact (gen_ai.system predates it and is
# kept as frozen wire format; both carry GEN_AI_SYSTEM_VALUE).
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"

GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"
GEN_AI_REQUEST_TOP_K = "gen_ai.request.top_k"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_STOP_SEQUENCES = "gen_ai.request.stop_sequences"
GEN_AI_REQUEST_REASONING_LEVEL = "gen_ai.request.reasoning.level"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK = "gen_ai.response.time_to_first_chunk"

GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS = "gen_ai.usage.cache_read.input_tokens"
GEN_AI_USAGE_REASONING_OUTPUT_TOKENS = "gen_ai.usage.reasoning.output_tokens"

GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_TYPE = "gen_ai.tool.type"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"

GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_AGENT_ID = "gen_ai.agent.id"
GEN_AI_AGENT_DESCRIPTION = "gen_ai.agent.description"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"

GEN_AI_RETRIEVAL_TOP_K = "gen_ai.retrieval.top_k"
GEN_AI_DATA_SOURCE_ID = "gen_ai.data_source.id"
GEN_AI_EMBEDDINGS_DIMENSION_COUNT = "gen_ai.embeddings.dimension.count"
GEN_AI_MEMORY_RECORD_COUNT = "gen_ai.memory.record.count"

ERROR_TYPE = "error.type"

# Request/response facts the GenAI standard does not model.  These are
# project extensions (项目扩展，非上游标准) and use the openjiuwen.* namespace
# — aligned with the observability extension — never gen_ai.* (issue #1833).
OJ_LLM_PREV_MESSAGE_COUNT = "openjiuwen.llm.prev_message_count"
OJ_GEN_AI_USAGE_TOTAL_COST = "openjiuwen.gen_ai.usage.total_cost"
OJ_GEN_AI_USAGE_INPUT_COST = "openjiuwen.gen_ai.usage.input_cost"
OJ_GEN_AI_USAGE_OUTPUT_COST = "openjiuwen.gen_ai.usage.output_cost"
# Conversation facts carried by the Session: source-metadata user id and the
# raw source metadata dict.
OJ_GEN_AI_USER_ID = "openjiuwen.gen_ai.user.id"
OJ_GEN_AI_METADATA = "openjiuwen.gen_ai.metadata"
# Latency facts the GenAI standard does not model: trace display name,
# inter-token latency, and reasoning duration (毫秒). first-token latency IS
# standard-modeled (gen_ai.response.time_to_first_chunk, above).
OJ_GEN_AI_TRACE_NAME = "openjiuwen.trace.name"
OJ_GEN_AI_RESPONSE_INTER_TOKEN_LATENCY_MS = "openjiuwen.gen_ai.response.inter_token_latency_ms"
OJ_GEN_AI_REASONING_DURATION_MS = "openjiuwen.gen_ai.reasoning.duration_ms"


# ---------------------------------------------------------------------------
# openjiuwen.workflow.* — Workflow-level custom attributes
# ---------------------------------------------------------------------------

OJ_WORKFLOW_ID = "openjiuwen.workflow.id"
OJ_WORKFLOW_NAME = "openjiuwen.workflow.name"
OJ_WORKFLOW_VERSION = "openjiuwen.workflow.version"
OJ_WORKFLOW_COMPONENT_ID = "openjiuwen.workflow.component.id"
OJ_WORKFLOW_COMPONENT_TYPE = "openjiuwen.workflow.component.type"
OJ_WORKFLOW_COMPONENT_NAME = "openjiuwen.workflow.component.name"
OJ_WORKFLOW_EXECUTION_ID = "openjiuwen.workflow.execution_id"
OJ_WORKFLOW_LOOP_NODE_ID = "openjiuwen.workflow.loop.node_id"
OJ_WORKFLOW_LOOP_INDEX = "openjiuwen.workflow.loop.index"


# ---------------------------------------------------------------------------
# openjiuwen.agent.* — Agent-level custom attributes (non-LLM types)
# ---------------------------------------------------------------------------

OJ_AGENT_INVOKE_TYPE = "openjiuwen.agent.invoke_type"
OJ_AGENT_NAME = "openjiuwen.agent.name"
OJ_AGENT_INPUTS = "openjiuwen.agent.inputs"
OJ_AGENT_OUTPUTS = "openjiuwen.agent.outputs"
OJ_AGENT_ERROR_MESSAGE = "openjiuwen.agent.error_message"


# ---------------------------------------------------------------------------
# Trace ID bridge — links OTel trace to tracer UUID
# ---------------------------------------------------------------------------

OJ_TRACE_ID = "openjiuwen.trace.id"
OJ_SESSION_ID = "openjiuwen.session_id"


# ---------------------------------------------------------------------------
# openjiuwen.* — Base Span attributes (shared by both handlers)
# ---------------------------------------------------------------------------

OJ_INVOKE_ID = "openjiuwen.invoke_id"
OJ_PARENT_INVOKE_ID = "openjiuwen.parent_invoke_id"
OJ_START_TIME = "openjiuwen.start_time"
OJ_END_TIME = "openjiuwen.end_time"
OJ_ELAPSED_TIME = "openjiuwen.elapsed_time"
OJ_STATUS = "openjiuwen.status"
OJ_ERROR = "openjiuwen.error"
OJ_CHILD_INVOKE_IDS = "openjiuwen.child_invoke_ids"
OJ_META_DATA = "openjiuwen.meta_data"


# ---------------------------------------------------------------------------
# openjiuwen.* — Workflow-specific base attributes
# ---------------------------------------------------------------------------

OJ_PARENT_NODE_ID = "openjiuwen.parent_node_id"
OJ_SOURCE_IDS = "openjiuwen.source_ids"
OJ_INNER_ERROR = "openjiuwen.inner_error"
OJ_STREAM_INPUTS = "openjiuwen.stream_inputs"
OJ_STREAM_OUTPUTS = "openjiuwen.stream_outputs"
OJ_INTERACTIVE_INPUTS = "openjiuwen.interactive_inputs"
OJ_WORKFLOW_INPUTS = "openjiuwen.workflow.inputs"
OJ_WORKFLOW_OUTPUTS = "openjiuwen.workflow.outputs"
OJ_WORKFLOW_ERROR_MESSAGE = "openjiuwen.workflow.error_message"
OJ_WORKFLOW_INVOKE_DATA = "openjiuwen.workflow.invoke_data"
