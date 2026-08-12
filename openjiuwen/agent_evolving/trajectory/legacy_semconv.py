# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Temporary semantic aliases emitted by the legacy trajectory model.

These keys exist only while ``TrajectoryStep`` callers are migrated to the
canonical observability span format. New trajectory producers must use the
current keys from :mod:`openjiuwen.agent_evolving.trajectory.semconv`.
"""

from __future__ import annotations


LEGACY_GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
LEGACY_GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"
LEGACY_GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
LEGACY_GEN_AI_TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
LEGACY_GEN_AI_TOOL_CALL_RESULT = "gen_ai.tool.call.result"
LEGACY_GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
LEGACY_GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
LEGACY_TRAJECTORY_STEP_KIND = "openjiuwen.trajectory.step.kind"
LEGACY_STEP_META = "openjiuwen.legacy.step.meta"


__all__ = [name for name in globals() if name.startswith("LEGACY_")]
