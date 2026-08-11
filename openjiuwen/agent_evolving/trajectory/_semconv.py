# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Import-cycle bridge for the Phase 2 canonical span accessors.

The legacy trajectory root still eagerly imports the evolution package while
the observability package imports the same evolution readers.  Keeping this
small data-only bridge local lets the accessors load during that transition.
Phase 8 removes the bridge once the observability exports are lazy.
"""

from __future__ import annotations

# Values mirror ``agent_teams.observability.semconv``.  They are intentionally
# data-only here; no Team runtime module is imported during package bootstrap.
GEN_AI_TOOL_CALLS = "gen_ai.tool_calls"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_PROMPT = "gen_ai.prompt"
GEN_AI_COMPLETION = "gen_ai.completion"
GEN_AI_TOOL_DEFINITIONS = "gen_ai.tool.definitions"
GEN_AI_USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
GEN_AI_USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_ID = "gen_ai.tool.id"
GEN_AI_TOOL_INPUT = "gen_ai.tool.input"
GEN_AI_TOOL_OUTPUT = "gen_ai.tool.output"

# Temporary read aliases emitted by the migration model retained in Phase 1.
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
GEN_AI_TOOL_CALL_RESULT = "gen_ai.tool.call.result"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
TRAJECTORY_STEP_KIND = "openjiuwen.trajectory.step.kind"
LEGACY_STEP_META = "openjiuwen.legacy.step.meta"

AT_TEAM_ID = "agentteam.team.id"
AT_TEAM_NAME = "agentteam.team.name"
AT_EVENT_TYPE = "agentteam.event_type"
AT_AGENT_ID = "agentteam.agent.id"
AT_AGENT_NAME = "agentteam.agent.name"
AT_SESSION_ID = "agentteam.session.id"
AT_MEMBER_ID = "agentteam.member.id"
AT_MEMBER_NAME = "agentteam.member.name"
AT_TASK_ID = "agentteam.task.id"


__all__ = [name for name in globals() if name.startswith(("GEN_AI_", "AT_"))]
