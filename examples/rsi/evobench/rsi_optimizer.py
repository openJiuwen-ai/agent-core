# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Constrained RSI candidate optimizer for Evo-Bench PolicyHarness packages.

The regular member optimizer understands Expert Harness manifests. Evo-Bench
owns a different, deliberately small PolicyHarness package contract. This
adapter preserves the regular optimizer artifact protocol while allowing only
the two PolicyHarness surfaces that are safe to tune without changing the
official execution framework.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import hashlib
import inspect
import json
from pathlib import Path
import re
import shutil
from typing import Any

import yaml


_ROLE = "policy_harness"
_PROMPT_PATH = "system_prompt.md"
_HARNESS_PATH = "harness.json"
_ALLOWED_HARNESS_FIELDS = {"max_steps", "rollout_wall_clock_seconds"}
_WALL_CLOCK_ALIASES = {"wall_clock", "wall_clock_seconds"}
_MAX_PROMPT_APPEND_CHARS = 8_000
_MAX_STRUCTURED_EVIDENCE_CHARS = 40_000
_MAX_LEGACY_RESULT_CHARS = 3_000
_MAX_LEGACY_TRACE_CHARS = 4_000
GENERIC_IMPROVER_PROTOCOL_VERSION = "generic_behavior_intervention_v1"
_CAUSAL_EVIDENCE_PATH_KEYS = {
    "causal_evidence_path",
    "causal_digest_path",
    "causal_digests_path",
}
_DIAGNOSIS_PATH_KEYS = {"per_case_diagnoses_path", "diagnoses_path"}
_FEEDBACK_PATH_KEYS = {"candidate_feedback_delta_path", "candidate_feedback_path"}
# Generic fields such as finish.answer are observed behavior, not hidden gold.
_SENSITIVE_VALUE_KEYS = {
    "expected_answer",
    "expected_answers",
    "gold",
    "gold_answer",
    "gold_answers",
    "known_answer",
    "known_answers",
    "reference_answer",
    "reference_answers",
}
_CASE_ID_KEYS = {
    "case_id",
    "case_ids",
    "affected_cases",
    "target_case_ids",
    "task_id",
    "issue_id",
    "issue_ids",
    "source_issue_id",
}
_NON_PUBLIC_TOOL_FIELD_KEYS = {
    "response_only_fields",
    "response_fields_not_in_public_request_schema",
    "response_leaf_fields_not_in_public_request_schema",
    "observed_request_fields_outside_public_schema",
    "hidden_fields",
    "private_fields",
    "non_public_fields",
}

PatchGenerator = Callable[[str], dict[str, Any] | Awaitable[dict[str, Any]]]


class PolicyHarnessRSIOptimizer:
    """Generate one safe PolicyHarness candidate for the RSI orchestrator."""

    def __init__(
        self,
        config: Any | None = None,
        *,
        model_config_ref: str = "",
        patch_generator: PatchGenerator | None = None,
    ) -> None:
        self.config = config
        self.model_config_ref = str(model_config_ref or getattr(config, "model_config_ref", "") or "").strip()
        self._patch_generator = patch_generator

    async def optimize(  # pylint: disable=huawei-too-many-arguments
        self,
        eval_ref_path: str,
        analysis_result_path: str,
        harness_refs_path: str,
        output_dir: str,
        *,
        defer_publish: bool = True,
        rejected_capabilities: list[dict[str, Any]] | None = None,
        single_harness: bool = True,
        optimization_hypotheses_path: str = "",
        optimization_issue_ids: list[str] | None = None,
        optimization_experience: dict[str, Any] | None = None,
    ) -> str:
        """Write a MemberOptimizer-compatible PolicyHarness candidate artifact."""
        if not single_harness:
            raise ValueError("Evo-Bench PolicyHarness optimization requires single_harness=True")

        source_refs_path, source_refs, source_harness = _load_policy_harness_ref(harness_refs_path)
        analysis_path, analysis = _load_analysis(analysis_result_path)
        selected_issues = _select_issues(analysis, optimization_issue_ids)
        if not selected_issues:
            selected_issues = [_build_unattributed_failure_issue(eval_ref_path)]

        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        run_dir = _allocate_run_dir(output_root)
        candidate_harness = _candidate_harness_path(run_dir, source_harness)
        candidate_harness.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_harness, candidate_harness)

        prompt_path = candidate_harness / _PROMPT_PATH
        harness_path = candidate_harness / _HARNESS_PATH
        if not prompt_path.is_file() or not harness_path.is_file():
            raise ValueError(f"PolicyHarness must contain system_prompt.md and harness.json: {source_harness}")
        source_prompt = prompt_path.read_text(encoding="utf-8")
        source_harness_json = _read_json_mapping(harness_path)
        source_tree = _tree_hashes(source_harness)

        evidence = _build_evidence_bundle(
            eval_ref_path=eval_ref_path,
            analysis_path=analysis_path,
            analysis=analysis,
            selected_issues=selected_issues,
            hypotheses_path=optimization_hypotheses_path,
        )
        leakage_guard = _build_prompt_leakage_guard(
            selected_issues=selected_issues,
            evidence=evidence,
        )
        generation_context = _generation_context(optimization_experience)
        request = _build_patch_request(
            source_prompt=source_prompt,
            source_harness=source_harness_json,
            evidence=evidence,
            generation_context=generation_context,
            rejected_capabilities=rejected_capabilities or [],
        )
        raw_patch = await self._generate_patch(
            request,
            run_dir=run_dir,
            leakage_guard=leakage_guard,
        )
        patch = _validate_patch(
            raw_patch,
            source_prompt=source_prompt,
            leakage_guard=leakage_guard,
        )
        _apply_patch(
            prompt_path=prompt_path,
            harness_path=harness_path,
            source_prompt=source_prompt,
            source_harness=source_harness_json,
            patch=patch,
        )
        changed_paths = _verify_candidate_tree(
            source_tree=source_tree,
            source_harness=source_harness,
            candidate_harness=candidate_harness,
        )

        issue_ids = [str(issue["issue_id"]) for issue in selected_issues]
        case_ids = _issue_case_ids(selected_issues)
        actions = _build_actions(
            patch=patch,
            issue_ids=issue_ids,
            case_ids=case_ids,
        )
        plan = _build_plan(
            source_harness=source_harness,
            issue_ids=issue_ids,
            case_ids=case_ids,
            actions=actions,
            generation_context=generation_context,
        )
        plan_path = run_dir / "plan.yaml"
        _write_yaml_atomic(plan_path, plan)

        capabilities = _capabilities(actions, case_ids)
        capabilities_path = run_dir / "capabilities.yaml"
        _write_yaml_atomic(capabilities_path, {"capabilities": capabilities})

        candidate_refs_path = run_dir / "candidate_harness_refs.yaml"
        candidate_refs = _candidate_refs_payload(
            source_refs=source_refs,
            source_refs_path=source_refs_path,
            candidate_harness=candidate_harness,
            defer_publish=defer_publish,
        )
        _write_yaml_atomic(candidate_refs_path, candidate_refs)

        execution_path = run_dir / "execution_results.json"
        _write_json_atomic(
            execution_path,
            {
                "status": "success",
                "changed_paths": changed_paths,
                "actions": [
                    {"action_id": action["action_id"], "role": _ROLE, "status": "success"} for action in actions
                ],
            },
        )
        verification_path = run_dir / "verification.json"
        _write_json_atomic(
            verification_path,
            {
                "status": "passed",
                "allowed_paths": [_PROMPT_PATH, _HARNESS_PATH],
                "changed_paths": changed_paths,
                "python_framework_unchanged": True,
                "source_tree_sha256": _canonical_digest(source_tree),
                "candidate_tree_sha256": _canonical_digest(_tree_hashes(candidate_harness)),
            },
        )

        promotion_status = "pending_gate" if defer_publish else "published"
        artifact = {
            "optimization_id": run_dir.name,
            "output_dir": str(run_dir),
            "status": "success",
            "optimized_harness_refs_path": str(candidate_refs_path),
            "roles": [_ROLE],
            "candidate_ready_roles": [_ROLE],
            "published_roles": [_ROLE],
            "staged_roles": [_ROLE],
            "verified_roles": [_ROLE],
            "promoted_roles": [] if defer_publish else [_ROLE],
            "promotion_status": promotion_status,
            "composition_mode": "opaque_snapshot",
            "failed_roles": [],
            "skipped_roles": [],
            "role_attribution_path": "",
            "mechanism_attribution_path": "",
            "selection_path": "",
            "plan_path": str(plan_path),
            "execution_result_path": str(execution_path),
            "verification_path": str(verification_path),
            "fix_result_path": "",
            "role_results": {
                _ROLE: {
                    "status": "candidate_ready" if defer_publish else "published",
                    "before_harness_ref_path": str(source_harness),
                    "after_harness_ref_path": str(candidate_harness),
                    "action_ids": [action["action_id"] for action in actions],
                    "verification_status": "passed",
                    "error": "",
                    "metadata": {"capabilities_path": str(capabilities_path)},
                }
            },
            "metadata": {
                "eval_ref_path": str(Path(eval_ref_path).expanduser().resolve()),
                "analysis_result_path": str(analysis_path),
                "source_harness_refs_path": str(source_refs_path),
                "optimization_hypotheses_path": str(optimization_hypotheses_path or ""),
                "optimization_issue_ids": issue_ids,
                "target_case_ids": case_ids,
                "capabilities_path": str(capabilities_path),
                "optimizer_kind": "evobench_policy_harness_v1",
                "improver_protocol_version": GENERIC_IMPROVER_PROTOCOL_VERSION,
                "composition_mode": "opaque_snapshot",
                "allowed_mutation_paths": [_PROMPT_PATH, _HARNESS_PATH],
                "generation_context": generation_context,
            },
            "role": _ROLE,
        }
        ref_path = run_dir / "member_optimization_ref.yaml"
        _write_yaml_atomic(ref_path, artifact)
        return str(ref_path)

    async def _generate_patch(
        self,
        request: str,
        *,
        run_dir: Path,
        leakage_guard: dict[str, Any],
    ) -> dict[str, Any]:
        if self._patch_generator is not None:
            generated = self._patch_generator(request)
            return await generated if inspect.isawaitable(generated) else generated
        if not self.model_config_ref:
            raise RuntimeError("model_config_ref is required for PolicyHarness optimization")
        return await _invoke_patch_agent(
            request=request,
            model_config_ref=self.model_config_ref,
            workspace=run_dir,
            leakage_guard=leakage_guard,
        )


EvoBenchPolicyHarnessOptimizer = PolicyHarnessRSIOptimizer


def _candidate_harness_path(run_dir: Path, source_harness: Path) -> Path:
    inline_path = run_dir / "candidate_harness" / _ROLE
    longest_relative_path = max(
        (len(str(path.relative_to(source_harness))) for path in source_harness.rglob("*") if path.is_file()),
        default=0,
    )
    if len(str(inline_path)) + longest_relative_path + 1 < 220:
        return inline_path

    # The orchestrator's cohort paths are deep enough to exceed the legacy
    # Windows MAX_PATH limit before copytree reaches files inside the harness.
    # Keep the control artifacts in their normal location, but stage the opaque
    # snapshot under a stable short root and reference it by absolute path.
    repository_root = Path(__file__).resolve().parents[3]
    candidate_id = hashlib.sha256(str(run_dir).encode("utf-8")).hexdigest()[:20]
    candidate_path = repository_root / ".evobench_runs" / "_rsi_candidates" / candidate_id / "h"
    if candidate_path.exists():
        shutil.rmtree(candidate_path)
    return candidate_path


async def _invoke_patch_agent(
    *,
    request: str,
    model_config_ref: str,
    workspace: Path,
    leakage_guard: dict[str, Any],
) -> dict[str, Any]:
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.harness.factory import create_deep_agent
    from openjiuwen.rsi.member_optimizer.agents.factory import load_member_optimizer_model
    from openjiuwen.rsi.member_optimizer.agents.output import (
        invoke_member_optimizer_agent_structured,
        parse_yaml_or_json_object_response,
    )

    agent = create_deep_agent(
        model=load_member_optimizer_model(model_config_ref),
        card=AgentCard(
            name="evobench_policy_harness_optimizer",
            description="Produces one constrained Evo-Bench PolicyHarness patch.",
        ),
        system_prompt=(
            "You improve an AI Harness from task contracts, observed behavior, and "
            "paired evaluation evidence. The task domain is not assumed. Separate facts "
            "from hypotheses and return only the requested JSON mapping. Make one "
            "falsifiable behavior intervention within the supplied mutation contract. "
            "Never encode case IDs, known answers, benchmark entities, or private tool fields."
        ),
        tools=None,
        workspace=str(workspace),
        enable_task_loop=False,
        max_iterations=3,
        language="en",
        restrict_to_work_dir=True,
        auto_create_workspace=True,
    )
    return await invoke_member_optimizer_agent_structured(
        agent=agent,
        agent_name="EvoBenchPolicyHarnessOptimizerAgent",
        user_message=request,
        session_id=f"evobench_policy_{hashlib.sha256(request.encode('utf-8')).hexdigest()[:16]}",
        retry_limit=2,
        parse_response=parse_yaml_or_json_object_response,
        validate_response=lambda value: _patch_validation_errors(
            value,
            leakage_guard=leakage_guard,
        ),
        build_retry_message=lambda _previous, error: (
            f"{request}\n\nThe previous output was invalid: {error}\nReturn only a corrected JSON mapping."
        ),
    )


def _load_policy_harness_ref(path_value: str) -> tuple[Path, dict[str, Any], Path]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"harness refs file not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"harness refs must be a mapping: {path}")
    refs = payload.get("harness_refs")
    refs = refs if isinstance(refs, dict) else payload
    raw_ref = refs.get(_ROLE)
    if not isinstance(raw_ref, str) or not raw_ref.strip():
        raise ValueError("harness refs must contain a non-empty policy_harness ref")
    harness = Path(raw_ref).expanduser()
    harness = harness.resolve() if harness.is_absolute() else (path.parent / harness).resolve()
    if not harness.is_dir():
        raise FileNotFoundError(f"policy_harness directory not found: {harness}")
    return path, payload, harness


def _load_analysis(path_value: str) -> tuple[Path, dict[str, Any]]:
    path = Path(path_value).expanduser().resolve()
    payload = _read_yaml_mapping(path)
    nested = payload.get("analysis_result_path")
    if isinstance(nested, str) and nested.strip():
        nested_path = Path(nested).expanduser()
        nested_path = nested_path.resolve() if nested_path.is_absolute() else (path.parent / nested_path).resolve()
        return nested_path, _read_yaml_mapping(nested_path)
    return path, payload


def _select_issues(
    analysis: dict[str, Any],
    requested_issue_ids: list[str] | None,
) -> list[dict[str, Any]]:
    issues = [dict(item) for item in analysis.get("issues", []) if isinstance(item, dict)]
    requested = {str(issue_id) for issue_id in (requested_issue_ids or []) if str(issue_id)}
    if not requested:
        return issues
    selected = [issue for issue in issues if str(issue.get("issue_id", "")) in requested]
    missing = sorted(requested - {str(issue.get("issue_id", "")) for issue in selected})
    if missing:
        raise ValueError(f"analysis does not contain requested optimization issues: {missing}")
    return selected


def _build_unattributed_failure_issue(eval_ref_path: str) -> dict[str, Any]:
    """Preserve the controller's evidence-first fallback when attribution is empty.

    The candidate remains low-confidence and must pass the same frozen target
    gate as an attributed candidate.  This is preferable to aborting a whole
    epoch because the Analyzer returned no optimizable target.
    """
    eval_ref = _read_yaml_mapping(Path(eval_ref_path).expanduser().resolve())
    failed_case_ids: list[str] = []
    for case in eval_ref.get("cases", []) if isinstance(eval_ref.get("cases"), list) else []:
        if not isinstance(case, dict):
            continue
        metadata = case.get("metadata") if isinstance(case.get("metadata"), dict) else {}
        explicit_passed = metadata.get("evaluation_passed")
        status = str(case.get("status", "") or "").strip().casefold()
        score = case.get("score")
        failed = explicit_passed is False or status in {"failed", "error"}
        if explicit_passed is not True and not failed and isinstance(score, (int, float)):
            failed = float(score) < 1.0
        case_id = str(case.get("case_id", "") or "").strip()
        if failed and case_id:
            failed_case_ids.append(case_id)
    if not failed_case_ids:
        raise ValueError("PolicyHarness optimization requires failed cases or an attributed analysis issue")
    return {
        "issue_id": "issue_unattributed_failure_001",
        "category": "member_harness",
        "severity": "medium",
        "summary": "Observed task failure has no usable Analyzer attribution.",
        "affected_cases": list(dict.fromkeys(failed_case_ids)),
        "affected_components": [_ROLE],
        "recommendation": (
            "Derive one reusable policy prompt intervention from the materialized "
            "causal evidence and validate it on the failed cases."
        ),
        "metadata": {
            "unattributed_fallback": True,
            "attribution": {
                "target_ref": "member_harness.policy_harness.prompt",
                "confidence": "low",
            },
        },
    }


def _build_evidence_bundle(
    *,
    eval_ref_path: str,
    analysis_path: Path,
    analysis: dict[str, Any],
    selected_issues: list[dict[str, Any]],
    hypotheses_path: str,
) -> dict[str, Any]:
    target_case_ids = set(_issue_case_ids(selected_issues))
    materialized_payloads = _load_analysis_evidence_artifacts(
        analysis=analysis,
        analysis_path=analysis_path,
    )
    causal_digests: list[Any] = []
    feedback_deltas: list[Any] = []
    diagnoses: list[Any] = []

    analysis_without_issues = {key: value for key, value in analysis.items() if key != "issues"}
    causal_sources = [
        analysis_without_issues,
        *selected_issues,
        *[_case_scoped_payload(payload, target_case_ids) for payload in materialized_payloads["causal"]],
    ]
    for source in causal_sources:
        causal_digests.extend(_find_named_values(source, {"causal_digest", "causal_digests"}))
        feedback_deltas.extend(
            _find_named_values(
                source,
                {
                    "candidate_feedback_delta",
                    "candidate_feedback_deltas",
                    "prior_candidate_feedback",
                    "paired_candidate_feedback",
                },
            )
        )

    for source in materialized_payloads["diagnoses"]:
        selected_diagnoses = _diagnosis_records(source, target_case_ids)
        diagnoses.extend(selected_diagnoses)
        for diagnosis in selected_diagnoses:
            feedback_deltas.extend(
                _find_named_values(
                    diagnosis,
                    {
                        "candidate_feedback_delta",
                        "candidate_feedback_deltas",
                        "prior_candidate_feedback",
                        "paired_candidate_feedback",
                    },
                )
            )
    for source in materialized_payloads["feedback"]:
        feedback_deltas.extend(_feedback_records(source, target_case_ids))

    causal_digests = [
        value for value in _filter_case_scoped_values(causal_digests, target_case_ids) if _has_evidence_content(value)
    ]
    feedback_deltas = [
        value for value in _filter_case_scoped_values(feedback_deltas, target_case_ids) if _has_evidence_content(value)
    ]
    diagnoses = [value for value in diagnoses if _has_evidence_content(value)]
    primary_structured_available = bool(causal_digests or diagnoses or feedback_deltas)

    eval_path = Path(eval_ref_path).expanduser().resolve()
    eval_ref = _read_yaml_mapping(eval_path)
    legacy_evidence: list[dict[str, Any]] = []
    legacy_guard_sources: list[Any] = []
    remaining_result = _MAX_LEGACY_RESULT_CHARS
    remaining_trace = _MAX_LEGACY_TRACE_CHARS if not primary_structured_available else 0
    for case in eval_ref.get("cases", []) if isinstance(eval_ref.get("cases"), list) else []:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "")
        if target_case_ids and case_id not in target_case_ids:
            continue
        entry: dict[str, Any] = {}
        for key in ("result_path", "trace_path"):
            remaining = remaining_result if key == "result_path" else remaining_trace
            raw_path = case.get(key)
            if not isinstance(raw_path, str) or not raw_path.strip() or remaining <= 0:
                continue
            evidence_path = Path(raw_path).expanduser()
            evidence_path = (
                evidence_path.resolve() if evidence_path.is_absolute() else (eval_path.parent / evidence_path).resolve()
            )
            if evidence_path.is_file():
                raw_content = evidence_path.read_text(encoding="utf-8", errors="replace")
                try:
                    parsed_content = json.loads(raw_content)
                except json.JSONDecodeError:
                    parsed_content = None
                if parsed_content is not None:
                    legacy_guard_sources.append(parsed_content)
                    content = json.dumps(
                        _sanitize_model_evidence(parsed_content, target_case_ids),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )[:remaining]
                else:
                    content = raw_content[:remaining]
                entry[key.removesuffix("_path")] = _replace_case_ids(content, target_case_ids)
                if key == "result_path":
                    remaining_result -= len(content)
                else:
                    remaining_trace -= len(content)
        if entry:
            legacy_evidence.append(entry)

    hypotheses: dict[str, Any] = {}
    if hypotheses_path:
        hypothesis_path = Path(hypotheses_path).expanduser().resolve()
        if hypothesis_path.is_file():
            hypotheses = _read_yaml_mapping(hypothesis_path)
            requested_issue_ids = {
                str(issue.get("issue_id", "") or "")
                for issue in selected_issues
                if str(issue.get("issue_id", "") or "")
                and not bool(
                    issue.get("metadata", {}).get("unattributed_fallback", False)
                    if isinstance(issue.get("metadata"), Mapping)
                    else False
                )
            }
            records = hypotheses.get("hypotheses", [])
            if requested_issue_ids and isinstance(records, list):
                selected_hypotheses = [
                    dict(item)
                    for item in records
                    if isinstance(item, dict) and str(item.get("source_issue_id", "") or "") in requested_issue_ids
                ]
                found_issue_ids = {str(item.get("source_issue_id", "") or "") for item in selected_hypotheses}
                missing_issue_ids = sorted(requested_issue_ids - found_issue_ids)
                if missing_issue_ids:
                    raise ValueError(f"optimization hypotheses do not contain selected issues: {missing_issue_ids}")
                hypotheses = {**hypotheses, "hypotheses": selected_hypotheses}
    guard_sources = [*selected_issues, *legacy_guard_sources]
    guard_sources.extend(_case_scoped_payload(payload, target_case_ids) for payload in materialized_payloads["causal"])
    for payload in materialized_payloads["diagnoses"]:
        guard_sources.extend(_diagnosis_records(payload, target_case_ids))
    for payload in materialized_payloads["feedback"]:
        guard_sources.extend(_feedback_records(payload, target_case_ids))
    return {
        "schema_version": 2,
        "evidence_priority": [
            "analyzer_causal_digest",
            "analyzer_diagnosis",
            "candidate_feedback_delta",
            "bounded_legacy_fallback",
        ],
        "analyzer_causal_digest": _bounded_evidence_records(
            [_sanitize_model_evidence(value, target_case_ids) for value in causal_digests],
            _MAX_STRUCTURED_EVIDENCE_CHARS,
        ),
        "analyzer_diagnoses": _bounded_evidence_records(
            [
                _sanitize_model_evidence(value, target_case_ids)
                for value in [*diagnoses, *_compact_selected_issue_diagnoses(selected_issues)]
            ],
            _MAX_STRUCTURED_EVIDENCE_CHARS // 2,
        ),
        "candidate_feedback_delta": _bounded_evidence_records(
            [_sanitize_model_evidence(value, target_case_ids) for value in feedback_deltas],
            _MAX_STRUCTURED_EVIDENCE_CHARS // 2,
        ),
        "optimization_hypotheses": _sanitize_model_evidence(hypotheses, target_case_ids),
        "legacy_fallback_used": not primary_structured_available,
        # Preserve the v1 key for older Analyzer artifacts. Raw traces are used
        # only when no materialized causal/feedback evidence is available.
        "failed_case_evidence": legacy_evidence,
        "_prompt_leakage_guard": _leakage_guard_from_sources(
            guard_sources,
            case_ids=target_case_ids,
        ),
    }


def _load_analysis_evidence_artifacts(
    *,
    analysis: dict[str, Any],
    analysis_path: Path,
) -> dict[str, list[Any]]:
    artifacts: dict[str, list[Any]] = {"causal": [], "diagnoses": [], "feedback": []}
    path_groups = (
        ("causal", _CAUSAL_EVIDENCE_PATH_KEYS),
        ("diagnoses", _DIAGNOSIS_PATH_KEYS),
        ("feedback", _FEEDBACK_PATH_KEYS),
    )
    for group, keys in path_groups:
        for raw_path in _find_named_values(analysis, keys):
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            path = Path(raw_path).expanduser()
            path = path.resolve() if path.is_absolute() else (analysis_path.parent / path).resolve()
            if not path.is_file():
                continue
            payload = _read_structured_artifact(path)
            if payload is not None:
                artifacts[group].append(payload)
    return artifacts


def _read_structured_artifact(path: Path) -> Any | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        if path.suffix.casefold() == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError):
        return None


def _find_named_values(value: Any, names: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).casefold()
            if key in names:
                found.append(child)
            else:
                found.extend(_find_named_values(child, names))
    elif isinstance(value, list):
        for child in value:
            found.extend(_find_named_values(child, names))
    return found


def _diagnosis_records(payload: Any, case_ids: set[str]) -> list[Any]:
    if isinstance(payload, Mapping):
        records = payload.get("per_case_diagnoses", payload.get("diagnoses"))
        if isinstance(records, list):
            return _filter_case_scoped_values(records, case_ids)
        cases = payload.get("cases")
        if isinstance(cases, list):
            return _filter_case_scoped_values(cases, case_ids)
        return []
    if isinstance(payload, list):
        return _filter_case_scoped_values(payload, case_ids)
    return []


def _feedback_records(payload: Any, case_ids: set[str]) -> list[Any]:
    if isinstance(payload, Mapping):
        cases = payload.get("cases")
        if isinstance(cases, list):
            return _filter_case_scoped_values(cases, case_ids)
        records = payload.get("candidate_feedback_delta", payload.get("candidate_feedback"))
        if isinstance(records, list):
            return _filter_case_scoped_values(records, case_ids)
        if records is not None:
            return [records]
        return [payload]
    if isinstance(payload, list):
        return _filter_case_scoped_values(payload, case_ids)
    return []


def _filter_case_scoped_values(values: list[Any], case_ids: set[str]) -> list[Any]:
    if not case_ids:
        return values
    filtered: list[Any] = []
    for value in values:
        if not isinstance(value, Mapping):
            filtered.append(value)
            continue
        raw_case_id = value.get("case_id", value.get("task_id"))
        if raw_case_id is None or str(raw_case_id) in case_ids:
            filtered.append(value)
    return filtered


def _has_evidence_content(value: Any) -> bool:
    return value is not None and value not in ("", {}, [])


def _case_scoped_payload(payload: Any, case_ids: set[str]) -> Any:
    if not case_ids or not isinstance(payload, Mapping):
        return payload
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return payload
    return {
        **payload,
        "cases": _filter_case_scoped_values(cases, case_ids),
    }


def _compact_selected_issue_diagnoses(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for issue in issues:
        metadata = issue.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        attribution = metadata.get("attribution")
        attribution = attribution if isinstance(attribution, Mapping) else {}
        diagnosis = {
            "summary": issue.get("summary", ""),
            "recommendation": issue.get("recommendation", ""),
            "severity": issue.get("severity", ""),
            "evidence_status": attribution.get("evidence_status", ""),
            "failed_requirement": attribution.get("failed_requirement", ""),
            "competing_hypotheses": attribution.get("competing_hypotheses", []),
            "discriminating_evidence": attribution.get("discriminating_evidence", ""),
            "root_cause": attribution.get("root_cause", ""),
            "critical_mistake": attribution.get("critical_mistake", ""),
            "general_mechanism": attribution.get("general_mechanism", ""),
            "decision_contract": attribution.get("decision_contract", {}),
            "failure_cluster": attribution.get("failure_cluster", {}),
            "confidence": attribution.get("confidence", ""),
        }
        compact.append({key: value for key, value in diagnosis.items() if value not in ("", {}, [])})
    return compact


def _sanitize_model_evidence(value: Any, case_ids: set[str]) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.casefold()
            if normalized in _CASE_ID_KEYS:
                continue
            if normalized in _SENSITIVE_VALUE_KEYS:
                sanitized[key] = "<redacted-known-answer-or-entity-set>"
                continue
            sanitized[key] = _sanitize_model_evidence(child, case_ids)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_model_evidence(child, case_ids) for child in value[:40]]
    if isinstance(value, str):
        return _replace_case_ids(value[:4_000], case_ids)
    return value


def _replace_case_ids(text: str, case_ids: set[str]) -> str:
    sanitized = str(text)
    for case_id in sorted(case_ids, key=len, reverse=True):
        if case_id:
            sanitized = re.sub(re.escape(case_id), "<case-id>", sanitized, flags=re.IGNORECASE)
    return sanitized


def _bounded_evidence_records(records: list[Any], limit: int) -> list[Any]:
    bounded: list[Any] = []
    seen: set[str] = set()
    remaining = limit
    for record in records:
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if encoded in seen:
            continue
        seen.add(encoded)
        if len(encoded) <= remaining:
            bounded.append(record)
            remaining -= len(encoded)
            continue
        if remaining > 256:
            bounded.append(
                {
                    "truncated": True,
                    "structured_json_excerpt": encoded[: remaining - 128],
                }
            )
        break
    return bounded


def _leakage_guard_from_sources(sources: list[Any], *, case_ids: set[str]) -> dict[str, Any]:
    sensitive_literals: set[str] = set()
    public_tool_fields: set[str] = set()
    non_public_tool_fields: set[str] = set()

    def visit(value: Any, parent_key: str = "") -> None:
        if isinstance(value, Mapping):
            for raw_key, child in value.items():
                key = str(raw_key).casefold()
                if key in _SENSITIVE_VALUE_KEYS:
                    sensitive_literals.update(_literal_values(child))
                if key in {"allowed_request_fields", "required_request_fields"}:
                    public_tool_fields.update(_identifier_values(child))
                if key in _NON_PUBLIC_TOOL_FIELD_KEYS:
                    non_public_tool_fields.update(_identifier_values(child))
                visit(child, key)
        elif isinstance(value, list):
            for child in value:
                visit(child, parent_key)

    for source in sources:
        visit(source)
    return {
        "case_ids": sorted(case_ids),
        "sensitive_literals": sorted(sensitive_literals),
        "public_tool_fields": sorted(public_tool_fields),
        "non_public_tool_fields": sorted(non_public_tool_fields - public_tool_fields),
    }


def _literal_values(value: Any) -> set[str]:
    values: set[str] = set()
    if isinstance(value, Mapping):
        for child in value.values():
            values.update(_literal_values(child))
    elif isinstance(value, list):
        for child in value:
            values.update(_literal_values(child))
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        text = str(value).strip()
        if len(text) >= 4:
            values.add(text)
    return values


def _identifier_values(value: Any) -> set[str]:
    values: set[str] = set()
    if isinstance(value, Mapping):
        for child in value.values():
            values.update(_identifier_values(child))
    elif isinstance(value, list):
        for child in value:
            values.update(_identifier_values(child))
    elif isinstance(value, str):
        item = value.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", item):
            values.add(item)
    return values


def _build_prompt_leakage_guard(
    *,
    selected_issues: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    internal = evidence.get("_prompt_leakage_guard")
    internal = dict(internal) if isinstance(internal, Mapping) else {}
    fallback = _leakage_guard_from_sources(
        [*selected_issues],
        case_ids=set(_issue_case_ids(selected_issues)),
    )
    return {
        key: sorted(set(internal.get(key, [])) | set(fallback.get(key, [])))
        for key in ("case_ids", "sensitive_literals", "public_tool_fields", "non_public_tool_fields")
    }


def _generation_context(experience: dict[str, Any] | None) -> dict[str, Any]:
    value = experience or {}
    sibling = value.get("sibling_generation")
    sibling = dict(sibling) if isinstance(sibling, dict) else {}
    index = max(1, int(sibling.get("generation_index", 1) or 1))
    focuses = (
        "direct repair of the diagnosed decision rule",
        "activation or routing of the diagnosed behavior",
        "verification of the diagnosed acceptance observable",
        "bounded recovery only when the evidence shows execution interruption",
    )
    return {
        "generation_index": index,
        "candidate_count": int(sibling.get("candidate_count", 1) or 1),
        "candidate_id": str(sibling.get("candidate_id", "") or ""),
        "differentiation_focus": focuses[(index - 1) % len(focuses)],
        "prior_proposals": [dict(item) for item in sibling.get("prior_proposals", []) if isinstance(item, dict)],
    }


def _build_patch_request(  # pylint: disable=huawei-too-many-arguments
    *,
    source_prompt: str,
    source_harness: dict[str, Any],
    evidence: dict[str, Any],
    generation_context: dict[str, Any],
    rejected_capabilities: list[dict[str, Any]],
) -> str:
    model_evidence = {key: value for key, value in evidence.items() if not key.startswith("_")}
    internal_guard = evidence.get("_prompt_leakage_guard")
    internal_guard = internal_guard if isinstance(internal_guard, Mapping) else {}
    case_ids = {str(case_id) for case_id in internal_guard.get("case_ids", []) if str(case_id)}
    payload = {
        "improvement_protocol": {
            "version": GENERIC_IMPROVER_PROTOCOL_VERSION,
            "objective": "Convert one evidenced behavior gap into one falsifiable intervention.",
        },
        "current_system_prompt": source_prompt,
        "current_harness_json": source_harness,
        "supported_mutation_contract": {
            "prompt_append": _PROMPT_PATH,
            "budget_fields": sorted(_ALLOWED_HARNESS_FIELDS),
            "unsupported": [
                "task implementation code",
                "tool implementation",
                "skill package",
                "benchmark or evaluator",
                "model weights",
                "environment",
            ],
        },
        "failure_evidence": model_evidence,
        "sibling_generation": _sanitize_model_evidence(generation_context, case_ids),
        "rejected_capabilities": _sanitize_model_evidence(rejected_capabilities, case_ids),
    }
    return f"""Create one minimal, domain-independent behavior intervention for the current AI Harness.

This is sibling candidate {generation_context["generation_index"]} of
{generation_context["candidate_count"]}. Its secondary differentiation lens is:
{generation_context["differentiation_focus"]}.
Use that lens only when the evidence supports it; it must not override the diagnosed
failed requirement or invent another root cause. Do not duplicate the semantic
intervention represented by prior_proposals.

Return ONLY this JSON shape:
{{
  "system_prompt_append": "a concise instruction block to append",
  "harness_updates": {{}},
  "rationale": "evidence-grounded reason",
  "expected_effect": "observable behavior change"
}}

Rules:
- system_prompt_append is required, must be no more than {_MAX_PROMPT_APPEND_CHARS} characters,
  and must not repeat text already in the current prompt.
- harness_updates is optional. Its only legal keys are max_steps and
  rollout_wall_clock_seconds. Change them only when the evidence specifically
  shows a step or wall-clock budget failure.
- Do not replace or summarize the current prompt. Return only the new append.
- Obey supported_mutation_contract. Do not disguise an unsupported Tool, Skill,
  Config, environment, evaluator, or framework defect as a Prompt defect.
- Keep one primary behavioral intervention expressed as trigger -> action ->
  observable. Do not write generic advice or a broad checklist.
- Read analyzer evidence_status before proposing the change. For `confirmed`,
  repair the confirmed decision. For `supported_hypothesis`, make the candidate a
  falsifiable experiment and state the predicted observable without upgrading the
  hypothesis to fact. Never invent a repair from `insufficient` evidence.
- The rationale must cite the observed behavior and the discriminator. The
  expected_effect must name what should be visible in the next trajectory or
  artifact, not merely claim that quality will improve.
- Paired candidate feedback outranks the original explanation. If a prior
  intervention activated without improving its target metric, do not repeat or
  paraphrase it. If it did not activate, change activation rather than the
  underlying semantic rule.
- The appended text is a global, reusable policy. Never copy a case ID, issue ID,
  known/gold/reference answer, expected output, benchmark-specific entity list,
  fixed answer set, or other case-specific literal into it.
- Never introduce or name a tool argument/return field unless the evidence marks
  it as part of that tool's public request schema. A response-only, hidden,
  private, or merely observed field is not a valid request field.
- Generalize the causal distinction and observable behavior. Do not memorize the
  benchmark instance. Redaction markers in the evidence must remain redacted.
- Treat analyzer_causal_digest, analyzer_diagnoses, and
  candidate_feedback_delta as primary. Use failed_case_evidence only as a
  bounded compatibility fallback when legacy_fallback_used is true.

INPUT:
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def _patch_validation_errors(
    value: dict[str, Any],
    *,
    leakage_guard: dict[str, Any] | None = None,
) -> list[str]:
    try:
        _validate_patch(
            value,
            source_prompt="",
            leakage_guard=leakage_guard,
        )
    except (TypeError, ValueError) as exc:
        return [str(exc)]
    return []


def _validate_patch(
    value: dict[str, Any],
    *,
    source_prompt: str,
    leakage_guard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("patch must be a mapping")
    allowed_top_level = {"system_prompt_append", "harness_updates", "rationale", "expected_effect"}
    unknown = sorted(set(value) - allowed_top_level)
    if unknown:
        raise ValueError(f"unknown patch fields: {unknown}")
    append = str(value.get("system_prompt_append", "") or "").strip()
    if not append:
        raise ValueError("system_prompt_append must not be empty")
    if len(append) > _MAX_PROMPT_APPEND_CHARS:
        raise ValueError(f"system_prompt_append exceeds {_MAX_PROMPT_APPEND_CHARS} characters")
    if source_prompt and append.casefold() in source_prompt.casefold():
        raise ValueError("system_prompt_append already exists in the parent prompt")
    _validate_global_prompt_append(append, leakage_guard or {})

    raw_updates = value.get("harness_updates", {})
    if not isinstance(raw_updates, dict):
        raise TypeError("harness_updates must be a mapping")
    normalized_updates: dict[str, int] = {}
    for raw_key, raw_value in raw_updates.items():
        key = "rollout_wall_clock_seconds" if raw_key in _WALL_CLOCK_ALIASES else str(raw_key)
        if key not in _ALLOWED_HARNESS_FIELDS:
            raise ValueError(f"harness update is not allowed: {raw_key}")
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise TypeError(f"harness update {raw_key} must be an integer")
        if key == "max_steps" and not 1 <= raw_value <= 5_000:
            raise ValueError("max_steps must be between 1 and 5000")
        if key == "rollout_wall_clock_seconds" and not 30 <= raw_value <= 14_400:
            raise ValueError("rollout_wall_clock_seconds must be between 30 and 14400")
        normalized_updates[key] = raw_value
    return {
        "system_prompt_append": append,
        "harness_updates": normalized_updates,
        "rationale": str(value.get("rationale", "") or "").strip(),
        "expected_effect": str(value.get("expected_effect", "") or "").strip(),
    }


def _validate_global_prompt_append(append: str, guard: dict[str, Any]) -> None:
    normalized = append.casefold()
    for case_id in guard.get("case_ids", []):
        case_id = str(case_id).strip()
        if case_id and re.search(re.escape(case_id), append, flags=re.IGNORECASE):
            raise ValueError("system_prompt_append contains a benchmark case id")

    for literal in guard.get("sensitive_literals", []):
        literal = str(literal).strip()
        if len(literal) >= 4 and literal.casefold() in normalized:
            raise ValueError("system_prompt_append contains a known answer or benchmark entity literal")

    for field in guard.get("non_public_tool_fields", []):
        field = str(field).strip()
        if field and re.search(rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])", append, re.IGNORECASE):
            raise ValueError(f"system_prompt_append names a non-public tool field: {field}")

    public_fields = {str(field).casefold() for field in guard.get("public_tool_fields", []) if str(field).strip()}
    if not public_fields:
        return
    claimed_fields = {
        match.group(1).casefold()
        for pattern in (
            r"(?:field|parameter|argument|key)\s+[`\"']([A-Za-z_][A-Za-z0-9_.-]*)[`\"']",
            r"[`\"']([A-Za-z_][A-Za-z0-9_.-]*)[`\"']\s*:",
        )
        for match in re.finditer(pattern, append, flags=re.IGNORECASE)
    }
    unknown_fields = sorted(claimed_fields - public_fields)
    if unknown_fields:
        raise ValueError(f"system_prompt_append names fields outside the public tool schema: {unknown_fields}")


def _apply_patch(
    *,
    prompt_path: Path,
    harness_path: Path,
    source_prompt: str,
    source_harness: dict[str, Any],
    patch: dict[str, Any],
) -> None:
    prompt = source_prompt.rstrip() + "\n\n" + patch["system_prompt_append"].strip() + "\n"
    prompt_path.write_text(prompt, encoding="utf-8")
    harness = dict(source_harness)
    harness.update(patch["harness_updates"])
    harness_path.write_text(json.dumps(harness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _verify_candidate_tree(
    *,
    source_tree: dict[str, str],
    source_harness: Path,
    candidate_harness: Path,
) -> list[str]:
    candidate_tree = _tree_hashes(candidate_harness)
    if set(candidate_tree) != set(source_tree):
        raise RuntimeError("candidate PolicyHarness must preserve the complete source directory tree")
    changed = sorted(path for path in source_tree if source_tree[path] != candidate_tree[path])
    illegal = sorted(set(changed) - {_PROMPT_PATH, _HARNESS_PATH})
    if illegal:
        raise RuntimeError(f"candidate modified forbidden PolicyHarness files: {illegal}")
    for relative in source_tree:
        if relative.endswith(".py") and source_tree[relative] != candidate_tree[relative]:
            raise RuntimeError(f"candidate modified execution framework Python: {relative}")

    before_harness = _read_json_mapping(source_harness / _HARNESS_PATH)
    after_harness = _read_json_mapping(candidate_harness / _HARNESS_PATH)
    changed_fields = {
        key for key in set(before_harness) | set(after_harness) if before_harness.get(key) != after_harness.get(key)
    }
    if changed_fields - _ALLOWED_HARNESS_FIELDS:
        raise RuntimeError(f"candidate changed forbidden harness.json fields: {sorted(changed_fields)}")
    if _PROMPT_PATH not in changed:
        raise RuntimeError("candidate did not change system_prompt.md")
    return changed


def _build_actions(
    *,
    patch: dict[str, Any],
    issue_ids: list[str],
    case_ids: list[str],
) -> list[dict[str, Any]]:
    common = {
        "role": _ROLE,
        "action_group": "prompt",
        "operation": "modify",
        "action_type": "update_file",
        "attributed_issue_ids": issue_ids,
        "depends_on": [],
        "run_if": "dependency_succeeded",
        "allowed_skills": [],
        "allowed_tools": [],
        "expected_effect": patch["expected_effect"],
        "risk_notes": ["Only the current PolicyHarness prompt/config package is changed."],
        "constraints": {"target_case_ids": case_ids},
    }
    actions = [
        {
            **common,
            "action_id": "evobench_policy_prompt",
            "target_path": _PROMPT_PATH,
            "description": "Append one evidence-grounded PolicyHarness instruction block.",
            "rationale": patch["rationale"],
        }
    ]
    if patch["harness_updates"]:
        actions.append(
            {
                **common,
                "action_id": "evobench_policy_budget",
                "target_path": _HARNESS_PATH,
                "description": "Adjust only the permitted PolicyHarness execution budget fields.",
                "rationale": patch["rationale"],
                "depends_on": ["evobench_policy_prompt"],
            }
        )
    return actions


def _build_plan(
    *,
    source_harness: Path,
    issue_ids: list[str],
    case_ids: list[str],
    actions: list[dict[str, Any]],
    generation_context: dict[str, Any],
) -> dict[str, Any]:
    evidence_refs = [{"case_id": case_id, "issue_id": issue_ids[0]} for case_id in case_ids]
    return {
        "plan_id": f"evobench_policy_{generation_context['generation_index']:03d}",
        "targets": [
            {
                "role": _ROLE,
                "member_name": _ROLE,
                "harness_ref_path": str(source_harness),
                "attributed_issue_ids": issue_ids,
                "confidence": 1.0,
                "reason": "Current PolicyHarness optimization target selected from Analyzer evidence.",
                "evidence_refs": evidence_refs,
                "mechanism_types": ["instruction"],
                "optimization_surfaces": ["prompt_section"],
            }
        ],
        "actions": actions,
        "action_waves": [[action["action_id"]] for action in actions],
        "metadata": {
            "optimizer_kind": "evobench_policy_harness_v1",
            "improver_protocol_version": GENERIC_IMPROVER_PROTOCOL_VERSION,
            "generation_context": generation_context,
            "target_case_ids": case_ids,
        },
    }


def _capabilities(actions: list[dict[str, Any]], case_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "action_id": action["action_id"],
            "role": _ROLE,
            "action_group": "prompt",
            "operation": "modify",
            "target_path": action["target_path"],
            "runtime_name": Path(action["target_path"]).stem,
            "expected_effect": action.get("expected_effect", ""),
            "target_case_ids": case_ids,
        }
        for action in actions
    ]


def _candidate_refs_payload(
    *,
    source_refs: dict[str, Any],
    source_refs_path: Path,
    candidate_harness: Path,
    defer_publish: bool,
) -> dict[str, Any]:
    payload = dict(source_refs)
    payload["version"] = payload.get("version", 1)
    payload["source_harness_refs_path"] = str(source_refs_path)
    payload["promotion_status"] = "pending_gate" if defer_publish else "published"
    payload["harness_refs"] = {_ROLE: str(candidate_harness)}
    payload["roles"] = [
        {
            "role": _ROLE,
            "member_name": _ROLE,
            "harness_ref_path": str(candidate_harness),
        }
    ]
    payload["role_results"] = {
        _ROLE: {
            "status": "candidate_ready" if defer_publish else "published",
            "after_harness_ref_path": str(candidate_harness),
        }
    }
    return payload


def _issue_case_ids(issues: list[dict[str, Any]]) -> list[str]:
    case_ids: set[str] = set()
    for issue in issues:
        case_ids.update(str(case_id) for case_id in issue.get("affected_cases", []) if str(case_id))
        for evidence in issue.get("evidence", []):
            if isinstance(evidence, dict) and str(evidence.get("case_id", "") or ""):
                case_ids.add(str(evidence["case_id"]))
    return sorted(case_ids)


def _allocate_run_dir(output_root: Path) -> Path:
    index = 1
    while True:
        run_dir = output_root / f"member_optimization_{index:03d}"
        try:
            run_dir.mkdir(parents=False, exist_ok=False)
            return run_dir
        except FileExistsError:
            index += 1


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML artifact must be a mapping: {path}")
    return payload


def _read_json_mapping(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON artifact must be a mapping: {path}")
    return payload


def _write_yaml_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


__all__ = [
    "EvoBenchPolicyHarnessOptimizer",
    "PolicyHarnessRSIOptimizer",
]
