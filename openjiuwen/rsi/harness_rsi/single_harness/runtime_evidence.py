# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Separate missing capability observations from demonstrated non-use."""

import json
from pathlib import Path
import yaml


def inconclusive_capability_cases(
    eval_ref_path: str,
    missing_invocations: list[dict[str, str]],
) -> set[str]:
    """Return cases whose negative activation claims lack complete timing."""
    required = {str(item.get("target_case_id", "")) for item in missing_invocations}
    if not required:
        return set()
    ref = Path(eval_ref_path)
    payload = yaml.safe_load(ref.read_text(encoding="utf-8")) or {}
    inconclusive = set()
    for case in payload.get("cases", []):
        case_id = str(case.get("case_id", ""))
        if case_id not in required:
            continue
        result_path = Path(str(case.get("result_path", "")))
        if not result_path.is_absolute():
            result_path = ref.parent / result_path
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            inconclusive.add(case_id)
            continue
        metadata = result.get("metadata", {}) if isinstance(result, dict) else {}
        execution = metadata.get("execution", {}) if isinstance(metadata, dict) else {}
        quality = execution.get("execution_evidence") if isinstance(execution, dict) else None
        if not isinstance(quality, dict) or quality.get("status") != "complete" or quality.get("timing_complete") is not True:
            inconclusive.add(case_id)
    return inconclusive
