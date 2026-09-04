# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Metric parity checks against SkillOpt reference behavior."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_skillopt_evaluator():
    skillopt_path = Path(r"D:\work\SkillOpt\skillopt\envs\searchqa\evaluator.py")
    if not skillopt_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("skillopt_searchqa_evaluator", skillopt_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compare_searchqa_evaluator(fixtures: List[Dict[str, Any]]) -> Dict[str, Any]:
    """L1 parity: compare agent-core vs SkillOpt SearchQA evaluator."""
    from openjiuwen.agent_evolving.skill_train.envs.searchqa import evaluator as jiuwen_eval

    skillopt_eval = _load_skillopt_evaluator()
    if skillopt_eval is None:
        return {"passed": True, "skipped": True, "reason": "SkillOpt reference not available"}

    mismatches: List[Dict[str, Any]] = []
    for item in fixtures:
        prediction = item["prediction"]
        gold = item["gold"]
        j = jiuwen_eval.evaluate(prediction, gold)
        s = skillopt_eval.evaluate(prediction, gold)
        for key in ("em", "f1", "sub_em", "predicted_answer"):
            if j.get(key) != s.get(key):
                mismatches.append({"id": item.get("id"), "key": key, "jiuwen": j.get(key), "skillopt": s.get(key)})

    return {
        "passed": not mismatches,
        "skipped": False,
        "total": len(fixtures),
        "mismatches": mismatches,
    }


def run_parity_report(level: str, fixtures_path: str | Path) -> Dict[str, Any]:
    fixtures = json.loads(Path(fixtures_path).read_text(encoding="utf-8"))
    if level == "evaluator":
        return compare_searchqa_evaluator(fixtures)
    raise ValueError(f"Unsupported parity level: {level}")
