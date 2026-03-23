# -*- coding: UTF-8 -*-
"""
Reward function for the NL2SQL scenario.

1.0 for execution-match with the gold SQL, 0.0 otherwise.
"""

import json
import os
import random
import re
import shutil
import tempfile

from sql_eval import eval_exec_match

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RolloutMessage

SPIDER_DATA_DIR = os.environ.get("SPIDER_DATA_DIR")


def _extract_sql_answer(content: str) -> str:
    """Extract the SQL from ``### ANSWER: <sql> ###`` pattern."""
    m = re.search(
        r"###\s*ANSWER:\s*(.*?)(?:###|<\|im_end\|>|$)",
        content,
        re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    return content.strip()


def _resolve_db_path(db_id: str, db_source: str) -> str:
    if not SPIDER_DATA_DIR:
        return ""
    return os.path.join(SPIDER_DATA_DIR, db_source, db_id, f"{db_id}.sqlite")


def nl2sql_reward(msg: RolloutMessage) -> dict:
    """1.0 if the predicted SQL is denotationally equivalent to gold, else 0.0."""
    if not msg.rollout_info:
        return {"reward_list": [], "global_reward": 0.0}

    first_turn = msg.rollout_info[0]
    ground_truth_raw = (first_turn.input_prompt or {}).get("ground_truth", "")

    try:
        gt = json.loads(ground_truth_raw)
        gold_sql = gt["gold_sql"]
        db_id = gt["db_id"]
        db_source = gt.get("db_source", "database")
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("[NL2SQL_REWARD] Cannot parse ground_truth: %s", ground_truth_raw)
        return {
            "reward_list": [0.0] * len(msg.rollout_info),
            "global_reward": 0.0,
        }

    last_turn = msg.rollout_info[-1]
    content = (last_turn.output_response or {}).get("content", "")
    predicted_sql = _extract_sql_answer(content)

    global_reward = 0.0
    matched = False

    if not SPIDER_DATA_DIR:
        logger.warning("[NL2SQL_REWARD] SPIDER_DATA_DIR is not set; cannot evaluate SQL.")
        return {
            "reward_list": [0.0] * len(msg.rollout_info),
            "global_reward": 0.0,
        }

    db_path = _resolve_db_path(db_id, db_source)
    if os.path.exists(db_path):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = os.path.join(tmp_dir, os.path.basename(db_path))
            shutil.copyfile(db_path, tmp_db)
            try:
                matched = eval_exec_match(
                    db_path=tmp_db,
                    predicted_sql=predicted_sql,
                    gold_sql=gold_sql,
                )
            except Exception as e:
                logger.debug("[NL2SQL_REWARD] eval_exec_match error: %s", e)
                matched = False
    else:
        logger.warning("[NL2SQL_REWARD] DB not found: %s", db_path)

    if matched:
        global_reward = 1.0

    reward_list = [global_reward] * len(msg.rollout_info)

    if random.random() < 0.1:
        logger.info(
            "[NL2SQL_REWARD] task=%s  turns=%d  db=%s  gold='%s'  pred='%s'  "
            "matched=%s  reward=%.1f",
            msg.task_id,
            len(msg.rollout_info),
            db_id,
            gold_sql[:80],
            predicted_sql[:80],
            matched,
            global_reward,
        )

    return {"reward_list": reward_list, "global_reward": global_reward}
