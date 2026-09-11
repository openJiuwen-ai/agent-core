# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Project epoch-level Harness versions into the shared RSI event vocabulary."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.events import EventNode, EventProgress
from openjiuwen.rsi.schema import RsiChange, RsiTreeNode
from openjiuwen.rsi.usage import usage_snapshot

_PROVISIONAL_STATUSES = {"provisional"}


def source_reuse_stage_payload(
    *, batch_index: int, total_cases: int, score: float | None, eval_ref_path: str, provenance: dict[str, Any]
) -> dict[str, Any]:
    """Report evidence provenance, not a new evaluation or model usage event."""
    reused = len(provenance["reused_case_ids"])
    return {
        "id": "source.reuse",
        "name": f"Batch {batch_index}: reused {reused}/{total_cases} case results",
        "status": "done",
        "batch_index": batch_index,
        "total_cases": total_cases,
        "reused_case_count": reused,
        "evaluated_case_count": len(provenance["evaluated_case_ids"]),
        "score": score,
        "eval_ref_path": eval_ref_path,
        **provenance,
    }


def case_stage_payload(
    case_index: int,
    total_cases: int,
    status: str,
    *,
    case_id: str | None = None,
    score: float | None = None,
    completed_cases: int | None = None,
) -> dict[str, Any]:
    """Build a real single-harness per-case evaluation stage payload.

    ``status`` mirrors the evaluator's persisted case status: ``running``,
    ``passed``, ``failed``, ``error`` or ``skipped``.  Numeric fields stay
    native in the engine contract so the frontend can render structured
    progress without string parsing.
    """

    label = f"Case {case_index}/{total_cases}"
    normalized_status = status or "running"
    if normalized_status == "passed":
        name = f"{label} passed"
    elif normalized_status == "failed":
        name = f"{label} failed"
    elif normalized_status == "error":
        name = f"{label} error"
    elif normalized_status == "skipped":
        name = f"{label} skipped"
    else:
        normalized_status = "running"
        name = f"{label} evaluating"
    if score is not None and normalized_status in {"passed", "failed"}:
        name = f"{name} · score {_format_case_score(score)}"
    payload: dict[str, Any] = {
        "id": f"evaluate.case.{case_index}",
        "name": name,
        "status": normalized_status,
        "case_index": case_index,
        "total_cases": total_cases,
    }
    if case_id:
        payload["case_id"] = str(case_id)
    if score is not None:
        payload["score"] = score
    if completed_cases is not None:
        payload.update(
            id="evaluate.parallel",
            name=f"Cases {completed_cases}/{total_cases} completed",
            status="done" if completed_cases == total_cases else "running",
            completed_cases=completed_cases,
        )
    return payload


def _format_case_score(score: float) -> str:
    """Format a normalized case score for a short, human-readable stage label."""

    try:
        return f"{float(score):.2f}"
    except (TypeError, ValueError):
        return str(score)


def generate_stage_payload(
    candidate_index: int,
    candidate_count: int,
    status: str,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a real single-harness candidate-generation stage payload."""

    normalized_status = status or "running"
    if normalized_status == "done":
        name = f"Candidate {candidate_index}/{candidate_count} generated"
    elif normalized_status == "error":
        name = f"Candidate {candidate_index}/{candidate_count} generation failed"
    else:
        normalized_status = "running"
        name = f"Generating candidate {candidate_index}/{candidate_count}"
    payload: dict[str, Any] = {
        "id": "generate.candidate",
        "name": name,
        "status": normalized_status,
        "candidate_index": candidate_index,
        "total_candidates": candidate_count,
    }
    if error:
        payload["error"] = str(error)
    return payload


def analysis_stage_payload(
    status: str,
    *,
    failed_case_count: int | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a failure-analysis stage payload for the current tree root.

    The single-Harness control plane performs a possibly long failure-analysis
    pass between baseline evaluation and candidate generation.  Exposing that
    pass as a ``node.stage`` event keeps the web view from appearing idle while
    the analyzer is still running.
    """

    normalized_status = status or "running"
    if normalized_status == "done":
        name = "Failure analysis completed"
    elif normalized_status == "error":
        name = "Failure analysis failed"
    else:
        normalized_status = "running"
        name = "Analyzing failed cases"
    payload: dict[str, Any] = {
        "id": "analyze.failures",
        "name": name,
        "status": normalized_status,
    }
    if failed_case_count is not None:
        payload["failed_case_count"] = int(failed_case_count)
    if error:
        payload["error"] = str(error)
    return payload


def progress_event(
    state: Mapping[str, Any],
    *,
    total_iterations: int,
) -> EventProgress:
    """Count completed epochs; local candidate attempts do not advance progress."""

    return EventProgress(
        iteration=len(_mapping_items(state.get("epoch_checkpoints"))),
        total_iterations=max(0, int(total_iterations)),
        score=_number(state.get("best_score")),
        baseline=_number(state.get("baseline_score")),
        usage=usage_snapshot(state.get("usage")),
    )


def root_node_event(state: Mapping[str, Any]) -> EventNode:
    """Expose the initial Harness without requiring an extra baseline run."""
    return EventNode(
        node=RsiTreeNode(
            node_id="h0",
            iteration=0,
            parent_id=None,
            type="ROOT",
            adopted=True,
            score=_number(state.get("baseline_score")),
            summary="Initial Harness",
            snapshot_artifact_id=None,
            reason=None,
            failure_class=None,
            changes=[],
            extra={"artifact_path": str(state.get("source_harness_refs_path", "") or ""), "iteration_unit": "epoch"},
        ),
        artifacts=harness_artifacts(str(state.get("source_harness_refs_path", "") or "")),
    )


def epoch_node_event(state: Mapping[str, Any], checkpoint: Mapping[str, Any]) -> EventNode:
    """Expose one final Harness per epoch, not each local repair candidate."""
    epoch = int(checkpoint["epoch"])
    selected = str(checkpoint.get("selected_harness_refs_path", "") or "")
    evaluated = str(checkpoint.get("harness_refs_path", "") or "")
    parent_id = _epoch_parent_id(state, checkpoint)
    adopted = bool(checkpoint.get("promotion_applied"))
    running = checkpoint.get("status") == "running"
    rejected = checkpoint.get("status") == "rejected"
    changes = []
    if adopted:
        for candidate in _mapping_items(state.get("candidate_gates")):
            if int(candidate.get("epoch", 0)) == epoch and candidate.get("status") == "accepted":
                changes.extend(_changes(candidate.get("capabilities")))
    # A filtered or rolled-back Harness was not the one in the full replay.
    score = _number(checkpoint.get("score")) if selected and selected == evaluated else None
    return EventNode(
        node=RsiTreeNode(
            node_id=f"epoch-{epoch:03d}",
            iteration=epoch,
            parent_id=parent_id,
            type="RUNNING" if running else "ADOPTED" if adopted else "REJECTED" if rejected else "UNCHANGED",
            adopted=adopted,
            score=score,
            summary=_summary(changes, "Optimizing Harness" if running else "No retained Harness change"),
            snapshot_artifact_id=None,
            reason=None if running or adopted else "No Harness change passed the acceptance checks",
            failure_class=None,
            changes=changes,
            extra={
                "artifact_path": selected,
                "iteration_unit": "epoch",
                "source_evidence": [
                    {
                        "batch_index": batch["batch_index"],
                        "eval_ref_path": batch["source_eval_ref_path"],
                        **batch["source_evidence"],
                    }
                    for batch in (state.get("completed_batches") or {}).values()
                    if int(batch.get("epoch", 0)) == epoch and batch.get("source_evidence")
                ],
            },
        ),
        artifacts=harness_artifacts(selected) if not running else [],
    )


def _epoch_parent_id(state: Mapping[str, Any], checkpoint: Mapping[str, Any]) -> str:
    """Rejected/no-op epochs observe a version; they do not create that version."""
    before = str(checkpoint.get("before_harness_refs_path", "") or "")
    if before and before != str(state.get("source_harness_refs_path", "") or ""):
        for prior in sorted(
            _mapping_items(state.get("epoch_checkpoints")), key=lambda item: int(item["epoch"]), reverse=True
        ):
            if (
                int(prior["epoch"]) < int(checkpoint["epoch"])
                and prior.get("promotion_applied")
                and str(prior.get("selected_harness_refs_path", "") or "") == before
            ):
                return f"epoch-{int(prior['epoch']):03d}"
    return "h0"


def active_epoch_node_event(state: Mapping[str, Any]) -> EventNode | None:
    """Use the same active epoch snapshot for live events and recovery queries."""
    epoch = int(state.get("active_epoch", 0) or 0)
    if not epoch or any(int(item["epoch"]) == epoch for item in _mapping_items(state.get("epoch_checkpoints"))):
        return None
    return epoch_node_event(
        state,
        {
            "epoch": epoch,
            "status": "running",
            "before_harness_refs_path": state.get("active_epoch_before_harness_refs_path", ""),
        },
    )


def harness_artifacts(refs_path: str) -> list[dict[str, str]]:
    """Describe the refs file and its plugin directories, never the whole run."""
    if not refs_path:
        return []
    artifacts = [{"role": "HARNESS_REFS", "path": refs_path, "format": "yaml"}]
    path = Path(refs_path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return artifacts
    if not isinstance(data, dict):
        return artifacts
    refs = data.get("harness_refs", data)
    if isinstance(refs, dict):
        for raw in refs.values():
            if not isinstance(raw, str):
                continue
            package = Path(raw).expanduser()
            if not package.is_absolute():
                package = path.parent / package
            if package.is_dir() and (package / "harness_config.yaml").is_file():
                artifacts.append({"role": "PRIMARY", "path": str(package.resolve()), "format": "dir"})
    return artifacts


def node_event(
    candidate: Mapping[str, Any],
    *,
    iteration: int,
    parent_id: str | None,
) -> EventNode:
    """Build a generic node snapshot from one persisted candidate record."""

    status = str(candidate.get("status", "") or "").strip().lower()
    adopted = bool(candidate.get("accepted")) and status == "accepted"
    node_type = "ADOPTED" if adopted else "PROVISIONAL" if status in _PROVISIONAL_STATUSES else "REJECTED"
    changes = _changes(candidate.get("capabilities"))
    reason = _public_text(str(candidate.get("reason", "") or "").strip()) or None
    artifact_path = str(candidate.get("candidate_harness_refs_path", "") or "").strip()
    return EventNode(
        node=RsiTreeNode(
            node_id=_node_id(candidate, iteration=iteration),
            iteration=iteration,
            parent_id=parent_id,
            type=node_type,
            adopted=adopted,
            score=_number(candidate.get("candidate_score")),
            summary=_summary(changes, reason),
            snapshot_artifact_id=None,
            reason=None if adopted else reason,
            failure_class=(
                str(candidate.get("failure_class") or candidate.get("causal_failure_class") or "").strip() or None
            ),
            changes=changes,
            extra={"artifact_path": artifact_path} if artifact_path else {},
        ),
        artifacts=harness_artifacts(artifact_path),
    )


def parent_node_id(
    candidate: Mapping[str, Any],
    persisted_candidates: Sequence[Mapping[str, Any]],
) -> str | None:
    """Resolve the latest candidate whose output is this candidate's parent."""

    parent_artifact = str(candidate.get("before_harness_refs_path", "") or "").strip()
    if not parent_artifact:
        return None
    for index in range(len(persisted_candidates) - 1, -1, -1):
        prior = persisted_candidates[index]
        prior_artifact = str(prior.get("candidate_harness_refs_path", "") or "").strip()
        if prior_artifact and prior_artifact == parent_artifact:
            return _node_id(prior, iteration=index + 1)
    return None


def _node_id(candidate: Mapping[str, Any], *, iteration: int) -> str:
    explicit = str(candidate.get("candidate_id", "") or "").strip()
    if explicit:
        return explicit
    stable_parts = (
        str(candidate.get("member_optimization_ref_path", "") or ""),
        str(candidate.get("candidate_harness_refs_path", "") or ""),
        str(iteration),
    )
    digest = hashlib.sha256("\0".join(stable_parts).encode("utf-8")).hexdigest()
    return f"candidate-{digest[:16]}"


def _changes(raw_capabilities: Any) -> list[RsiChange]:
    changes: list[RsiChange] = []
    for capability in raw_capabilities if isinstance(raw_capabilities, list) else []:
        if not isinstance(capability, Mapping):
            continue
        summary = str(
            capability.get("expected_effect")
            or capability.get("description")
            or capability.get("rationale")
            or capability.get("purpose")
            or ""
        ).strip()
        changes.append(
            RsiChange(
                group=str(capability.get("action_group", "") or "").strip().upper(),
                operation=str(capability.get("operation", "") or "").strip().upper(),
                function=(str(capability.get("function", "") or "").strip() or None),
                target=(
                    str(
                        capability.get("target_path") or capability.get("target_ref") or capability.get("target") or ""
                    ).strip()
                    or None
                ),
                summary=_public_text(summary),
            )
        )
    return changes


def _summary(changes: list[RsiChange], reason: str | None) -> str | None:
    descriptions = [change.summary for change in changes if change.summary]
    return "; ".join(descriptions[:3]) if descriptions else reason


def _public_text(value: str) -> str:
    """Remove control-plane vocabulary from user-facing event text."""

    text = value.replace("_", " ")
    replacements = {
        r"\bgates?\b": "reviews",
        r"\bepochs?\b": "cycles",
        r"\bbatches?\b": "case groups",
        r"\battempts?\b": "proposals",
        r"\bcheckpoints?\b": "reviews",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _mapping_items(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "active_epoch_node_event",
    "analysis_stage_payload",
    "case_stage_payload",
    "epoch_node_event",
    "generate_stage_payload",
    "harness_artifacts",
    "root_node_event",
    "node_event",
    "parent_node_id",
    "progress_event",
    "source_reuse_stage_payload",
]
