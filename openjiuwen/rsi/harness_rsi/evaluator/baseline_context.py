# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Immutable, case-specific baseline evidence supplied exclusively to the judge."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.evaluator.task_identity import case_identity


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _evidence_digest(case_dir: Path) -> str:
    files = [case_dir / "result.json"]
    artifacts = case_dir / "artifacts"
    if artifacts.is_dir():
        files.extend(sorted(path for path in artifacts.rglob("*") if path.is_file()))
    trace = case_dir / "judge" / "normalized_trace.json"
    if trace.is_file():
        files.append(trace)
    identities = []
    for path in files:
        if not path.resolve().is_relative_to(case_dir.resolve()):
            raise EvaluationInfrastructureError("Baseline evidence escapes its case directory")
        if not path.is_file():
            raise EvaluationInfrastructureError(f"Baseline evidence is missing: {path.name}")
        identities.append((path.relative_to(case_dir).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return _digest(identities)


@dataclass(frozen=True, slots=True)
class BaselineJudgeContext:
    """Bind a baseline grade and its actual deliverables to one task identity."""

    case_id: str
    eval_ref_path: str
    result_path: str
    task_identity: str
    evidence_digest: str

    @property
    def fingerprint(self) -> str:
        """Include provenance and bytes in every candidate cache identity."""
        return _digest({"case_id": self.case_id, "eval_ref": self.eval_ref_path,
                        "task": self.task_identity, "evidence": self.evidence_digest,
                        "protocol": "baseline_reference_v1"})

    def validate(self, case: dict[str, Any]) -> dict[str, Any]:
        """Fail closed if the frozen evidence or grading inputs have changed."""
        if str(case.get("case_id")) != self.case_id or case_identity(case) != self.task_identity:
            raise EvaluationInfrastructureError("Baseline task or rubric does not match the candidate")
        path = Path(self.result_path)
        if _evidence_digest(path.parent) != self.evidence_digest:
            raise EvaluationInfrastructureError("Frozen baseline evidence changed or is missing")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("case_id") != self.case_id:
            raise EvaluationInfrastructureError("Baseline result belongs to a different case")
        artifacts = path.parent / "artifacts"
        for name in data.get("artifacts", {}).get("harvested", []):
            evidence = (artifacts / str(name)).resolve()
            if not evidence.is_relative_to(artifacts.resolve()) or not evidence.is_file():
                raise EvaluationInfrastructureError("A recorded baseline deliverable is missing")
        metadata = data.get("evaluation", {}).get("metadata", {})
        if metadata.get("score_mode") == "continuous" and data.get("evaluation", {}).get("method") == "llm_as_judge":
            judge_name = Path(metadata.get("judge_dir", "")).name
            manifest_path = path.parent / "judge" / judge_name / "artifact_manifest.json"
            if not manifest_path.is_file():
                raise EvaluationInfrastructureError("Baseline is missing its original judged-artifact manifest")
            expected = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", {})
            actual = {
                item.relative_to(artifacts).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
                for item in artifacts.rglob("*") if item.is_file()
            }
            if expected != actual:
                raise EvaluationInfrastructureError("Baseline deliverables differ from the evidence originally judged")
        score = data.get("score")
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(score) or not 0 <= score <= 1
                or data.get("status") not in {"passed", "failed"}
                or data.get("execution_status") == "error"):
            raise EvaluationInfrastructureError("Baseline does not contain a valid correctness grade")
        return data


def baseline_contexts(eval_ref_path: str, cases: list[dict[str, Any]]) -> dict[str, BaselineJudgeContext]:
    """Create frozen contexts from the full evaluation underlying comparison B."""
    source = Path(eval_ref_path).expanduser().resolve()
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise EvaluationInfrastructureError("Baseline evaluation is not a mapping")
    rows = payload.get("cases", [])
    index = {str(row["case_id"]): row for row in rows}
    if len(index) != len(rows):
        raise EvaluationInfrastructureError("Baseline contains duplicate case identities")
    result = {}
    for case in cases:
        case_id = str(case["case_id"])
        if case_id not in index:
            raise EvaluationInfrastructureError(f"Baseline does not cover case {case_id}")
        path = Path(index[case_id]["result_path"])
        path = (source.parent / path).resolve() if not path.is_absolute() else path.resolve()
        context = BaselineJudgeContext(case_id, str(source), str(path), case_identity(case), _evidence_digest(path.parent))
        context.validate(case)
        result[case_id] = context
    return result
