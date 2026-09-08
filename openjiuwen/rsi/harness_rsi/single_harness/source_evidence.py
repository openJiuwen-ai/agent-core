# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Match persisted evaluations and build batch-local evidence without model calls."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.harness_rsi.artifact_io import _io_path
from openjiuwen.rsi.harness_rsi.evaluator.judger.base import _is_execution_only_evaluation
from openjiuwen.rsi.harness_rsi.evaluator.metrics_collector import MetricsCollector
from openjiuwen.rsi.harness_rsi.member_optimizer.loader import EvalRef, resolve_candidate_roles

_IGNORED = {".git", "__pycache__", ".pytest_cache"}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def read_mapping(path: str | Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _material_identity(value: Any, base: Path, seen: frozenset[Path] = frozenset()) -> Any:
    """Hash referenced config/assets too, not just a stable filename.

    Expanded credentials only enter the digest, never the persisted manifest.
    Cycles in referenced configuration files terminate at the file hash.
    """
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: _material_identity(item, base, seen) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_material_identity(item, base, seen) for item in value]
    if not isinstance(value, str) or not value:
        return value
    expanded = re.sub(r"\$\{(\w+)}", lambda match: os.environ.get(match[1], match[0]), value)
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = base / path
    try:
        if _io_path(path).is_file():
            path = path.resolve()
            content = _io_path(path).read_bytes()
            identity = {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}
            if path.suffix.lower() in {".yaml", ".yml", ".json"} and path not in seen:
                identity["references"] = _material_identity(read_mapping(path), path.parent, seen | {path})
            return identity
    except (OSError, ValueError):
        # Text prompts and remote/container paths are not local file references.
        return expanded
    return expanded


def _harness_identity(raw_path: str) -> Any:
    path = Path(raw_path)
    if not path.is_dir():
        return _material_identity(raw_path, Path.cwd())
    # Only the immutable package is recursive, not model workspace/output dirs.
    files = {}
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(path)
        if _IGNORED.intersection(relative.parts) or item.suffix in {".pyc", ".pyo"}:
            continue
        files[relative.as_posix()] = _material_identity(str(item), path)
    return {
        "path": str(path.resolve()),
        "files": files,
    }


def evaluation_context(*, harness_refs_path: str, evaluator_config: Any, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Identify the executed Harness and evaluation inputs, excluding promotion bookkeeping."""
    roles = resolve_candidate_roles(harness_refs_path, EvalRef.from_dict({}))
    harnesses = {role.role: role.harness_ref_path for role in roles}
    complete = bool(harnesses) and all(Path(path).exists() for path in harnesses.values())
    runtime = {
        key: value for key, value in os.environ.items() if key.startswith(("EVOBENCH_", "SWEBENCH_", "RSI_EVOBENCH_"))
    }
    return {
        "version": 1,
        "signature": _digest(
            {
                "harnesses": {role: _harness_identity(path) for role, path in harnesses.items()},
                "evaluator": _material_identity(evaluator_config, Path.cwd()),
                "runtime": runtime,
            }
        )
        if complete
        else "",
        "cases": {
            str(case["case_id"]): _digest(
                _material_identity(
                    case, Path(case["case_path"]).resolve().parent if case.get("case_path") else Path.cwd()
                )
            )
            for case in cases
        },
    }


def stamp_evaluation(path: str, context: dict[str, Any]) -> None:
    payload = read_mapping(path)
    payload["evaluation_context"] = context
    target = Path(path)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(target)


def matching_cases(paths: list[str], context: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    """Prefer the newest matching checkpoint; incomplete/infra results are not evidence."""
    selected: dict[str, tuple[str, dict[str, Any]]] = {}
    seen: set[str] = set()
    if not context["signature"]:
        return selected
    for path in reversed(paths):
        payload = read_mapping(path)
        recorded = payload.get("evaluation_context", {})
        if not isinstance(recorded, dict) or recorded.get("signature") != context["signature"]:
            continue
        for case in payload.get("cases", []):
            if not isinstance(case, dict):
                continue
            case_id = str(case.get("case_id", ""))
            if case_id in seen or case_id not in context["cases"]:
                continue
            if recorded.get("cases", {}).get(case_id) != context["cases"][case_id]:
                continue
            seen.add(case_id)
            metadata = case.get("metadata") or {}
            score = case.get("score")
            if case.get("status") not in {"passed", "failed"} or metadata.get("infrastructure_skip"):
                continue
            if not isinstance(score, (int, float)) or not math.isfinite(score):
                continue
            if not all(str(case.get(key, "")) and Path(case[key]).is_file() for key in ("result_path", "trace_path")):
                continue
            try:
                result = json.loads(Path(case["result_path"]).read_text(encoding="utf-8"))
                trace = json.loads(Path(case["trace_path"]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(result, dict) or not isinstance(trace, dict):
                continue
            if result.get("status") in {"error", "skipped"} or result.get("execution_status") == "error":
                continue
            evaluation = result.get("evaluation")
            if isinstance(evaluation, dict) and _is_execution_only_evaluation(evaluation):
                continue
            selected[case_id] = (path, case)
    return selected


async def materialize_source(
    *,
    cases: list[dict[str, Any]],
    selected: dict[str, tuple[str, dict[str, Any]]],
    output_dir: Path,
    harness_refs_path: str,
    context: dict[str, Any],
    reused_case_ids: set[str],
) -> str:
    """Copy only this batch's evidence; never expose neighboring cases to Analyzer."""
    case_refs = []
    provenance: dict[str, list[str]] = {}
    for index, case in enumerate(cases, 1):
        case_id = str(case["case_id"])
        origin, ref = selected[case_id]
        source = Path(ref["result_path"]).parent
        target = output_dir / "cases" / f"c{index:03d}"
        shutil.copytree(_io_path(source), _io_path(target), dirs_exist_ok=True)
        copied = dict(ref)
        for key in ("result_path", "trace_path", "case_path"):
            raw_path = str(ref.get(key) or "")
            if not raw_path:
                continue
            path = Path(raw_path)
            if path.is_relative_to(source):
                copied[key] = str(target / path.relative_to(source))
        case_refs.append(copied)
        provenance.setdefault(origin, []).append(case_id)
    summary_path = await MetricsCollector().collect(str(output_dir / "cases"), str(output_dir / "summary.json"))
    first = read_mapping(next(iter(provenance)))
    # Do not carry full-evaluation metrics or usage into a new batch view.
    payload = {
        "eval_id": f"source-{_digest(str(output_dir))[:16]}",
        "harness_refs_path": harness_refs_path,
        "team_skill_ref_path": "",
        "dataset": first.get("dataset", {}),
        "eval_dir": str(output_dir),
        "case_results_dir": str(output_dir / "cases"),
        "case_traces_dir": str(output_dir / "cases"),
        "summary_path": summary_path,
        "cases": case_refs,
        "evaluation_context": context,
        "source_evidence": {
            "reused_case_ids": sorted(reused_case_ids),
            "evaluated_case_ids": [
                str(case["case_id"]) for case in cases if str(case["case_id"]) not in reused_case_ids
            ],
            "evaluations": [{"eval_ref_path": path, "case_ids": ids} for path, ids in provenance.items()],
        },
    }
    output = output_dir / "eval_ref.yaml"
    temporary = output.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(output)
    return str(output)
