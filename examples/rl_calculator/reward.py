"""
Reward function for the calculator scenario.

1.0 for correct answer, 0.0 otherwise.
"""

import math
import random
import re

from openjiuwen.core.common.logging import logger
from openjiuwen.agent_evolving.agent_rl.schemas import RolloutMessage


def _try_parse_number(s: str) -> float:
    s = s.strip().replace("_", "").replace(",", "")
    return float(s)


def _extract_answer(content: str) -> str:
    """Extract the answer from ``### ANSWER: <value>`` pattern."""
    m = re.search(
        r"###\s*ANSWER:\s*(.*?)(?:<\|im_end\|>|###|$)",
        content,
        re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    return ""


def _results_match(pred: str, truth: str, rel_tol: float = 1e-2) -> bool:
    pred = pred.strip()
    truth = truth.strip()
    if not pred or not truth:
        return False
    if pred.lower() == truth.lower():
        return True
    try:
        pred_f = _try_parse_number(pred)
        truth_f = _try_parse_number(truth)
        return math.isclose(pred_f, truth_f, rel_tol=rel_tol)
    except (ValueError, ZeroDivisionError):
        pass
    return False


def calc_reward(msg: RolloutMessage) -> dict:
    """1.0 for correct answer, 0.0 otherwise."""
    if not msg.rollout_info:
        return {"reward_list": [], "global_reward": 0.0}

    first_turn = msg.rollout_info[0]
    ground_truth = (first_turn.input_prompt or {}).get("ground_truth", "")

    last_turn = msg.rollout_info[-1]
    response = last_turn.output_response or {}
    content = response.get("content", "")

    global_reward = 0.0
    answer = ""
    matched = False
    if ground_truth:
        answer = _extract_answer(content)
        matched = _results_match(answer, ground_truth)
        if matched:
            global_reward = 1.0

    reward_list = [global_reward] * len(msg.rollout_info)

    if random.random() < 0.1:
        logger.info(
            "[REWARD] task=%s  turns=%d  ground_truth='%s'  answer='%s'  "
            "matched=%s  global=%.1f",
            msg.task_id,
            len(msg.rollout_info),
            ground_truth,
            answer,
            matched,
            global_reward,
        )

    return {"reward_list": reward_list, "global_reward": global_reward}
