# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent Team evaluator facade."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.config import EvaluatorConfig
from openjiuwen.rsi.evaluator.case_backend import (
    build_backend,
)
from openjiuwen.rsi.evaluator.case_runner import CaseRunner
from openjiuwen.rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.evaluator.judger import build_judger
from openjiuwen.rsi.evaluator.metrics_collector import MetricsCollector
from openjiuwen.rsi.schema import (
    DatasetArtifact,
    EvaluationCaseTraceRef,
)

CASE_RESULTS_DIR_NAME = "cases"
_EVALUATION_INPUT_FILE = "evaluation_input.json"
_TRANSIENT_ERROR_MARKERS = (
    "connection error",
    "connection reset",
    "connection refused",
    "connection aborted",
    "connectionerror",
    "remoteprotocolerror",
    "peer closed connection",
    "incomplete chunked read",
    "server disconnected",
    "remotedisconnected",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "temporary failure in name resolution",
    "could not resolve host",
    "service unavailable",
    "rate limit",
    "too many requests",
    "http 429",
    "http 502",
    "http 503",
    "http 504",
)


class TeamEvaluator:
    """Evaluate a Team Skill plus member harness set against a dataset."""

    def __init__(self, config: EvaluatorConfig) -> None:
        self.config = config
        self.metrics_collector = MetricsCollector()
        self.case_runner = CaseRunner(
            backend=build_backend(config=config),
            judger=build_judger(config),
        )

    async def evaluate(
        self,
        dataset: DatasetArtifact,
        team_skill_ref_path: str,
        harness_refs_path: str,
        output_dir: str,
    ) -> str:
        """Evaluate all cases from a dataset artifact and return ``eval_ref.yaml``."""
        return await self.evaluate_batch(
            cases=_load_dataset_cases(dataset.dataset_files),
            team_skill_ref_path=team_skill_ref_path,
            harness_refs_path=harness_refs_path,
            output_dir=output_dir,
            dataset=dataset,
        )

    async def evaluate_batch(  # pylint: disable=huawei-too-many-arguments
        self,
        cases: list[dict[str, Any]],
        team_skill_ref_path: str,
        harness_refs_path: str,
        output_dir: str,
        dataset: DatasetArtifact | None = None,
    ) -> str:
        """Run one batch of cases with fresh Team runtimes and persist artifacts."""
        eval_dir = _prepare_eval_dir(output_dir)
        case_results_dir = eval_dir / CASE_RESULTS_DIR_NAME
        case_refs: list[EvaluationCaseTraceRef] = []

        input_fingerprint = _evaluation_input_fingerprint(
            cases=cases,
            team_skill_ref_path=team_skill_ref_path,
            harness_refs_path=harness_refs_path,
        )
        manifest_path = eval_dir / _EVALUATION_INPUT_FILE
        can_resume_cases = _stored_evaluation_fingerprint(manifest_path) == input_fingerprint
        manifest_path.write_text(
            json.dumps({"fingerprint": input_fingerprint}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        harness_refs = _load_harness_refs(harness_refs_path) if harness_refs_path else {}

        for case_index, case in enumerate(cases, start=1):
            case_id = str(case.get("case_id") or f"case_{case_index:03d}")
            case_output_dir = case_results_dir / _case_dir_name(case_id, case_index)

            if can_resume_cases:
                completed_ref = _load_completed_case_ref(case_output_dir, case_id=case_id)
                if completed_ref is not None:
                    case_refs.append(completed_ref)
                    continue

            retry_history: list[dict[str, Any]] = []
            retry_limit = max(0, int(self.config.transient_case_retry_limit))
            for attempt in range(retry_limit + 1):
                try:
                    case_ref = await self.case_runner.execute(
                        case={**case, "case_id": case_id},
                        output_dir=str(case_output_dir),
                        team_skill_ref_path=team_skill_ref_path,
                        harness_refs=harness_refs,
                    )
                except EvaluationInfrastructureError as exc:
                    transient_error = _transient_transport_error(str(exc))
                    if not transient_error or attempt >= retry_limit:
                        raise
                    retry_history.append(
                        {
                            "attempt": attempt + 1,
                            "error": transient_error,
                        }
                    )
                    shutil.rmtree(case_output_dir, ignore_errors=True)
                    await asyncio.sleep(min(2**attempt, 4))
                    continue
                transient_error = _transient_case_error(case_ref)
                if not transient_error or attempt >= retry_limit:
                    break
                retry_history.append(
                    {
                        "attempt": attempt + 1,
                        "error": transient_error,
                    }
                )
                shutil.rmtree(case_output_dir, ignore_errors=True)
                await asyncio.sleep(min(2**attempt, 4))
            if retry_history:
                history_path = case_results_dir / (f"{case_output_dir.name}_transient_retries.json")
                history_path.write_text(
                    json.dumps(retry_history, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            case_refs.append(case_ref)

        summary_path = await self.metrics_collector.collect(
            str(case_results_dir),
            str(eval_dir / "summary.json"),
        )
        eval_ref_path = _write_eval_ref(
            eval_dir=eval_dir,
            team_name=_evaluation_name(team_skill_ref_path),
            team_skill_ref_path=team_skill_ref_path,
            harness_refs_path=harness_refs_path,
            dataset=dataset,
            case_refs=case_refs,
            summary_path=summary_path,
        )
        return eval_ref_path


__all__ = [
    "TeamEvaluator",
]


def _prepare_eval_dir(output_dir: str) -> Path:
    eval_dir = Path(output_dir).expanduser().resolve()
    eval_dir.mkdir(parents=True, exist_ok=True)
    return eval_dir


def _evaluation_name(skill_ref_path: str) -> str:
    if not str(skill_ref_path or "").strip():
        return "single_harness"
    path = Path(skill_ref_path).expanduser()
    return path.parent.name if path.name.lower() == "skill.md" else path.name


def _evaluation_input_fingerprint(
    *,
    cases: list[dict[str, Any]],
    team_skill_ref_path: str,
    harness_refs_path: str,
) -> str:
    payload = {
        "cases": cases,
        "team_skill": _path_identity(team_skill_ref_path),
        "harness_refs": _path_identity(harness_refs_path),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _path_identity(path_value: str) -> dict[str, str]:
    if not str(path_value or "").strip():
        return {"path": "", "sha256": ""}
    path = Path(path_value).expanduser().resolve()
    digest = ""
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    elif path.is_dir():
        hasher = hashlib.sha256()
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            hasher.update(child.relative_to(path).as_posix().encode("utf-8"))
            hasher.update(child.read_bytes())
        digest = hasher.hexdigest()
    return {"path": str(path), "sha256": digest}


def _stored_evaluation_fingerprint(manifest_path: Path) -> str:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("fingerprint", "")) if isinstance(payload, dict) else ""


def _load_completed_case_ref(
    case_output_dir: Path,
    *,
    case_id: str,
) -> EvaluationCaseTraceRef | None:
    result_path = case_output_dir / "result.json"
    trace_path = case_output_dir / "trace.json"
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict) or not isinstance(trace, dict):
        return None
    if str(result.get("case_id", "")) != case_id or str(trace.get("case_id", "")) != case_id:
        return None
    status = str(result.get("status", ""))
    if not status:
        return None
    evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    return EvaluationCaseTraceRef(
        case_id=case_id,
        case_path=str(metadata.get("case_path", "")),
        trace_path=str(trace_path),
        result_path=str(result_path),
        status=status,
        score=result.get("score"),
        metadata={
            "session_id": str(result.get("session_id", "")),
            "team_name": str(metadata.get("team_name", "")),
            "execution_status": str(result.get("execution_status", "")),
            "evaluation_method": str(evaluation.get("method", "")),
            "evaluation_passed": bool(evaluation.get("passed", False)),
            "resumed": True,
        },
    )


def _transient_case_error(case_ref: EvaluationCaseTraceRef) -> str:
    """Return a retryable transport error, never a semantic test failure."""
    if str(case_ref.status).lower() != "error":
        return ""
    try:
        payload = json.loads(Path(case_ref.result_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    error = str(payload.get("error", "") or "").strip()
    return _transient_transport_error(error)


def _transient_transport_error(error: str) -> str:
    """Return the original error when it describes a retryable transport failure."""
    lowered = error.lower()
    if any(marker in lowered for marker in _TRANSIENT_ERROR_MARKERS):
        return error
    return ""


def _eval_id_from_dir(eval_dir: Path) -> str:
    parts = [part for part in eval_dir.parts[-3:] if part]
    return "_".join(parts)


def _case_dir_name(case_id: str, case_index: int) -> str:
    """Return a short path-safe directory name without changing the business case id."""
    digest = hashlib.sha1(case_id.encode("utf-8")).hexdigest()[:8]
    return f"c{case_index:03d}_{digest}"


def _load_dataset_cases(dataset_files: list[str]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for dataset_file in dataset_files:
        path = Path(dataset_file).expanduser().resolve()
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict) and isinstance(data.get("cases"), list):
            raw_cases = data["cases"]
        elif isinstance(data, dict):
            raw_cases = [data]
        elif isinstance(data, list):
            raw_cases = data
        else:
            raise ValueError(f"dataset json must contain case mappings: {path}")
        for index, case in enumerate(raw_cases, start=1):
            if not isinstance(case, dict):
                raise ValueError(f"dataset case must be a mapping: {path}#{index}")
            cases.append({**case, "case_path": str(path), "case_index": index})
    return cases


def _load_harness_refs(harness_refs_path: str) -> dict[str, str]:
    """Load runtime ``{member_name: harness_path}`` refs from a YAML file.

    The canonical artifact shape is the MemberOptimizer-compatible
    ``{"harness_refs": {role: path}, "roles": [...]}`` mapping.  A legacy
    top-level ``{role: path}`` mapping is still accepted for existing configs.
    """
    path = Path(harness_refs_path).expanduser().resolve()
    with open(path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"harness_refs file must contain a mapping, got {type(data).__name__}: {path}")
    refs = data.get("harness_refs")
    if isinstance(refs, dict):
        return {str(k): str(v) for k, v in refs.items() if isinstance(v, str)}
    return {
        str(k): str(v)
        for k, v in data.items()
        if isinstance(v, str) and k not in {"version", "source_harness_refs_path"}
    }


def _write_eval_ref(
    *,
    eval_dir: Path,
    team_name: str,
    team_skill_ref_path: str,
    harness_refs_path: str,
    dataset: DatasetArtifact | None,
    case_refs: list[EvaluationCaseTraceRef],
    summary_path: str,
) -> str:
    eval_ref_path = eval_dir / "eval_ref.yaml"
    payload = {
        "eval_id": _eval_id_from_dir(eval_dir),
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "team_name": team_name,
        "team_skill_ref_path": team_skill_ref_path,
        "harness_refs_path": harness_refs_path,
        "dataset": (
            {
                "dataset_id": dataset.dataset_id,
                "dataset_dir": dataset.dataset_dir,
                "dataset_files": dataset.dataset_files,
            }
            if dataset is not None
            else None
        ),
        "eval_dir": str(eval_dir),
        "case_results_dir": str(eval_dir / CASE_RESULTS_DIR_NAME),
        "case_traces_dir": str(eval_dir / CASE_RESULTS_DIR_NAME),
        "summary_path": summary_path,
        "cases": [
            {
                "case_id": item.case_id,
                "case_path": item.case_path,
                "trace_path": item.trace_path,
                "result_path": item.result_path,
                "status": item.status,
                "score": item.score,
                "metadata": item.metadata,
            }
            for item in case_refs
        ],
    }
    with open(eval_ref_path, "w", encoding="utf-8") as file:
        yaml.safe_dump(payload, file, allow_unicode=True, sort_keys=False)
    return str(eval_ref_path)


def _load_summary_from_eval_ref(eval_ref_path: str) -> dict[str, Any]:
    with open(eval_ref_path, "r", encoding="utf-8") as file:
        eval_ref = yaml.safe_load(file) or {}
    summary_path = eval_ref.get("summary_path")
    if not summary_path:
        return {}
    with open(summary_path, "r", encoding="utf-8") as file:
        summary = json.load(file)
    if isinstance(summary, dict):
        return summary
    return {}
