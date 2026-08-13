# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Semantic keys retained for read-only historical trajectory conversion.

Current producers do not emit these keys. They remain isolated here so stored
legacy records can be converted to the canonical observability span format.
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
