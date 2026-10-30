# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reward stage integration for the attribution pipeline (attribution.reward.v1).

This package is the stage-two integration layer: the adapter that routes to
the standalone Reward scoring engine (``openjiuwen.agent_evolving.reward``),
the legacy Reward deep agent (``ATTRIBUTION_REWARD=agent`` fallback), and the
shared ``attribution.reward.v1`` schema.  The engine itself is not duplicated
here and is unrelated to ``agent_evolving/evaluator`` metric evaluation.
"""

from openjiuwen.agent_evolving.attribution.reward_stage.reward_agent import (
    RewardBuilder,
    RewardSink,
    RewardSubmitRail,
    SubmitRewardTool,
    build_reward_agent,
    load_reward_prompt,
)
from openjiuwen.agent_evolving.attribution.reward_stage.reward_schema import (
    SCHEMA_VERSION,
    RewardResult,
    RewardSchemaError,
    parse_reward_payload,
    reward_to_signals,
    validate_reward_payload,
)

__all__ = [
    "SCHEMA_VERSION",
    "RewardBuilder",
    "RewardResult",
    "RewardSchemaError",
    "RewardSink",
    "RewardSubmitRail",
    "SubmitRewardTool",
    "build_reward_agent",
    "load_reward_prompt",
    "parse_reward_payload",
    "reward_to_signals",
    "validate_reward_payload",
]
