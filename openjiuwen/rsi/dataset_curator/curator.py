# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LangSmith-style replay dataset curation from evaluation artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_io import (
    read_json_mapping,
    read_yaml_mapping,
    write_json_mapping,
    write_yaml_mapping,
)
from openjiuwen.rsi.config import DatasetCurationConfig
from openjiuwen.rsi.schema import DatasetCurationArtifact


class DatasetCurator:
    """Mine failed, judgeable evaluation cases into a replay dataset."""

    def __init__(self, config: DatasetCurationConfig) -> None:
        self.config = config

    def curate(self, *, eval_ref_path: str, output_dir: str) -> DatasetCurationArtifact:
        """Create a replay dataset and curation report from an eval_ref artifact."""
        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        eval_ref = read_yaml_mapping(eval_ref_path)
        report_path = output_root / self.config.report_filename
        if not self.config.enabled:
            write_yaml_mapping(
                report_path,
                _report_payload(
                    status="disabled",
                    eval_ref_path=eval_ref_path,
                    accepted_cases=[],
                    rejected_cases=[],
                ),
            )
            return DatasetCurationArtifact(
                status="disabled",
                eval_ref_path=str(Path(eval_ref_path).expanduser().resolve()),
                output_dir=str(output_root),
                report_path=str(report_path),
            )

        accepted_cases: list[dict[str, Any]] = []
        rejected_cases: list[dict[str, Any]] = []
        targeted_tasks: list[dict[str, Any]] = []
        for case_ref in _case_refs(eval_ref):
            original_case = _load_original_case(case_ref)
            decision = self._curate_case(
                eval_ref_path=eval_ref_path,
                case_ref=case_ref,
                original_case=original_case,
            )
            if decision["status"] == "accepted":
                accepted_cases.append(decision["case"])
                targeted_seed = _build_targeted_seed_task(
                    eval_ref_path=eval_ref_path,
                    case_ref=case_ref,
                    original_case=original_case,
                )
                if targeted_seed:
                    targeted_tasks.append(targeted_seed)
            else:
                rejected_cases.append(
                    {
                        "case_id": str(case_ref.get("case_id", "")),
                        "reason": decision["reason"],
                    }
                )

        dataset_file = ""
        if accepted_cases:
            dataset_file = str((output_root / self.config.output_filename).resolve())
            write_json_mapping(
                dataset_file,
                {
                    "dataset_id": output_root.name,
                    "created_at": datetime.now(UTC).astimezone().isoformat(),
                    "source": self.config.source_label,
                    "cases": accepted_cases,
                },
            )
        targeted_seed_file = ""
        if targeted_tasks:
            targeted_seed_file = str((output_root / self.config.targeted_seed_filename).resolve())
            write_json_mapping(
                targeted_seed_file,
                {
                    "dataset_id": f"{output_root.name}_targeted_seed",
                    "created_at": datetime.now(UTC).astimezone().isoformat(),
                    "source": f"{self.config.source_label}_targeted_seed",
                    "source_eval_ref_path": str(Path(eval_ref_path).expanduser().resolve()),
                    "recommended_synthetic_tasks": targeted_tasks,
                },
            )
        write_yaml_mapping(
            report_path,
            _report_payload(
                status="completed",
                eval_ref_path=eval_ref_path,
                accepted_cases=accepted_cases,
                rejected_cases=rejected_cases,
                dataset_file=dataset_file,
                targeted_dataset_seed_file=targeted_seed_file,
            ),
        )
        return DatasetCurationArtifact(
            status="completed",
            eval_ref_path=str(Path(eval_ref_path).expanduser().resolve()),
            output_dir=str(output_root),
            dataset_file=dataset_file,
            targeted_seed_file=targeted_seed_file,
            report_path=str(report_path.resolve()),
            accepted_cases=len(accepted_cases),
            rejected_cases=len(rejected_cases),
        )

    def _curate_case(
        self,
        *,
        eval_ref_path: str,
        case_ref: dict[str, Any],
        original_case: dict[str, Any],
    ) -> dict[str, Any]:
        if not original_case:
            return {"status": "rejected", "reason": "missing_original_case"}
        if _case_inconclusive(case_ref):
            return {"status": "rejected", "reason": "case_result_inconclusive"}
        if not _case_failed(case_ref, self.config.score_threshold):
            return {"status": "rejected", "reason": "case_passed_threshold"}
        if self.config.require_judgeable_reference and not _is_judgeable(original_case):
            return {"status": "rejected", "reason": "missing_judgeable_reference"}

        source_case_id = str(case_ref.get("case_id") or original_case.get("case_id", "case"))
        replay_case = dict(original_case)
        replay_case["case_id"] = f"replay_{source_case_id}"
        metadata = dict(replay_case.get("metadata") or {})
        metadata.update(
            {
                "source": self.config.source_label,
                "synthetic": False,
                "judgeable": _is_judgeable(original_case),
                "provenance": {
                    "source_case_id": source_case_id,
                    "source_eval_ref_path": str(Path(eval_ref_path).expanduser().resolve()),
                    "source_case_path": str(case_ref.get("case_path", "")),
                    "source_case_index": case_ref.get("case_index"),
                    "result_path": str(case_ref.get("result_path", "")),
                    "trace_path": str(case_ref.get("trace_path", "")),
                    "score": case_ref.get("score"),
                    "status": str(case_ref.get("status", "")),
                },
            }
        )
        replay_case["metadata"] = metadata
        replay_case["source"] = self.config.source_label
        return {"status": "accepted", "case": replay_case}


def _case_refs(eval_ref: dict[str, Any]) -> list[dict[str, Any]]:
    cases = eval_ref.get("cases") or []
    if not isinstance(cases, list):
        return []
    return [case for case in cases if isinstance(case, dict)]


def _case_failed(case_ref: dict[str, Any], score_threshold: float) -> bool:
    score = case_ref.get("score")
    if isinstance(score, int | float):
        return float(score) < score_threshold
    result = read_json_mapping(str(case_ref.get("result_path", "")))
    result_score = result.get("score")
    if isinstance(result_score, int | float):
        return float(result_score) < score_threshold
    evaluation = result.get("evaluation")
    if isinstance(evaluation, dict) and "passed" in evaluation:
        return not bool(evaluation.get("passed"))
    return str(case_ref.get("status", "")) != "passed"


def _case_inconclusive(case_ref: dict[str, Any]) -> bool:
    status = str(case_ref.get("status", "") or "").lower()
    result = read_json_mapping(str(case_ref.get("result_path", "")))
    result_status = str(result.get("status", "") or "").lower()
    evaluation = result.get("evaluation")
    evaluation_method = ""
    if isinstance(evaluation, dict):
        evaluation_method = str(evaluation.get("method", "") or "").lower()
    return status == "error" or result_status == "error" or evaluation_method == "error"


def _load_original_case(case_ref: dict[str, Any]) -> dict[str, Any]:
    case_path = str(case_ref.get("case_path", "") or "")
    if not case_path:
        return {}
    path = Path(case_path).expanduser().resolve()
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        cases = data["cases"]
    elif isinstance(data, list):
        cases = data
    elif isinstance(data, dict):
        cases = [data]
    else:
        return {}
    case_id = str(case_ref.get("case_id", "") or "")
    for case in cases:
        if isinstance(case, dict) and str(case.get("case_id", "") or "") == case_id:
            return dict(case)
    case_index = case_ref.get("case_index")
    if isinstance(case_index, int) and 1 <= case_index <= len(cases):
        case = cases[case_index - 1]
        if isinstance(case, dict):
            return dict(case)
    return {}


def _is_judgeable(case: dict[str, Any]) -> bool:
    if case.get("verification_contract") or case.get("evaluation_adapter"):
        return True
    reference = case.get("reference")
    if isinstance(reference, dict):
        if reference.get("answer") not in (None, ""):
            return True
        behaviors = reference.get("required_behaviors")
        if isinstance(behaviors, list) and behaviors:
            return True
    if case.get("expected") not in (None, ""):
        return True
    if case.get("assertions"):
        return True
    if case.get("verifier"):
        return True
    return False


def _build_targeted_seed_task(
    *,
    eval_ref_path: str,
    case_ref: dict[str, Any],
    original_case: dict[str, Any],
) -> dict[str, Any]:
    training_signal = original_case.get("training_signal")
    if not isinstance(training_signal, dict) or not training_signal:
        return {}

    result = read_json_mapping(str(case_ref.get("result_path", "")))
    evaluation = result.get("evaluation")
    if not isinstance(evaluation, dict):
        evaluation = {}
    behavior_results = evaluation.get("behavior_results")
    if not isinstance(behavior_results, list):
        behavior_results = []

    source_case_id = str(case_ref.get("case_id") or original_case.get("case_id", "case"))
    reference = original_case.get("reference") if isinstance(original_case.get("reference"), dict) else {}
    success_criteria = _string_list(reference.get("success_criteria"))
    if not success_criteria:
        success_criteria = [
            str(behavior.get("description", "")).strip()
            for behavior in reference.get("required_behaviors", [])
            if isinstance(behavior, dict) and str(behavior.get("description", "")).strip()
        ]

    expected_failure_modes = _string_list(training_signal.get("expected_failure_modes"))
    capability_gap = str(training_signal.get("capability_gap", "") or "").strip()
    target_capabilities = _string_list(training_signal.get("target_capabilities"))
    root_causes = _root_cause_capabilities(
        behavior_results=behavior_results,
        target_capabilities=target_capabilities,
        capability_gap=capability_gap,
    )
    input_block = original_case.get("input")
    if isinstance(input_block, dict):
        task_pattern = str(input_block.get("user_message", input_block.get("query", "")) or "")
    else:
        task_pattern = str(input_block or original_case.get("query", "") or "")

    return {
        "source_case_id": source_case_id,
        "source_eval_ref_path": str(Path(eval_ref_path).expanduser().resolve()),
        "result_path": str(case_ref.get("result_path", "")),
        "trace_path": str(case_ref.get("trace_path", "")),
        "task_pattern": task_pattern,
        "difficulty_level": _difficulty_level(original_case.get("metadata", {})),
        "target_capabilities": target_capabilities,
        "capability_combination": str(training_signal.get("capability_combination", "") or ""),
        "target_surfaces": _string_list(training_signal.get("target_surfaces")),
        "specific_trap_to_include": (expected_failure_modes[0] if expected_failure_modes else capability_gap),
        "success_criteria": success_criteria,
        "failure_summary": str(
            evaluation.get("reason") or result.get("error") or result.get("status") or "case failed"
        ),
        "trace_evidence": _trace_evidence(str(case_ref.get("trace_path", "") or "")),
        "root_cause_capabilities": root_causes,
        "generation_reason": capability_gap,
    }


def _trace_evidence(trace_path: str) -> dict[str, str]:
    path_text = str(trace_path or "").strip()
    if not path_text:
        return {"trace_path": "", "excerpt": ""}
    path = Path(path_text).expanduser()
    if not path.is_file():
        return {"trace_path": path_text, "excerpt": ""}
    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
        excerpt = json.dumps(payload, ensure_ascii=False)
    except (OSError, json.JSONDecodeError):
        try:
            excerpt = path.read_text(encoding="utf-8")
        except OSError:
            excerpt = ""
    return {
        "trace_path": path_text,
        "excerpt": _bounded_text(excerpt, limit=3000),
    }


def _bounded_text(text: str, *, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _root_cause_capabilities(
    *,
    behavior_results: list[Any],
    target_capabilities: list[str],
    capability_gap: str,
) -> list[dict[str, str]]:
    root_causes: list[dict[str, str]] = []
    for item in behavior_results:
        if not isinstance(item, dict):
            continue
        score = item.get("score")
        if isinstance(score, int | float) and float(score) >= 0.8:
            continue
        missing_capability = str(item.get("missing_capability", "") or "").strip()
        failure_reason = str(item.get("failure_reason", "") or "").strip()
        evidence = str(item.get("evidence", "") or "").strip()
        if missing_capability or failure_reason or evidence:
            root_causes.append(
                {
                    "capability_name": missing_capability
                    or (target_capabilities[0] if target_capabilities else "unknown"),
                    "failure_type": failure_reason or "low_scored_behavior",
                    "evidence_from_trace": evidence,
                    "why_it_caused_failure": failure_reason or capability_gap,
                    "data_needed_to_fix": capability_gap,
                }
            )
    if root_causes:
        return root_causes
    return [
        {
            "capability_name": target_capabilities[0] if target_capabilities else "unknown",
            "failure_type": "case_failed",
            "evidence_from_trace": "",
            "why_it_caused_failure": capability_gap,
            "data_needed_to_fix": capability_gap,
        }
    ]


def _difficulty_level(metadata: Any) -> int:
    difficulty = ""
    if isinstance(metadata, dict):
        difficulty = str(metadata.get("difficulty", "") or "").lower()
    return {"easy": 2, "medium": 3, "hard": 4}.get(difficulty, 3)


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _report_payload(
    *,
    status: str,
    eval_ref_path: str,
    accepted_cases: list[dict[str, Any]],
    rejected_cases: list[dict[str, Any]],
    dataset_file: str = "",
    targeted_dataset_seed_file: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "source_eval_ref_path": str(Path(eval_ref_path).expanduser().resolve()),
        "dataset_file": dataset_file,
        "targeted_dataset_seed_file": targeted_dataset_seed_file,
        "summary": {
            "candidate_cases": len(accepted_cases) + len(rejected_cases),
            "accepted_cases": len(accepted_cases),
            "rejected_cases": len(rejected_cases),
        },
        "accepted_cases": [
            {
                "case_id": case.get("case_id"),
                "source_case_id": ((case.get("metadata") or {}).get("provenance", {}).get("source_case_id", "")),
            }
            for case in accepted_cases
        ],
        "rejected_cases": rejected_cases,
    }


__all__ = [
    "DatasetCurator",
]
