# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Semantic keys retained for read-only historical trajectory conversion.

Current producers do not emit these keys. They remain isolated here so stored
legacy records can be converted to the canonical observability span format.
"""

from __future__ import annotations


LEGACY_GEN_AI_TOOL_CALLS = "gen_ai.tool_calls"
LEGACY_GEN_AI_PROMPT = "gen_ai.prompt"
LEGACY_GEN_AI_COMPLETION = "gen_ai.completion"
LEGACY_GEN_AI_TOOL_ID = "gen_ai.tool.id"
LEGACY_GEN_AI_TOOL_INPUT = "gen_ai.tool.input"
LEGACY_GEN_AI_TOOL_OUTPUT = "gen_ai.tool.output"
LEGACY_GEN_AI_USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
LEGACY_GEN_AI_USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"
LEGACY_GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
LEGACY_TRAJECTORY_STEP_KIND = "openjiuwen.trajectory.step.kind"
LEGACY_STEP_META = "openjiuwen.legacy.step.meta"


__all__ = [name for name in globals() if name.startswith("LEGACY_")]
