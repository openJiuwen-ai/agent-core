# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Immutable optimization hypotheses for standalone harness evolution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.member_optimizer.lever import (
    build_hypothesis_lever_policy,
)
from openjiuwen.rsi.member_optimizer.loader import (
    load_analysis_ref,
    resolve_team_issues,
)

_HYPOTHESIS_VERSION = 3


def compile_optimization_hypotheses(
    *,
    analysis_ref_path: str,
    cases: list[dict[str, Any]],
    output_path: str | Path,
) -> str:
    """Compile analyzer output into immutable, case-bound optimization contracts.

    The analyzer is the final semantic author. Downstream stages receive these
    records verbatim and may choose a runtime surface, but may not reinterpret
    the required behavior.
    """
    analysis_path = Path(analysis_ref_path).expanduser().resolve()
    analysis_ref = load_analysis_ref(analysis_path)
    case_inputs = {
        str(case.get("case_id", "") or ""): _public_case_input(case)
        for case in cases
        if str(case.get("case_id", "") or "")
    }
    hypotheses: list[dict[str, Any]] = []
    for issue in resolve_team_issues(analysis_ref):
        attribution = issue.metadata.get("attribution", {}) if isinstance(issue.metadata, dict) else {}
        if not isinstance(attribution, dict):
            attribution = {}
        target_case_ids = sorted(
            {
                *[str(case_id) for case_id in issue.affected_cases if str(case_id)],
                *[
                    str(item.get("case_id", "") or "")
                    for item in issue.evidence
                    if isinstance(item, dict) and str(item.get("case_id", "") or "")
                ],
            }
        )
        public_triggers = [
            {"case_id": case_id, "task": case_inputs[case_id]}
            for case_id in target_case_ids
            if case_inputs.get(case_id)
        ]
        decisive_probe = _decisive_probe(issue)
        target_ref = str(attribution.get("target_ref", "") or "").strip()
        decision_contract = _decision_contract(issue, attribution)
        payload: dict[str, Any] = {
            "source_issue_id": issue.issue_id,
            "target_case_ids": target_case_ids,
            "authoritative_observations": {
                "summary": issue.summary,
                "evidence": issue.evidence,
                "metadata": issue.metadata,
            },
            "required_behavior": str(attribution.get("general_mechanism") or issue.recommendation),
            "forbidden_behavior": _deduplicate_strings(
                [
                    *_string_list(
                        issue.metadata.get("forbidden_behavior") or issue.metadata.get("forbidden_behaviors")
                    ),
                ]
            ),
            "public_trigger": public_triggers,
            "decisive_probe": decisive_probe,
            "decision_contract": decision_contract,
            "evidence_refs": _evidence_refs(issue.evidence),
            "diagnostic_lens": _diagnostic_lens(issue),
            "intent": "corrective",
            "deficiency_class": (
                "harness_deficiency" if target_ref and target_ref != "unassigned" else "insufficient_evidence"
            ),
            "lever_policy": build_hypothesis_lever_policy(
                target_ref=target_ref,
                target_case_ids=target_case_ids,
                decisive_probe=decisive_probe,
            ),
        }
        digest = _payload_digest(payload)
        payload["hypothesis_id"] = f"hyp_{digest[:12]}"
        payload["content_sha256"] = digest
        hypotheses.append(payload)

    document = {
        "version": _HYPOTHESIS_VERSION,
        "source_analysis_ref_path": str(analysis_path),
        "hypotheses": hypotheses,
    }
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return str(target)


def load_optimization_hypotheses(path: str | Path) -> list[dict[str, Any]]:
    """Load hypotheses and fail if any supposedly immutable payload drifted."""
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        return []
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    records = payload.get("hypotheses", []) if isinstance(payload, dict) else []
    hypotheses: list[dict[str, Any]] = []
    for raw in records if isinstance(records, list) else []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        expected = str(item.pop("content_sha256", "") or "")
        item.pop("hypothesis_id", None)
        actual = _payload_digest(item)
        if not expected or actual != expected:
            raise ValueError(
                f"optimization hypothesis content digest mismatch: {raw.get('hypothesis_id', '<unknown>')}"
            )
        hypotheses.append(dict(raw))
    return hypotheses


def write_candidate_manifest(
    *,
    output_path: str | Path,
    source_harness_refs_path: str,
    hypotheses_path: str,
    plan: dict[str, Any],
) -> str:
    """Write optimizer-only provenance without polluting runtime resources."""
    hypotheses = load_optimization_hypotheses(hypotheses_path)
    selected_issue_ids: set[str] = set()
    actions: list[dict[str, Any]] = []
    for raw_action in plan.get("actions", []):
        if not isinstance(raw_action, dict):
            continue
        actions.append(raw_action)
        for issue_id in raw_action.get("attributed_issue_ids", []):
            normalized_issue_id = str(issue_id)
            if normalized_issue_id:
                selected_issue_ids.add(normalized_issue_id)
    selected = [
        hypothesis for hypothesis in hypotheses if str(hypothesis.get("source_issue_id", "")) in selected_issue_ids
    ]
    manifest = {
        "version": 2,
        "status": "planned",
        "source_harness_refs_path": str(Path(source_harness_refs_path).expanduser().resolve()),
        "optimization_hypotheses_path": str(Path(hypotheses_path).expanduser().resolve()),
        "hypothesis_ids": [item["hypothesis_id"] for item in selected],
        "actions": [
            {
                "action_id": action.get("action_id", ""),
                "action_group": action.get("action_group", ""),
                "operation": action.get("operation", ""),
                "target_path": action.get("target_path", ""),
                "attributed_issue_ids": action.get("attributed_issue_ids", []),
                "lever_decision": (
                    action.get("constraints", {}).get("lever_decision", {})
                    if isinstance(action.get("constraints"), dict)
                    else {}
                ),
            }
            for action in actions
        ],
    }
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return str(target)


def _payload_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _diagnostic_lens(issue: Any) -> str:
    text = " ".join(
        (
            str(getattr(issue, "category", "") or ""),
            str(getattr(issue, "summary", "") or ""),
        )
    ).lower()
    if "capability" in text or "missing" in text:
        return "capability_gap"
    return "failure"


def _public_case_input(case: dict[str, Any]) -> str:
    for key in ("input", "inputs", "task_input", "query", "prompt"):
        if key not in case:
            continue
        value = case[key]
        if isinstance(value, dict) and set(value) == {"user_message"}:
            return _strip_benchmark_transport(str(value.get("user_message", "") or ""))
        if isinstance(value, str):
            return _strip_benchmark_transport(value)
        return _strip_benchmark_transport(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return ""


def _strip_benchmark_transport(value: str) -> str:
    """Remove harness routing metadata while preserving the public issue text."""
    transport_prefixes = (
        "execution environment:",
        "shell commands already run there.",
        "use `bash` for repository file inspection",
        "swe-bench lite instance:",
        "repository:",
        "base commit:",
        "work in the checked-out repository.",
        "diagnose the issue, implement the smallest correct fix",
        "do not modify tests to make them pass.",
    )
    kept = [line for line in str(value or "").splitlines() if not line.strip().lower().startswith(transport_prefixes)]
    return "\n".join(kept).strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _deduplicate_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _decisive_probe(issue: Any) -> dict[str, Any]:
    metadata = issue.metadata if isinstance(issue.metadata, dict) else {}
    attribution = metadata.get("attribution", {})
    if not isinstance(attribution, dict):
        attribution = {}
    return {
        "root_cause": attribution.get("root_cause", ""),
        "critical_mistake": attribution.get("critical_mistake", ""),
        "validation_observations": metadata.get("validation_observations", {}),
        "verifier_observations": metadata.get("verifier_observations", {}),
    }


def _decision_contract(issue: Any, attribution: dict[str, Any]) -> dict[str, Any]:
    """Preserve the decision change that the runtime artifact must teach.

    ``general_mechanism`` alone is intentionally broad. Passing only that prose
    to the artifact author previously allowed a task-specific causal finding to
    be reopened as a generic protocol choice. The decision contract keeps the
    analyzer's direction of change intact while later stages still decide how
    to express it as a native Skill, Prompt Section, or Tool.
    """
    supplied = attribution.get("decision_contract", {})
    supplied = dict(supplied) if isinstance(supplied, dict) else {}
    metadata = issue.metadata if isinstance(issue.metadata, dict) else {}
    boundaries = _deduplicate_strings(
        [
            *_string_list(supplied.get("scope_boundary")),
            *_string_list(supplied.get("invalid_alternatives")),
            *_string_list(metadata.get("forbidden_behavior") or metadata.get("forbidden_behaviors")),
        ]
    )
    activation_phase = str(supplied.get("activation_phase", "")).strip().lower()
    if activation_phase not in {
        "task_start",
        "during_investigation",
        "post_diagnosis",
        "pre_submission",
    }:
        phase_parts = (
            supplied.get("required_action"),
            issue.recommendation,
            supplied.get("acceptance_observable"),
        )
        phase_text = " ".join(str(value or "") for value in phase_parts).lower()
        post_diagnosis_terms = (
            "after diagnosis",
            "once diagnosed",
            "after confirming",
            "once confirmed",
            "edit site",
        )
        if any(token in phase_text for token in ("before submit", "pre-submit", "finalize")):
            activation_phase = "pre_submission"
        elif any(token in phase_text for token in post_diagnosis_terms):
            activation_phase = "post_diagnosis"
        else:
            activation_phase = "task_start"
    return {
        "wrong_decision": str(
            supplied.get("wrong_decision") or attribution.get("critical_mistake") or issue.summary or ""
        ).strip(),
        "causal_distinction": str(
            supplied.get("causal_distinction") or attribution.get("general_mechanism") or issue.summary or ""
        ).strip(),
        "required_action": str(supplied.get("required_action") or issue.recommendation or "").strip(),
        "acceptance_observable": str(
            supplied.get("acceptance_observable") or attribution.get("root_cause") or issue.summary or ""
        ).strip(),
        "scope_boundary": boundaries,
        "activation_phase": activation_phase,
    }


def _evidence_refs(evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        ref = {
            key: str(item[key])
            for key in ("case_id", "trace_id", "role", "step_pointer")
            if item.get(key) not in (None, "")
        }
        if ref:
            refs.append(ref)
    return refs


__all__ = [
    "compile_optimization_hypotheses",
    "load_optimization_hypotheses",
    "write_candidate_manifest",
]
