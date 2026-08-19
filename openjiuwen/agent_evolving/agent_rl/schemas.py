# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Unified Pydantic data models for the RL training pipeline.

All modules import data structures from here, eliminating circular
dependencies between the trainer and rollout layers.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    decode_json_attribute,
    iter_spans,
    read_llm_exchange,
    read_rl_fields,
    span_attributes,
)
from openjiuwen.agent_evolving.trajectory.team import span_category
from openjiuwen.extensions.observability import semconv


# ---------------------------------------------------------------------------
# Runtime-side models (trajectory representation)
# ---------------------------------------------------------------------------


class Rollout(BaseModel):
    """
    Single-turn dialogue rollout.

    Format compatible with jiuwen_rl v1:
    - input_prompt["message"]: input message list (OpenAI message format)
    - input_prompt["tools"]:   tool definition list
    - output_response:         LLM output message (content or tool_calls)

    When the LLM service returns token IDs (e.g. vLLM ``return_token_ids``),
    they are stored in ``input_prompt_ids`` / ``output_response_ids`` so the
    training encoder can skip local re-tokenisation.  Both fields are ``None``
    for trajectories collected without that capability.
    """

    turn_id: Optional[int] = None
    input_prompt: Optional[Dict[str, Any]] = None
    output_response: Optional[Dict[str, Any]] = None
    llm_config: Optional[Dict[str, Any]] = None
    input_prompt_ids: Optional[List[int]] = None
    """Prompt token IDs returned by the LLM service (e.g. vLLM return_token_ids).
    None when token IDs were not requested or not available."""
    output_response_ids: Optional[List[int]] = None
    """Completion token IDs returned by the LLM service.
    None when token IDs were not requested or not available."""


class RolloutMessage(BaseModel):
    """
    Complete execution result for a single task, aggregating
    multiple turns and the associated rewards.
    """

    task_id: Optional[str] = None
    origin_task_id: Optional[str] = None
    rollout_id: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    rollout_info: List[Rollout] = []
    reward_list: List[float] = []
    global_reward: Optional[float] = None
    turn_count: int = 0
    round_num: Optional[int] = None


# ---------------------------------------------------------------------------
# Training-side models
# ---------------------------------------------------------------------------


class RLTask(BaseModel):
    """Minimal training task unit."""

    task_id: str
    origin_task_id: str
    task_sample: Dict[str, Any] = {}
    round_num: int = 0


class RolloutWithReward(BaseModel):
    """
    Standard MDP data unit used by the training framework.

    Represents one (input, output, reward) triple at token level
    after tokenisation.
    """

    turn_id: Optional[int] = None
    task_id: Optional[str] = None
    rollout_id: Optional[str] = None

    input_prompt_ids: List[int]
    output_response_ids: List[int]

    reward: Optional[float] = None
    n_turns: Optional[int] = None

    # Per-token loss mask for whole-trajectory mode.
    # 1 = model-generated token (participates in loss),
    # 0 = environment token (excluded from loss).
    loss_mask: Optional[List[int]] = None


# ---------------------------------------------------------------------------
# Trajectory → Rollout adapter
# ---------------------------------------------------------------------------


def trajectory_to_rollouts(trajectory: Trajectory) -> List[Rollout]:
    """Convert an OTLP-first Trajectory to a list of Rollout objects.

    Extracts canonical ``llm.call`` spans and maps their prompt/completion
    projections to a Rollout compatible with the RL training pipeline.

    Args:
        trajectory: An OTLP-first Trajectory from agent_evolving.trajectory.

    Returns:
        List of Rollout objects, one per LLM turn.
    """
    rollouts: List[Rollout] = []
    for span in iter_spans(trajectory):
        if span_category(span) != "llm":
            continue
        prompt_messages, completion_messages = read_llm_exchange(span)
        attrs = span_attributes(span)
        tools_norm = decode_json_attribute(attrs.get(semconv.GEN_AI_TOOL_DEFINITIONS))
        if tools_norm is not None and not isinstance(tools_norm, list):
            tools_norm = None
        output_response = completion_messages[-1] if completion_messages else None
        input_prompt: Dict[str, Any] = {
            "message": prompt_messages,
            "tools": tools_norm,
        }
        llm_config = decode_json_attribute(attrs.get("llm_config")) if "llm_config" in attrs else None
        rl_fields = read_rl_fields(span)
        prompt_ids = rl_fields.get("prompt_token_ids")
        completion_ids = rl_fields.get("completion_token_ids")

        rollouts.append(
            Rollout(
                turn_id=len(rollouts),
                input_prompt=input_prompt,
                output_response=output_response,
                llm_config=llm_config,
                input_prompt_ids=prompt_ids,
                output_response_ids=completion_ids,
            )
        )

    return rollouts
