# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Location-independent identity of a task and its trusted grading inputs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from openjiuwen.rsi.harness_rsi.data_loader.case_files import file_fingerprint, task_input
from openjiuwen.rsi.harness_rsi.data_loader.grading_contract import normalize_grading_case


def case_identity(case: dict[str, Any]) -> str:
    """Identify grading inputs independently of candidate and batch directories."""
    case = normalize_grading_case(case)
    payload = {
        "case_id": case.get("case_id"), "input": task_input(case),
        "reference": case.get("reference"), "judge_rubrics": case.get("judge_rubrics"),
        "files": file_fingerprint(case),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str,
                                     allow_nan=False).encode("utf-8")).hexdigest()
