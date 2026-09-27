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

# GenAI keys have one authoritative definition for every instrumentation path.
# Standard keys are re-exported from extensions.observability so the two stacks
# never drift on a standard name; keys the pinned registry does not carry
# (gen_ai.usage.cost.*, gen_ai.request.prev_message_count) are defined below.
from openjiuwen.extensions.observability.semconv import (  # noqa: F401
    ERROR_TYPE,
    GEN_AI_AGENT_ID,
    GEN_AI_AGENT_NAME,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_REQUEST_MAX_TOKENS,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_K,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RETRIEVAL_TOP_K,
    GEN_AI_SYSTEM_INSTRUCTIONS,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_TOOL_TYPE,
    GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
    GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_REASONING_OUTPUT_TOKENS,
)

# ---------------------------------------------------------------------------
# gen_ai.* — request/response facts the pinned GenAI registry does not model
# (custom keys owned by this package; see AGENTS.md before renaming them)
# ---------------------------------------------------------------------------

GEN_AI_REQUEST_PREV_MESSAGE_COUNT = "gen_ai.request.prev_message_count"
GEN_AI_USAGE_COST_TOTAL_USD = "gen_ai.usage.cost.total_usd"
GEN_AI_USAGE_COST_INPUT_USD = "gen_ai.usage.cost.input_usd"
GEN_AI_USAGE_COST_OUTPUT_USD = "gen_ai.usage.cost.output_usd"


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
