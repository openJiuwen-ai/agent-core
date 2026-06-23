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
# ---------------------------------------------------------------------------

GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_SYSTEM_VALUE = "openjiuwen"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROMPT = "gen_ai.prompt"
GEN_AI_COMPLETION = "gen_ai.completion"

GEN_AI_USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
GEN_AI_USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"

GEN_AI_TOOL_NAME = "gen_ai.tool.name"


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
