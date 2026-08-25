# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Constrained RSI candidate optimizer for Evo-Bench PolicyHarness packages.

The regular member optimizer understands Expert Harness manifests. Evo-Bench
owns a different, deliberately small PolicyHarness package contract. This
adapter preserves the regular optimizer artifact protocol while allowing only
the PolicyHarness surfaces declared by the runtime package itself. Python
execution code remains immutable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
import copy
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
_SKILLS_DIR = "skills"
_BUDGET_HARNESS_FIELDS = {"command_timeout_seconds", "max_steps", "rollout_wall_clock_seconds"}
_RAIL_HARNESS_FIELDS = {
    "tool_loop_compaction": {"enabled", "consecutive_threshold", "bailout_threshold"},
    "submission_checkpoint": {"enabled", "instruction", "max_revisions"},
}
_ALLOWED_HARNESS_FIELDS = _BUDGET_HARNESS_FIELDS | set(_RAIL_HARNESS_FIELDS)
_WALL_CLOCK_ALIASES = {"wall_clock", "wall_clock_seconds"}
_MAX_PROMPT_APPEND_CHARS = 8_000
_MAX_SKILL_DESCRIPTION_CHARS = 500
_MAX_SKILL_BODY_CHARS = 12_000
_MAX_STRUCTURED_EVIDENCE_CHARS = 40_000
_MAX_LEGACY_RESULT_CHARS = 3_000
_MAX_LEGACY_TRACE_CHARS = 4_000
GENERIC_IMPROVER_PROTOCOL_VERSION = "generic_behavior_intervention_v21"
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
_TASK_SPECIFIC_VALUE_KEYS = {
    "benchmark_entities",
    "fixed_values",
    "non_generalizable_literals",
    "proper_nouns",
    "task_entities",
}
_PUBLIC_TASK_TEXT_KEYS = {"input", "input_excerpt", "prompt", "query", "task_input"}
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
_OUTCOME_ONLY_CAUSAL_PATTERNS = (
    re.compile(r"\b(?:evaluator|grader|scorer|rubric|criterion|criteria)\s+(?:expects?|requires?)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:match|align(?:ment)?)\b.{0,40}\b(?:expected|gold|scored)\s+(?:answer|label|verdict|outcome)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\breverse[- ]keyed\b", re.IGNORECASE),
)
_PERSISTENT_HIDDEN_OUTCOME_PATTERNS = (
    re.compile(
        r"\b(?:compare|match|align|equal|verify|validate|confirm)\w*\b.{0,80}"
        r"\b(?:expected|gold|reference|known)\s+(?:answer|result|output|value|outcome)s?\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:expected|gold|reference|known)\s+(?:answer|result|output|value|outcome)s?\b.{0,80}"
        r"\b(?:compare|match|align|equal|verify|validate|confirm)\w*\b",
        re.IGNORECASE | re.DOTALL,
    ),
)

PatchGenerator = Callable[[str], dict[str, Any] | Awaitable[dict[str, Any]]]
TransferReviewer = Callable[[str], dict[str, Any] | Awaitable[dict[str, Any]]]


class PolicyHarnessRSIOptimizer:
    """Generate one safe PolicyHarness candidate for the RSI orchestrator."""

    def __init__(
        self,
        config: Any | None = None,
        *,
        model_config_ref: str = "",
        patch_generator: PatchGenerator | None = None,
        transfer_reviewer: TransferReviewer | None = None,
    ) -> None:
        self.config = config
        self.model_config_ref = str(model_config_ref or getattr(config, "model_config_ref", "") or "").strip()
        self._patch_generator = patch_generator
        self._transfer_reviewer = transfer_reviewer

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
            raise ValueError("PolicyHarness optimization requires an actionable, evidence-grounded analysis issue")

        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        run_dir = _allocate_run_dir(output_root)
        requested_surfaces = _requested_issue_surfaces(selected_issues)
        source_prompt_path = source_harness / _PROMPT_PATH
        source_harness_path = source_harness / _HARNESS_PATH
        if not source_prompt_path.is_file() or not source_harness_path.is_file():
            raise ValueError(f"PolicyHarness must contain system_prompt.md and harness.json: {source_harness}")
        source_harness_json = _read_json_mapping(source_harness_path)
        skill_mutation_policy = _skill_mutation_policy(
            source_harness=source_harness,
            selected_issues=selected_issues,
            requested_surfaces=requested_surfaces,
        )
        supported_surfaces = {"prompt", "skill", "budget"}
        if any(isinstance(source_harness_json.get(field), Mapping) for field in _RAIL_HARNESS_FIELDS):
            supported_surfaces.add("rail")
        unsupported_surfaces = sorted(requested_surfaces - supported_surfaces)
        if unsupported_surfaces:
            return _write_unsupported_surface_artifact(
                run_dir=run_dir,
                source_refs_path=source_refs_path,
                source_harness=source_harness,
                selected_issues=selected_issues,
                requested_surfaces=requested_surfaces,
                unsupported_surfaces=unsupported_surfaces,
            )
        candidate_harness = _candidate_harness_path(run_dir, source_harness)
        candidate_harness.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_harness, candidate_harness)

        prompt_path = candidate_harness / _PROMPT_PATH
        harness_path = candidate_harness / _HARNESS_PATH
        if not prompt_path.is_file() or not harness_path.is_file():
            raise ValueError(f"PolicyHarness must contain system_prompt.md and harness.json: {source_harness}")
        source_prompt = prompt_path.read_text(encoding="utf-8")
        source_tree = _tree_hashes(source_harness)

        evidence = _build_evidence_bundle(
            eval_ref_path=eval_ref_path,
            analysis_path=analysis_path,
            analysis=analysis,
            selected_issues=selected_issues,
            hypotheses_path=optimization_hypotheses_path,
        )
        causal_hypothesis_policy = _causal_hypothesis_policy(selected_issues)
        causal_binding = _causal_binding(selected_issues)
        _validate_causal_binding_independence(causal_binding)
        required_budget_fields = _required_budget_fields(selected_issues, source_harness_json)
        leakage_guard = _build_prompt_leakage_guard(
            selected_issues=selected_issues,
            evidence=evidence,
        )
        generation_context = _generation_context(optimization_experience)
        request = _build_patch_request(
            source_prompt=source_prompt,
            source_harness=source_harness_json,
            evidence=evidence,
            causal_hypothesis_policy=causal_hypothesis_policy,
            causal_binding=causal_binding,
            generation_context=generation_context,
            rejected_capabilities=rejected_capabilities or [],
            requested_surfaces=requested_surfaces,
            required_budget_fields=required_budget_fields,
            skill_mutation_policy=skill_mutation_policy,
        )
        raw_patch = await self._generate_patch(
            request,
            run_dir=run_dir,
            source_harness=source_harness_json,
            leakage_guard=leakage_guard,
            causal_hypothesis_policy=causal_hypothesis_policy,
            causal_binding=causal_binding,
            requested_surfaces=requested_surfaces,
            required_budget_fields=required_budget_fields,
            skill_mutation_policy=skill_mutation_policy,
        )
        patch = _validate_patch(
            raw_patch,
            source_prompt=source_prompt,
            source_harness=source_harness_json,
            leakage_guard=leakage_guard,
            causal_hypothesis_policy=causal_hypothesis_policy,
            requested_surfaces=requested_surfaces,
            required_budget_fields=required_budget_fields,
            skill_mutation_policy=skill_mutation_policy,
        )
        _apply_patch(
            prompt_path=prompt_path,
            harness_path=harness_path,
            candidate_harness=candidate_harness,
            source_prompt=source_prompt,
            source_harness=source_harness_json,
            patch=patch,
            skill_mutation_policy=skill_mutation_policy,
        )
        changed_paths = _verify_candidate_tree(
            source_tree=source_tree,
            source_harness=source_harness,
            candidate_harness=candidate_harness,
            requested_surfaces=requested_surfaces,
            skill_mutation_policy=skill_mutation_policy,
        )

        issue_ids = [str(issue["issue_id"]) for issue in selected_issues]
        case_ids = _issue_case_ids(selected_issues)
        analyzer_counterfactual_predictions = _issue_counterfactual_predictions(selected_issues)
        actions = _build_actions(
            patch=patch,
            issue_ids=issue_ids,
            case_ids=case_ids,
            source_hypothesis_id=patch["source_hypothesis_id"],
            source_hypothesis_semantic_id=str(
                causal_hypothesis_policy.get("semantic_ids_by_hypothesis_id", {}).get(
                    patch["source_hypothesis_id"],
                    "",
                )
            ),
            analyzer_counterfactual_predictions=analyzer_counterfactual_predictions,
            skill_mutation_policy=skill_mutation_policy,
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
                "allowed_paths": [_PROMPT_PATH, _HARNESS_PATH, f"{_SKILLS_DIR}/*/SKILL.md"],
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
                "allowed_mutation_paths": [_PROMPT_PATH, _HARNESS_PATH, f"{_SKILLS_DIR}/*/SKILL.md"],
                "generation_context": generation_context,
                "skill_mutation_policy": skill_mutation_policy,
                "source_causal_hypothesis_id": patch["source_hypothesis_id"],
                "causal_binding_digest": _canonical_digest(causal_binding),
                "transfer_audit_path": str(run_dir / "transfer_audit.json")
                if (run_dir / "transfer_audit.json").is_file()
                else "",
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
        source_harness: dict[str, Any],
        leakage_guard: dict[str, Any],
        causal_hypothesis_policy: dict[str, Any],
        causal_binding: dict[str, Any],
        requested_surfaces: set[str],
        required_budget_fields: set[str],
        skill_mutation_policy: dict[str, Any],
    ) -> dict[str, Any]:
        if self._patch_generator is not None:
            generated = self._patch_generator(request)
            candidate = await generated if inspect.isawaitable(generated) else generated
            if self._transfer_reviewer is None or not ({"prompt", "skill", "rail", "unspecified"} & requested_surfaces):
                return candidate
            return await _review_injected_candidate(
                candidate=candidate,
                request=request,
                generator=self._patch_generator,
                reviewer=self._transfer_reviewer,
                causal_binding=causal_binding,
            )
        if not self.model_config_ref:
            raise RuntimeError("model_config_ref is required for PolicyHarness optimization")
        return await _invoke_patch_agent(
            request=request,
            model_config_ref=self.model_config_ref,
            workspace=run_dir,
            source_harness=source_harness,
            leakage_guard=leakage_guard,
            causal_hypothesis_policy=causal_hypothesis_policy,
            causal_binding=causal_binding,
            requested_surfaces=requested_surfaces,
            required_budget_fields=required_budget_fields,
            skill_mutation_policy=skill_mutation_policy,
        )


EvoBenchPolicyHarnessOptimizer = PolicyHarnessRSIOptimizer


def _requested_issue_surfaces(issues: list[dict[str, Any]]) -> set[str]:
    """Resolve Analyzer targets without silently converting them to prompts."""
    surfaces: set[str] = set()
    for issue in issues:
        metadata = issue.get("metadata") if isinstance(issue.get("metadata"), Mapping) else {}
        attribution = metadata.get("attribution") if isinstance(metadata.get("attribution"), Mapping) else {}
        target_ref = str(attribution.get("target_ref", "") or "").strip().casefold()
        if not target_ref:
            surfaces.add("unknown")
            continue
        tokens = {token for token in re.split(r"[^a-z0-9_]+", target_ref) if token}
        if tokens & {"tool", "tools"}:
            surfaces.add("tool")
        elif tokens & {"skill", "skills"}:
            surfaces.add("skill")
        elif tokens & {"rail", "rails"}:
            surfaces.add("rail")
        elif tokens & {"budget", "execution_budget"}:
            surfaces.add("budget")
        elif tokens & {"config", "configuration"}:
            surfaces.add("config")
        elif tokens & {"prompt", "system_prompt", "prompt_section"}:
            # A pre-submission decision check with a runtime-visible record is
            # a control boundary, not more static prompt content. Routing this
            # failure class to the declared checkpoint rail avoids repeating a
            # prompt intervention that the candidate can simply skip.
            surfaces.add("rail" if _requires_submission_checkpoint(issue) else "prompt")
        else:
            surfaces.add("unknown")
    return surfaces


def _requires_submission_checkpoint(issue: Mapping[str, Any]) -> bool:
    metadata = issue.get("metadata") if isinstance(issue.get("metadata"), Mapping) else {}
    attribution = metadata.get("attribution") if isinstance(metadata.get("attribution"), Mapping) else {}
    failure_mode = " ".join(
        str(value or "")
        for value in (
            issue.get("summary"),
            attribution.get("root_cause"),
            attribution.get("general_mechanism"),
            *(evidence.get("failure_mode") for evidence in issue.get("evidence", []) if isinstance(evidence, Mapping)),
        )
    ).casefold()
    # These failures share one enforceable boundary: an unverified result
    # survived into the released answer or deliverable. Static guidance can be
    # skipped, so route the repair to a final-response checkpoint regardless of
    # a model-authored phase label.
    explicit_modes = {
        "unverified_decision_ground_used",
        "unverified_computation_written_as_validated",
        "stale_cached_values_from_crashed_engine",
    }
    if any(mode in failure_mode for mode in explicit_modes):
        return True
    release_terms = ("released answer", "final answer", "deliverable", "submitted", "written")
    return "unverified" in failure_mode and any(term in failure_mode for term in release_terms)


def _skill_mutation_policy(
    *,
    source_harness: Path,
    selected_issues: list[dict[str, Any]],
    requested_surfaces: set[str],
) -> dict[str, Any]:
    """Choose add versus update from references to runtime-visible Skills."""
    if "skill" not in requested_surfaces:
        return {"operation": "none", "allowed_names": [], "required_name": "", "existing_skills": []}

    existing = _source_skill_specs(source_harness)
    evidence_text = "\n".join(_iter_text_values(selected_issues)).casefold()
    referenced = [
        skill
        for skill in existing
        if any(
            re.search(
                rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
                evidence_text,
            )
            for alias in {
                str(skill["name"]).casefold(),
                str(skill["name"]).casefold().replace("-", "_"),
            }
        )
    ]
    if referenced:
        names = [str(skill["name"]) for skill in referenced]
        return {
            "operation": "update",
            "allowed_names": names,
            "required_name": names[0] if len(names) == 1 else "",
            "existing_skills": referenced,
        }
    return {
        "operation": "add",
        "allowed_names": [],
        "required_name": "",
        "existing_skills": [
            {"name": str(skill["name"]), "description": str(skill["description"])} for skill in existing
        ],
    }


def _source_skill_specs(source_harness: Path) -> list[dict[str, str]]:
    skills_root = source_harness / _SKILLS_DIR
    if not skills_root.is_dir():
        return []
    specs: list[dict[str, str]] = []
    for skill_path in sorted(skills_root.glob("*/SKILL.md")):
        text = skill_path.read_text(encoding="utf-8")
        metadata: dict[str, Any] = {}
        body = text.strip()
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                loaded = yaml.safe_load(parts[1]) or {}
                metadata = dict(loaded) if isinstance(loaded, Mapping) else {}
                body = parts[2].strip()
        specs.append(
            {
                "name": skill_path.parent.name,
                "description": str(metadata.get("description", "") or "").strip(),
                "body": body,
            }
        )
    return specs


def _iter_text_values(value: Any) -> Iterator[str]:
    if isinstance(value, Mapping):
        for nested in value.values():
            yield from _iter_text_values(nested)
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            yield from _iter_text_values(nested)
    elif isinstance(value, str):
        yield value


def _required_budget_fields(
    issues: list[dict[str, Any]],
    source_harness: Mapping[str, Any],
) -> set[str]:
    """Bind a budget mutation to the exact runtime limit named by evidence."""
    required: set[str] = set()
    for issue in issues:
        metadata = issue.get("metadata") if isinstance(issue.get("metadata"), Mapping) else {}
        attribution = metadata.get("attribution") if isinstance(metadata.get("attribution"), Mapping) else {}
        decision_contract = (
            attribution.get("decision_contract") if isinstance(attribution.get("decision_contract"), Mapping) else {}
        )
        primary_text = str(decision_contract.get("required_action", "") or "")
        fields = _budget_fields_named_in_text(primary_text, source_harness)
        if not fields:
            causal_coverage = (
                attribution.get("causal_coverage") if isinstance(attribution.get("causal_coverage"), Mapping) else {}
            )
            fields = _budget_fields_named_in_text(
                str(causal_coverage.get("counterfactual_prediction", "") or ""),
                source_harness,
            )
        required.update(fields)
    return required


def _budget_fields_named_in_text(text: str, source_harness: Mapping[str, Any]) -> set[str]:
    return {
        field
        for field in _BUDGET_HARNESS_FIELDS
        if field in source_harness and re.search(rf"(?<![A-Za-z0-9_]){re.escape(field)}(?![A-Za-z0-9_])", text)
    }


def _write_unsupported_surface_artifact(
    *,
    run_dir: Path,
    source_refs_path: Path,
    source_harness: Path,
    selected_issues: list[dict[str, Any]],
    requested_surfaces: set[str],
    unsupported_surfaces: list[str],
) -> str:
    """Return a rejected proposal artifact instead of fabricating a prompt fix."""
    issue_ids = [str(issue.get("issue_id", "") or "") for issue in selected_issues]
    case_ids = _issue_case_ids(selected_issues)
    plan_path = run_dir / "plan.yaml"
    _write_yaml_atomic(
        plan_path,
        {
            "plan_id": f"evobench_policy_{run_dir.name}",
            "targets": [
                {
                    "role": _ROLE,
                    "member_name": _ROLE,
                    "harness_ref_path": str(source_harness),
                    "attributed_issue_ids": issue_ids,
                    "evidence_refs": [
                        {"case_id": case_id, "issue_id": issue_ids[0]} for case_id in case_ids if issue_ids
                    ],
                    "optimization_surfaces": sorted(requested_surfaces),
                }
            ],
            "actions": [],
            "action_waves": [],
            "metadata": {
                "optimizer_kind": "evobench_policy_harness_v1",
                "improver_protocol_version": GENERIC_IMPROVER_PROTOCOL_VERSION,
                "requested_surfaces": sorted(requested_surfaces),
                "unsupported_surfaces": unsupported_surfaces,
            },
        },
    )
    execution_path = run_dir / "execution_results.json"
    _write_json_atomic(
        execution_path,
        {
            "status": "unsupported_surface",
            "requested_surfaces": sorted(requested_surfaces),
            "unsupported_surfaces": unsupported_surfaces,
            "actions": [],
        },
    )
    artifact = {
        "optimization_id": run_dir.name,
        "output_dir": str(run_dir),
        "status": "unsupported_surface",
        "optimized_harness_refs_path": str(source_refs_path),
        "roles": [_ROLE],
        "candidate_ready_roles": [],
        "published_roles": [],
        "staged_roles": [],
        "verified_roles": [],
        "promoted_roles": [],
        "promotion_status": "not_applicable",
        "composition_mode": "opaque_snapshot",
        "failed_roles": [_ROLE],
        "skipped_roles": [],
        "plan_path": str(plan_path),
        "execution_result_path": str(execution_path),
        "verification_path": "",
        "fix_result_path": "",
        "role_results": {
            _ROLE: {
                "status": "unsupported_surface",
                "before_harness_ref_path": str(source_harness),
                "after_harness_ref_path": "",
                "action_ids": [],
                "verification_status": "not_run",
                "error": (
                    "The current PolicyHarness mutation contract does not expose " + ", ".join(unsupported_surfaces)
                ),
            }
        },
        "metadata": {
            "source_harness_refs_path": str(source_refs_path),
            "optimization_issue_ids": issue_ids,
            "target_case_ids": case_ids,
            "optimizer_kind": "evobench_policy_harness_v1",
            "improver_protocol_version": GENERIC_IMPROVER_PROTOCOL_VERSION,
            "requested_surfaces": sorted(requested_surfaces),
            "unsupported_surfaces": unsupported_surfaces,
            "routing_decision": "reject_without_prompt_downgrade",
        },
        "role": _ROLE,
    }
    ref_path = run_dir / "member_optimization_ref.yaml"
    _write_yaml_atomic(ref_path, artifact)
    return str(ref_path)


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
    source_harness: dict[str, Any],
    leakage_guard: dict[str, Any],
    causal_hypothesis_policy: dict[str, Any],
    causal_binding: dict[str, Any],
    requested_surfaces: set[str],
    required_budget_fields: set[str],
    skill_mutation_policy: dict[str, Any],
) -> dict[str, Any]:
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.harness.factory import create_deep_agent
    from openjiuwen.rsi.member_optimizer.agents.factory import load_member_optimizer_model
    from openjiuwen.rsi.member_optimizer.agents.output import (
        invoke_member_optimizer_agent_structured,
        parse_yaml_or_json_object_response,
    )

    model = load_member_optimizer_model(model_config_ref)
    generator = create_deep_agent(
        model=model,
        card=AgentCard(
            name="evobench_policy_harness_optimizer",
            description="Produces one constrained Evo-Bench PolicyHarness patch.",
        ),
        system_prompt=(
            "You improve an AI Harness from task contracts, observed behavior, and "
            "paired evaluation evidence. The task domain is not assumed. Separate facts "
            "from hypotheses and return only the requested JSON mapping. Make one "
            "falsifiable behavior intervention within the supplied mutation contract. "
            "Express every persistent Prompt, Skill, or Rail instruction as a transferable behavior rule rather than "
            "an answer for the observed benchmark instance. Persistent prompt text must "
            "remain valid across materially different task domains and must omit the "
            "observed domain, artifact type, application, command, library, and example. Rail instructions obey "
            "the same global transfer rule as Prompt text. "
            "A Skill may retain the public domain operations required by its capability, "
            "but it must transfer across different tasks and omit instance literals. "
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
    reviewer = create_deep_agent(
        model=model,
        card=AgentCard(
            name="evobench_policy_harness_transfer_reviewer",
            description="Audits one candidate without authoring or rewriting it.",
        ),
        system_prompt=(
            "You are an independent Harness intervention auditor, not an improver. "
            "Never propose, rewrite, or complete a candidate. Judge only whether the "
            "submitted candidate preserves the controller-frozen causal binding, avoids "
            "task-answer leakage, and transfers beyond the observed instance. Return only "
            "the requested JSON audit."
        ),
        tools=None,
        workspace=str(workspace),
        enable_task_loop=False,
        max_iterations=2,
        language="en",
        restrict_to_work_dir=True,
        auto_create_workspace=True,
    )

    forbidden_prompt_terms: list[str] = []

    async def generate(message: str) -> dict[str, Any]:
        return await invoke_member_optimizer_agent_structured(
            agent=generator,
            agent_name="EvoBenchPolicyHarnessOptimizerAgent",
            user_message=message,
            session_id=f"evobench_policy_{hashlib.sha256(message.encode('utf-8')).hexdigest()[:16]}",
            retry_limit=2,
            parse_response=parse_yaml_or_json_object_response,
            validate_response=lambda value: [
                *_patch_validation_errors(
                    value,
                    source_harness=source_harness,
                    leakage_guard=leakage_guard,
                    causal_hypothesis_policy=causal_hypothesis_policy,
                    requested_surfaces=requested_surfaces,
                    required_budget_fields=required_budget_fields,
                    skill_mutation_policy=skill_mutation_policy,
                ),
                *_forbidden_concrete_term_errors(value, forbidden_prompt_terms),
            ],
            build_retry_message=lambda _previous, error: (
                f"{message}\n\nThe previous output was invalid: {error}\nReturn only a corrected JSON mapping."
            ),
        )

    async def rewrite_rail_instruction(message: str, base_candidate: dict[str, Any]) -> dict[str, Any]:
        """Rewrite the Rail payload without letting the model mutate another surface."""

        def validate_rewrite(value: Any) -> list[str]:
            if not isinstance(value, Mapping):
                return ["rail rewrite must be a JSON mapping"]
            if set(value) != {"instruction"}:
                return ["rail rewrite must contain exactly the instruction field"]
            instruction = value.get("instruction")
            if not isinstance(instruction, str):
                return ["instruction must be a string"]
            instruction = instruction.strip()
            if not 40 <= len(instruction) <= 4_000:
                return ["instruction must contain 40-4000 characters"]
            projected = copy.deepcopy(base_candidate)
            updates = projected.setdefault("harness_updates", {})
            checkpoint = updates.setdefault("submission_checkpoint", {})
            checkpoint["instruction"] = instruction
            return [
                *_patch_validation_errors(
                    projected,
                    source_harness=source_harness,
                    leakage_guard=leakage_guard,
                    causal_hypothesis_policy=causal_hypothesis_policy,
                    requested_surfaces=requested_surfaces,
                    required_budget_fields=required_budget_fields,
                    skill_mutation_policy=skill_mutation_policy,
                ),
                *_forbidden_concrete_term_errors(projected, forbidden_prompt_terms),
                *_global_submission_checkpoint_errors(instruction),
            ]

        rewritten = await invoke_member_optimizer_agent_structured(
            agent=generator,
            agent_name="EvoBenchPolicyHarnessRailRewriterAgent",
            user_message=message,
            session_id=f"evobench_rail_rewrite_{hashlib.sha256(message.encode('utf-8')).hexdigest()[:16]}",
            retry_limit=2,
            parse_response=parse_yaml_or_json_object_response,
            validate_response=validate_rewrite,
            build_retry_message=lambda _previous, error: (
                f"{message}\n\nThe previous Rail rewrite was invalid: {error}\n"
                'Return only {"instruction": "the corrected transferable instruction"}. '
                "Do not return a Skill, Prompt, harness_updates, rationale, or any other field."
            ),
        )
        projected = copy.deepcopy(base_candidate)
        updates = projected.setdefault("harness_updates", {})
        checkpoint = updates.setdefault("submission_checkpoint", {})
        checkpoint["instruction"] = str(rewritten["instruction"]).strip()
        return projected

    candidate = await generate(request)
    if not ({"prompt", "skill", "rail", "unspecified"} & requested_surfaces):
        return candidate
    forbidden_prompt_terms.extend(_quoted_binding_phrases(causal_binding))
    abstraction_request = _build_mandatory_abstraction_request(
        candidate,
        causal_binding,
        requested_surfaces=requested_surfaces,
    )
    candidate = (
        await rewrite_rail_instruction(abstraction_request, candidate)
        if "rail" in requested_surfaces and "skill" not in requested_surfaces
        else await generate(abstraction_request)
    )

    audit_history: list[dict[str, Any]] = []
    consecutive_approvals = 0
    for review_index in range(4):
        validated = _validate_patch(
            candidate,
            source_prompt="",
            source_harness=source_harness,
            leakage_guard=leakage_guard,
            causal_hypothesis_policy=causal_hypothesis_policy,
            requested_surfaces=requested_surfaces,
            required_budget_fields=required_budget_fields,
            skill_mutation_policy=skill_mutation_policy,
        )
        review_request = _build_transfer_review_request(
            validated,
            causal_binding,
            audit_pass=review_index + 1,
            prior_substitution_families=[
                str(family)
                for prior in audit_history
                for family in (
                    (prior.get("substitution_test", {}) or {}).get("task_family_a", ""),
                    (prior.get("substitution_test", {}) or {}).get("task_family_b", ""),
                )
                if str(family)
            ],
        )
        audit = await invoke_member_optimizer_agent_structured(
            agent=reviewer,
            agent_name="EvoBenchPolicyHarnessTransferReviewerAgent",
            user_message=review_request,
            session_id=f"evobench_transfer_{hashlib.sha256(review_request.encode('utf-8')).hexdigest()[:16]}",
            retry_limit=2,
            parse_response=parse_yaml_or_json_object_response,
            validate_response=_transfer_audit_validation_errors,
            build_retry_message=lambda _previous, error: (
                f"{review_request}\n\nThe audit JSON was invalid: {error}\n"
                "Return only a corrected audit JSON mapping. Do not rewrite the candidate."
            ),
        )
        audit = _validate_transfer_audit(audit)
        audit = _enforce_controller_decision_dependency(audit, causal_binding)
        dependency_errors = _candidate_decision_dependency_errors(audit, validated)
        if dependency_errors:
            audit = {
                **audit,
                "trigger_non_vacuous": False,
                "violations": list(dict.fromkeys([*audit["violations"], *dependency_errors])),
            }
        if "rail" in requested_surfaces and not _audit_exercises_global_rail_axes(audit):
            audit = {
                **audit,
                "cross_domain_transferable": False,
                "violations": list(
                    dict.fromkeys(
                        [
                            *audit["violations"],
                            "Rail substitution audit must exercise a computed-artifact result and an "
                            "external-action postcondition, not two requirement-classification tasks",
                        ]
                    )
                ),
            }
        independently_sampled = _audit_uses_new_substitution_families(audit, audit_history)
        approved = _transfer_audit_approved(audit) and independently_sampled
        if not independently_sampled:
            audit = {
                **audit,
                "independent_substitution_sample": False,
                "violations": [
                    *audit["violations"],
                    "substitution task families repeated an earlier audit sample",
                ],
            }
        consecutive_approvals = consecutive_approvals + 1 if approved else 0
        audit_history.append(
            {
                "review_index": review_index + 1,
                "consecutive_approvals": consecutive_approvals,
                **audit,
            }
        )
        _write_json_atomic(
            workspace / "transfer_audit.json",
            {
                "schema_version": 1,
                "status": "approved"
                if consecutive_approvals >= 2
                else ("pending_consensus" if approved else "rejected"),
                "attempts": audit_history,
            },
        )
        if consecutive_approvals >= 2:
            return validated
        if approved:
            continue
        if not independently_sampled:
            if review_index == 3:
                raise ValueError("transfer audit did not produce two independent substitution samples")
            continue
        if review_index == 3:
            raise ValueError("transfer audit rejected candidate: " + "; ".join(audit["violations"]))
        forbidden_prompt_terms.extend(audit["concrete_terms"])
        repair_request = _build_generation_repair_request(request, validated, audit)
        candidate = (
            await rewrite_rail_instruction(repair_request, validated)
            if "rail" in requested_surfaces and "skill" not in requested_surfaces
            else await generate(repair_request)
        )
    raise AssertionError("unreachable transfer review state")


def _build_mandatory_abstraction_request(
    patch: dict[str, Any],
    causal_binding: dict[str, Any],
    *,
    requested_surfaces: set[str] | None = None,
) -> str:
    """Lift a concrete repair into a persistent functional decision rule."""
    surfaces = set(requested_surfaces or {"prompt"})
    skill_target = "skill" in surfaces
    rail_target = "rail" in surfaces and not skill_target
    rewrite_instruction = (
        "Rewrite only skill.name, skill.description, and skill.body. Preserve "
        "source_hypothesis_id, system_prompt_append, harness_updates, rationale, and expected_effect exactly. "
        "Retain public domain operations required to execute this reusable capability, while removing "
        "observed-instance names, exact locations, answers, values, worked solutions, and the observed task "
        "family when it appears only as an example. The resulting Skill "
        "must apply unchanged to at least two different tasks in the same capability family. Its validation "
        "must use task-visible requirements, invariants, independent recomputation, or artifact read-back; "
        "it must never compare against an expected, gold, reference, or known answer."
        if skill_target
        else (
            "Return only a JSON mapping with one field named instruction. Rewrite the current "
            "harness_updates.submission_checkpoint.instruction; the controller will preserve every other field. "
            "Remove every observed domain and implementation term so the runtime checkpoint applies unchanged "
            "across materially different task domains. It must explicitly cover all material release surfaces: "
            "factual claims, computed or derived values, artifact or state mutations, conclusions or classifications, "
            "and actions or operations. Express the observed mandatory/optional distinction as the general question "
            "of whether verified evidence makes a ground decision-changing or merely advisory; do not retain "
            "compliance-review vocabulary or an assessed-document workflow."
            if rail_target
            else "Rewrite only system_prompt_append. Preserve source_hypothesis_id, skill, harness_updates, "
            "rationale, and expected_effect exactly. Remove every observed domain and implementation term so the "
            "global policy applies unchanged across materially different task domains."
        )
    )
    return f"""Perform the mandatory abstraction pass for this Harness candidate.
{rewrite_instruction}
For a Rail target, return only {{"instruction": "..."}}. Otherwise return the
complete six-field candidate JSON mapping.

The persistent intervention must keep the same falsifiable trigger -> action ->
observable relationship. For a global Prompt, express the mechanism through functional
concepts such as requirement, evidence, dependency, operation, state, result,
validation, fallback, and completion. For a Skill, preserve the executable public
method while removing only observed-instance details. Do not turn either surface into
broad quality advice.

Preserve causal roles and ordering, not merely topic words. If the binding distinguishes
an upstream state or decision from a downstream computation, validation, or outcome,
the abstract rule must still require the upstream causal action before the downstream
operation. Never replace a required causal action with an easier proxy such as any
edit, any tool call, any saved artifact, visible progress, or a final statement.
If the intervention depends on classifying an alleged gap against requirements, retain
the full task-visible derivation: authority and scope, ownership of the required outcome,
an exhaustive inventory of atomic decision-changing output claims, including conclusions
and prescribed or recommended actions, exact-witness evidence-to-requirement entailment,
the requirement's scope and activation conditions, a countermodel attempt that can defeat the mapping,
and a final pre-release scan that restarts on any unlisted material claim,
and the gate for a negative decision or prescribed correction. A downstream rule that
begins only after the agent has "confirmed" or "concluded" the controlling
classification, or lets a later draft claim bypass the inventory, is not a causal
intervention and must be rewritten to include that upstream procedure.
Treat every scope_boundary entry as immutable. Inspect the full persistent
intervention, including fallback and recovery sections, and remove any action that
contradicts a boundary; never trade semantic correctness for visible completion.
Do not copy, quote, negate, or present as an example any phrase in
OBSERVED_QUOTED_PHRASES. Express the underlying branch rule functionally instead.

OBSERVED_QUOTED_PHRASES:
{json.dumps(_quoted_binding_phrases(causal_binding), ensure_ascii=False, indent=2)}

CAUSAL_BINDING (controller-frozen; preserve its causal roles):
{json.dumps(causal_binding, ensure_ascii=False, indent=2)}

SOURCE CANDIDATE:
{json.dumps(patch, ensure_ascii=False, indent=2)}
"""


def _build_transfer_review_request(
    patch: dict[str, Any],
    causal_binding: dict[str, Any],
    *,
    audit_pass: int = 1,
    prior_substitution_families: list[str] | None = None,
) -> str:
    """Ask an independent model to audit without changing candidate semantics."""
    skill = patch.get("skill") if isinstance(patch.get("skill"), Mapping) else {}
    skill_target = bool(skill)
    rail_updates = patch.get("harness_updates") if isinstance(patch.get("harness_updates"), Mapping) else {}
    checkpoint = (
        rail_updates.get("submission_checkpoint")
        if isinstance(rail_updates.get("submission_checkpoint"), Mapping)
        else {}
    )
    rail_target = bool(str(checkpoint.get("instruction", "") or "").strip())
    persistent_field = (
        "skill.description and skill.body"
        if skill_target
        else ("harness_updates.submission_checkpoint.instruction" if rail_target else "system_prompt_append")
    )
    controller_dependency = _controller_decision_dependency_contract(causal_binding)
    detail_rule = (
        "For a Skill, public artifact classes, commands, libraries, and algorithms that are necessary to execute "
        "the declared capability are allowed. concrete_terms must instead contain observed-instance source names, "
        "case-specific locations, known answers, exact target values, benchmark labels, and worked solutions. "
        "Also flag the observed task-family label when it is included only as an example and is not needed to "
        "define or execute the capability. "
        "cross_domain_transferable means unchanged transfer to two different tasks inside the same declared "
        "capability family; the tasks may share the public tool or artifact class."
        if skill_target
        else "For a global Prompt or Rail instruction, concrete_terms must contain every observed-domain term, artifact/media class, "
        "file format, application, command, package/library, API, function, implementation parameter, named entity, "
        "fixed value, and worked example. Domain vocabulary is never allowed in a global persistent prompt. "
        "cross_domain_transferable requires two task families that do not share "
        "the observed artifact, tool, or execution environment. For a Rail audit, task_family_a must begin exactly "
        "with `computed-artifact:` and describe a computed or derived result persisted into an artifact; "
        "task_family_b must begin exactly with `external-action:` and describe an executed action with an externally "
        "observable postcondition. A classification or conformance review cannot serve as either family."
    )
    return f"""Audit pass {audit_pass}. Audit this proposed global Harness intervention. Do not rewrite it and do
not propose an alternative.

Return ONLY this JSON shape:
{{
  "causal_faithful": true,
  "intervention_entails_expected_effect": true,
  "trigger_non_vacuous": true,
  "preserves_supported_behavior": true,
  "evidence_independent": true,
  "task_detail_free": true,
  "cross_domain_transferable": true,
  "concrete_terms": [],
  "decision_dependency_test": {{
    "upstream_decision_required": true,
    "requirement_classification_required": {json.dumps(controller_dependency["requirement_classification_required"])},
    "upstream_decision": "the decision that controls whether the intervention runs",
    "easiest_avoidance_path": "how the original defect could survive by changing that decision",
    "decision_derivation_clause": "an exact quote from the persistent candidate that derives the decision",
    "authority_clause": "",
    "scope_clause": "",
    "responsibility_clause": "",
    "claim_inventory_clause": "",
    "conclusion_claim_clause": "",
    "action_claim_clause": "",
    "late_claim_gate_clause": "",
    "final_release_scan_clause": "",
    "unlisted_claim_restart_clause": "",
    "source_witness_clause": "",
    "stable_locator_clause": "",
    "evidence_entailment_clause": "",
    "trigger_clause": "",
    "falsification_clause": "",
    "negative_decision_gate_clause": "",
    "avoidance_blocked": true
  }},
  "substitution_test": {{
    "task_family_a": "one materially different task family",
    "task_family_b": "another materially different task family",
    "required_edits_a": [],
    "required_edits_b": [],
    "works_unchanged": true
  }},
  "violations": []
}}

Audit rules:
- CONTROLLER_DECISION_DEPENDENCY is frozen evidence, not a reviewer opinion. When
  its requirement_classification_required is true, copy true into the audit and
  populate all eleven exact candidate clauses. You may upgrade false to true when
  the candidate contains another requirement-classification dependency, but you
  may never downgrade controller true to false.
- causal_faithful is true only when the candidate implements the observed_decision
  -> required_behavior -> predicted_observable relationship in CAUSAL_BINDING. A
  familiar mechanism from another task is a violation, even if it sounds general.
- causal_faithful is false when any instruction, validation step, recovery path, or
  fallback contradicts a CAUSAL_BINDING scope_boundary. Audit the full candidate,
  not only its primary procedure. A last-resort action is still a violation.
- intervention_entails_expected_effect is true only when following the executable
  persistent instruction is sufficient to produce the claimed expected_effect. Test
  the exact instruction, not its rationale. It is false when expected_effect claims
  a branch choice, classification, mutation, or observable that the instruction never
  requires or derives.
- trigger_non_vacuous is true only when the agent cannot satisfy or evade the new rule
  merely by changing an upstream label, branch, or wording while leaving the evidenced
  decision procedure unchanged. Simulate both the intended trigger path and the easiest
  trigger-avoidance path. If avoidance preserves the original defect, set it false.
- Complete decision_dependency_test before setting trigger_non_vacuous. Set
  upstream_decision_required=true whenever the claimed effect depends on a verdict,
  classification, branch, eligibility judgment, or other state that the candidate
  merely assumes in its trigger. The easiest_avoidance_path must describe how an
  agent could preserve the diagnosed defect by choosing a different upstream state.
  decision_derivation_clause must be an exact, contiguous quote from the executable
  persistent candidate that tells the agent how to derive that upstream state from
  task-visible evidence before the dependent action. A clause that only says "when",
  "if", "after confirming", "check", or "make sure" does not derive the state.
- Set requirement_classification_required=true when the upstream decision determines
  whether an alleged gap is an in-scope mandatory requirement of the evaluated target.
  In that case copy fifteen exact, contiguous candidate quotes: claim_inventory_clause must
  require a draft before verification and enumerate every atomic material output claim;
  conclusion_claim_clause must explicitly cover every conclusion, verdict, and
  classification; action_claim_clause must explicitly cover every prescribed,
  recommended, or required action; late_claim_gate_clause must prohibit any later or
  released material claim from bypassing that inventory; final_release_scan_clause must
  require an immediate pre-release scan of every material conclusion, classification,
  prescription, recommendation, and required modification in the final output against
  the original inventory; unlisted_claim_restart_clause must block release and restart
  the complete procedure whenever that scan finds an unlisted claim; authority_clause
  must identify the task-visible source that makes the requirement binding; scope_clause
  must establish that source's governed subject, object, operation, and boundary and
  match them to the assessed target; responsibility_clause
  must determine which actor, process, dependency, or target owns the required outcome;
  source_witness_clause must require an exact task-visible quote or bounded span for every
  retained claim; stable_locator_clause must separately require a stable source locator
  for that witness; evidence_entailment_clause must map
  that witness to the exact in-scope requirement and assessed target; trigger_clause must
  establish from task-visible evidence whether every condition that activates that
  requirement is satisfied in the current state; falsification_clause must challenge
  that mapping with a countermodel in which the evidence is true but the proposed claim
  is false; and negative_decision_gate_clause
  must first account for every atomic reason that changes the conclusion or prescribed
  action, then permit a negative verdict only after each retained mapped requirement
  remains unsupported. A draft claim that bypasses that inventory makes the clause
  incomplete. These clauses may be separate steps. Empty, post-verdict, self-attested,
  or non-exhaustive clauses mean avoidance_blocked=false and trigger_non_vacuous=false.
- Set avoidance_blocked=true only if the quoted executable procedure blocks the stated
  easiest avoidance path. Do not credit rationale or expected_effect as instructions.
- preserves_supported_behavior is true only when the intervention changes the diagnosed
  decision and does not ask the agent to overturn already-supported upstream decisions
  solely to make downstream output consistent. A consistency repair must determine or
  preserve the evidence-backed branch first, then align dependent actions with it.
- evidence_independent is true only when the persistent rule is justified by
  evidence available to the task agent. Audit both the binding and the candidate:
  controller-frozen means the causal relationship cannot be rewritten, not that it
  is automatically valid. A hidden expected answer, gold label, evaluator
  preference, or one lucky candidate outcome is not an independent cause. Remove
  scores, pass/fail labels, and evaluator-owned expected outcomes mentally; if no
  causal reason remains, evidence_independent must be false.
- Apply evidence_independent to the executable persistent surface named above.
  rationale and expected_effect may cite the observed evidence that motivated the
  experiment; those provenance citations are not persistent task instructions and
  do not by themselves make the candidate dependent on a hidden outcome. Reject
  only when the executable trigger, procedure, validation, or fallback needs a
  hidden expected/gold/reference/known answer to run or to decide success.
- Before assigning booleans, scan {persistent_field} phrase by phrase and copy
  every disallowed concrete term and every phrase requiring replacement in either
  substitution task into concrete_terms.
- {detail_rule}
- task_detail_free is true only when concrete_terms is empty and
  {persistent_field} contains no case ID, answer, fixed target value, named source
  document/entity, benchmark label, or worked solution.
- cross_domain_transferable is true only when the behavioral decision rule can be
  applied unchanged in the two materially different tasks named in substitution_test.
  List every candidate phrase that must be changed for each family in
  required_edits_a and required_edits_b.
  Set works_unchanged=false when either application would require replacing a noun,
  required term or implementation step in {persistent_field}.
- This is an independent audit. Choose two task families not used by an earlier
  audit pass. Previously used families are listed below; do not reuse or paraphrase
  them.
- violations must contain short, concrete reasons for every false field and must
  be empty only when all audit booleans are true and the decision-dependency test
  contains the required executable clauses.

PRIOR_SUBSTITUTION_FAMILIES:
{json.dumps(prior_substitution_families or [], ensure_ascii=False, indent=2)}

CONTROLLER_DECISION_DEPENDENCY:
{json.dumps(controller_dependency, ensure_ascii=False, indent=2)}

CAUSAL_BINDING (controller-frozen; audit may reject but never replace it):
{json.dumps(causal_binding, ensure_ascii=False, indent=2)}

CANDIDATE (read-only):
{json.dumps(patch, ensure_ascii=False, indent=2)}
"""


def _build_generation_repair_request(
    original_request: str,
    rejected_patch: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    del original_request
    skill_target = isinstance(rejected_patch.get("skill"), Mapping) and bool(rejected_patch.get("skill"))
    rail_updates = (
        rejected_patch.get("harness_updates") if isinstance(rejected_patch.get("harness_updates"), Mapping) else {}
    )
    checkpoint = (
        rail_updates.get("submission_checkpoint")
        if isinstance(rail_updates.get("submission_checkpoint"), Mapping)
        else {}
    )
    rail_target = bool(str(checkpoint.get("instruction", "") or "").strip())
    rewrite_target = (
        "Rewrite only skill.name, skill.description, and skill.body. Preserve the public operations required by "
        "the capability, but remove every observed-instance term listed by the audit. Replace any validation "
        "against an expected, gold, reference, or known answer with task-visible contract checks, invariants, "
        "independent recomputation, or artifact read-back."
        if skill_target
        else (
            "Return only a JSON mapping with one field named instruction. Rewrite the current Rail instruction "
            "at the functional trigger -> action -> observable level. The controller preserves all other fields. "
            "Remove every listed concrete term and "
            "any synonymous observed domain, artifact, application, command, library, parameter, or worked-example term. "
            "The replacement must govern factual claims, computed or derived values, artifact or state mutations, "
            "conclusions or classifications, and actions or operations. Use the general distinction between a verified "
            "decision-changing ground and a merely advisory ground, not compliance-specific mandatory/optional language."
            if rail_target
            else "Rewrite only system_prompt_append at the functional trigger -> action -> observable level. Remove "
            "every listed concrete term and any synonymous observed domain, artifact, application, command, library, "
            "parameter, or worked-example term."
        )
    )
    return f"""The independent transfer audit rejected the persistent instruction.
{rewrite_target}
Preserve source_hypothesis_id, the other persistent surface, harness_updates,
rationale, and expected_effect exactly as supplied.
Do not weaken the causal intervention into generic advice.
When the audit exposes a trigger-avoidance or upstream-decision gap, repair the
executable decision procedure that precedes the trigger. Do not merely strengthen
the downstream wording or repeat the conditional trigger.

For a Rail target, return only {{"instruction": "..."}}. Otherwise return the
complete candidate JSON mapping with the same six top-level fields.

REJECTED CANDIDATE:
{json.dumps(rejected_patch, ensure_ascii=False, indent=2)}

AUDIT VIOLATIONS:
{json.dumps(audit["violations"], ensure_ascii=False, indent=2)}
"""


def _validate_transfer_audit(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("transfer audit must be a mapping")
    expected = {
        "causal_faithful",
        "intervention_entails_expected_effect",
        "trigger_non_vacuous",
        "preserves_supported_behavior",
        "evidence_independent",
        "task_detail_free",
        "cross_domain_transferable",
        "concrete_terms",
        "decision_dependency_test",
        "substitution_test",
        "violations",
    }
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown or missing:
        raise ValueError(f"transfer audit fields mismatch: missing={missing}, unknown={unknown}")
    boolean_fields = {
        "causal_faithful",
        "intervention_entails_expected_effect",
        "trigger_non_vacuous",
        "preserves_supported_behavior",
        "evidence_independent",
        "task_detail_free",
        "cross_domain_transferable",
    }
    for key in boolean_fields:
        if not isinstance(value[key], bool):
            raise TypeError(f"transfer audit {key} must be a boolean")
    concrete_terms = value["concrete_terms"]
    if not isinstance(concrete_terms, list) or any(
        not isinstance(item, str) or not item.strip() for item in concrete_terms
    ):
        raise TypeError("transfer audit concrete_terms must be a list of non-empty strings")
    dependency = value["decision_dependency_test"]
    if not isinstance(dependency, Mapping):
        raise TypeError("transfer audit decision_dependency_test must be a mapping")
    dependency_fields = {
        "upstream_decision_required",
        "requirement_classification_required",
        "upstream_decision",
        "easiest_avoidance_path",
        "decision_derivation_clause",
        "authority_clause",
        "scope_clause",
        "responsibility_clause",
        "claim_inventory_clause",
        "conclusion_claim_clause",
        "action_claim_clause",
        "late_claim_gate_clause",
        "final_release_scan_clause",
        "unlisted_claim_restart_clause",
        "source_witness_clause",
        "stable_locator_clause",
        "evidence_entailment_clause",
        "trigger_clause",
        "falsification_clause",
        "negative_decision_gate_clause",
        "avoidance_blocked",
    }
    if set(dependency) != dependency_fields:
        raise ValueError("transfer audit decision_dependency_test fields mismatch")
    for field in (
        "upstream_decision_required",
        "requirement_classification_required",
        "avoidance_blocked",
    ):
        if not isinstance(dependency[field], bool):
            raise TypeError(f"transfer audit decision_dependency_test {field} must be a boolean")
    dependency_text_fields = dependency_fields - {
        "upstream_decision_required",
        "requirement_classification_required",
        "avoidance_blocked",
    }
    for field in dependency_text_fields:
        if not isinstance(dependency[field], str):
            raise TypeError(f"transfer audit decision_dependency_test {field} must be a string")
    if dependency["requirement_classification_required"] and not dependency["upstream_decision_required"]:
        raise ValueError("requirement classification implies an upstream decision dependency")
    substitution = value["substitution_test"]
    if not isinstance(substitution, Mapping):
        raise TypeError("transfer audit substitution_test must be a mapping")
    substitution_fields = {
        "task_family_a",
        "task_family_b",
        "required_edits_a",
        "required_edits_b",
        "works_unchanged",
    }
    if set(substitution) != substitution_fields:
        raise ValueError("transfer audit substitution_test fields mismatch")
    family_a = str(substitution["task_family_a"] or "").strip()
    family_b = str(substitution["task_family_b"] or "").strip()
    if not family_a or not family_b or family_a.casefold() == family_b.casefold():
        raise ValueError("transfer audit substitution_test requires two distinct task families")
    required_edits_a = substitution["required_edits_a"]
    required_edits_b = substitution["required_edits_b"]
    for field, edits in (("required_edits_a", required_edits_a), ("required_edits_b", required_edits_b)):
        if not isinstance(edits, list) or any(not isinstance(item, str) or not item.strip() for item in edits):
            raise TypeError(f"transfer audit substitution_test {field} must be a list of non-empty strings")
    if not isinstance(substitution["works_unchanged"], bool):
        raise TypeError("transfer audit substitution_test works_unchanged must be a boolean")
    if bool(concrete_terms) == bool(value["task_detail_free"]):
        raise ValueError("transfer audit task_detail_free must be false exactly when concrete_terms is non-empty")
    if bool(substitution["works_unchanged"]) != bool(value["cross_domain_transferable"]):
        raise ValueError("transfer audit cross_domain_transferable must match substitution_test works_unchanged")
    if bool(substitution["works_unchanged"]) == bool(required_edits_a or required_edits_b):
        raise ValueError("transfer audit works_unchanged must be false exactly when substitution edits are required")
    violations = value["violations"]
    if not isinstance(violations, list) or any(not isinstance(item, str) or not item.strip() for item in violations):
        raise TypeError("transfer audit violations must be a list of non-empty strings")
    normalized = {key: value[key] for key in boolean_fields}
    normalized["concrete_terms"] = [item.strip() for item in concrete_terms]
    normalized["decision_dependency_test"] = {
        field: dependency[field] if isinstance(dependency[field], bool) else dependency[field].strip()
        for field in dependency_fields
    }
    normalized["substitution_test"] = {
        "task_family_a": family_a,
        "task_family_b": family_b,
        "required_edits_a": [item.strip() for item in required_edits_a],
        "required_edits_b": [item.strip() for item in required_edits_b],
        "works_unchanged": substitution["works_unchanged"],
    }
    normalized["violations"] = [item.strip() for item in violations]
    if all(normalized[key] for key in boolean_fields) and normalized["violations"]:
        raise ValueError("approved transfer audit must not contain violations")
    if not all(normalized[key] for key in boolean_fields) and not normalized["violations"]:
        raise ValueError("rejected transfer audit must explain at least one violation")
    return normalized


def _audit_uses_new_substitution_families(
    audit: Mapping[str, Any],
    audit_history: list[dict[str, Any]],
) -> bool:
    """Require each approval to test a fresh cross-domain substitution pair."""
    previous = {
        _normalized_phrase(str(family))
        for item in audit_history
        for family in (
            ((item.get("substitution_test", {}) or {}).get("task_family_a", "")),
            ((item.get("substitution_test", {}) or {}).get("task_family_b", "")),
        )
        if str(family).strip()
    }
    current_test = audit.get("substitution_test")
    current_test = current_test if isinstance(current_test, Mapping) else {}
    current = {
        _normalized_phrase(str(current_test.get(key, "") or ""))
        for key in ("task_family_a", "task_family_b")
        if str(current_test.get(key, "") or "").strip()
    }
    return bool(current) and not (previous & current)


def _transfer_audit_validation_errors(value: Any) -> list[str]:
    try:
        _validate_transfer_audit(value)
    except (TypeError, ValueError) as exc:
        return [str(exc)]
    return []


def _controller_decision_dependency_contract(causal_binding: Mapping[str, Any]) -> dict[str, Any]:
    """Derive non-optional decision-chain checks from controller-owned causal structure."""
    required = False
    links: set[str] = set()
    bindings = causal_binding.get("bindings", [])
    for binding in bindings if isinstance(bindings, list) else []:
        if not isinstance(binding, Mapping):
            continue
        semantic_id = str(binding.get("source_hypothesis_semantic_id", "") or "").strip().casefold()
        binding_links = {
            str(item).strip().casefold() for item in binding.get("required_decision_links", []) if str(item).strip()
        }
        binding_required = binding.get("requirement_classification_required") is True
        if semantic_id == "chs:unverified_decision_ground_used":
            binding_required = True
            binding_links.update({"authority", "scope", "owner", "trigger", "entailment"})
        if binding_required:
            required = True
            links.update(binding_links)
    return {
        "requirement_classification_required": required,
        "required_decision_links": sorted(links),
    }


def _enforce_controller_decision_dependency(
    audit: Mapping[str, Any],
    causal_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Prevent a reviewer from switching off a controller-required dependency audit."""
    contract = _controller_decision_dependency_contract(causal_binding)
    if not contract["requirement_classification_required"]:
        return dict(audit)
    dependency = audit.get("decision_dependency_test")
    dependency = dict(dependency) if isinstance(dependency, Mapping) else {}
    dependency["upstream_decision_required"] = True
    dependency["requirement_classification_required"] = True
    return {**audit, "decision_dependency_test": dependency}


def _transfer_audit_approved(audit: Mapping[str, Any]) -> bool:
    dependency = audit.get("decision_dependency_test")
    dependency = dependency if isinstance(dependency, Mapping) else {}
    upstream_ready = not bool(dependency.get("upstream_decision_required")) or bool(
        str(dependency.get("upstream_decision", "")).strip()
        and str(dependency.get("easiest_avoidance_path", "")).strip()
        and str(dependency.get("decision_derivation_clause", "")).strip()
    )
    requirement_ready = not bool(dependency.get("requirement_classification_required")) or all(
        str(dependency.get(key, "")).strip()
        for key in (
            "authority_clause",
            "scope_clause",
            "responsibility_clause",
            "claim_inventory_clause",
            "conclusion_claim_clause",
            "action_claim_clause",
            "late_claim_gate_clause",
            "final_release_scan_clause",
            "unlisted_claim_restart_clause",
            "source_witness_clause",
            "stable_locator_clause",
            "evidence_entailment_clause",
            "trigger_clause",
            "falsification_clause",
            "negative_decision_gate_clause",
        )
    )
    return (
        all(
            bool(audit.get(key))
            for key in (
                "causal_faithful",
                "intervention_entails_expected_effect",
                "trigger_non_vacuous",
                "preserves_supported_behavior",
                "evidence_independent",
                "task_detail_free",
                "cross_domain_transferable",
            )
        )
        and not audit.get("concrete_terms")
        and bool(dependency.get("avoidance_blocked"))
        and upstream_ready
        and requirement_ready
        and bool((audit.get("substitution_test") or {}).get("works_unchanged"))
        and not audit.get("violations")
    )


def _audit_exercises_global_rail_axes(audit: Mapping[str, Any]) -> bool:
    """Require transfer tests outside the failure's original decision genre."""
    substitution = audit.get("substitution_test")
    if not isinstance(substitution, Mapping):
        return False
    family_a = str(substitution.get("task_family_a", "") or "").strip().casefold()
    family_b = str(substitution.get("task_family_b", "") or "").strip().casefold()
    return family_a.startswith("computed-artifact:") and family_b.startswith("external-action:")


def _candidate_decision_dependency_errors(
    audit: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[str]:
    """Verify that decision-audit clauses are executable candidate text, not reviewer inventions."""
    dependency = audit.get("decision_dependency_test")
    dependency = dependency if isinstance(dependency, Mapping) else {}
    persistent_text = _persistent_intervention_text(candidate)
    normalized_candidate = _normalize_dependency_quote(persistent_text)
    errors: list[str] = []

    required_quotes = ["decision_derivation_clause"] if dependency.get("upstream_decision_required") else []
    if dependency.get("requirement_classification_required"):
        required_quotes.extend(
            [
                "claim_inventory_clause",
                "conclusion_claim_clause",
                "action_claim_clause",
                "late_claim_gate_clause",
                "final_release_scan_clause",
                "unlisted_claim_restart_clause",
                "authority_clause",
                "scope_clause",
                "responsibility_clause",
                "source_witness_clause",
                "stable_locator_clause",
                "evidence_entailment_clause",
                "trigger_clause",
                "falsification_clause",
                "negative_decision_gate_clause",
            ]
        )
    for field in required_quotes:
        quote = str(dependency.get(field, "") or "").strip()
        if not quote:
            errors.append(f"decision dependency audit is missing {field}")
            continue
        if _normalize_dependency_quote(quote) not in normalized_candidate:
            errors.append(f"decision dependency audit {field} is not an exact candidate quote")
    if dependency.get("upstream_decision_required") and not dependency.get("avoidance_blocked"):
        errors.append("candidate does not block the audited upstream trigger-avoidance path")
    return errors


def _persistent_intervention_text(candidate: Mapping[str, Any]) -> str:
    """Collect every executable surface that persists into the candidate Harness."""
    skill = candidate.get("skill") if isinstance(candidate.get("skill"), Mapping) else {}
    harness_updates = candidate.get("harness_updates") if isinstance(candidate.get("harness_updates"), Mapping) else {}
    checkpoint = (
        harness_updates.get("submission_checkpoint")
        if isinstance(harness_updates.get("submission_checkpoint"), Mapping)
        else {}
    )
    return "\n".join(
        str(value or "")
        for value in (
            skill.get("description", ""),
            skill.get("body", ""),
            candidate.get("system_prompt_append", ""),
            checkpoint.get("instruction", ""),
        )
        if str(value or "").strip()
    )


def _global_submission_checkpoint_errors(instruction: str) -> list[str]:
    """Reject release checkpoints that only work for the observed decision genre."""
    normalized = _normalized_phrase(instruction)
    token_groups = {
        "factual claims": ("claim", "assertion", "fact"),
        "computed or derived values": ("comput", "calculat", "deriv", "numeric"),
        "artifact or state mutations": ("artifact", "state", "mutation", "persist", "read back"),
        "actions or operations": ("action", "operation", "tool call", "execution"),
    }
    missing = [label for label, markers in token_groups.items() if not any(marker in normalized for marker in markers)]
    if not missing:
        return []
    return [
        "global submission checkpoint is narrowed to the observed decision genre; "
        "it must explicitly cover " + ", ".join(missing)
    ]


def _normalize_dependency_quote(value: str) -> str:
    """Canonicalize presentation-only Markdown while preserving token order.

    Transfer reviewers quote prose without reliably copying list markers,
    emphasis delimiters, or Unicode punctuation. Those presentation differences
    must not send an otherwise unchanged intervention back through generation.
    Requiring the complete normalized token sequence to occur contiguously still
    prevents a reviewer from inventing a missing executable clause.
    """
    return " ".join(re.findall(r"[^\W_]+", str(value or "").casefold(), flags=re.UNICODE))


async def _review_injected_candidate(
    *,
    candidate: dict[str, Any],
    request: str,
    generator: PatchGenerator,
    reviewer: TransferReviewer,
    causal_binding: dict[str, Any],
) -> dict[str, Any]:
    for review_index in range(2):
        raw_audit = reviewer(_build_transfer_review_request(candidate, causal_binding))
        audit = await raw_audit if inspect.isawaitable(raw_audit) else raw_audit
        audit = _validate_transfer_audit(audit)
        audit = _enforce_controller_decision_dependency(audit, causal_binding)
        dependency_errors = _candidate_decision_dependency_errors(audit, candidate)
        if dependency_errors:
            audit = {
                **audit,
                "trigger_non_vacuous": False,
                "violations": list(dict.fromkeys([*audit["violations"], *dependency_errors])),
            }
        if _transfer_audit_approved(audit):
            return candidate
        if review_index == 1:
            raise ValueError("transfer audit rejected candidate: " + "; ".join(audit["violations"]))
        generated = generator(_build_generation_repair_request(request, candidate, audit))
        candidate = await generated if inspect.isawaitable(generated) else generated
    raise AssertionError("unreachable injected transfer review state")


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
    all_issues = [dict(item) for item in analysis.get("issues", []) if isinstance(item, dict)]
    issues = [issue for issue in all_issues if _issue_is_actionable(issue)]
    requested = {str(issue_id) for issue_id in (requested_issue_ids or []) if str(issue_id)}
    if not requested:
        return issues
    selected = [issue for issue in issues if str(issue.get("issue_id", "")) in requested]
    missing = sorted(requested - {str(issue.get("issue_id", "")) for issue in selected})
    if missing:
        raise ValueError(f"analysis does not contain requested actionable optimization issues: {missing}")
    return selected


def _selected_supported_hypothesis_id(attribution: Mapping[str, Any]) -> str:
    assessments = [item for item in attribution.get("hypothesis_assessment", []) if isinstance(item, Mapping)]
    supported_ids = [
        str(item.get("hypothesis_id", "") or "").strip()
        for item in assessments
        if str(item.get("status", "") or "").strip().casefold() == "supported"
        and str(item.get("hypothesis_id", "") or "").strip()
    ]
    selected = str(attribution.get("selected_hypothesis_id", "") or "").strip()
    if selected:
        return selected if selected in supported_ids else ""
    # Pre-v12 analysis artifacts did not persist an explicit selection. They
    # remain safe only when positive support is already unique.
    return supported_ids[0] if len(supported_ids) == 1 else ""


def _issue_is_actionable(issue: Mapping[str, Any]) -> bool:
    metadata = issue.get("metadata") if isinstance(issue.get("metadata"), Mapping) else {}
    attribution = metadata.get("attribution") if isinstance(metadata.get("attribution"), Mapping) else {}
    target_ref = str(attribution.get("target_ref", "") or "").strip().casefold()
    evidence_status = str(attribution.get("evidence_status", "") or "").strip().casefold()
    if not target_ref or target_ref == "unassigned":
        return False
    if evidence_status not in {"confirmed", "supported_hypothesis"}:
        return False
    assessments = [item for item in attribution.get("hypothesis_assessment", []) if isinstance(item, Mapping)]
    if not assessments:
        return False
    selected_hypothesis_id = _selected_supported_hypothesis_id(attribution)
    if not selected_hypothesis_id:
        return False
    statuses = {str(item.get("status", "") or "").strip().casefold() for item in assessments}
    if evidence_status == "confirmed" and "unresolved" in statuses:
        return False
    return any(
        str(item.get("hypothesis_id", "") or "").strip() == selected_hypothesis_id
        and str(item.get("status", "") or "").strip().casefold() == "supported"
        for item in assessments
    )


def _causal_hypothesis_policy(issues: list[dict[str, Any]]) -> dict[str, Any]:
    supported: set[str] = set()
    falsified: set[str] = set()
    unresolved: set[str] = set()
    instrumented = False
    semantic_ids_by_hypothesis_id: dict[str, str] = {}
    for issue in issues:
        metadata = issue.get("metadata") if isinstance(issue.get("metadata"), Mapping) else {}
        attribution = metadata.get("attribution") if isinstance(metadata.get("attribution"), Mapping) else {}
        selected_hypothesis_id = _selected_supported_hypothesis_id(attribution)
        assessments = attribution.get("hypothesis_assessment", [])
        for assessment in assessments if isinstance(assessments, list) else []:
            if not isinstance(assessment, Mapping):
                continue
            hypothesis_id = str(assessment.get("hypothesis_id", "") or "").strip()
            status = str(assessment.get("status", "") or "").strip().casefold()
            if not hypothesis_id or status not in {"supported", "falsified", "unresolved"}:
                continue
            instrumented = True
            if status == "supported":
                if hypothesis_id != selected_hypothesis_id:
                    continue
                supported.add(hypothesis_id)
                semantic_id = str(assessment.get("hypothesis_semantic_id", "") or "").strip()
                if semantic_id:
                    semantic_ids_by_hypothesis_id[hypothesis_id] = semantic_id
            elif status == "falsified":
                falsified.add(hypothesis_id)
            else:
                unresolved.add(hypothesis_id)
    supported -= falsified | unresolved
    return {
        "instrumented": instrumented,
        "supported_hypothesis_ids": sorted(supported),
        "falsified_hypothesis_ids": sorted(falsified),
        "unresolved_hypothesis_ids": sorted(unresolved),
        "semantic_ids_by_hypothesis_id": {
            hypothesis_id: semantic_ids_by_hypothesis_id[hypothesis_id]
            for hypothesis_id in sorted(supported)
            if hypothesis_id in semantic_ids_by_hypothesis_id
        },
    }


def _causal_binding(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Freeze each selected Issue's own causal claim for generation and review."""
    bindings: list[dict[str, Any]] = []
    for issue in issues:
        metadata = issue.get("metadata") if isinstance(issue.get("metadata"), Mapping) else {}
        attribution = metadata.get("attribution") if isinstance(metadata.get("attribution"), Mapping) else {}
        selected_hypothesis_id = _selected_supported_hypothesis_id(attribution)
        decision = (
            attribution.get("decision_contract") if isinstance(attribution.get("decision_contract"), Mapping) else {}
        )
        coverage = attribution.get("causal_coverage") if isinstance(attribution.get("causal_coverage"), Mapping) else {}
        supported_hypotheses = []
        assessments = attribution.get("hypothesis_assessment", [])
        for assessment in assessments if isinstance(assessments, list) else []:
            if not isinstance(assessment, Mapping):
                continue
            if str(assessment.get("status", "") or "").strip().casefold() != "supported":
                continue
            if str(assessment.get("hypothesis_id", "") or "").strip() != selected_hypothesis_id:
                continue
            supported_hypotheses.append(
                {
                    key: assessment[key]
                    for key in ("hypothesis_id", "claim", "reason", "logic_check")
                    if assessment.get(key) not in (None, "", {}, [])
                }
            )
        semantic_id = str(attribution.get("selected_hypothesis_semantic_id", "") or "").strip()
        requirement_required = decision.get("requirement_classification_required") is True
        required_links = [str(item) for item in decision.get("required_decision_links", []) if str(item)]
        if semantic_id.casefold() == "chs:unverified_decision_ground_used":
            requirement_required = True
            required_links = sorted(set(required_links) | {"authority", "scope", "owner", "trigger", "entailment"})
        binding = {
            "source_issue_id": str(issue.get("issue_id", "") or ""),
            "target_ref": attribution.get("target_ref", ""),
            "evidence_status": attribution.get("evidence_status", ""),
            "failed_requirement": attribution.get("failed_requirement", ""),
            "observed_decision": decision.get("wrong_decision", attribution.get("critical_mistake", "")),
            "causal_distinction": decision.get("causal_distinction", attribution.get("general_mechanism", "")),
            "required_behavior": decision.get("required_action", issue.get("recommendation", "")),
            "predicted_observable": decision.get(
                "acceptance_observable",
                coverage.get("counterfactual_prediction", ""),
            ),
            "scope_boundary": decision.get("scope_boundary", []),
            "source_hypothesis_semantic_id": semantic_id,
            "requirement_classification_required": requirement_required,
            "required_decision_links": required_links,
            "sufficiency_status": coverage.get("sufficiency_status", ""),
            "supported_hypotheses": supported_hypotheses,
        }
        bindings.append({key: value for key, value in binding.items() if value not in (None, "", {}, [])})
    return {
        "schema_version": 1,
        "immutability": "review_may_reject_but_must_not_replace_this_causal_relationship",
        "bindings": bindings,
    }


def _validate_causal_binding_independence(causal_binding: Mapping[str, Any]) -> None:
    """Reject causal rules that merely encode an evaluator-owned outcome."""
    bindings = causal_binding.get("bindings", [])
    for index, binding in enumerate(bindings if isinstance(bindings, list) else []):
        if not isinstance(binding, Mapping):
            continue
        causal_texts = [
            str(binding.get("causal_distinction", "") or ""),
            str(binding.get("required_behavior", "") or ""),
        ]
        hypotheses = binding.get("supported_hypotheses", [])
        for hypothesis in hypotheses if isinstance(hypotheses, list) else []:
            if not isinstance(hypothesis, Mapping):
                continue
            # The claim is part of the causal relationship.  Its reason and
            # logic_check are evidence provenance and may legitimately quote a
            # failed evaluator requirement; treating those citations as the
            # causal rule itself creates false positives.
            causal_texts.append(str(hypothesis.get("claim", "") or ""))
        combined = " ".join(causal_texts)
        if any(pattern.search(combined) for pattern in _OUTCOME_ONLY_CAUSAL_PATTERNS):
            raise ValueError(
                f"causal binding {index} depends on an evaluator-owned expected outcome; "
                "return it to the Analyzer as unassigned instead of generating a Harness rule"
            )


def _issue_counterfactual_predictions(issues: list[dict[str, Any]]) -> list[str]:
    predictions: list[str] = []
    for issue in issues:
        metadata = issue.get("metadata") if isinstance(issue.get("metadata"), Mapping) else {}
        attribution = metadata.get("attribution") if isinstance(metadata.get("attribution"), Mapping) else {}
        coverage = attribution.get("causal_coverage") if isinstance(attribution.get("causal_coverage"), Mapping) else {}
        prediction = str(coverage.get("counterfactual_prediction", "") or "").strip()
        if prediction and prediction not in predictions:
            predictions.append(prediction)
    return predictions


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
    remaining_result = 0 if primary_structured_available else _MAX_LEGACY_RESULT_CHARS
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
    evidence_locations = _issue_evidence_locations(selected_issues)
    return {
        "schema_version": 2,
        "evidence_priority": [
            "analyzer_causal_digest",
            "analyzer_diagnosis",
            "candidate_feedback_delta",
            "bounded_legacy_fallback",
        ],
        "analyzer_causal_digest": _bounded_evidence_records(
            [
                _sanitize_model_evidence(
                    _compact_causal_digest_for_improver(value, evidence_locations),
                    target_case_ids,
                )
                for value in causal_digests
            ],
            _MAX_STRUCTURED_EVIDENCE_CHARS,
        ),
        "analyzer_diagnoses": _bounded_evidence_records(
            [
                _sanitize_model_evidence(_compact_materialized_diagnosis(value), target_case_ids)
                for value in [*diagnoses, *_compact_selected_issue_diagnoses(selected_issues)]
            ],
            _MAX_STRUCTURED_EVIDENCE_CHARS // 2,
        ),
        "candidate_feedback_delta": _bounded_evidence_records(
            [_sanitize_model_evidence(value, target_case_ids) for value in feedback_deltas],
            _MAX_STRUCTURED_EVIDENCE_CHARS // 2,
        ),
        "optimization_hypotheses": _sanitize_model_evidence(
            _compact_optimization_hypotheses(hypotheses),
            target_case_ids,
        ),
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
            "causal_coverage": attribution.get("causal_coverage", {}),
            "decision_contract": attribution.get("decision_contract", {}),
            "failure_cluster": attribution.get("failure_cluster", {}),
            "confidence": attribution.get("confidence", ""),
        }
        compact.append({key: value for key, value in diagnosis.items() if value not in ("", {}, [])})
    return compact


def _compact_materialized_diagnosis(value: Any) -> Any:
    """Keep causal decisions while dropping verbose protocol bookkeeping."""
    if not isinstance(value, Mapping):
        return value
    metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
    attribution = metadata.get("attribution") if isinstance(metadata.get("attribution"), Mapping) else {}
    fields = (
        "failure_mode",
        "failed_requirement",
        "root_cause",
        "critical_mistake",
        "general_mechanism",
        "recommendation",
        "target_ref",
        "evidence_status",
        "decision_contract",
        "causal_coverage",
        "prior_experiment_assessment",
    )
    compact = {
        key: value.get(key, attribution.get(key))
        for key in fields
        if value.get(key, attribution.get(key)) not in (None, "", {}, [])
    }
    return compact or dict(value)


def _issue_evidence_locations(issues: list[dict[str, Any]]) -> set[tuple[str, int | str]]:
    locations: set[tuple[str, int | str]] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            trace_id = str(value.get("trace_id", "") or "")
            message_index = value.get("message_index")
            step_pointer = str(value.get("step_pointer", "") or "")
            if message_index is not None:
                try:
                    locations.add((trace_id, int(message_index)))
                except (TypeError, ValueError):
                    pass
            if step_pointer:
                locations.add((trace_id, step_pointer))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(issues)
    return locations


def _compact_causal_digest_for_improver(
    value: Any,
    evidence_locations: set[tuple[str, int | str]],
) -> Any:
    """Project Analyzer evidence onto cited decisions without head truncation."""
    if not isinstance(value, Mapping) or not isinstance(value.get("trials"), list):
        return value
    compact = {
        key: value[key]
        for key in (
            "schema_version",
            "decision",
            "causal_decision",
            "task_contract",
            "outcome",
            "tool_contract_observations",
            "critical_evidence_terms",
            "cross_trial_contrast",
            "fallback_final_response",
            "compression_stats",
        )
        if key in value and value[key] not in (None, "", {}, [])
    }
    compact_trials: list[dict[str, Any]] = []
    for raw_trial in value["trials"]:
        if not isinstance(raw_trial, Mapping):
            continue
        trial = {
            key: raw_trial[key]
            for key in (
                "trial_id",
                "role",
                "passed",
                "score",
                "exit_reason",
                "tool_call_count",
                "tool_sequence",
                "tool_sequence_truncated",
                "selection_coverage",
                "final_output",
                "trial_evaluation",
            )
            if key in raw_trial and raw_trial[key] not in (None, "", {}, [])
        }
        actions = [action for action in raw_trial.get("selected_actions", []) if isinstance(action, Mapping)]
        cited = [action for action in actions if _action_matches_evidence_location(action, evidence_locations)]
        if not cited:
            cited = [
                action
                for action in actions
                if {
                    str(reason)
                    for reason in (
                        action.get("selection_reasons", [])
                        if isinstance(action.get("selection_reasons"), list)
                        else [action.get("selection_reasons", "")]
                    )
                    if str(reason)
                }
                & {"observed_failure", "terminal_window"}
            ][:8]
        trial["cited_actions"] = [_compact_causal_action(action) for action in cited]
        compact_trials.append(trial)
    compact["trials"] = compact_trials
    return compact


def _action_matches_evidence_location(
    action: Mapping[str, Any],
    evidence_locations: set[tuple[str, int | str]],
) -> bool:
    trace_id = str(action.get("trace_id", "") or "")
    message_index = action.get("message_index")
    step_pointer = str(action.get("step_pointer", "") or "")
    candidates: set[tuple[str, int | str]] = set()
    if message_index is not None:
        try:
            candidates.add((trace_id, int(message_index)))
            candidates.add(("", int(message_index)))
        except (TypeError, ValueError):
            pass
    if step_pointer:
        candidates.add((trace_id, step_pointer))
        candidates.add(("", step_pointer))
    return bool(candidates & evidence_locations)


def _compact_causal_action(action: Mapping[str, Any]) -> dict[str, Any]:
    compact = {
        key: action[key]
        for key in (
            "evidence_id",
            "trace_id",
            "role",
            "message_index",
            "step_pointer",
            "tool",
            "request",
            "selection_reasons",
        )
        if key in action and action[key] not in (None, "", {}, [])
    }
    response_evidence = action.get("response_evidence")
    if isinstance(response_evidence, Mapping):
        compact["response_evidence"] = response_evidence
    elif action.get("response") not in (None, "", {}, []):
        compact["response"] = action["response"]
    return compact


def _compact_optimization_hypotheses(document: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate immutable contracts before presenting them to the model."""
    records = document.get("hypotheses") if isinstance(document, Mapping) else None
    if not isinstance(records, list):
        return {}
    compact_records: list[dict[str, Any]] = []
    fields = (
        "hypothesis_id",
        "required_behavior",
        "forbidden_behavior",
        "decision_contract",
        "causal_coverage",
        "supported_causal_hypothesis_ids",
        "falsified_causal_hypothesis_ids",
        "prior_experiment_assessment",
        "lever_policy",
    )
    for record in records:
        if not isinstance(record, Mapping):
            continue
        compact_record = {key: record[key] for key in fields if key in record and record[key] not in (None, "", {}, [])}
        lever_policy = compact_record.get("lever_policy")
        if isinstance(lever_policy, Mapping):
            compact_record["lever_policy"] = {
                key: lever_policy[key]
                for key in ("recommended_lever", "target_ref", "why_this_lever", "why_not_other_levers")
                if key in lever_policy and lever_policy[key] not in (None, "", {}, [])
            }
        compact_records.append(compact_record)
    return {"version": document.get("version"), "hypotheses": compact_records}


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
        if not bounded:
            # A complete causal record is safer than a head-only JSON excerpt:
            # decisive evidence and counterfactuals often live near the tail.
            bounded.append(record)
        elif remaining > 256:
            bounded.append(
                {
                    "omitted_due_to_budget": True,
                    "content_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    "encoded_chars": len(encoded),
                }
            )
        break
    return bounded


def _leakage_guard_from_sources(sources: list[Any], *, case_ids: set[str]) -> dict[str, Any]:
    sensitive_literals: set[str] = set()
    task_specific_literals: set[str] = set()
    task_numeric_literals: set[str] = set()
    public_tool_fields: set[str] = set()
    non_public_tool_fields: set[str] = set()

    def visit(value: Any, parent_key: str = "") -> None:
        if isinstance(value, Mapping):
            for raw_key, child in value.items():
                key = str(raw_key).casefold()
                if key in _SENSITIVE_VALUE_KEYS:
                    sensitive_literals.update(_literal_values(child))
                if key in _TASK_SPECIFIC_VALUE_KEYS:
                    task_specific_literals.update(_literal_values(child))
                if key in _PUBLIC_TASK_TEXT_KEYS and isinstance(child, str):
                    task_numeric_literals.update(_numeric_literals(child))
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
        "task_specific_literals": sorted(task_specific_literals),
        "task_numeric_literals": sorted(task_numeric_literals),
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


def _numeric_literals(value: str) -> set[str]:
    literals: set[str] = set()
    for match in re.finditer(r"(?<![A-Za-z0-9_])[$€£¥￥]?\s*\d[\d,]*(?:\.\d+)?%?(?![A-Za-z0-9_])", value):
        normalized = re.sub(r"[^0-9.]", "", match.group())
        if len(re.sub(r"\D", "", normalized)) >= 3:
            literals.add(normalized)
    return literals


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
        for key in (
            "case_ids",
            "sensitive_literals",
            "task_specific_literals",
            "task_numeric_literals",
            "public_tool_fields",
            "non_public_tool_fields",
        )
    }


def _generation_context(experience: dict[str, Any] | None) -> dict[str, Any]:
    value = experience or {}
    sibling = value.get("sibling_generation")
    sibling = dict(sibling) if isinstance(sibling, dict) else {}
    improver_policy = value.get("improver_policy")
    improver_policy = dict(improver_policy) if isinstance(improver_policy, Mapping) else {}
    generation_directives = improver_policy.get("generation_directives")
    generation_directives = dict(generation_directives) if isinstance(generation_directives, Mapping) else {}
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
        "improver_policy": {
            "version_id": str(improver_policy.get("version_id", "") or ""),
            "policy_digest": str(improver_policy.get("policy_digest", "") or ""),
            "generation_directives": generation_directives,
        },
    }


def _build_patch_request(  # pylint: disable=huawei-too-many-arguments
    *,
    source_prompt: str,
    source_harness: dict[str, Any],
    evidence: dict[str, Any],
    causal_hypothesis_policy: dict[str, Any],
    causal_binding: dict[str, Any],
    generation_context: dict[str, Any],
    rejected_capabilities: list[dict[str, Any]],
    requested_surfaces: set[str],
    required_budget_fields: set[str],
    skill_mutation_policy: dict[str, Any],
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
            "skill_package": f"{_SKILLS_DIR}/<skill-name>/SKILL.md",
            "skill_mutation": skill_mutation_policy,
            "budget_fields": sorted(_BUDGET_HARNESS_FIELDS),
            "required_budget_fields": sorted(required_budget_fields),
            "rail_config": {
                field: {
                    "allowed_fields": sorted(allowed_fields),
                    "behavior": (
                        "Intercept an answer-shaped draft before release and require a task-visible "
                        "evidence-closure revision. The instruction field contains the minimal "
                        "failure-specific trigger, action, and observable enforced at that boundary."
                        if field == "submission_checkpoint"
                        else "Compact repeated tool-loop behavior without changing task semantics."
                    ),
                }
                for field, allowed_fields in _RAIL_HARNESS_FIELDS.items()
                if isinstance(source_harness.get(field), Mapping)
            },
            "unsupported": [
                "task implementation code",
                "tool implementation",
                "benchmark or evaluator",
                "model weights",
                "environment",
            ],
        },
        "requested_mutation_surfaces": sorted(requested_surfaces),
        "failure_evidence": model_evidence,
        "causal_hypothesis_policy": causal_hypothesis_policy,
        "causal_binding": causal_binding,
        "sibling_generation": _sanitize_model_evidence(generation_context, case_ids),
        "improver_policy_directives": _sanitize_model_evidence(
            generation_context.get("improver_policy", {}),
            case_ids,
        ),
        "rejected_capabilities": _sanitize_model_evidence(rejected_capabilities, case_ids),
    }
    return f"""Create one minimal, transferable behavior intervention for the current AI Harness.

This is sibling candidate {generation_context["generation_index"]} of
{generation_context["candidate_count"]}. Its secondary differentiation lens is:
{generation_context["differentiation_focus"]}.
Use that lens only when the evidence supports it; it must not override the diagnosed
failed requirement or invent another root cause. Do not duplicate the semantic
intervention represented by prior_proposals.

Return ONLY this JSON shape:
{{
  "source_hypothesis_id": "the supported causal hypothesis this candidate implements",
  "system_prompt_append": "a concise instruction block to append",
  "skill": {{"name": "", "description": "", "body": ""}},
  "harness_updates": {{}},
  "rationale": "evidence-grounded reason",
  "expected_effect": "observable behavior change"
}}

Rules:
- system_prompt_append is required for a Prompt target and must be empty when
  the requested target does not include Prompt. A non-empty append must be no more than
  {_MAX_PROMPT_APPEND_CHARS} characters and must not repeat existing text.
- skill is required for a Skill target and must be an empty object otherwise.
  A Skill is one reusable procedural capability, not a disguised answer: use a
  lowercase hyphenated name, a routing description that states when it applies,
  and a bounded SKILL.md body with trigger -> procedure -> validation -> fallback.
  It may retain public domain/tool concepts needed to execute the capability, but
  must omit benchmark IDs, named source artifacts, known answers, exact target
  values, cells/sections unique to the observed instance, evaluator language, and
  the observed task family when it appears only as an illustrative example.
  Validation and fallback must be executable without a hidden expected, gold,
  reference, or known answer. Use task-visible requirements, invariants,
  independent recomputation from public inputs, and artifact read-back instead.
  Obey supported_mutation_contract.skill_mutation: for `update`, rewrite one
  allowed existing Skill under its exact current name instead of adding an
  overlapping Skill; for `add`, choose a new name that is not already present.
- harness_updates is optional. Budget targets may change only fields explicitly
  listed under supported_mutation_contract.budget_fields. When
  supported_mutation_contract.required_budget_fields is non-empty, every named
  field must be changed; modifying an adjacent budget does not implement the
  evidenced causal intervention. Rail targets may change only fields explicitly
  listed under supported_mutation_contract.rail_config. Each rail entry exposes
  its allowed_fields and runtime behavior. An update is required
  for either target and must be empty for a Prompt-only target.
- A submission_checkpoint update must enable the checkpoint and include a concise
  instruction derived from causal_binding. The instruction must state what must be
  verified before release and what observable invalidates completion. It must not
  include case IDs, hidden answers, evaluator wording, or instance-only literals.
- Do not replace or summarize the current prompt. Return only the new append.
- Apply improver_policy_directives as versioned search guidance only when they
  are compatible with current causal evidence and the supported mutation
  contract. They cannot override a failed requirement, invent an activation
  event, or weaken the one-intervention rule.
- Obey supported_mutation_contract. Do not disguise an unsupported Tool,
  Config, environment, evaluator, or framework defect as a Prompt defect.
- Keep one primary behavioral intervention expressed as trigger -> action ->
  observable. Do not write generic advice or a broad checklist.
- causal_binding is controller-frozen. The candidate must implement its own
  observed_decision -> required_behavior -> predicted_observable relationship.
  Do not replace that relationship with a familiar mechanism from another task,
  even when the replacement sounds more general or easier to test.
- Every causal_binding.scope_boundary entry is a hard negative constraint. No
  primary step, validation step, recovery path, or last-resort fallback may perform
  an action that the boundary excludes. Omit a fallback rather than violate the
  diagnosed task semantics.
- Read analyzer evidence_status before proposing the change. For `confirmed`,
  repair the confirmed decision. For `supported_hypothesis`, make the candidate a
  falsifiable experiment and state the predicted observable without upgrading the
  hypothesis to fact. Never invent a repair from `insufficient` evidence.
- Read causal_coverage before treating a diagnosis as the whole repair. A
  local_contributor is a bounded defect, not a complete explanation; do not claim
  its change will resolve residual_requirement_ids. For cluster_sufficient,
  preserve the cluster boundary. Only task_sufficient supports a whole-task
  repair claim. Use counterfactual_prediction as the candidate's behavioral test.
- The rationale must cite the observed behavior and the discriminator. The
  expected_effect must name what should be visible in the next trajectory or
  artifact, not merely claim that quality will improve.
- Paired candidate feedback outranks the original explanation. If a prior
  intervention activated without improving its target metric, do not repeat or
  paraphrase it. If it did not activate, change activation rather than the
  underlying semantic rule.
- Read hypothesis_assessment and prior_experiment_assessment. Never build a
  candidate from a hypothesis marked falsified. A falsified prior explanation
  cannot be renamed as a downstream problem and appended to the same repair;
  select a newly supported hypothesis with new discriminating evidence.
- When causal_hypothesis_policy.instrumented is true, source_hypothesis_id is
  required and must be one of supported_hypothesis_ids. Falsified or unresolved
  IDs are forbidden. This binding is validated by the controller.
- The intervention must implement the supported causal distinction itself. Do
  not substitute an easy-to-observe proxy for the predicted behavior. The
  expected_effect must preserve the Analyzer's pre-recorded counterfactual so
  the next candidate result can support or refute it.
- The executable intervention must entail its expected_effect. Do not claim a
  branch choice or upstream decision that the prompt or Skill only constrains after
  that branch has already been chosen. Simulate an avoidance path: if the rule can
  be satisfied merely by switching a label, branch, or wording while preserving the
  faulty decision process, add the missing evidence-based decision procedure.
- Preserve supported source behavior outside the diagnosed decision. In particular,
  never repair a downstream contradiction by arbitrarily reversing its upstream
  classification. Establish the upstream decision from task-visible evidence first,
  then make dependent conclusions and actions consistent with it.
- When the diagnosed mechanism is a requirement, eligibility, conformance, or other
  evidence-backed classification, the executable intervention must contain the
  reusable decision procedure, not only a rule that starts after the classification:
  (1) before verification, draft and inventory every atomic material output claim,
  explicitly including every conclusion, verdict, classification, prescribed action,
  recommended action, and required modification; assign every claim its own verification
  record, and allow no later or released material claim to bypass this inventory;
  (2) identify the authoritative task-visible requirement; (3) establish the governed
  subject, object, operation, and boundary of that source and match them to the assessed
  target; (4) identify which actor, process, dependency, or evaluated target owns
  the required outcome and whether the assessed target itself must contain it; (5) map
  the observed evidence to that exact in-scope requirement with an exact task-visible
  quote or bounded span for every retained claim and separately record a stable locator
  that lets another pass retrieve the same source span, instead of
  promoting one possible form, metadata field, or useful practice into a
  requirement; (6) identify every condition that activates the mapped requirement and
  establish from task-visible evidence whether each condition holds in the current state;
  an inactive or unresolved trigger cannot support the claim; (7) try to construct a
  minimal task-visible countermodel in which the
  cited evidence remains true while the proposed claim is false, including satisfaction
  by another owner, dependency, representation, or an inactive trigger; a surviving
  countermodel defeats the claim; (8) allow a negative classification or prescribed
  correction only for an inventoried mandatory claim that survives this falsification
  attempt; and (9) immediately before release, scan every material conclusion,
  classification, prescription, recommendation, and required modification in the final
  output against the original inventory. If any is absent, block release and rerun the
  complete procedure from the expanded inventory. Recompute after rejected claims are removed.
  If the candidate trigger says "when/if/after you confirm" a classification but does
  not define these preceding decisions, the candidate is incomplete and avoidable.
- The appended text is a global, reusable policy. Never copy a case ID, issue ID,
  known/gold/reference answer, expected output, benchmark-specific entity list,
  fixed answer set, or other case-specific literal into it.
- Never introduce or name a tool argument/return field unless the evidence marks
  it as part of that tool's public request schema. A response-only, hidden,
  private, or merely observed field is not a valid request field.
- Generalize the causal distinction and observable behavior. Do not memorize the
  benchmark instance. Redaction markers in the evidence must remain redacted.
- A persistent Prompt rule must transfer across materially different task domains,
  not merely to another instance in the observed domain. Write the append one
  abstraction level above the evidence: preserve the causal decision rule while
  removing the observed domain, artifact type, file extension, application,
  command, package/library, function name, benchmark role, and worked example.
  Those concrete details may appear in rationale as evidence, but not in the
  persistent system_prompt_append.
- Apply this substitution test before returning: if every observed domain noun,
  artifact name, application, and command were replaced by unrelated ones, the
  system_prompt_append must remain valid without editing. It must plausibly govern
  at least two materially different task families. If it fails, abstract the
  trigger/action/observable further without turning it into generic advice or
  changing the causal relationship in causal_binding.
- A persistent submission_checkpoint instruction is also a global Harness policy.
  Apply the same cross-domain substitution test and abstraction requirements as a
  Prompt rule; runtime enforcement does not permit observed-domain vocabulary.
- A persistent Skill must transfer unchanged to at least two materially different
  tasks in the same capability family. Keep the concrete public operations required
  by that capability, but remove every observed-instance noun, source name, answer,
  fixed target value, and worked solution. Do not over-abstract a procedural Skill
  until it no longer tells the runtime what to do.
- State a public trigger, reusable decision procedure, scope boundary, and
  observable. Prefer operational words over broad advice; do not force output
  content that only this case requested.
- Treat analyzer_causal_digest, analyzer_diagnoses, and
  candidate_feedback_delta as primary. Use failed_case_evidence only as a
  bounded compatibility fallback when legacy_fallback_used is true.

INPUT:
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def _patch_validation_errors(
    value: dict[str, Any],
    *,
    source_harness: dict[str, Any] | None = None,
    leakage_guard: dict[str, Any] | None = None,
    causal_hypothesis_policy: dict[str, Any] | None = None,
    requested_surfaces: set[str] | None = None,
    required_budget_fields: set[str] | None = None,
    skill_mutation_policy: dict[str, Any] | None = None,
) -> list[str]:
    try:
        _validate_patch(
            value,
            source_prompt="",
            source_harness=source_harness,
            leakage_guard=leakage_guard,
            causal_hypothesis_policy=causal_hypothesis_policy,
            requested_surfaces=requested_surfaces,
            required_budget_fields=required_budget_fields,
            skill_mutation_policy=skill_mutation_policy,
        )
    except (TypeError, ValueError) as exc:
        return [str(exc)]
    return []


def _forbidden_concrete_term_errors(value: Any, forbidden_terms: list[str]) -> list[str]:
    """Keep rejected implementation vocabulary out of the persistent retry."""
    if not forbidden_terms or not isinstance(value, Mapping):
        return []
    persistent_text = _persistent_intervention_text(value)
    append_normalized = _normalized_phrase(persistent_text)
    append_stems = {_light_stem(token) for token in append_normalized.split()}
    remaining: list[str] = []
    for term in forbidden_terms:
        raw_term = str(term).strip()
        term_normalized = _normalized_phrase(raw_term)
        if not term_normalized:
            continue
        plain_term = re.fullmatch(r"[A-Za-z0-9 ]+", raw_term) is not None
        exact_match = (
            f" {term_normalized} " in f" {append_normalized} "
            if plain_term
            else raw_term.casefold() in persistent_text.casefold()
        )
        tokens = term_normalized.split()
        stem_match = (
            len(tokens) == 1
            and re.fullmatch(r"[A-Za-z]+", raw_term) is not None
            and _light_stem(tokens[0]) in append_stems
        )
        if exact_match or stem_match:
            remaining.append(raw_term)
    if not remaining:
        return []
    return [
        "persistent intervention retained concrete terms rejected by the transfer audit: "
        + ", ".join(sorted(set(remaining), key=str.casefold))
    ]


def _quoted_binding_phrases(causal_binding: Mapping[str, Any]) -> list[str]:
    """Extract observed worked examples that must not survive abstraction."""
    phrases: list[str] = []
    bindings = causal_binding.get("bindings", [])
    texts: list[str] = []
    for binding in bindings if isinstance(bindings, list) else []:
        if not isinstance(binding, Mapping):
            continue
        for key in (
            "observed_decision",
            "causal_distinction",
            "required_behavior",
            "predicted_observable",
        ):
            value = binding.get(key)
            if isinstance(value, str):
                texts.append(value)
        boundary = binding.get("scope_boundary", [])
        if isinstance(boundary, list):
            texts.extend(str(item) for item in boundary if isinstance(item, str))

    for value in texts:
        for match in re.finditer(r"(?P<quote>['\"`])(?P<body>[^'\"`\r\n]{8,160})(?P=quote)", value):
            phrase = " ".join(match.group("body").split())
            if len(re.findall(r"[A-Za-z0-9]+", phrase)) < 3:
                continue
            if phrase not in phrases:
                phrases.append(phrase)
    return phrases


def _normalized_phrase(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _light_stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _validate_patch(
    value: dict[str, Any],
    *,
    source_prompt: str,
    source_harness: dict[str, Any] | None = None,
    leakage_guard: dict[str, Any] | None = None,
    causal_hypothesis_policy: dict[str, Any] | None = None,
    requested_surfaces: set[str] | None = None,
    required_budget_fields: set[str] | None = None,
    skill_mutation_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("patch must be a mapping")
    allowed_top_level = {
        "source_hypothesis_id",
        "system_prompt_append",
        "skill",
        "harness_updates",
        "rationale",
        "expected_effect",
    }
    unknown = sorted(set(value) - allowed_top_level)
    if unknown:
        raise ValueError(f"unknown patch fields: {unknown}")
    surfaces = set(requested_surfaces or {"prompt"})
    prompt_required = bool(surfaces & {"prompt", "unspecified"})
    skill_required = "skill" in surfaces
    budget_required = "budget" in surfaces
    rail_required = "rail" in surfaces
    append = str(value.get("system_prompt_append", "") or "").strip()
    _validate_generated_text_integrity(append, field="system_prompt_append")
    if prompt_required and not append:
        raise ValueError("system_prompt_append must not be empty")
    if not prompt_required and append:
        raise ValueError("system_prompt_append must be empty when Prompt is not the requested mutation surface")
    if len(append) > _MAX_PROMPT_APPEND_CHARS:
        raise ValueError(f"system_prompt_append exceeds {_MAX_PROMPT_APPEND_CHARS} characters")
    if append and source_prompt and append.casefold() in source_prompt.casefold():
        raise ValueError("system_prompt_append already exists in the parent prompt")
    _validate_global_prompt_append(append, leakage_guard or {})

    raw_skill = value.get("skill", {})
    if raw_skill is None:
        raw_skill = {}
    if not isinstance(raw_skill, Mapping):
        raise TypeError("skill must be a mapping")
    if raw_skill and all(not str(item or "").strip() for item in raw_skill.values()):
        raw_skill = {}
    skill = _validate_skill_spec(raw_skill, leakage_guard=leakage_guard or {}) if raw_skill else {}
    if skill_required and not skill:
        raise ValueError("skill must not be empty for a Skill intervention")
    if not skill_required and skill:
        raise ValueError("skill must be empty when Skill is not the requested mutation surface")
    if skill:
        policy = skill_mutation_policy or {"operation": "add", "allowed_names": []}
        operation = str(policy.get("operation", "add") or "add")
        allowed_names = {str(name) for name in policy.get("allowed_names", []) if str(name)}
        if operation == "update" and str(skill["name"]) not in allowed_names:
            raise ValueError(
                "skill.name must identify an existing evidence-referenced Skill selected for update: "
                f"{sorted(allowed_names)}"
            )
        existing_names = {
            str(item.get("name", "") or "") for item in policy.get("existing_skills", []) if isinstance(item, Mapping)
        }
        if operation == "add" and str(skill["name"]) in existing_names:
            raise ValueError(f"new Skill name already exists in the parent Harness: {skill['name']}")

    source_hypothesis_id = str(value.get("source_hypothesis_id", "") or "").strip()
    hypothesis_policy = causal_hypothesis_policy or {}
    supported = {str(item) for item in hypothesis_policy.get("supported_hypothesis_ids", []) if str(item).strip()}
    forbidden = {
        str(item)
        for key in ("falsified_hypothesis_ids", "unresolved_hypothesis_ids")
        for item in hypothesis_policy.get(key, [])
        if str(item).strip()
    }
    if bool(hypothesis_policy.get("instrumented")):
        if not source_hypothesis_id:
            if len(supported) == 1:
                source_hypothesis_id = next(iter(supported))
            else:
                raise ValueError("source_hypothesis_id is required when multiple causal hypotheses are supported")
        if source_hypothesis_id in forbidden:
            raise ValueError(f"source_hypothesis_id is not actionable: {source_hypothesis_id}")
        if source_hypothesis_id not in supported:
            raise ValueError(f"source_hypothesis_id is not a supported hypothesis: {source_hypothesis_id}")

    raw_updates = value.get("harness_updates", {})
    if not isinstance(raw_updates, dict):
        raise TypeError("harness_updates must be a mapping")
    normalized_updates: dict[str, Any] = {}
    for raw_key, raw_value in raw_updates.items():
        key = "rollout_wall_clock_seconds" if raw_key in _WALL_CLOCK_ALIASES else str(raw_key)
        if key not in _ALLOWED_HARNESS_FIELDS:
            raise ValueError(f"harness update is not allowed: {raw_key}")
        if key in _BUDGET_HARNESS_FIELDS and not budget_required:
            raise ValueError(f"budget harness update is outside the requested mutation surface: {raw_key}")
        if key in _RAIL_HARNESS_FIELDS:
            if not rail_required:
                raise ValueError("rail harness update is outside the requested mutation surface")
            source_rail = (source_harness or {}).get(key)
            if not isinstance(source_rail, Mapping):
                raise ValueError(f"the current PolicyHarness does not expose {key} rail config")
            if not isinstance(raw_value, Mapping) or not raw_value:
                raise TypeError(f"{key} update must be a non-empty mapping")
            unknown_rail_fields = sorted(set(raw_value) - _RAIL_HARNESS_FIELDS[key])
            if unknown_rail_fields:
                raise ValueError(f"{key} update contains unsupported fields: {unknown_rail_fields}")
            normalized_rail: dict[str, bool | int | str] = {}
            for rail_key, rail_value in raw_value.items():
                if rail_key == "enabled":
                    if not isinstance(rail_value, bool):
                        raise TypeError(f"{key} enabled must be a boolean")
                elif rail_key == "instruction":
                    if key != "submission_checkpoint" or not isinstance(rail_value, str):
                        raise TypeError(f"{key} {rail_key} must be a string")
                    rail_value = rail_value.strip()
                    if not 40 <= len(rail_value) <= 4_000:
                        raise ValueError("submission_checkpoint instruction must contain 40-4000 characters")
                    _validate_generated_text_integrity(rail_value, field="submission_checkpoint.instruction")
                    _validate_global_prompt_append(rail_value, leakage_guard or {})
                elif isinstance(rail_value, bool) or not isinstance(rail_value, int):
                    raise TypeError(f"{key} {rail_key} must be an integer")
                elif key == "submission_checkpoint" and not 1 <= rail_value <= 3:
                    raise ValueError("submission_checkpoint max_revisions must be between 1 and 3")
                elif key != "submission_checkpoint" and not 1 <= rail_value <= 100:
                    raise ValueError(f"{key} {rail_key} must be between 1 and 100")
                normalized_rail[str(rail_key)] = rail_value
            if key == "submission_checkpoint":
                if normalized_rail.get("enabled") is not True:
                    raise ValueError("submission_checkpoint intervention must set enabled to true")
                if not str(normalized_rail.get("instruction", "") or "").strip():
                    raise ValueError("submission_checkpoint intervention must include instruction")
            normalized_updates[key] = normalized_rail
            continue
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise TypeError(f"harness update {raw_key} must be an integer")
        if key == "max_steps" and not 1 <= raw_value <= 5_000:
            raise ValueError("max_steps must be between 1 and 5000")
        if key == "rollout_wall_clock_seconds" and not 30 <= raw_value <= 14_400:
            raise ValueError("rollout_wall_clock_seconds must be between 30 and 14400")
        if key == "command_timeout_seconds" and not 30 <= raw_value <= 14_400:
            raise ValueError("command_timeout_seconds must be between 30 and 14400")
        if source_harness is not None and raw_value == source_harness.get(key):
            raise ValueError(f"harness update must change the current value: {key}")
        normalized_updates[key] = raw_value
    if budget_required and not (_BUDGET_HARNESS_FIELDS & set(normalized_updates)):
        raise ValueError("harness_updates must not be empty for a budget intervention")
    required_budget = set(required_budget_fields or ())
    unknown_required_budget = required_budget - _BUDGET_HARNESS_FIELDS
    if unknown_required_budget:
        raise ValueError(f"required budget fields are unsupported: {sorted(unknown_required_budget)}")
    missing_required_budget = required_budget - set(normalized_updates)
    if budget_required and missing_required_budget:
        raise ValueError(
            f"harness_updates must change the evidence-identified budget fields: {sorted(missing_required_budget)}"
        )
    if rail_required and not (set(_RAIL_HARNESS_FIELDS) & set(normalized_updates)):
        raise ValueError("harness_updates must include one declared rail config for a rail intervention")
    if not budget_required and not rail_required and normalized_updates:
        raise ValueError("harness_updates must be empty when no config or Rail mutation is requested")
    rationale = str(value.get("rationale", "") or "").strip()
    expected_effect = str(value.get("expected_effect", "") or "").strip()
    _validate_generated_text_integrity(rationale, field="rationale")
    _validate_generated_text_integrity(expected_effect, field="expected_effect")
    if not rationale:
        raise ValueError("rationale must name the evidence-grounded discriminator")
    if not expected_effect:
        raise ValueError("expected_effect must name an observable candidate outcome")
    return {
        "source_hypothesis_id": source_hypothesis_id,
        "system_prompt_append": append,
        "skill": skill,
        "harness_updates": normalized_updates,
        "rationale": rationale,
        "expected_effect": expected_effect,
    }


def _validate_generated_text_integrity(value: str, *, field: str) -> None:
    if "\ufffd" in value:
        raise ValueError(f"{field} contains Unicode replacement characters; regenerate clean text")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        raise ValueError(f"{field} contains unsupported control characters")


def _validate_skill_spec(value: Mapping[str, Any], *, leakage_guard: dict[str, Any]) -> dict[str, str]:
    unknown = sorted(set(value) - {"name", "description", "body"})
    if unknown:
        raise ValueError(f"unknown skill fields: {unknown}")
    name = str(value.get("name", "") or "").strip().casefold().replace("_", "-")
    description = str(value.get("description", "") or "").strip()
    body = str(value.get("body", "") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", name):
        raise ValueError("skill.name must be a lowercase hyphenated identifier of 3-64 characters")
    if not 20 <= len(description) <= _MAX_SKILL_DESCRIPTION_CHARS:
        raise ValueError(f"skill.description must contain 20-{_MAX_SKILL_DESCRIPTION_CHARS} characters")
    if not 80 <= len(body) <= _MAX_SKILL_BODY_CHARS:
        raise ValueError(f"skill.body must contain 80-{_MAX_SKILL_BODY_CHARS} characters")
    _validate_generated_text_integrity(description, field="skill.description")
    _validate_generated_text_integrity(body, field="skill.body")
    if body.startswith("---"):
        raise ValueError("skill.body must not include YAML front matter")
    persistent_text = f"{description}\n{body}"
    if any(pattern.search(persistent_text) for pattern in _PERSISTENT_HIDDEN_OUTCOME_PATTERNS):
        raise ValueError(
            "skill validation must not depend on an expected, gold, reference, or known answer; "
            "use task-visible contracts, invariants, independent recomputation, or artifact read-back"
        )
    _validate_global_prompt_append(persistent_text, leakage_guard)
    return {"name": name, "description": description, "body": body}


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

    for literal in guard.get("task_specific_literals", []):
        literal = str(literal).strip()
        if len(literal) >= 4 and literal.casefold() in normalized:
            raise ValueError("system_prompt_append contains a task-specific entity literal")

    appended_numbers = _numeric_literals(append)
    if appended_numbers & {str(value) for value in guard.get("task_numeric_literals", [])}:
        raise ValueError("system_prompt_append contains a task-specific numeric literal")

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
    candidate_harness: Path,
    source_prompt: str,
    source_harness: dict[str, Any],
    patch: dict[str, Any],
    skill_mutation_policy: dict[str, Any],
) -> None:
    if patch["system_prompt_append"]:
        prompt = source_prompt.rstrip() + "\n\n" + patch["system_prompt_append"].strip() + "\n"
        prompt_path.write_text(prompt, encoding="utf-8")
    skill = patch.get("skill") if isinstance(patch.get("skill"), Mapping) else {}
    if skill:
        skill_dir = candidate_harness / _SKILLS_DIR / str(skill["name"])
        operation = str(skill_mutation_policy.get("operation", "add") or "add")
        if operation == "add" and skill_dir.exists():
            raise ValueError(f"skill already exists in the parent Harness: {skill['name']}")
        if operation == "update" and not skill_dir.is_dir():
            raise ValueError(f"skill selected for update does not exist in the parent Harness: {skill['name']}")
        skill_dir.mkdir(parents=True, exist_ok=operation == "update")
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(
            "---\n"
            + yaml.safe_dump(
                {"name": str(skill["name"]), "description": str(skill["description"])},
                allow_unicode=True,
                sort_keys=False,
            ).strip()
            + "\n---\n\n"
            + str(skill["body"]).strip()
            + "\n",
            encoding="utf-8",
        )
    harness_updates = dict(patch["harness_updates"])
    if not harness_updates:
        return
    harness = dict(source_harness)
    rail_updates = {field: harness_updates.pop(field) for field in _RAIL_HARNESS_FIELDS if field in harness_updates}
    harness.update(harness_updates)
    for field, updates in rail_updates.items():
        if isinstance(updates, Mapping):
            rail_config = dict(harness.get(field, {}))
            rail_config.update(updates)
            harness[field] = rail_config
    harness_path.write_text(json.dumps(harness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _verify_candidate_tree(
    *,
    source_tree: dict[str, str],
    source_harness: Path,
    candidate_harness: Path,
    requested_surfaces: set[str],
    skill_mutation_policy: dict[str, Any],
) -> list[str]:
    candidate_tree = _tree_hashes(candidate_harness)
    if not set(source_tree) <= set(candidate_tree):
        raise RuntimeError("candidate PolicyHarness must preserve the complete source directory tree")
    changed = sorted(
        path for path in set(source_tree) | set(candidate_tree) if source_tree.get(path) != candidate_tree.get(path)
    )
    added = set(candidate_tree) - set(source_tree)
    added_skill_paths = {
        path
        for path in added
        if re.fullmatch(rf"{_SKILLS_DIR}/[a-z][a-z0-9-]{{2,63}}/SKILL\.md", path.replace("\\", "/"))
    }
    operation = str(skill_mutation_policy.get("operation", "none") or "none")
    updatable_skill_paths = {
        f"{_SKILLS_DIR}/{name}/SKILL.md" for name in skill_mutation_policy.get("allowed_names", []) if str(name)
    }
    allowed_skill_paths = added_skill_paths if operation == "add" else updatable_skill_paths
    illegal = sorted(set(changed) - {_PROMPT_PATH, _HARNESS_PATH} - allowed_skill_paths)
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
    if requested_surfaces & {"prompt", "unspecified"} and _PROMPT_PATH not in changed:
        raise RuntimeError("candidate did not change system_prompt.md")
    if "budget" in requested_surfaces and _HARNESS_PATH not in changed:
        raise RuntimeError("candidate did not change harness.json for the requested budget intervention")
    if "rail" in requested_surfaces and _HARNESS_PATH not in changed:
        raise RuntimeError("candidate did not change harness.json for the requested rail intervention")
    changed_skill_paths = set(changed) & allowed_skill_paths
    if "skill" in requested_surfaces and not changed_skill_paths:
        raise RuntimeError(f"candidate did not {operation} the selected native SKILL.md package")
    if operation == "update" and added_skill_paths:
        raise RuntimeError("candidate added a Skill when the evidence requires updating an existing Skill")
    if "skill" not in requested_surfaces and added_skill_paths:
        raise RuntimeError("candidate added a Skill outside the requested mutation surface")
    return changed


def _build_actions(
    *,
    patch: dict[str, Any],
    issue_ids: list[str],
    case_ids: list[str],
    source_hypothesis_id: str,
    source_hypothesis_semantic_id: str,
    analyzer_counterfactual_predictions: list[str],
    skill_mutation_policy: dict[str, Any],
) -> list[dict[str, Any]]:
    common = {
        "role": _ROLE,
        "operation": "modify",
        "action_type": "update_file",
        "attributed_issue_ids": issue_ids,
        "depends_on": [],
        "run_if": "dependency_succeeded",
        "allowed_skills": [],
        "allowed_tools": [],
        "expected_effect": patch["expected_effect"],
        "risk_notes": ["Only the current PolicyHarness prompt/config package is changed."],
        "constraints": {
            "target_case_ids": case_ids,
            "source_causal_hypothesis_id": source_hypothesis_id,
            "source_causal_hypothesis_semantic_id": source_hypothesis_semantic_id,
            "analyzer_counterfactual_predictions": analyzer_counterfactual_predictions,
        },
    }
    actions = []
    if patch["system_prompt_append"]:
        actions.append(
            {
                **common,
                "action_group": "prompt",
                "action_id": "evobench_policy_prompt",
                "target_path": _PROMPT_PATH,
                "description": "Append one evidence-grounded PolicyHarness instruction block.",
                "rationale": patch["rationale"],
                "intervention": patch["system_prompt_append"],
            }
        )
    skill = patch.get("skill") if isinstance(patch.get("skill"), Mapping) else {}
    if skill:
        skill_path = f"{_SKILLS_DIR}/{skill['name']}/SKILL.md"
        skill_operation = str(skill_mutation_policy.get("operation", "add") or "add")
        actions.append(
            {
                **common,
                "operation": skill_operation,
                "action_type": "add_file" if skill_operation == "add" else "update_file",
                "action_group": "skill",
                "action_id": f"evobench_policy_skill_{skill['name']}",
                "target_path": skill_path,
                "description": str(skill["description"]),
                "rationale": patch["rationale"],
                "intervention": str(skill["body"]),
            }
        )
    budget_updates = {key: value for key, value in patch["harness_updates"].items() if key in _BUDGET_HARNESS_FIELDS}
    if budget_updates:
        actions.append(
            {
                **common,
                "action_group": "config",
                "action_id": "evobench_policy_budget",
                "target_path": _HARNESS_PATH,
                "description": "Adjust only the permitted PolicyHarness execution budget fields.",
                "rationale": patch["rationale"],
                "intervention": json.dumps(budget_updates, ensure_ascii=False, sort_keys=True),
                "depends_on": ["evobench_policy_prompt"] if patch["system_prompt_append"] else [],
            }
        )
    for rail_field in sorted(set(_RAIL_HARNESS_FIELDS) & set(patch["harness_updates"])):
        actions.append(
            {
                **common,
                "action_group": "rail",
                "action_id": f"evobench_policy_{rail_field}",
                "target_path": _HARNESS_PATH,
                "description": f"Adjust the declared {rail_field} rail configuration.",
                "rationale": patch["rationale"],
                "intervention": json.dumps(
                    {rail_field: patch["harness_updates"][rail_field]},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "depends_on": [action["action_id"] for action in actions],
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
                "mechanism_types": sorted({str(action["action_group"]) for action in actions}),
                "optimization_surfaces": sorted({str(action["action_group"]) for action in actions}),
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
            "action_group": str(action.get("action_group", "") or ""),
            "operation": str(action.get("operation", "modify") or "modify"),
            "target_path": action["target_path"],
            "runtime_name": (
                Path(action["target_path"]).parent.name
                if str(action.get("action_group", "") or "") == "skill"
                else Path(action["target_path"]).stem
            ),
            "expected_effect": action.get("expected_effect", ""),
            "description": action.get("description", ""),
            "rationale": action.get("rationale", ""),
            "intervention": action.get("intervention", ""),
            "attributed_issue_ids": [
                str(issue_id) for issue_id in action.get("attributed_issue_ids", []) if str(issue_id)
            ],
            "source_causal_hypothesis_id": str(
                (action.get("constraints", {}) or {}).get("source_causal_hypothesis_id", "")
            ),
            "source_causal_hypothesis_semantic_id": str(
                (action.get("constraints", {}) or {}).get("source_causal_hypothesis_semantic_id", "")
            ),
            "analyzer_counterfactual_predictions": [
                str(item)
                for item in (action.get("constraints", {}) or {}).get(
                    "analyzer_counterfactual_predictions",
                    [],
                )
                if str(item)
            ],
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
    for transient_key in (
        "candidate_gate",
        "candidate_ready_roles",
        "promoted_roles",
        "published_roles",
        "staged_roles",
        "verified_roles",
    ):
        payload.pop(transient_key, None)
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
