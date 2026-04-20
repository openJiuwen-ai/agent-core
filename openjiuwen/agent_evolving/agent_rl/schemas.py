# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Unified Pydantic data models for the RL training pipeline.

All modules import data structures from here, eliminating circular
dependencies between the trainer and rollout layers.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


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
    """

    turn_id: Optional[int] = None
    input_prompt: Optional[Dict[str, Any]] = None
    output_response: Optional[Dict[str, Any]] = None
    llm_config: Optional[Dict[str, Any]] = None


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


def trajectory_to_rollouts(trajectory: Any) -> List[Rollout]:
    """Convert a Trajectory (from agent_evolving.trajectory) to a list of Rollout objects.

    Extracts LLM steps from the Trajectory, mapping each TrajectoryStep
    with kind='llm' and an LLMCallDetail to a Rollout compatible with
    the RL training pipeline.

    Args:
        trajectory: A Trajectory object from agent_evolving.trajectory.types.

    Returns:
        List of Rollout objects, one per LLM turn.
    """
    rollouts: List[Rollout] = []
    for step in trajectory.steps:
        if step.kind != "llm":
            continue
        detail = step.detail
        if detail is None:
            continue

        # EvolutionRail stores ``response`` / message list entries as Pydantic
        # models (e.g. AssistantMessage); Rollout and tokenization need dicts.
        raw_messages = detail.messages if hasattr(detail, "messages") else []
        if not isinstance(raw_messages, list):
            raw_messages = []
        messages_norm: List[Any] = []
        for m in raw_messages:
            if isinstance(m, dict):
                messages_norm.append(m)
                continue
            dump = getattr(m, "model_dump", None)
            if callable(dump):
                try:
                    messages_norm.append(dump())
                except Exception:
                    messages_norm.append(m)
            else:
                messages_norm.append(m)

        raw_tools = detail.tools if hasattr(detail, "tools") else None
        tools_norm: Optional[List[Any]] = None
        if isinstance(raw_tools, list):
            tools_norm = []
            for t in raw_tools:
                if isinstance(t, dict):
                    tools_norm.append(t)
                    continue
                dump = getattr(t, "model_dump", None)
                if callable(dump):
                    try:
                        tools_norm.append(dump())
                    except Exception:
                        tools_norm.append(t)
                else:
                    tools_norm.append(t)

        raw_resp = detail.response if hasattr(detail, "response") else None
        if raw_resp is None:
            output_response = None
        elif isinstance(raw_resp, dict):
            output_response = raw_resp
        else:
            dump = getattr(raw_resp, "model_dump", None)
            if callable(dump):
                try:
                    dumped = dump()
                    if isinstance(dumped, dict):
                        output_response = dumped
                    elif isinstance(dumped, str):
                        output_response = {"role": "assistant", "content": dumped}
                    else:
                        output_response = {
                            "role": "assistant",
                            "content": str(dumped),
                        }
                except Exception:
                    output_response = {
                        "role": "assistant",
                        "content": str(raw_resp),
                    }
            else:
                output_response = {
                    "role": getattr(raw_resp, "role", None) or "assistant",
                    "content": getattr(raw_resp, "content", "") or "",
                }

        input_prompt: Dict[str, Any] = {
            "message": messages_norm,
            "tools": tools_norm,
        }
        llm_config = None
        if hasattr(step, "meta") and step.meta:
            llm_config = step.meta.get("llm_config")

        rollouts.append(Rollout(
            turn_id=len(rollouts),
            input_prompt=input_prompt,
            output_response=output_response,
            llm_config=llm_config,
        ))

    return rollouts
