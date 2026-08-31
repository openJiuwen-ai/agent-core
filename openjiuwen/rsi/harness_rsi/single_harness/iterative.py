# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Iterative benchmark optimization for one standalone Expert Harness."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.harness_rsi.config import AutoCoordinatingHarnessConfig
from openjiuwen.rsi.harness_rsi.data_loader import DataLoader
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import (
    EvaluationResultAnalyzer,
)
from openjiuwen.rsi.harness_rsi.evaluator import TeamEvaluator
from openjiuwen.rsi.harness_rsi.evaluator.optimization_signals import (
    evaluation_optimization_signals,
)
from openjiuwen.rsi.harness_rsi.evaluator.requirement_results import (
    ATOMIC_CHECK_GROUP,
    FAIL_TO_PASS_GROUP,
    PASS_TO_PASS_GROUP,
    evaluation_requirement_results,
)
from openjiuwen.rsi.harness_rsi.evaluator.trajectory_usage import (
    collect_jsonl_pre_edit_successful_usage,
    collect_jsonl_successful_usage,
    collect_pre_edit_successful_usage,
    collect_successful_skill_names,
    collect_successful_tool_names,
)
from openjiuwen.rsi.harness_rsi.improver_evolution.policy import (
    default_improver_policy,
    load_improver_policy,
)
from openjiuwen.rsi.harness_rsi.member_optimizer import MemberOptimizer
from openjiuwen.rsi.harness_rsi.member_optimizer.hypothesis import (
    compile_optimization_hypotheses,
    load_optimization_hypotheses,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.path_layout import (
    MemberOptimizerPathLayout,
)
from openjiuwen.rsi.harness_rsi.schema import (
    DatasetArtifact,
    EvaluationResultAnalysisInvocation,
)
from openjiuwen.rsi.harness_rsi.single_harness.candidate_feedback import (
    build_candidate_feedback_cohort,
    canonical_candidate_fingerprint,
    rank_candidate_proposals,
)

_ALLOWED_ACTION_GROUPS = ["prompt", "skill", "tool", "rail"]
_ALLOWED_PROMPT_SURFACES = ["prompt_section"]


@dataclass(frozen=True, slots=True)
class IterativeSingleHarnessRequest:
    """Inputs for a resumable epoch/batch optimization run."""

    dataset_files: list[str]
    harness_refs_path: str
    output_dir: str
    dataset_id: str = "single_harness_benchmark"
    resume: bool = False
    baseline_eval_ref_path: str = ""
    auto_full_baseline: bool = False


@dataclass(frozen=True, slots=True)
class IterativeSingleHarnessResult:
    """Final artifacts from an iterative single-harness run."""

    state_path: str
    report_path: str
    current_harness_refs_path: str
    best_harness_refs_path: str
    published_harness_refs_path: str
    best_score: float | None


class SingleHarnessIterativeOptimizationOrchestrator:
    """Run epochs, batches, candidate gates, and checkpoints for one harness.

    This is a single-harness control plane. It never constructs or optimizes a
    Team Skill, never generates a dataset, and never invokes Team execution.
    The member optimizer is machine-restricted to member-local Skill, Prompt
    Section, Tool, and Rail surfaces even when the caller omits those
    restrictions from YAML. Identity, soul, subagent, and Team Skill surfaces
    stay closed.
    """

    def __init__(
        self,
        config: AutoCoordinatingHarnessConfig,
        *,
        evaluator: Any | None = None,
        analyzer: Any | None = None,
        member_optimizer: Any | None = None,
        data_loader: Any | None = None,
    ) -> None:
        if config.evaluator.backend != "single_harness":
            raise ValueError(
                "SingleHarnessIterativeOptimizationOrchestrator requires config.evaluator.backend='single_harness'"
            )
        if not config.scheduling.full_evaluation_enabled:
            raise ValueError("iterative single-harness optimization requires the epoch full checkpoint")
        restricted_member_config = replace(
            config.member_optimizer,
            action_group_configs=list(_ALLOWED_ACTION_GROUPS),
            allowed_action_groups=list(_ALLOWED_ACTION_GROUPS),
            allowed_prompt_surfaces=list(_ALLOWED_PROMPT_SURFACES),
            candidate_holdout_cases=0,
            max_roles_per_run=1,
            max_actions_per_plan=(
                config.member_optimizer.max_actions_per_plan if config.member_optimizer.max_actions_per_plan > 0 else 3
            ),
        )
        self.config = replace(config, member_optimizer=restricted_member_config)
        self.evaluator = evaluator or TeamEvaluator(self.config.evaluator)
        self.analyzer = analyzer or EvaluationResultAnalyzer(self.config.evaluation_result_analyzer)
        self.member_optimizer = member_optimizer or MemberOptimizer(restricted_member_config)
        self.data_loader = data_loader or DataLoader(self.config.data_loader)
        policy_ref = restricted_member_config.improver_policy_ref.strip()
        self.improver_policy = (
            load_improver_policy(Path(policy_ref).expanduser().resolve()) if policy_ref else default_improver_policy()
        )
        self._explicit_improver_policy = bool(policy_ref)

    async def run(
        self,
        request: IterativeSingleHarnessRequest,
    ) -> IterativeSingleHarnessResult:
        output_dir = Path(request.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        state_path = output_dir / "single_harness_state.yaml"
        report_path = output_dir / "single_harness_report.yaml"
        source_refs = str(Path(request.harness_refs_path).expanduser().resolve())
        _validate_single_harness_refs(source_refs)
        dataset = _dataset_artifact(request)
        dataset_dir = Path(dataset.dataset_dir)
        fingerprint = _request_fingerprint(
            request,
            source_refs,
            sibling_candidate_count=self.config.member_optimizer.sibling_candidate_count,
            improver_policy_digest=self.improver_policy.canonical_digest,
        )
        state = _load_or_create_state(
            state_path=state_path,
            resume=request.resume,
            fingerprint=fingerprint,
            source_harness_refs_path=source_refs,
            dataset=dataset,
        )
        state["improver_policy"] = {
            "version_id": self.improver_policy.version_id,
            "policy_digest": self.improver_policy.canonical_digest,
            "policy_ref": self.config.member_optimizer.improver_policy_ref,
            "explicit": self._explicit_improver_policy,
        }
        if request.resume and state.get("status") == "completed":
            _ensure_final_publication(state=state, output_dir=output_dir)
            _write_yaml_atomic(state_path, state)
            _write_yaml_atomic(report_path, _build_report(state, dataset))
            return _result_from_state(state, state_path, report_path)

        all_cases = _load_cases(dataset.dataset_files)
        all_case_ids = {str(case.get("case_id", "") or "") for case in all_cases if str(case.get("case_id", "") or "")}
        _initialize_frozen_baseline(
            state,
            baseline_eval_ref_path=request.baseline_eval_ref_path,
            source_harness_refs_path=source_refs,
            expected_case_ids=all_case_ids,
        )
        if request.auto_full_baseline and not str(state.get("baseline_eval_ref_path", "") or ""):
            baseline_eval_ref = await self._evaluate(
                cases=all_cases,
                harness_refs_path=source_refs,
                output_dir=output_dir / "evaluations" / "frozen_baseline",
                dataset=dataset,
            )
            _initialize_frozen_baseline(
                state,
                baseline_eval_ref_path=baseline_eval_ref,
                source_harness_refs_path=source_refs,
                expected_case_ids=all_case_ids,
            )
        _write_yaml_atomic(state_path, state)
        current_refs = str(state["best_harness_refs_path"])
        max_epochs = int(self.config.max_epochs)
        for epoch in range(1, max_epochs + 1):
            existing_checkpoint = next(
                (item for item in state["epoch_checkpoints"] if int(item.get("epoch", 0) or 0) == epoch),
                None,
            )
            if request.resume and existing_checkpoint is not None:
                current_refs = str(state["best_harness_refs_path"])
                state["current_harness_refs_path"] = current_refs
                state["working_harness_refs_path"] = current_refs
                continue

            current_refs = str(state["best_harness_refs_path"])
            working_retained_case_ids = set(state.get("retained_case_ids", []))
            epoch_start_refs = current_refs
            epoch_start_score = _number(state.get("best_score"))
            epoch_start_retained_case_ids = set(working_retained_case_ids)
            if all_case_ids <= working_retained_case_ids:
                break
            if callable(getattr(type(self.data_loader), "load_files", None)):
                planned_batches = list(self.data_loader.load_files(request.dataset_files, epoch=epoch))
            else:
                planned_batches = list(self.data_loader.load(str(dataset_dir), epoch=epoch))
            batches = _validate_and_filter_planned_batches(planned_batches, expected_case_ids=all_case_ids)
            state["batch_plan_paths"][f"epoch_{epoch:03d}"] = str(
                getattr(self.data_loader, "batch_plan_path", "") or ""
            )
            _write_yaml_atomic(state_path, state)
            for batch_index, planned_batch in enumerate(batches, start=1):
                batch_key = f"epoch_{epoch:03d}:batch_{batch_index:03d}"
                completed = state["completed_batches"].get(batch_key)
                if request.resume and isinstance(completed, dict):
                    current_refs = str(completed["after_harness_refs_path"])
                    working_retained_case_ids.update(
                        completed.get(
                            "retained_case_ids_after_batch",
                            completed.get("accepted_target_case_ids", []),
                        )
                    )
                    state["working_harness_refs_path"] = current_refs
                    continue

                batch = [
                    case
                    for case in planned_batch
                    if str(case.get("case_id", "") or "") not in working_retained_case_ids
                ]
                if not batch:
                    state["completed_batches"][batch_key] = {
                        "epoch": epoch,
                        "batch_index": batch_index,
                        "source_eval_ref_path": "",
                        "analysis_ref_path": "",
                        "optimization_hypotheses_path": "",
                        "member_optimization_ref_path": "",
                        "before_harness_refs_path": current_refs,
                        "after_harness_refs_path": current_refs,
                        "candidate_gate_status": "skipped",
                        "candidate_gate_reason": "no_active_cases",
                        "accepted_target_case_ids": [],
                        "retained_case_ids_after_batch": sorted(working_retained_case_ids),
                    }
                    _write_yaml_atomic(state_path, state)
                    continue

                batch_dir = output_dir / "evaluations" / f"e{epoch:03d}" / f"b{batch_index:03d}"
                source_eval_ref = await self._evaluate(
                    cases=batch,
                    harness_refs_path=current_refs,
                    output_dir=batch_dir / "source",
                    dataset=dataset,
                )
                batch_before_refs = current_refs
                attempt_records: list[dict[str, Any]] = []
                accepted_target_case_ids: set[str] = set()
                last_member_ref = ""
                last_gate: dict[str, Any] | None = None
                analysis_ref = ""
                hypotheses_ref = ""
                analysis_refs: list[str] = []
                hypotheses_refs: list[str] = []
                residual_eval_refs: list[str] = []
                attempted_issue_signatures: set[str] = set()
                max_issue_attempts = int(self.config.member_optimizer.max_issue_attempts_per_batch)
                max_repair_rounds = int(self.config.member_optimizer.max_repair_rounds_per_batch)
                attempt_index = 0
                issue_attempt_count = 0
                analysis_round_index = 0
                last_analysis_issue_count = 0
                last_optimization_hypothesis_count = 0
                repair_stop_reason = "repair_round_limit_reached"
                current_repair_round = 1
                repair_resume_contexts: list[dict[str, Any]] = []
                attempt_source_eval_ref = source_eval_ref
                active_cases = _cases_with_ids(batch, _nonpassing_case_ids(source_eval_ref))
                pending_analysis_ref = ""

                while active_cases:
                    analysis_round_index += 1
                    analysis_dir = (
                        batch_dir / "analysis"
                        if analysis_round_index == 1
                        else batch_dir / "residual_analyses" / f"r{analysis_round_index:03d}"
                    )
                    if pending_analysis_ref:
                        analysis_ref = pending_analysis_ref
                        pending_analysis_ref = ""
                    else:
                        analysis_ref = await self._analyze(
                            eval_ref_path=attempt_source_eval_ref,
                            harness_refs_path=current_refs,
                            output_dir=analysis_dir,
                            source_stage=(
                                "single_harness_batch"
                                if analysis_round_index == 1
                                else "single_harness_residual_repair"
                            ),
                            prior_candidate_feedback=_prior_candidate_feedback(state, active_cases),
                        )
                    hypotheses_ref = compile_optimization_hypotheses(
                        analysis_ref_path=analysis_ref,
                        cases=active_cases,
                        output_path=analysis_dir / "optimization_hypotheses.yaml",
                    )
                    analysis_refs.append(analysis_ref)
                    hypotheses_refs.append(hypotheses_ref)
                    hypotheses = load_optimization_hypotheses(hypotheses_ref)
                    issue_case_ids = _analysis_issue_case_ids(analysis_ref)
                    issue_signatures = _analysis_issue_signatures(analysis_ref)
                    last_analysis_issue_count = len(issue_case_ids)
                    last_optimization_hypothesis_count = len(hypotheses)
                    nonpassing_case_ids = _nonpassing_case_ids(attempt_source_eval_ref)
                    issue_queue: list[tuple[str, str]] = []
                    repeated_issue_ids: list[str] = []
                    known_issue_signatures = set(attempted_issue_signatures)
                    for hypothesis in hypotheses:
                        issue_id = str(hypothesis.get("source_issue_id", "") or "")
                        if not issue_id:
                            continue
                        target_case_ids = issue_case_ids.get(issue_id, set())
                        if not target_case_ids or not (target_case_ids & nonpassing_case_ids):
                            continue
                        signature = issue_signatures.get(issue_id, issue_id)
                        if signature in known_issue_signatures:
                            repeated_issue_ids.append(issue_id)
                            continue
                        issue_queue.append((issue_id, signature))
                        known_issue_signatures.add(signature)
                    if not issue_queue:
                        if repair_resume_contexts:
                            resume = repair_resume_contexts.pop()
                            current_refs = str(resume["harness_refs_path"])
                            attempt_source_eval_ref = str(resume["eval_ref_path"])
                            pending_analysis_ref = str(resume["analysis_ref_path"])
                            active_cases = list(resume["active_cases"])
                            current_repair_round = int(resume["repair_round_index"])
                            continue
                        if repeated_issue_ids:
                            repair_stop_reason = "repeated_issue_detected"
                        elif not issue_case_ids:
                            repair_stop_reason = "no_actionable_analysis_issues"
                        elif not hypotheses:
                            repair_stop_reason = "no_executable_hypotheses"
                        else:
                            repair_stop_reason = "no_applicable_hypotheses_for_active_cases"
                        break

                    refresh_residual_analysis = False
                    issue_budget_exhausted = False
                    repair_budget_exhausted = False
                    deferred_repairs: list[dict[str, Any]] = []
                    for issue_queue_index, (issue_id, issue_signature) in enumerate(issue_queue):
                        if max_issue_attempts and issue_attempt_count >= max_issue_attempts:
                            issue_budget_exhausted = True
                            break
                        attempt_index += 1
                        issue_attempt_count += 1
                        attempted_issue_signatures.add(issue_signature)
                        attempt_dir = batch_dir / "attempts" / f"a{attempt_index:03d}"
                        before_attempt_refs = current_refs
                        frozen_target_case_ids = set(issue_case_ids.get(issue_id, set()))
                        frozen_target_case_ids &= nonpassing_case_ids
                        if not frozen_target_case_ids:
                            continue
                        cohort_id = _improvement_cohort_id(
                            epoch=epoch,
                            batch_index=batch_index,
                            repair_round_index=current_repair_round,
                            issue_signature=issue_signature,
                            parent_harness_refs_path=before_attempt_refs,
                            source_eval_ref_path=attempt_source_eval_ref,
                            analysis_ref_path=analysis_ref,
                            frozen_target_case_ids=frozen_target_case_ids,
                            improver_policy_digest=self.improver_policy.canonical_digest,
                        )
                        frozen_experience = {
                            "journal": list(state.get("optimization_journal", [])),
                            "lever_scoreboard": dict(state.get("lever_scoreboard", {})),
                        }
                        proposals, cohort_manifest_path = await self._generate_sibling_candidate_proposals(
                            cohort_id=cohort_id,
                            source_eval_ref=attempt_source_eval_ref,
                            analysis_ref=analysis_ref,
                            parent_harness_refs_path=before_attempt_refs,
                            optimization_hypotheses_path=hypotheses_ref,
                            source_issue_id=issue_id,
                            source_issue_signature=issue_signature,
                            frozen_target_case_ids=frozen_target_case_ids,
                            frozen_experience=frozen_experience,
                            rejected_capabilities=_rejected_capability_history(state["candidate_gates"]),
                            output_dir=output_dir,
                        )
                        _verify_frozen_candidate_proposals(proposals)
                        sibling_gates: list[dict[str, Any]] = []
                        for proposal in proposals:
                            candidate_id = str(proposal.get("candidate_id", "") or "")
                            candidate_index = int(proposal.get("candidate_index", 0) or 0)
                            candidate_refs = str(proposal.get("candidate_harness_refs_path", "") or before_attempt_refs)
                            gate = await self._candidate_gate(
                                cases=active_cases,
                                source_eval_ref=attempt_source_eval_ref,
                                analysis_ref=analysis_ref,
                                before_harness_refs_path=before_attempt_refs,
                                candidate_harness_refs_path=candidate_refs,
                                member_status=str(proposal.get("member_status", "") or ""),
                                capabilities=[
                                    dict(item) for item in proposal.get("capabilities", []) if isinstance(item, dict)
                                ],
                                causal_intervention_contracts=[
                                    dict(item)
                                    for item in proposal.get("causal_intervention_contracts", [])
                                    if isinstance(item, dict)
                                ],
                                output_dir=_candidate_evaluation_output_dir(
                                    optimization_output_dir=output_dir,
                                    epoch=epoch,
                                    batch_index=batch_index,
                                    attempt_index=attempt_index,
                                    candidate_index=candidate_index,
                                ),
                                dataset=dataset,
                                frozen_target_case_ids=frozen_target_case_ids,
                            )
                            primary_accepted = bool(gate["accepted"])
                            gate.update(
                                {
                                    "epoch": epoch,
                                    "batch_index": batch_index,
                                    "batch_attempt_index": attempt_index,
                                    "repair_round_index": current_repair_round,
                                    "analysis_round_index": analysis_round_index,
                                    "improvement_cohort_id": cohort_id,
                                    "cohort_manifest_path": cohort_manifest_path,
                                    "candidate_id": candidate_id,
                                    "candidate_index": candidate_index,
                                    "candidate_fingerprint": str(proposal.get("candidate_fingerprint", "") or ""),
                                    "predicted_score": _number(proposal.get("predicted_score")),
                                    "predicted_rank": int(proposal.get("predicted_rank", 0) or 0),
                                    "ranking_policy": str(proposal.get("ranking_policy", "") or ""),
                                    "ranking_features": (
                                        dict(proposal.get("ranking_features", {}))
                                        if isinstance(proposal.get("ranking_features"), dict)
                                        else {}
                                    ),
                                    "improver_version_id": str(proposal.get("improver_version_id", "") or ""),
                                    "improver_policy_digest": str(proposal.get("improver_policy_digest", "") or ""),
                                    "rank_frozen": bool(proposal.get("rank_frozen")),
                                    "rank_frozen_before_evaluation": True,
                                    "source_issue_id": issue_id or "",
                                    "source_issue_signature": issue_signature,
                                    "member_optimization_ref_path": str(
                                        proposal.get("member_optimization_ref_path", "") or ""
                                    ),
                                    "candidate_generation_error": (
                                        dict(proposal.get("generation_error", {}))
                                        if isinstance(proposal.get("generation_error"), dict)
                                        else {}
                                    ),
                                    "composition_mode": str(proposal.get("composition_mode", "") or ""),
                                    "primary_gate_accepted": primary_accepted,
                                    "primary_gate_reason": str(gate.get("reason", "")),
                                    "qualified_for_promotion": primary_accepted,
                                    "selected_for_promotion": False,
                                    "evaluation_input_mode": "original_task",
                                }
                            )
                            sibling_gates.append(gate)

                        selection_top_m = min(
                            int(self.improver_policy.budget_policy["top_m"]),
                            len(sibling_gates),
                        )
                        for gate in sibling_gates:
                            within_budget = int(gate.get("predicted_rank", 0) or 0) <= selection_top_m
                            gate["selection_top_m"] = selection_top_m
                            gate["within_selection_budget"] = within_budget
                            gate["selection_budget_role"] = "counterfactual_metric_only"
                            gate["qualified_for_promotion"] = bool(gate.get("primary_gate_accepted"))
                        winner = _select_sibling_winner(sibling_gates)
                        winner_id = str((winner or {}).get("candidate_id", "") or "")
                        winner_attempt_record: dict[str, Any] | None = None
                        for gate in sibling_gates:
                            selected = bool(winner_id) and gate.get("candidate_id") == winner_id
                            gate["selected_for_promotion"] = selected
                            if selected:
                                gate["status"] = "provisional"
                                gate["reason"] = "candidate_passed_batch_gate_pending_epoch_checkpoint"
                            elif gate.get("primary_gate_accepted"):
                                gate["accepted"] = False
                                gate["status"] = "superseded"
                                gate["reason"] = "qualified_sibling_not_selected"
                            member_ref = str(gate.get("member_optimization_ref_path", "") or "")
                            candidate_refs = str(gate.get("candidate_harness_refs_path", "") or "")
                            _persist_promotion(
                                member_ref,
                                (candidate_refs if candidate_refs != before_attempt_refs else ""),
                                gate,
                            )
                            state["candidate_gates"].append(gate)
                            attempt_record = {
                                "attempt_index": attempt_index,
                                "candidate_index": int(gate.get("candidate_index", 0) or 0),
                                "candidate_id": str(gate.get("candidate_id", "") or ""),
                                "improvement_cohort_id": cohort_id,
                                "analysis_round_index": analysis_round_index,
                                "source_issue_id": issue_id or "",
                                "source_issue_signature": issue_signature,
                                "source_eval_ref_path": attempt_source_eval_ref,
                                "analysis_ref_path": analysis_ref,
                                "optimization_hypotheses_path": hypotheses_ref,
                                "member_optimization_ref_path": member_ref,
                                "before_harness_refs_path": before_attempt_refs,
                                "after_harness_refs_path": candidate_refs if selected else before_attempt_refs,
                                "candidate_gate_status": gate["status"],
                                "candidate_gate_reason": gate["reason"],
                                "source_native_target_score": gate.get("source_native_target_score"),
                                "candidate_native_target_score": gate.get("candidate_native_target_score"),
                                "native_target_score_delta": gate.get("native_target_score_delta"),
                                "native_dimension_delta": gate.get("native_dimension_delta"),
                                "accepted_target_case_ids": (list(gate.get("target_case_ids", [])) if selected else []),
                                "completed_target_case_ids": [],
                                "residual_case_ids": [],
                                "residual_eval_ref_path": "",
                            }
                            attempt_records.append(attempt_record)
                            if selected:
                                winner_attempt_record = attempt_record

                        cohort_feedback = build_candidate_feedback_cohort(
                            cohort={
                                "cohort_id": cohort_id,
                                "parent_harness_ref": before_attempt_refs,
                                "source_eval_ref": attempt_source_eval_ref,
                                "analysis_ref_path": analysis_ref,
                                "optimization_hypotheses_path": hypotheses_ref,
                                "source_issue_id": issue_id or "",
                                "source_issue_signature": issue_signature,
                                "target_case_ids": sorted(frozen_target_case_ids),
                                "evaluation_protocol": "single_harness_target_local_v1",
                                "requested_candidate_count": int(self.config.member_optimizer.sibling_candidate_count),
                                "rank_frozen": True,
                                "ranking_policy": self.improver_policy.ranking_policy,
                                "improver_version_id": self.improver_policy.version_id,
                                "improver_policy_digest": self.improver_policy.canonical_digest,
                                "cohort_manifest_path": cohort_manifest_path,
                            },
                            candidates=sibling_gates,
                            selected_candidate_id=winner_id,
                            top_m=selection_top_m,
                        )
                        _attach_native_signal_feedback(cohort_feedback, sibling_gates)
                        state["improvement_instances"][cohort_id] = cohort_feedback
                        _write_candidate_feedback_ledger(state, output_dir)
                        _refresh_optimization_experience(state, output_dir)
                        last_gate = winner or _best_realized_sibling_gate(sibling_gates)
                        last_member_ref = str((last_gate or {}).get("member_optimization_ref_path", "") or "")
                        if winner is None:
                            repair_parent = _best_realized_sibling_gate(sibling_gates)
                            repair_analysis_ref = str(
                                (repair_parent or {}).get("candidate_failure_analysis_ref_path", "") or ""
                            )
                            repair_eval_ref = str((repair_parent or {}).get("candidate_eval_ref_path", "") or "")
                            repair_refs = str((repair_parent or {}).get("candidate_harness_refs_path", "") or "")
                            if repair_analysis_ref and repair_eval_ref and repair_refs:
                                residual_case_ids = _nonpassing_case_ids(repair_eval_ref)
                                repair_active_cases = _cases_with_ids(active_cases, residual_case_ids)
                                if repair_active_cases and current_repair_round < max_repair_rounds:
                                    deferred_repairs.append(
                                        {
                                            "gate": repair_parent,
                                            "harness_refs_path": repair_refs,
                                            "eval_ref_path": repair_eval_ref,
                                            "analysis_ref_path": repair_analysis_ref,
                                            "active_cases": repair_active_cases,
                                            "resume_context": {
                                                "harness_refs_path": before_attempt_refs,
                                                "eval_ref_path": attempt_source_eval_ref,
                                                "analysis_ref_path": analysis_ref,
                                                "active_cases": list(active_cases),
                                                "repair_round_index": current_repair_round,
                                            },
                                        }
                                    )
                                    remaining_sibling_count = len(issue_queue) - issue_queue_index - 1
                                    remaining_attempt_budget = (
                                        max_issue_attempts - issue_attempt_count if max_issue_attempts else None
                                    )
                                    # Prefer immediate evidence-led repair when
                                    # budget permits, but never let one semantic
                                    # hypothesis consume the slots required to
                                    # try already-queued alternatives.
                                    if not _must_preserve_budget_for_siblings(
                                        remaining_attempt_budget=remaining_attempt_budget,
                                        remaining_sibling_count=remaining_sibling_count,
                                    ):
                                        break
                                    continue
                                elif repair_active_cases:
                                    repair_budget_exhausted = True
                            continue

                        current_refs = str(winner.get("candidate_harness_refs_path", "") or before_attempt_refs)
                        accepted_target_case_ids.update(
                            str(case_id) for case_id in winner.get("target_case_ids", []) if str(case_id)
                        )

                        residual_eval_ref = await self._evaluate(
                            cases=active_cases,
                            harness_refs_path=current_refs,
                            output_dir=attempt_dir / "residual_source",
                            dataset=dataset,
                        )
                        residual_eval_refs.append(residual_eval_ref)
                        residual_scores = _eval_case_scores(residual_eval_ref)
                        completed_case_ids = {case_id for case_id, score in residual_scores.items() if score >= 1.0}
                        _sync_retained_case_ids(working_retained_case_ids, residual_scores)
                        residual_case_ids = _nonpassing_case_ids(residual_eval_ref)
                        if winner_attempt_record is not None:
                            winner_attempt_record.update(
                                {
                                    "completed_target_case_ids": sorted(
                                        set(winner.get("target_case_ids", [])) & completed_case_ids
                                    ),
                                    "residual_case_ids": sorted(residual_case_ids),
                                    "residual_eval_ref_path": residual_eval_ref,
                                }
                            )
                        active_cases = _cases_with_ids(active_cases, residual_case_ids)
                        attempt_source_eval_ref = residual_eval_ref
                        current_repair_round = 1
                        repair_resume_contexts.clear()
                        refresh_residual_analysis = True
                        if not active_cases:
                            repair_stop_reason = "all_batch_cases_completed"
                        break

                    if not active_cases:
                        break
                    if refresh_residual_analysis:
                        continue
                    if deferred_repairs:
                        selected_repair = max(
                            deferred_repairs,
                            key=lambda item: _sibling_realized_sort_key(item["gate"]),
                        )
                        selected_repair["gate"]["selected_as_repair_parent"] = True
                        repair_resume_contexts.append(dict(selected_repair["resume_context"]))
                        current_refs = str(selected_repair["harness_refs_path"])
                        attempt_source_eval_ref = str(selected_repair["eval_ref_path"])
                        pending_analysis_ref = str(selected_repair["analysis_ref_path"])
                        active_cases = list(selected_repair["resume_context"]["active_cases"])
                        current_repair_round += 1
                        continue
                    if issue_budget_exhausted:
                        repair_stop_reason = "issue_attempt_limit_reached"
                        break
                    if repair_budget_exhausted:
                        if repair_resume_contexts:
                            resume = repair_resume_contexts.pop()
                            current_refs = str(resume["harness_refs_path"])
                            attempt_source_eval_ref = str(resume["eval_ref_path"])
                            pending_analysis_ref = str(resume["analysis_ref_path"])
                            active_cases = list(resume["active_cases"])
                            current_repair_round = int(resume["repair_round_index"])
                            continue
                        repair_stop_reason = "repair_round_limit_reached"
                        break
                    repair_stop_reason = (
                        "repeated_issue_detected" if repeated_issue_ids else "issue_queue_exhausted_without_acceptance"
                    )
                    break

                if not active_cases and repair_stop_reason != "all_batch_cases_completed":
                    repair_stop_reason = "all_batch_cases_completed"

                batch_accepted = bool(accepted_target_case_ids)
                if not batch_accepted:
                    current_refs = batch_before_refs
                batch_gate_status = (
                    "provisional" if batch_accepted else str((last_gate or {}).get("status", "not_generated"))
                )
                batch_gate_reason = (
                    "candidate_passed_batch_gate_pending_epoch_checkpoint"
                    if batch_accepted
                    else str((last_gate or {}).get("reason", repair_stop_reason))
                )
                state["working_harness_refs_path"] = current_refs
                state["completed_batches"][batch_key] = {
                    "epoch": epoch,
                    "batch_index": batch_index,
                    "source_eval_ref_path": source_eval_ref,
                    "analysis_ref_path": analysis_ref,
                    "optimization_hypotheses_path": hypotheses_ref,
                    "analysis_ref_paths": analysis_refs,
                    "optimization_hypotheses_paths": hypotheses_refs,
                    "residual_eval_ref_paths": residual_eval_refs,
                    "repair_round_count": current_repair_round if attempt_index else 0,
                    "candidate_cohort_count": attempt_index,
                    "issue_attempt_count": issue_attempt_count,
                    "max_issue_attempts": max_issue_attempts,
                    "max_repair_rounds": max_repair_rounds,
                    "repair_stop_reason": repair_stop_reason,
                    "last_analysis_issue_count": last_analysis_issue_count,
                    "last_optimization_hypothesis_count": last_optimization_hypothesis_count,
                    "residual_case_ids": sorted(
                        str(case.get("case_id", "") or "")
                        for case in active_cases
                        if str(case.get("case_id", "") or "")
                    ),
                    "member_optimization_ref_path": last_member_ref,
                    "candidate_attempts": attempt_records,
                    "improvement_cohort_ids": list(
                        dict.fromkeys(
                            str(item.get("improvement_cohort_id", "") or "")
                            for item in attempt_records
                            if str(item.get("improvement_cohort_id", "") or "")
                        )
                    ),
                    "before_harness_refs_path": batch_before_refs,
                    "after_harness_refs_path": current_refs,
                    "candidate_gate_status": batch_gate_status,
                    "candidate_gate_reason": batch_gate_reason,
                    "accepted_target_case_ids": sorted(accepted_target_case_ids),
                    "retained_case_ids_after_batch": sorted(working_retained_case_ids),
                }
                _write_yaml_atomic(state_path, state)

            full_eval_ref = await self._evaluate(
                cases=all_cases,
                harness_refs_path=current_refs,
                output_dir=output_dir / "evaluations" / f"e{epoch:03d}" / "full",
                dataset=dataset,
            )
            full_score = _eval_score(full_eval_ref)
            epoch_provisional_gates = []
            provisional_target_case_ids: set[str] = set()
            for gate in state["candidate_gates"]:
                is_current_epoch = int(gate.get("epoch", 0) or 0) == epoch
                if not is_current_epoch or gate.get("status") != "provisional":
                    continue
                epoch_provisional_gates.append(gate)
                for case_id in gate.get("target_case_ids", []):
                    if str(case_id):
                        provisional_target_case_ids.add(str(case_id))
            checkpoint = {
                "epoch": epoch,
                "score": full_score,
                "eval_ref_path": full_eval_ref,
                "harness_refs_path": current_refs,
                "evaluation_input_mode": "original_task",
            }
            best_score = _number(state.get("best_score"))
            full_case_scores = _eval_case_scores(full_eval_ref)
            full_failed_case_ids = sorted(case_id for case_id, score in full_case_scores.items() if score < 1.0)
            previous_best_eval_ref = str(state.get("best_eval_ref_path", "") or "")
            epoch_source_eval_ref = _epoch_full_source_eval_ref(
                state,
                epoch=epoch,
                expected_case_ids=all_case_ids,
            )
            comparison_eval_ref = previous_best_eval_ref or epoch_source_eval_ref
            previous_best_case_scores = _eval_case_scores(comparison_eval_ref) if comparison_eval_ref else {}
            current_official_metrics = _eval_official_metrics(full_eval_ref)
            previous_official_metrics = _eval_official_metrics(comparison_eval_ref) if comparison_eval_ref else {}
            metric_name_changed = bool(
                previous_official_metrics
                and current_official_metrics
                and previous_official_metrics.get("primary_metric") != current_official_metrics.get("primary_metric")
            )
            official_score_regressed = bool(
                previous_official_metrics
                and current_official_metrics
                and (_number(current_official_metrics.get("primary_score")) or 0.0)
                < (_number(previous_official_metrics.get("primary_score")) or 0.0)
            )
            new_infra_failures = bool(
                current_official_metrics
                and int(current_official_metrics.get("infra_failures", 0) or 0)
                > int(previous_official_metrics.get("infra_failures", 0) or 0)
            )
            new_policy_violations = bool(
                current_official_metrics
                and int(current_official_metrics.get("policy_violations", 0) or 0)
                > int(previous_official_metrics.get("policy_violations", 0) or 0)
            )
            opaque_snapshot_epoch = any(
                str(gate.get("composition_mode", "") or "") == "opaque_snapshot" for gate in epoch_provisional_gates
            )
            protected_case_ids = set(state.get("retained_case_ids", []))
            if opaque_snapshot_epoch and not protected_case_ids:
                protected_case_ids = {case_id for case_id, score in previous_best_case_scores.items() if score >= 1.0}
            regressed_best_case_ids = []
            for case_id, previous_score in previous_best_case_scores.items():
                if case_id in protected_case_ids and full_case_scores.get(case_id, 0.0) < previous_score:
                    regressed_best_case_ids.append(case_id)
            regressed_best_case_ids.sort()
            failed_retention_case_ids = sorted(
                case_id for case_id in working_retained_case_ids if full_case_scores.get(case_id, 0.0) < 1.0
            )
            failed_target_case_ids = sorted(
                case_id for case_id in provisional_target_case_ids if full_case_scores.get(case_id, 0.0) < 1.0
            )
            full_passing_case_ids = {case_id for case_id, score in full_case_scores.items() if score >= 1.0}
            full_failed_machine_evidence = _failed_machine_evidence(full_eval_ref)
            full_error_case_ids = _error_case_ids(full_eval_ref)
            gate_selections = []
            for gate in epoch_provisional_gates:
                gate_selections.append(
                    _select_gate_from_epoch_checkpoint(
                        gate,
                        full_eval_ref=full_eval_ref,
                        error_case_ids=full_error_case_ids,
                        machine_evidence_case_ids=_machine_evidence_case_ids(full_failed_machine_evidence),
                    )
                )
            checkpoint_blocked = bool(
                metric_name_changed
                or official_score_regressed
                or new_infra_failures
                or new_policy_violations
                or regressed_best_case_ids
            )
            if checkpoint_blocked:
                for selection in gate_selections:
                    selection.update(
                        {
                            "retained": False,
                            "reason": "epoch_full_checkpoint_regressed_or_inconclusive",
                            "failure_class": "regression_or_retention_failure",
                        }
                    )
            opaque_partial_selection = _reject_mixed_opaque_snapshot_selection(
                epoch_provisional_gates,
                gate_selections,
            )
            if opaque_partial_selection:
                checkpoint_blocked = True
            retained_gates = []
            removed_gates = []
            for gate, selection in zip(epoch_provisional_gates, gate_selections, strict=True):
                destination = retained_gates if selection["retained"] else removed_gates
                destination.append(gate)
            selected_refs = current_refs
            checkpoint_status = (
                "verified_with_inconclusive_cases"
                if full_error_case_ids or full_failed_machine_evidence
                else "verified"
            )
            if retained_gates and removed_gates and not opaque_snapshot_epoch:
                selected_refs = _materialize_checkpoint_filtered_harness(
                    output_dir=output_dir,
                    epoch=epoch,
                    base_harness_refs_path=epoch_start_refs,
                    replayed_harness_refs_path=current_refs,
                    retained_gates=retained_gates,
                    removed_gates=removed_gates,
                    full_eval_ref_path=full_eval_ref,
                )
                checkpoint_status = "filtered"
            elif retained_gates and failed_retention_case_ids:
                checkpoint_status = "verified_with_unrelated_failures"
            elif not retained_gates and epoch_provisional_gates:
                checkpoint_status = "rejected"
            elif failed_retention_case_ids:
                checkpoint_status = "rejected"

            checkpoint.update(
                {
                    "status": checkpoint_status,
                    "previous_best_score": best_score,
                    "previous_best_eval_ref_path": previous_best_eval_ref,
                    "comparison_eval_ref_path": comparison_eval_ref,
                    "regressed_best_case_ids": regressed_best_case_ids,
                    "failed_retention_case_ids": failed_retention_case_ids,
                    "failed_case_ids": full_failed_case_ids,
                    "failed_target_case_ids": failed_target_case_ids,
                    "failed_machine_evidence": full_failed_machine_evidence,
                    "error_case_ids": sorted(full_error_case_ids),
                    "official_metrics": current_official_metrics,
                    "previous_official_metrics": previous_official_metrics,
                    "metric_name_changed": metric_name_changed,
                    "official_score_regressed": official_score_regressed,
                    "new_infra_failures": new_infra_failures,
                    "new_policy_violations": new_policy_violations,
                    "opaque_snapshot_epoch": opaque_snapshot_epoch,
                    "opaque_partial_selection": opaque_partial_selection,
                    "retained_candidate_action_ids": _capability_action_ids(retained_gates),
                    "removed_candidate_action_ids": _capability_action_ids(removed_gates),
                    "selected_harness_refs_path": (selected_refs if retained_gates else epoch_start_refs),
                    "post_checkpoint_replay_performed": False,
                }
            )
            state["epoch_checkpoints"].append(checkpoint)
            promotion_applied = bool(not checkpoint_blocked and retained_gates)
            noop_initial_score_seed = bool(
                state.get("best_score") is None
                and not epoch_provisional_gates
                and current_refs == epoch_start_refs
                and checkpoint_status == "verified"
            )
            checkpoint["promotion_applied"] = promotion_applied
            checkpoint["noop_initial_score_seed"] = noop_initial_score_seed
            if promotion_applied or noop_initial_score_seed:
                current_refs = selected_refs
                state["best_score"] = full_score
                state["best_eval_ref_path"] = full_eval_ref
                state["best_harness_refs_path"] = current_refs
                state["retained_case_ids"] = sorted(full_passing_case_ids)
                if noop_initial_score_seed:
                    state["baseline_score"] = full_score
                    state["baseline_eval_ref_path"] = full_eval_ref
            else:
                current_refs = epoch_start_refs
                state["best_score"] = epoch_start_score
                state["best_harness_refs_path"] = epoch_start_refs
                state["retained_case_ids"] = sorted(epoch_start_retained_case_ids)
            for gate, selection in zip(
                epoch_provisional_gates,
                gate_selections,
                strict=True,
            ):
                retained = bool(selection["retained"])
                gate["accepted"] = retained
                gate["status"] = "accepted" if retained else "rejected"
                gate["reason"] = str(selection["reason"])
                gate["failure_class"] = str(selection.get("failure_class", ""))
                gate["epoch_checkpoint_outcome"] = {
                    **selection,
                    "status": "retained" if retained else "removed",
                    "eval_ref_path": full_eval_ref,
                    "selected_harness_refs_path": (selected_refs if retained else ""),
                    "post_checkpoint_replay_performed": False,
                }
                _persist_promotion(
                    str(gate.get("member_optimization_ref_path", "")),
                    str(gate.get("candidate_harness_refs_path", "")),
                    gate,
                )
                completed = state["completed_batches"].get(f"epoch_{epoch:03d}:batch_{int(gate['batch_index']):03d}")
                if isinstance(completed, dict):
                    completed["candidate_gate_status"] = gate["status"]
                    completed["candidate_gate_reason"] = gate["reason"]
                    _update_batch_attempt_record(completed, gate)
            state["current_harness_refs_path"] = str(state["best_harness_refs_path"])
            state["working_harness_refs_path"] = str(state["best_harness_refs_path"])
            _refresh_optimization_experience(state, output_dir)
            _write_yaml_atomic(state_path, state)

        _refresh_optimization_experience(state, output_dir)
        _ensure_final_publication(state=state, output_dir=output_dir)
        state["status"] = "completed"
        _write_yaml_atomic(state_path, state)
        _write_yaml_atomic(report_path, _build_report(state, dataset))
        return _result_from_state(state, state_path, report_path)

    async def _evaluate(
        self,
        *,
        cases: list[dict[str, Any]],
        harness_refs_path: str,
        output_dir: Path,
        dataset: DatasetArtifact,
    ) -> str:
        existing = output_dir / "eval_ref.yaml"
        if _eval_ref_complete(existing):
            return str(existing)
        return await self.evaluator.evaluate_batch(
            cases=cases,
            team_skill_ref_path="",
            harness_refs_path=harness_refs_path,
            output_dir=str(output_dir),
            dataset=dataset,
        )

    async def _analyze(
        self,
        *,
        eval_ref_path: str,
        harness_refs_path: str,
        output_dir: Path,
        source_stage: str = "single_harness_batch",
        prior_candidate_feedback: dict[str, Any] | None = None,
    ) -> str:
        existing = output_dir / "analysis_ref.yaml"
        if existing.is_file():
            return str(existing)
        return await self.analyzer.analyze(
            EvaluationResultAnalysisInvocation(
                eval_ref_path=eval_ref_path,
                case_results_dir=str(Path(eval_ref_path).parent / "cases"),
                case_traces_dir=str(Path(eval_ref_path).parent / "cases"),
                team_skill_ref_path="",
                harness_refs_path=harness_refs_path,
                output_dir=str(output_dir),
                source_stage=source_stage,
                prior_candidate_feedback=dict(prior_candidate_feedback or {}),
            )
        )

    async def _generate_sibling_candidate_proposals(
        self,
        *,
        cohort_id: str,
        source_eval_ref: str,
        analysis_ref: str,
        parent_harness_refs_path: str,
        optimization_hypotheses_path: str,
        source_issue_id: str | None,
        source_issue_signature: str,
        frozen_target_case_ids: set[str],
        frozen_experience: dict[str, Any],
        rejected_capabilities: list[dict[str, Any]],
        output_dir: Path,
    ) -> tuple[list[dict[str, Any]], str]:
        """Materialize and freeze every sibling before any task evaluation."""
        requested_count = int(self.config.member_optimizer.sibling_candidate_count)
        cohort_dir = output_dir / "member_optimizations" / "sibling_cohorts" / cohort_id
        manifest_path = cohort_dir / "cohort_manifest.yaml"
        existing = _read_yaml(manifest_path) if manifest_path.is_file() else {}
        expected_identity = {
            "parent_harness_refs_path": parent_harness_refs_path,
            "source_eval_ref_path": source_eval_ref,
            "analysis_ref_path": analysis_ref,
            "optimization_hypotheses_path": optimization_hypotheses_path,
            "source_issue_id": source_issue_id or "",
            "source_issue_signature": source_issue_signature,
            "frozen_target_case_ids": sorted(frozen_target_case_ids),
            "requested_candidate_count": requested_count,
            "improver_version_id": self.improver_policy.version_id,
            "improver_policy_digest": self.improver_policy.canonical_digest,
        }
        if existing and any(existing.get(key) != value for key, value in expected_identity.items()):
            raise RuntimeError(f"sibling cohort manifest identity changed during resume: {manifest_path}")
        existing_candidates = {
            str(item.get("candidate_id", "") or ""): dict(item)
            for item in existing.get("candidates", [])
            if isinstance(item, dict) and str(item.get("candidate_id", "") or "")
        }
        proposals: list[dict[str, Any]] = []
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "status": "generating",
            "cohort_id": cohort_id,
            **expected_identity,
            "rank_frozen": False,
            "candidates": [],
        }
        for candidate_index in range(1, requested_count + 1):
            candidate_id = f"{cohort_id}_c{candidate_index:03d}"
            cached = existing_candidates.get(candidate_id, {})
            cached_member_ref = str(cached.get("member_optimization_ref_path", "") or "")
            cached_candidate_refs = str(cached.get("candidate_harness_refs_path", "") or "")
            if Path(cached_member_ref).is_file() and Path(cached_candidate_refs).is_file():
                proposal = cached
            else:
                sibling_generation = {
                    "cohort_id": cohort_id,
                    "candidate_id": candidate_id,
                    "generation_index": candidate_index,
                    "candidate_index": candidate_index,
                    "candidate_count": requested_count,
                    "prior_proposals": [_candidate_proposal_summary(item) for item in proposals],
                    "outcomes_available": False,
                }
                candidate_experience = {
                    "journal": list(frozen_experience.get("journal", [])),
                    "lever_scoreboard": dict(frozen_experience.get("lever_scoreboard", {})),
                    "frozen_target_case_ids": sorted(frozen_target_case_ids),
                }
                if requested_count > 1:
                    candidate_experience["sibling_generation"] = sibling_generation
                if self._explicit_improver_policy:
                    policy_payload = self.improver_policy.to_dict()
                    candidate_experience["improver_policy"] = {
                        "version_id": self.improver_policy.version_id,
                        "policy_digest": self.improver_policy.canonical_digest,
                        "generation_directives": policy_payload["generation_directives"],
                        "budget_policy": policy_payload["budget_policy"],
                    }
                try:
                    member_ref = await self.member_optimizer.optimize(
                        eval_ref_path=source_eval_ref,
                        analysis_result_path=analysis_ref,
                        harness_refs_path=parent_harness_refs_path,
                        output_dir=str(cohort_dir / f"c{candidate_index:03d}"),
                        defer_publish=True,
                        rejected_capabilities=list(rejected_capabilities),
                        single_harness=True,
                        optimization_hypotheses_path=optimization_hypotheses_path,
                        optimization_issue_ids=([source_issue_id] if source_issue_id else None),
                        optimization_experience=candidate_experience,
                    )
                except Exception as exc:  # noqa: BLE001 - isolate one candidate, preserve the benchmark run
                    proposal = {
                        "candidate_id": candidate_id,
                        "candidate_index": candidate_index,
                        "generation_order": candidate_index,
                        "member_optimization_ref_path": "",
                        "candidate_harness_refs_path": parent_harness_refs_path,
                        "member_status": "generation_error",
                        "composition_mode": "",
                        "plan_path": "",
                        "plan_sha256": "",
                        "capabilities": [],
                        "causal_intervention_contracts": [],
                        "candidate_fingerprint": "",
                        "generation_error": _safe_candidate_error(exc),
                    }
                else:
                    member_info = _read_yaml(member_ref)
                    candidate_refs = str(member_info.get("optimized_harness_refs_path", "") or parent_harness_refs_path)
                    capabilities = _candidate_capabilities(member_info)
                    plan_path = str(member_info.get("plan_path", "") or "")
                    proposal = {
                        "candidate_id": candidate_id,
                        "candidate_index": candidate_index,
                        "generation_order": candidate_index,
                        "member_optimization_ref_path": member_ref,
                        "candidate_harness_refs_path": candidate_refs,
                        "member_status": str(member_info.get("status", "") or ""),
                        "composition_mode": _member_composition_mode(member_info),
                        "plan_path": plan_path,
                        "plan_sha256": _file_sha256(plan_path) if Path(plan_path).is_file() else "",
                        "capabilities": capabilities,
                        "causal_intervention_contracts": _causal_intervention_contracts(capabilities),
                        "candidate_fingerprint": canonical_candidate_fingerprint(capabilities),
                    }
            proposals.append(proposal)
            manifest["candidates"] = proposals
            _write_yaml_atomic(manifest_path, manifest)

        ranked = rank_candidate_proposals(
            proposals,
            frozen_target_case_ids=set(frozen_target_case_ids),
            improver_policy=self.improver_policy,
        )
        manifest.update(
            {
                "status": "frozen",
                "rank_frozen": True,
                "ranking_policy": self.improver_policy.ranking_policy,
                "improver_version_id": self.improver_policy.version_id,
                "improver_policy_digest": self.improver_policy.canonical_digest,
                "generated_candidate_count": len(ranked),
                "candidates": ranked,
            }
        )
        _write_yaml_atomic(manifest_path, manifest)
        return ranked, str(manifest_path)

    async def _candidate_gate(
        self,
        *,
        cases: list[dict[str, Any]],
        source_eval_ref: str,
        analysis_ref: str = "",
        before_harness_refs_path: str,
        candidate_harness_refs_path: str,
        member_status: str,
        capabilities: list[dict[str, Any]],
        causal_intervention_contracts: list[dict[str, Any]] | None = None,
        output_dir: Path,
        dataset: DatasetArtifact,
        frozen_target_case_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        causal_intervention_contracts = [
            dict(item) for item in (causal_intervention_contracts or []) if isinstance(item, dict)
        ] or _causal_intervention_contracts(capabilities)
        original_source_score = _eval_score(source_eval_ref)
        base = {
            "source_eval_ref_path": source_eval_ref,
            "paired_source_eval_ref_path": source_eval_ref,
            "candidate_eval_ref_path": "",
            "before_harness_refs_path": before_harness_refs_path,
            "candidate_harness_refs_path": candidate_harness_refs_path,
            "original_source_score": original_source_score,
            "source_score": original_source_score,
            "candidate_score": None,
            "score_delta": None,
            "capabilities": capabilities,
        }
        if member_status not in {"success", "partial_success"}:
            return {
                "accepted": False,
                "status": "rejected",
                "reason": f"member_optimization_status_{member_status or 'unknown'}",
                **base,
            }
        if candidate_harness_refs_path == before_harness_refs_path:
            return {
                "accepted": False,
                "status": "rejected",
                "reason": "member_optimizer_produced_no_candidate",
                **base,
            }
        fallback_target_case_ids = _nonpassing_case_ids(source_eval_ref)
        if not fallback_target_case_ids:
            fallback_target_case_ids = {
                str(case.get("case_id", "") or "") for case in cases if str(case.get("case_id", "") or "")
            }
        batch_case_ids = {str(case.get("case_id", "") or "") for case in cases if str(case.get("case_id", "") or "")}
        target_case_ids = (
            set(frozen_target_case_ids) & batch_case_ids
            if frozen_target_case_ids is not None
            else _capability_target_case_ids(
                capabilities,
                fallback_target_case_ids=fallback_target_case_ids,
            )
            & batch_case_ids
        )
        if not target_case_ids:
            target_case_ids = set(fallback_target_case_ids) & batch_case_ids
        inconclusive_source_target_case_ids = sorted(_error_case_ids(source_eval_ref) & target_case_ids)
        if inconclusive_source_target_case_ids:
            return {
                "accepted": False,
                "status": "inconclusive",
                "reason": "source_gate_inconclusive_due_to_error_cases",
                **base,
                "target_case_ids": sorted(target_case_ids),
                "inconclusive_source_target_case_ids": inconclusive_source_target_case_ids,
            }
        non_target_case_ids = batch_case_ids - target_case_ids
        _, task_acceptance_contracts = _bind_task_acceptance_contracts(
            cases,
            analysis_ref=analysis_ref,
            target_case_ids=target_case_ids,
        )
        # A candidate owns only the cases attributed to its action. Evaluating
        # unrelated batch cases here couples acceptance to independent solver
        # samples and makes later candidates progressively harder to accept.
        candidate_cases = [dict(case) for case in cases if str(case.get("case_id", "") or "") in target_case_ids]
        paired_source_eval_ref = source_eval_ref
        source_case_scores = _eval_case_scores(paired_source_eval_ref)
        source_target_score = _average_case_scores(source_case_scores, target_case_ids)
        try:
            candidate_eval_ref = await self._evaluate(
                cases=candidate_cases,
                harness_refs_path=candidate_harness_refs_path,
                output_dir=output_dir,
                dataset=dataset,
            )
        except Exception as exc:  # noqa: BLE001 - one candidate must not abort the benchmark
            return {
                "accepted": False,
                "status": "inconclusive",
                "reason": "candidate_evaluation_failed",
                **base,
                "target_case_ids": sorted(target_case_ids),
                "candidate_evaluation_error": _safe_candidate_error(exc),
            }
        skipped_target_case_ids = sorted(_skipped_case_ids(candidate_eval_ref) & target_case_ids)
        if skipped_target_case_ids:
            return {
                "accepted": False,
                "status": "inconclusive",
                "reason": "candidate_gate_inconclusive_due_to_infrastructure_skip",
                **base,
                "candidate_eval_ref_path": candidate_eval_ref,
                "skipped_target_case_ids": skipped_target_case_ids,
            }
        candidate_case_scores = _eval_case_scores(candidate_eval_ref)
        source_native_signals = _eval_case_native_signals(paired_source_eval_ref)
        candidate_native_signals = _eval_case_native_signals(candidate_eval_ref)
        source_native_case_scores = _native_case_scores(source_native_signals)
        candidate_native_case_scores = _native_case_scores(candidate_native_signals)
        source_native_target_score = _average_case_scores(source_native_case_scores, target_case_ids)
        candidate_native_target_score = _average_case_scores(candidate_native_case_scores, target_case_ids)
        native_target_score_delta = candidate_native_target_score - source_native_target_score
        native_dimension_deltas_by_case, native_dimension_delta = _native_dimension_deltas(
            source_native_signals,
            candidate_native_signals,
            target_case_ids,
        )
        verifier_deltas_by_case = _verifier_deltas_by_case(
            paired_source_eval_ref,
            candidate_eval_ref,
            target_case_ids,
        )
        candidate_patch_excerpts_by_case = _candidate_patch_excerpts_by_case(
            candidate_eval_ref,
            target_case_ids,
        )
        intervention_excerpts = _candidate_intervention_excerpts_by_case(
            capabilities,
            target_case_ids,
        )
        for case_id, excerpt in intervention_excerpts.items():
            if not candidate_patch_excerpts_by_case.get(case_id):
                candidate_patch_excerpts_by_case[case_id] = excerpt
        candidate_target_score = _average_case_scores(candidate_case_scores, target_case_ids)
        target_score_delta = candidate_target_score - source_target_score
        source_score = source_target_score
        candidate_score = candidate_target_score
        score_delta = target_score_delta
        base.update(
            {
                "paired_source_eval_ref_path": paired_source_eval_ref,
                "source_score": source_score,
            }
        )
        source_non_target_score = _average_case_scores(
            source_case_scores,
            non_target_case_ids,
        )
        candidate_non_target_score = None
        non_target_score_delta = None
        expected_tools = _expected_runtime_names(capabilities, action_group="tool")
        invoked_tools_by_case = _invoked_tool_names_by_case(candidate_eval_ref)
        pre_edit_tools_by_case, first_edit_steps_by_case = _pre_edit_invoked_names_by_case(
            candidate_eval_ref, action_group="tool"
        )
        invoked_tool_names: set[str] = set()
        for names in invoked_tools_by_case.values():
            invoked_tool_names.update(names)
        invoked_tools = sorted(invoked_tool_names)
        expected_skills = _expected_runtime_names(capabilities, action_group="skill")
        invoked_skills_by_case = _invoked_skill_names_by_case(candidate_eval_ref)
        pre_edit_skills_by_case, skill_first_edit_steps_by_case = _pre_edit_invoked_names_by_case(
            candidate_eval_ref, action_group="skill"
        )
        for case_id, step in skill_first_edit_steps_by_case.items():
            current = first_edit_steps_by_case.get(case_id)
            if current is None or (step is not None and step < current):
                first_edit_steps_by_case[case_id] = step
        invoked_skill_names: set[str] = set()
        for names in invoked_skills_by_case.values():
            invoked_skill_names.update(names)
        invoked_skills = sorted(invoked_skill_names)
        missing_tool_invocations = _missing_capability_invocations(
            capabilities,
            action_group="tool",
            invoked_names_by_case=pre_edit_tools_by_case,
            fallback_target_case_ids=fallback_target_case_ids,
            names_match=_tool_names_match,
        )
        missing_tools = sorted({item["runtime_name"] for item in missing_tool_invocations})
        missing_skill_invocations = _missing_capability_invocations(
            capabilities,
            action_group="skill",
            invoked_names_by_case=pre_edit_skills_by_case,
            fallback_target_case_ids=fallback_target_case_ids,
            names_match=_skill_names_match,
        )
        missing_skills = sorted({item["runtime_name"] for item in missing_skill_invocations})
        failed_machine_evidence = _failed_machine_evidence(candidate_eval_ref)
        min_target_delta = float(self.config.member_optimizer.candidate_min_target_behavior_delta)
        failing_target_case_ids = sorted(
            case_id for case_id in target_case_ids if source_case_scores.get(case_id, 0.0) < 1.0
        )
        improved_target_case_ids = sorted(
            case_id
            for case_id in failing_target_case_ids
            if candidate_case_scores.get(case_id, 0.0) > source_case_scores.get(case_id, 0.0) + min_target_delta
        )
        unimproved_target_case_ids = sorted(set(failing_target_case_ids) - set(improved_target_case_ids))
        regressed_target_case_ids = sorted(
            case_id
            for case_id in target_case_ids
            if candidate_case_scores.get(case_id, 0.0) < source_case_scores.get(case_id, 0.0)
        )
        partial_progress_target_case_ids = sorted(
            case_id for case_id, delta in verifier_deltas_by_case.items() if bool(delta.get("partial_progress"))
        )
        verifier_progress_target_case_ids = sorted(
            case_id for case_id, delta in verifier_deltas_by_case.items() if delta.get("newly_passed_requirements")
        )
        target_improved = bool(failing_target_case_ids) and (
            not unimproved_target_case_ids and not regressed_target_case_ids
        )
        regressed_non_target_case_ids: list[str] = []
        non_target_confirmation: dict[str, Any] = {
            "status": "not_evaluated",
            "reason": "candidate_gate_is_target_local",
            "case_count": len(non_target_case_ids),
        }
        accepted = (
            not _eval_has_errors(candidate_eval_ref)
            and target_improved
            and not missing_tools
            and not missing_skills
            and not failed_machine_evidence
        )
        target_confirmation: dict[str, Any] = {
            "status": "not_needed",
            "reason": "primary_candidate_evaluation_is_natural",
            "case_count": len(target_case_ids),
            "confirmed": accepted,
            "capability_activation_mode": "natural",
        }

        retention: dict[str, Any] = {
            "status": "not_performed",
            "reason": "cross_case_retention_deferred_to_epoch_checkpoint",
            "case_count": 0,
            "retained": True,
        }
        reason = "candidate_improved_target_cases" if accepted else "candidate_did_not_improve_target_cases"
        if _eval_has_errors(candidate_eval_ref):
            reason = "candidate_gate_inconclusive_due_to_error_cases"
        elif missing_tools:
            if _missing_invocations_were_late(
                missing_tool_invocations,
                invoked_tools_by_case,
                names_match=_tool_names_match,
            ):
                reason = "expected_tool_invoked_after_first_persistent_edit"
            else:
                reason = "expected_tool_not_invoked_on_target_case"
        elif missing_skills:
            if _missing_invocations_were_late(
                missing_skill_invocations,
                invoked_skills_by_case,
                names_match=_skill_names_match,
            ):
                reason = "expected_skill_invoked_after_first_persistent_edit"
            else:
                reason = "expected_skill_not_invoked_on_target_case"
        elif failed_machine_evidence:
            reason = "candidate_machine_evidence_failed"
        elif not target_improved:
            reason = (
                "candidate_made_partial_verifier_progress"
                if partial_progress_target_case_ids
                else "candidate_did_not_improve_target_cases"
            )

        failure_class = _classify_gate_failure(
            accepted=accepted,
            reason=reason,
            target_case_ids=target_case_ids,
            candidate_case_scores=candidate_case_scores,
            first_edit_steps_by_case=first_edit_steps_by_case,
            missing_skill_invocations=missing_skill_invocations,
            verifier_deltas_by_case=verifier_deltas_by_case,
        )
        candidate_failure_analysis_ref = ""
        candidate_failure_diagnoses: dict[str, list[dict[str, Any]]] = {}
        if not accepted and not _eval_has_errors(candidate_eval_ref):
            paired_feedback = {
                "by_case": {
                    case_id: [
                        {
                            "schema_version": 2,
                            "experiment_id": output_dir.name,
                            "kind": "paired_source_candidate_delta",
                            "surface": ",".join(
                                sorted(
                                    {
                                        str(capability.get("action_group", "") or "")
                                        for capability in capabilities
                                        if str(capability.get("action_group", "") or "")
                                    }
                                )
                            ),
                            "status": "rejected",
                            "reason": reason,
                            "selected_for_promotion": False,
                            "verifier_delta": dict(delta),
                            "candidate_patch_excerpt": str(candidate_patch_excerpts_by_case.get(case_id, "")),
                            "source_native_score": _number(source_native_case_scores.get(case_id)),
                            "candidate_native_score": _number(candidate_native_case_scores.get(case_id)),
                            "native_score_delta": _paired_score_delta(
                                source_native_case_scores,
                                candidate_native_case_scores,
                                case_id,
                            ),
                            "native_dimension_deltas": dict(native_dimension_deltas_by_case.get(case_id, {})),
                            "source_native_signal": str(source_native_signals.get(case_id, {}).get("source", "")),
                            "candidate_native_signal": str(candidate_native_signals.get(case_id, {}).get("source", "")),
                            "native_signal_role": "sibling_and_repair_ranking_only",
                            "source_target_score": _number(source_case_scores.get(case_id)),
                            "candidate_target_score": _number(candidate_case_scores.get(case_id)),
                            "target_score_delta": _paired_score_delta(
                                source_case_scores,
                                candidate_case_scores,
                                case_id,
                            ),
                            "activation": _paired_candidate_activation(
                                capabilities,
                                case_id=case_id,
                                pre_edit_tools_by_case=pre_edit_tools_by_case,
                                pre_edit_skills_by_case=pre_edit_skills_by_case,
                            ),
                            "prediction": {
                                "candidate_patch_excerpt": str(candidate_patch_excerpts_by_case.get(case_id, "")),
                                "causal_intervention_contracts": [
                                    dict(contract)
                                    for contract in causal_intervention_contracts
                                    if case_id in set(contract.get("target_case_ids", []))
                                ],
                            },
                            "observed_outcome": {
                                "status": "rejected",
                                "reason": reason,
                                "selected_for_promotion": False,
                                "strict_score": {
                                    "source": _number(source_case_scores.get(case_id)),
                                    "candidate": _number(candidate_case_scores.get(case_id)),
                                    "delta": _paired_score_delta(
                                        source_case_scores,
                                        candidate_case_scores,
                                        case_id,
                                    ),
                                },
                                "continuous_score": {
                                    "source": _number(source_native_case_scores.get(case_id)),
                                    "candidate": _number(candidate_native_case_scores.get(case_id)),
                                    "delta": _paired_score_delta(
                                        source_native_case_scores,
                                        candidate_native_case_scores,
                                        case_id,
                                    ),
                                    "source_signal": str(source_native_signals.get(case_id, {}).get("source", "")),
                                    "candidate_signal": str(
                                        candidate_native_signals.get(case_id, {}).get("source", "")
                                    ),
                                    "role": "sibling_and_repair_ranking_only",
                                },
                                "requirement_delta": dict(delta),
                                "dimension_deltas": dict(native_dimension_deltas_by_case.get(case_id, {})),
                            },
                            "causal_intervention_contracts": [
                                dict(contract)
                                for contract in causal_intervention_contracts
                                if case_id in set(contract.get("target_case_ids", []))
                            ],
                        }
                    ]
                    for case_id, delta in verifier_deltas_by_case.items()
                }
            }
            candidate_failure_analysis_ref = await self._analyze(
                eval_ref_path=candidate_eval_ref,
                harness_refs_path=candidate_harness_refs_path,
                output_dir=output_dir / "failure_analysis",
                source_stage="single_harness_candidate_failure",
                prior_candidate_feedback=paired_feedback,
            )
            candidate_failure_diagnoses = _compact_analysis_diagnoses(candidate_failure_analysis_ref)
            _materialize_candidate_activation_repair(
                candidate_failure_analysis_ref,
                capabilities=capabilities,
                causal_intervention_contracts=causal_intervention_contracts,
                diagnoses_by_case=candidate_failure_diagnoses,
            )
            activation_by_case = {
                case_id: _refine_paired_candidate_activation(
                    _paired_candidate_activation(
                        capabilities,
                        case_id=case_id,
                        pre_edit_tools_by_case=pre_edit_tools_by_case,
                        pre_edit_skills_by_case=pre_edit_skills_by_case,
                    ),
                    candidate_failure_diagnoses.get(case_id, []),
                )
                for case_id in target_case_ids
            }
        else:
            activation_by_case = {
                case_id: _paired_candidate_activation(
                    capabilities,
                    case_id=case_id,
                    pre_edit_tools_by_case=pre_edit_tools_by_case,
                    pre_edit_skills_by_case=pre_edit_skills_by_case,
                )
                for case_id in target_case_ids
            }
        causal_failure_class = ""
        if not accepted and not _eval_has_errors(candidate_eval_ref):
            causal_failure_class = _causal_candidate_failure_classification(activation_by_case)
        return {
            "accepted": accepted,
            "status": (
                "accepted"
                if accepted
                else "inconclusive"
                if "inconclusive" in reason or "error_cases" in reason
                else "rejected"
            ),
            "reason": reason,
            "failure_class": failure_class,
            "causal_failure_class": causal_failure_class,
            **base,
            "candidate_eval_ref_path": candidate_eval_ref,
            "candidate_score": candidate_score,
            "score_delta": score_delta,
            "target_case_ids": sorted(target_case_ids),
            "source_target_score": source_target_score,
            "candidate_target_score": candidate_target_score,
            "target_score_delta": target_score_delta,
            # Evaluator-native scores are diagnostic ranking signals only. The
            # acceptance and epoch-promotion gates above remain on eval-ref
            # strict scores and must not be relaxed by this continuous delta.
            "source_native_target_score": source_native_target_score,
            "candidate_native_target_score": candidate_native_target_score,
            "native_target_score_delta": native_target_score_delta,
            "source_native_case_scores": source_native_case_scores,
            "candidate_native_case_scores": candidate_native_case_scores,
            "source_native_signal_sources_by_case": {
                case_id: str(signal.get("source", "")) for case_id, signal in source_native_signals.items()
            },
            "candidate_native_signal_sources_by_case": {
                case_id: str(signal.get("source", "")) for case_id, signal in candidate_native_signals.items()
            },
            "native_dimension_deltas_by_case": native_dimension_deltas_by_case,
            "native_dimension_delta": native_dimension_delta,
            "native_signal_role": "sibling_and_repair_ranking_only",
            "failing_target_case_ids": failing_target_case_ids,
            "improved_target_case_ids": improved_target_case_ids,
            "unimproved_target_case_ids": unimproved_target_case_ids,
            "regressed_target_case_ids": regressed_target_case_ids,
            "partial_progress_target_case_ids": partial_progress_target_case_ids,
            "verifier_progress_target_case_ids": verifier_progress_target_case_ids,
            "verifier_deltas_by_case": verifier_deltas_by_case,
            "candidate_patch_excerpts_by_case": candidate_patch_excerpts_by_case,
            "candidate_failure_analysis_ref_path": (candidate_failure_analysis_ref),
            "candidate_failure_diagnoses": candidate_failure_diagnoses,
            "activation_by_case": activation_by_case,
            "causal_intervention_contracts": causal_intervention_contracts,
            "task_acceptance_contracts": task_acceptance_contracts,
            "non_target_case_ids": sorted(non_target_case_ids),
            "source_non_target_score": source_non_target_score,
            "candidate_non_target_score": candidate_non_target_score,
            "non_target_score_delta": non_target_score_delta,
            "regressed_non_target_case_ids": regressed_non_target_case_ids,
            "non_target_confirmation": non_target_confirmation,
            "expected_tool_names": expected_tools,
            "invoked_tool_names": invoked_tools,
            "invoked_tool_names_by_case": {case_id: sorted(names) for case_id, names in invoked_tools_by_case.items()},
            "pre_edit_invoked_tool_names_by_case": {
                case_id: sorted(names) for case_id, names in pre_edit_tools_by_case.items()
            },
            "missing_expected_tool_names": missing_tools,
            "missing_expected_tool_invocations": missing_tool_invocations,
            "expected_skill_names": expected_skills,
            "invoked_skill_names": invoked_skills,
            "invoked_skill_names_by_case": {
                case_id: sorted(names) for case_id, names in invoked_skills_by_case.items()
            },
            "pre_edit_invoked_skill_names_by_case": {
                case_id: sorted(names) for case_id, names in pre_edit_skills_by_case.items()
            },
            "first_persistent_edit_step_by_case": first_edit_steps_by_case,
            "missing_expected_skill_names": missing_skills,
            "missing_expected_skill_invocations": missing_skill_invocations,
            "candidate_evaluation_mode": "natural",
            "failed_machine_evidence": failed_machine_evidence,
            "target_confirmation": target_confirmation,
            "retention": retention,
        }


def _dataset_artifact(request: IterativeSingleHarnessRequest) -> DatasetArtifact:
    files = [str(Path(path).expanduser().resolve()) for path in request.dataset_files]
    dirs = {str(Path(path).parent) for path in files}
    if len(dirs) != 1:
        raise ValueError("single-harness dataset files must share one directory")
    return DatasetArtifact(
        dataset_id=request.dataset_id,
        dataset_dir=next(iter(dirs)),
        dataset_files=files,
        cases=len(_load_cases(files)),
    )


def _load_cases(dataset_files: list[str]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for dataset_file in dataset_files:
        path = Path(dataset_file).expanduser().resolve()
        with path.open("r", encoding="utf-8") as file:
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
    case_ids = [str(case.get("case_id", "") or "").strip() for case in cases]
    if any(not case_id for case_id in case_ids):
        raise ValueError("single-harness dataset cases must have non-empty case_id values")
    seen_case_ids: set[str] = set()
    duplicates: set[str] = set()
    for case_id in case_ids:
        if case_id in seen_case_ids:
            duplicates.add(case_id)
        seen_case_ids.add(case_id)
    if duplicates:
        raise ValueError(f"single-harness dataset contains duplicate case ids: {sorted(duplicates)}")
    return cases


def _validate_and_filter_planned_batches(
    batches: list[list[dict[str, Any]]],
    *,
    expected_case_ids: set[str],
) -> list[list[dict[str, Any]]]:
    """Reject missing/duplicate requested cases and ignore neighboring datasets."""
    filtered: list[list[dict[str, Any]]] = []
    seen: set[str] = set()
    for batch in batches:
        selected: list[dict[str, Any]] = []
        for case in batch:
            case_id = str(case.get("case_id", "") or "").strip()
            if case_id not in expected_case_ids:
                continue
            if case_id in seen:
                raise ValueError(f"data loader returned duplicate requested case id: {case_id}")
            seen.add(case_id)
            selected.append(case)
        if selected:
            filtered.append(selected)
    missing = sorted(expected_case_ids - seen)
    if missing:
        raise ValueError(f"data loader omitted requested case ids: {missing}")
    return filtered


def _validate_single_harness_refs(harness_refs_path: str) -> None:
    path = Path(harness_refs_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"harness refs must be a mapping: {path}")
    refs = data.get("harness_refs")
    if isinstance(refs, dict):
        normalized = {str(role): str(ref) for role, ref in refs.items() if str(ref).strip()}
    else:
        normalized = {
            str(role): str(ref)
            for role, ref in data.items()
            if isinstance(ref, str) and role not in {"version", "source_harness_refs_path"}
        }
    if len(normalized) != 1:
        raise ValueError(
            f"single harness optimization requires exactly one harness ref; got {len(normalized)} "
            f"from {harness_refs_path}"
        )


def _request_fingerprint(
    request: IterativeSingleHarnessRequest,
    source_harness_refs_path: str,
    *,
    sibling_candidate_count: int,
    improver_policy_digest: str,
) -> dict[str, Any]:
    return {
        "optimization_chain_version": 18,
        "dataset_files": [str(Path(path).expanduser().resolve()) for path in request.dataset_files],
        "dataset_sha256": [_file_sha256(path) for path in request.dataset_files],
        "source_harness_refs_path": source_harness_refs_path,
        "baseline_eval_ref_path": (
            str(Path(request.baseline_eval_ref_path).expanduser().resolve()) if request.baseline_eval_ref_path else ""
        ),
        "baseline_eval_ref_sha256": (
            _file_sha256(request.baseline_eval_ref_path) if request.baseline_eval_ref_path else ""
        ),
        "auto_full_baseline": bool(request.auto_full_baseline),
        "sibling_candidate_count": sibling_candidate_count,
        "improver_policy_digest": improver_policy_digest,
    }


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().resolve().open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _improvement_cohort_id(
    *,
    epoch: int,
    batch_index: int,
    repair_round_index: int,
    issue_signature: str,
    parent_harness_refs_path: str,
    source_eval_ref_path: str,
    analysis_ref_path: str,
    frozen_target_case_ids: set[str],
    improver_policy_digest: str,
) -> str:
    signature = hashlib.sha256(str(issue_signature).encode("utf-8")).hexdigest()[:8]
    identity = json.dumps(
        {
            "parent_harness_refs_path": parent_harness_refs_path,
            "source_eval_ref_path": source_eval_ref_path,
            "analysis_ref_path": analysis_ref_path,
            "frozen_target_case_ids": sorted(frozen_target_case_ids),
            "improver_policy_digest": improver_policy_digest,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    identity_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    return f"e{epoch:03d}_b{batch_index:03d}_r{repair_round_index:03d}_{signature}_{identity_digest}"


def _candidate_evaluation_output_dir(
    *,
    optimization_output_dir: Path,
    epoch: int,
    batch_index: int,
    attempt_index: int,
    candidate_index: int,
) -> Path:
    """Keep candidate case artifacts below the Windows path-length boundary."""
    return (
        optimization_output_dir.parent
        / "ce"
        / f"e{epoch:03d}"
        / f"b{batch_index:03d}"
        / f"a{attempt_index:03d}"
        / f"c{candidate_index:03d}"
    )


def _candidate_proposal_summary(proposal: dict[str, Any]) -> dict[str, Any]:
    """Expose only pre-execution proposal semantics to later sibling planners."""
    capabilities = []
    for raw in proposal.get("capabilities", []):
        if not isinstance(raw, dict):
            continue
        capabilities.append(
            {
                "action_group": str(raw.get("action_group", "") or ""),
                "operation": str(raw.get("operation", "") or ""),
                "target_path": str(raw.get("target_path", "") or ""),
                "expected_effect": str(raw.get("expected_effect", "") or ""),
                "lever": str((raw.get("lever_decision", {}) or {}).get("selected_lever", "") or ""),
            }
        )
    return {
        "candidate_id": str(proposal.get("candidate_id", "") or ""),
        "candidate_fingerprint": str(proposal.get("candidate_fingerprint", "") or ""),
        "capabilities": capabilities,
        "execution_status": "not_started",
    }


def _safe_candidate_error(exc: Exception) -> dict[str, str]:
    message = str(exc).strip()
    message = re.sub(r"(?i)\bsk-[A-Za-z0-9._-]{8,}", "[redacted]", message)
    message = re.sub(r"(?i)(api[_-]?key\s*[=:]\s*)\S+", r"\1[redacted]", message)
    message = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)\S+", r"\1[redacted]", message)
    return {
        "error_type": type(exc).__name__,
        "message": message[:2_000],
    }


def _verify_frozen_candidate_proposals(proposals: list[dict[str, Any]]) -> None:
    """Reject plan drift after pre-execution priority has been frozen."""
    for proposal in proposals:
        expected = str(proposal.get("plan_sha256", "") or "")
        plan_path = str(proposal.get("plan_path", "") or "")
        if not expected:
            continue
        if not Path(plan_path).is_file() or _file_sha256(plan_path) != expected:
            raise RuntimeError(
                f"frozen sibling candidate plan changed before evaluation: {proposal.get('candidate_id', '<unknown>')}"
            )


@dataclass(frozen=True, order=True, slots=True)
class _SiblingRealizedSortKey:
    candidate_score: float
    target_delta: float
    native_score: float
    native_delta: float
    dimension_delta: float
    progress: int
    negative_action_count: int
    negative_predicted_rank: int


def _sibling_realized_sort_key(gate: dict[str, Any]) -> _SiblingRealizedSortKey:
    candidate_score = _number(gate.get("candidate_target_score"))
    target_delta = _number(gate.get("target_score_delta"))
    native_score = _number(gate.get("candidate_native_target_score"))
    native_delta = _number(gate.get("native_target_score_delta"))
    dimension_delta = _number(gate.get("native_dimension_delta"))
    progress = len(gate.get("verifier_progress_target_case_ids", []) or [])
    action_count = len(gate.get("capabilities", []) or [])
    predicted_rank = int(gate.get("predicted_rank", 0) or 0)
    return _SiblingRealizedSortKey(
        candidate_score if candidate_score is not None else float("-inf"),
        target_delta if target_delta is not None else float("-inf"),
        native_score if native_score is not None else float("-inf"),
        native_delta if native_delta is not None else float("-inf"),
        dimension_delta if dimension_delta is not None else float("-inf"),
        progress,
        -action_count,
        -predicted_rank,
    )


def _select_sibling_winner(
    gates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Select the best realized candidate; frozen ranks are feedback only."""
    qualified = [gate for gate in gates if bool(gate.get("primary_gate_accepted"))]
    return max(qualified, key=_sibling_realized_sort_key) if qualified else None


def _best_realized_sibling_gate(gates: list[dict[str, Any]]) -> dict[str, Any] | None:
    conclusive = [gate for gate in gates if _number(gate.get("candidate_target_score")) is not None]
    return max(conclusive, key=_sibling_realized_sort_key) if conclusive else (gates[0] if gates else None)


def _attach_native_signal_feedback(
    cohort_feedback: dict[str, Any],
    gates: list[dict[str, Any]],
) -> None:
    """Persist ranking-only continuous outcomes without changing gate metrics."""
    gates_by_id = {
        str(gate.get("candidate_id", "") or ""): gate for gate in gates if str(gate.get("candidate_id", "") or "")
    }
    for candidate in cohort_feedback.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        gate = gates_by_id.get(str(candidate.get("candidate_id", "") or ""))
        if gate is None:
            continue
        candidate["continuous_outcome"] = {
            "source_native_target_score": _number(gate.get("source_native_target_score")),
            "candidate_native_target_score": _number(gate.get("candidate_native_target_score")),
            "native_target_score_delta": _number(gate.get("native_target_score_delta")),
            "native_dimension_delta": _number(gate.get("native_dimension_delta")),
            "source_signal_sources_by_case": dict(
                gate.get("source_native_signal_sources_by_case", {})
                if isinstance(gate.get("source_native_signal_sources_by_case"), dict)
                else {}
            ),
            "candidate_signal_sources_by_case": dict(
                gate.get("candidate_native_signal_sources_by_case", {})
                if isinstance(gate.get("candidate_native_signal_sources_by_case"), dict)
                else {}
            ),
            "role": "sibling_and_repair_ranking_only",
            "promotion_authority": "eval_ref_case_score",
        }
    selection = cohort_feedback.get("selection")
    if isinstance(selection, dict):
        selection["realized_sort_policy"] = "strict_eval_ref_score_then_continuous_signal_v1"


def _write_candidate_feedback_ledger(state: dict[str, Any], output_dir: Path) -> str:
    path = output_dir / "candidate_feedback_ledger.yaml"
    instances = state.get("improvement_instances", {})
    cohorts = [instances[key] for key in sorted(instances)] if isinstance(instances, dict) else []
    _write_yaml_atomic(
        path,
        {
            "schema_version": 1,
            "ledger_type": "single_harness_sibling_candidate_feedback",
            "cohorts": cohorts,
        },
    )
    state["candidate_feedback_ledger_path"] = str(path)
    return str(path)


def _result_from_state(
    state: dict[str, Any],
    state_path: Path,
    report_path: Path,
) -> IterativeSingleHarnessResult:
    return IterativeSingleHarnessResult(
        state_path=str(state_path),
        report_path=str(report_path),
        current_harness_refs_path=str(state["current_harness_refs_path"]),
        best_harness_refs_path=str(state["best_harness_refs_path"]),
        published_harness_refs_path=str(state.get("published_harness_refs_path", "")),
        best_score=_number(state.get("best_score")),
    )


def _load_or_create_state(
    *,
    state_path: Path,
    resume: bool,
    fingerprint: dict[str, Any],
    source_harness_refs_path: str,
    dataset: DatasetArtifact,
) -> dict[str, Any]:
    if resume and state_path.is_file():
        state = _read_yaml(state_path)
        stored_fingerprint = state.get("fingerprint")
        if not _resume_fingerprint_matches(stored_fingerprint, fingerprint):
            raise ValueError("resume inputs do not match single-harness state")
        if stored_fingerprint != fingerprint:
            state["fingerprint"] = fingerprint
        state.setdefault(
            "working_harness_refs_path",
            state.get("current_harness_refs_path", source_harness_refs_path),
        )
        state.setdefault("retained_case_ids", [])
        state.setdefault("best_eval_ref_path", "")
        state.setdefault("baseline_eval_ref_path", "")
        state.setdefault("baseline_score", None)
        state.setdefault("publication_status", "not_published")
        state.setdefault("optimization_journal", [])
        state.setdefault("lever_scoreboard", {})
        state.setdefault("improvement_instances", {})
        state.setdefault("candidate_feedback_ledger_path", "")
        return state
    return {
        "version": 7,
        "status": "running",
        "mode": "single_harness_benchmark",
        "fingerprint": fingerprint,
        "dataset": asdict(dataset),
        "allowed_action_groups": list(_ALLOWED_ACTION_GROUPS),
        "allowed_prompt_surfaces": list(_ALLOWED_PROMPT_SURFACES),
        "source_harness_refs_path": source_harness_refs_path,
        "current_harness_refs_path": source_harness_refs_path,
        "working_harness_refs_path": source_harness_refs_path,
        "best_harness_refs_path": source_harness_refs_path,
        "published_harness_refs_path": "",
        "publication_status": "not_published",
        "best_score": None,
        "best_eval_ref_path": "",
        "baseline_eval_ref_path": "",
        "baseline_score": None,
        "retained_case_ids": [],
        "batch_plan_paths": {},
        "completed_batches": {},
        "candidate_gates": [],
        "improvement_instances": {},
        "epoch_checkpoints": [],
        "optimization_journal": [],
        "lever_scoreboard": {},
        "candidate_feedback_ledger_path": "",
    }


def _initialize_frozen_baseline(
    state: dict[str, Any],
    *,
    baseline_eval_ref_path: str,
    source_harness_refs_path: str,
    expected_case_ids: set[str],
) -> None:
    """Seed global comparison state without changing per-batch execution."""
    if not baseline_eval_ref_path:
        return

    baseline_path = Path(baseline_eval_ref_path).expanduser().resolve()
    if not _eval_ref_complete(baseline_path):
        raise ValueError(f"baseline eval ref is incomplete: {baseline_path}")
    baseline = _read_yaml(baseline_path)

    raw_refs_path = str(baseline.get("harness_refs_path", "") or "").strip()
    if not raw_refs_path:
        raise ValueError(f"baseline eval ref has no harness_refs_path: {baseline_path}")
    recorded_refs_path = Path(raw_refs_path).expanduser()
    if not recorded_refs_path.is_absolute():
        recorded_refs_path = baseline_path.parent / recorded_refs_path
    if recorded_refs_path.resolve() != Path(source_harness_refs_path).expanduser().resolve():
        raise ValueError("baseline eval ref was not produced by the source Harness")

    raw_cases = baseline.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError(f"baseline eval ref has no cases: {baseline_path}")
    baseline_case_ids = [str(case.get("case_id", "") or "") for case in raw_cases if isinstance(case, dict)]
    if any(not case_id for case_id in baseline_case_ids) or len(baseline_case_ids) != len(set(baseline_case_ids)):
        raise ValueError(f"baseline eval ref contains missing or duplicate case ids: {baseline_path}")
    if set(baseline_case_ids) != expected_case_ids:
        missing = sorted(expected_case_ids - set(baseline_case_ids))
        extra = sorted(set(baseline_case_ids) - expected_case_ids)
        raise ValueError(f"baseline eval ref does not match the frozen dataset: missing={missing}, extra={extra}")

    existing_baseline = str(state.get("baseline_eval_ref_path", "") or "")
    if existing_baseline and Path(existing_baseline).expanduser().resolve() != baseline_path:
        raise ValueError("resume baseline eval ref does not match single-harness state")
    state["baseline_eval_ref_path"] = str(baseline_path)
    state["baseline_score"] = _eval_score(baseline_path)
    if not str(state.get("best_eval_ref_path", "") or ""):
        state["best_eval_ref_path"] = str(baseline_path)
        state["best_score"] = state["baseline_score"]
        state["retained_case_ids"] = sorted(
            case_id for case_id, score in _eval_case_scores(baseline_path).items() if score >= 1.0
        )


def _resume_fingerprint_matches(stored: Any, requested: dict[str, Any]) -> bool:
    if stored == requested:
        return True
    if not isinstance(stored, dict):
        return False
    if stored.get("optimization_chain_version") != 12:
        return False
    if requested.get("optimization_chain_version") != 13:
        return False
    if requested.get("improver_policy_digest") != default_improver_policy().canonical_digest:
        return False
    migrated = dict(stored)
    migrated["optimization_chain_version"] = 13
    migrated["improver_policy_digest"] = requested["improver_policy_digest"]
    return migrated == requested


def _refresh_optimization_experience(
    state: dict[str, Any],
    output_dir: Path,
) -> None:
    """Rebuild the cross-round journal and lever scoreboard from gated experiments."""
    journal: list[dict[str, Any]] = []
    for gate in state.get("candidate_gates", []):
        if not isinstance(gate, dict):
            continue
        if (
            str(gate.get("status", "")) == "inconclusive"
            or str(gate.get("failure_class", "")) == "infrastructure_or_evidence_failure"
        ):
            continue
        capabilities = [capability for capability in gate.get("capabilities", []) if isinstance(capability, dict)] or [
            {}
        ]
        for capability in capabilities:
            decision = (
                dict(capability.get("lever_decision", {})) if isinstance(capability.get("lever_decision"), dict) else {}
            )
            epoch_checkpoint = gate.get("epoch_checkpoint_outcome", {})
            if not isinstance(epoch_checkpoint, dict):
                epoch_checkpoint = {}
            journal.append(
                {
                    "experiment_id": (
                        f"e{int(gate.get('epoch', 0) or 0):03d}-"
                        f"b{int(gate.get('batch_index', 0) or 0):03d}-"
                        f"{capability.get('action_id', 'no_action')}"
                    ),
                    "epoch": int(gate.get("epoch", 0) or 0),
                    "batch_index": int(gate.get("batch_index", 0) or 0),
                    "action_id": str(capability.get("action_id", "") or ""),
                    "improvement_cohort_id": str(gate.get("improvement_cohort_id", "") or ""),
                    "candidate_id": str(gate.get("candidate_id", "") or ""),
                    "candidate_index": int(gate.get("candidate_index", 0) or 0),
                    "predicted_rank": int(gate.get("predicted_rank", 0) or 0),
                    "predicted_score": _number(gate.get("predicted_score")),
                    "improver_version_id": str(gate.get("improver_version_id", "") or ""),
                    "improver_policy_digest": str(gate.get("improver_policy_digest", "") or ""),
                    "selected_for_promotion": bool(gate.get("selected_for_promotion")),
                    "qualified_for_promotion": bool(gate.get("qualified_for_promotion")),
                    "surface": str(capability.get("action_group", "") or ""),
                    "lever": str(decision.get("selected_lever", "unresolved") or "unresolved"),
                    "lever_decision": decision,
                    "target_case_ids": list(gate.get("target_case_ids", [])),
                    "source_target_score": _number(gate.get("source_target_score")),
                    "candidate_target_score": _number(gate.get("candidate_target_score")),
                    "target_score_delta": _number(gate.get("target_score_delta")),
                    "source_native_target_score": _number(gate.get("source_native_target_score")),
                    "candidate_native_target_score": _number(gate.get("candidate_native_target_score")),
                    "native_target_score_delta": _number(gate.get("native_target_score_delta")),
                    "native_dimension_delta": _number(gate.get("native_dimension_delta")),
                    "native_signal_role": str(gate.get("native_signal_role", "") or ""),
                    "non_target_score_delta": _number(gate.get("non_target_score_delta")),
                    "status": str(gate.get("status", "") or ""),
                    "reason": str(gate.get("reason", "") or ""),
                    "failure_class": str(gate.get("failure_class", "") or ""),
                    "causal_failure_class": str(gate.get("causal_failure_class", "") or ""),
                    "outcome": _experiment_outcome(gate),
                    "source_eval_ref_path": str(gate.get("paired_source_eval_ref_path", "") or ""),
                    "candidate_eval_ref_path": str(gate.get("candidate_eval_ref_path", "") or ""),
                    "candidate_failure_analysis_ref_path": str(
                        gate.get("candidate_failure_analysis_ref_path", "") or ""
                    ),
                    "verifier_deltas_by_case": dict(
                        gate.get("verifier_deltas_by_case", {})
                        if isinstance(gate.get("verifier_deltas_by_case"), dict)
                        else {}
                    ),
                    "candidate_patch_excerpts_by_case": dict(
                        gate.get("candidate_patch_excerpts_by_case", {})
                        if isinstance(gate.get("candidate_patch_excerpts_by_case"), dict)
                        else {}
                    ),
                    "candidate_failure_diagnoses": dict(
                        gate.get("candidate_failure_diagnoses", {})
                        if isinstance(gate.get("candidate_failure_diagnoses"), dict)
                        else {}
                    ),
                    "causal_intervention_contracts": [
                        dict(item) for item in gate.get("causal_intervention_contracts", []) if isinstance(item, dict)
                    ],
                    "epoch_checkpoint": {
                        "status": str(epoch_checkpoint.get("status", "") or ""),
                        "eval_ref_path": str(epoch_checkpoint.get("eval_ref_path", "") or ""),
                        "failed_target_case_ids": list(epoch_checkpoint.get("failed_target_case_ids", []) or []),
                    },
                }
            )

    scoreboard: dict[str, dict[str, Any]] = {}
    for record in journal:
        lever = str(record.get("lever", "unresolved") or "unresolved")
        entry = scoreboard.setdefault(
            lever,
            {
                "attempts": 0,
                "accepted": 0,
                "rejected": 0,
                "target_improvements": 0,
                "partial_contract_progress": 0,
                "regressions": 0,
                "total_target_score_delta": 0.0,
                "surfaces": {},
                "last_reason": "",
            },
        )
        entry["attempts"] += 1
        accepted = record.get("status") == "accepted"
        if record.get("status") == "superseded":
            entry["qualified_not_selected"] = int(entry.get("qualified_not_selected", 0)) + 1
        else:
            entry["accepted" if accepted else "rejected"] += 1
        target_delta = _number(record.get("target_score_delta")) or 0.0
        entry["total_target_score_delta"] += target_delta
        if target_delta > 0:
            entry["target_improvements"] += 1
        if record.get("outcome") == "partial_contract_progress":
            entry["partial_contract_progress"] += 1
        if record.get("outcome") == "regressed":
            entry["regressions"] += 1
        surface = str(record.get("surface", "") or "unknown")
        entry["surfaces"][surface] = entry["surfaces"].get(surface, 0) + 1
        entry["last_reason"] = str(record.get("reason", "") or "")
    for entry in scoreboard.values():
        attempts = int(entry["attempts"] or 0)
        entry["average_target_score_delta"] = entry.pop("total_target_score_delta") / attempts if attempts else 0.0

    state["optimization_journal"] = journal
    state["lever_scoreboard"] = scoreboard
    journal_path = output_dir / "optimization_journal.yaml"
    scoreboard_path = output_dir / "lever_scoreboard.yaml"
    _write_yaml_atomic(journal_path, {"version": 1, "experiments": journal})
    _write_yaml_atomic(scoreboard_path, {"version": 1, "levers": scoreboard})
    state["optimization_journal_path"] = str(journal_path)
    state["lever_scoreboard_path"] = str(scoreboard_path)


def _experiment_outcome(gate: dict[str, Any]) -> str:
    if gate.get("regressed_target_case_ids") or gate.get("regressed_non_target_case_ids"):
        return "regressed"
    if (_number(gate.get("target_score_delta")) or 0.0) > 0:
        return "flipped" if gate.get("status") == "accepted" else "improved_but_rejected"
    verifier_deltas = gate.get("verifier_deltas_by_case", {})
    if isinstance(verifier_deltas, dict) and any(
        isinstance(delta, dict)
        and (
            delta.get("newly_passed_requirements")
            or delta.get("newly_passed_fail_to_pass")
            or delta.get("newly_passed_atomic_checks")
        )
        for delta in verifier_deltas.values()
    ):
        return "partial_contract_progress"
    return "still_failed"


def _candidate_capabilities(member_info: dict[str, Any]) -> list[dict[str, Any]]:
    plan = _read_yaml(str(member_info.get("plan_path", "") or ""))
    issue_case_ids, target_case_ids_by_role = _target_case_ids(plan, member_info)
    capabilities: list[dict[str, Any]] = []
    for action in plan.get("actions", []) if isinstance(plan, dict) else []:
        if not isinstance(action, dict):
            continue
        target_path = str(action.get("target_path", "") or "")
        constraints = action.get("constraints") if isinstance(action.get("constraints"), dict) else {}
        optimization_contracts = [
            dict(item) for item in constraints.get("optimization_contracts", []) if isinstance(item, dict)
        ]
        decision_contracts = [
            dict(item.get("decision_contract", {}))
            for item in optimization_contracts
            if isinstance(item.get("decision_contract"), dict)
        ]
        capabilities.append(
            {
                "action_id": str(action.get("action_id", "") or ""),
                "role": str(action.get("role", "") or ""),
                "action_group": str(action.get("action_group", "") or ""),
                "operation": str(action.get("operation", "") or ""),
                "description": str(action.get("description", "") or ""),
                "rationale": str(action.get("rationale", "") or ""),
                "intervention": str(action.get("intervention", "") or ""),
                "target_path": target_path,
                "runtime_name": _capability_runtime_name(
                    str(action.get("action_group", "") or ""),
                    target_path,
                ),
                "expected_effect": str(action.get("expected_effect", "") or ""),
                "decision_contracts": decision_contracts,
                "attributed_issue_ids": [str(item) for item in action.get("attributed_issue_ids", []) if str(item)],
                "analyzer_counterfactual_predictions": [
                    str(item) for item in constraints.get("analyzer_counterfactual_predictions", []) if str(item)
                ],
                "lever_decision": (
                    dict(constraints.get("lever_decision", {}))
                    if isinstance(constraints.get("lever_decision"), dict)
                    else {}
                ),
                "optimization_hypothesis_ids": [
                    str(item.get("hypothesis_id", "") or "")
                    for item in optimization_contracts
                    if str(item.get("hypothesis_id", "") or "")
                ],
                "source_causal_hypothesis_id": str(constraints.get("source_causal_hypothesis_id", "") or ""),
                "source_causal_hypothesis_ids": [
                    str(item) for item in constraints.get("source_causal_hypothesis_ids", []) if str(item)
                ],
                "source_causal_hypothesis_semantic_id": str(
                    constraints.get("source_causal_hypothesis_semantic_id", "") or ""
                ),
                "source_causal_hypothesis_semantic_ids": [
                    str(item) for item in constraints.get("source_causal_hypothesis_semantic_ids", []) if str(item)
                ],
                "optimization_contract_sha256": [
                    str(item.get("content_sha256", "") or "")
                    for item in optimization_contracts
                    if str(item.get("content_sha256", "") or "")
                ],
                "target_case_ids": sorted(
                    _action_target_case_ids(
                        action,
                        issue_case_ids=issue_case_ids,
                        target_case_ids_by_role=target_case_ids_by_role,
                    )
                ),
            }
        )
    return capabilities


def _member_composition_mode(member_info: dict[str, Any]) -> str:
    """Return the candidate package-composition contract, when declared."""
    metadata = member_info.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    return str(member_info.get("composition_mode", metadata.get("composition_mode", "")) or "")


def _target_case_ids(
    plan: dict[str, Any],
    member_info: dict[str, Any],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    metadata = member_info.get("metadata") if isinstance(member_info.get("metadata"), dict) else {}
    issue_case_ids = _analysis_issue_case_ids(str(metadata.get("analysis_result_path", "") or ""))

    result: dict[str, set[str]] = {}
    for target in plan.get("targets", []) if isinstance(plan, dict) else []:
        if not isinstance(target, dict):
            continue
        role = str(target.get("role", target.get("member_name", "")) or "")
        if not role:
            continue
        role_case_ids = result.setdefault(role, set())
        for issue_id in target.get("attributed_issue_ids", []):
            role_case_ids.update(issue_case_ids.get(str(issue_id), set()))
        for evidence in target.get("evidence_refs", []):
            if not isinstance(evidence, dict):
                continue
            case_id = str(evidence.get("case_id", "") or "")
            if case_id:
                role_case_ids.add(case_id)
            issue_id = str(evidence.get("issue_id", "") or "")
            if issue_id:
                role_case_ids.update(issue_case_ids.get(issue_id, set()))
    return issue_case_ids, result


def _analysis_issue_case_ids(analysis_ref: str | Path) -> dict[str, set[str]]:
    analysis = _read_yaml(analysis_ref)
    issue_case_ids: dict[str, set[str]] = {}
    for issue in analysis.get("issues", []) if isinstance(analysis, dict) else []:
        if not isinstance(issue, dict):
            continue
        issue_id = str(issue.get("issue_id", "") or "")
        if not issue_id:
            continue
        case_ids = {str(case_id) for case_id in issue.get("affected_cases", []) if str(case_id)}
        for evidence in issue.get("evidence", []):
            if isinstance(evidence, dict) and str(evidence.get("case_id", "") or ""):
                case_ids.add(str(evidence["case_id"]))
        issue_case_ids[issue_id] = case_ids
    return issue_case_ids


def _must_preserve_budget_for_siblings(
    *,
    remaining_attempt_budget: int | None,
    remaining_sibling_count: int,
) -> bool:
    """Keep queued alternatives reachable before deepening one failed route."""
    return (
        remaining_attempt_budget is not None
        and remaining_sibling_count > 0
        and remaining_attempt_budget <= remaining_sibling_count
    )


def _analysis_issue_signatures(analysis_ref: str | Path) -> dict[str, str]:
    """Build stable semantic keys so a residual loop cannot retry one issue."""
    analysis = _read_yaml(analysis_ref)
    signatures: dict[str, str] = {}
    for issue in analysis.get("issues", []) if isinstance(analysis, dict) else []:
        if not isinstance(issue, dict):
            continue
        issue_id = str(issue.get("issue_id", "") or "")
        if not issue_id:
            continue
        metadata = issue.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        attribution = metadata.get("attribution", {})
        attribution = attribution if isinstance(attribution, dict) else {}
        causal_coverage = attribution.get("causal_coverage", {})
        causal_coverage = causal_coverage if isinstance(causal_coverage, dict) else {}
        semantic_payload = {
            "category": str(issue.get("category", "") or "").strip().lower(),
            "summary": " ".join(str(issue.get("summary", "") or "").lower().split()),
            "recommendation": " ".join(str(issue.get("recommendation", "") or "").lower().split()),
            "failure_mode": " ".join(str(issue.get("failure_mode", "") or "").lower().split()),
            "target_ref": str(attribution.get("target_ref", issue.get("target_ref", "")) or "").strip().lower(),
            "affected_cases": sorted(str(case_id) for case_id in issue.get("affected_cases", []) if str(case_id)),
            # A residual issue is a different repair problem after part of the
            # original contract has been fixed, even when prose stays similar.
            "explained_requirement_ids": sorted(
                str(requirement_id)
                for requirement_id in causal_coverage.get("explained_requirement_ids", [])
                if str(requirement_id)
            ),
            "residual_requirement_ids": sorted(
                str(requirement_id)
                for requirement_id in causal_coverage.get("residual_requirement_ids", [])
                if str(requirement_id)
            ),
        }
        if not any(value for key, value in semantic_payload.items() if key != "affected_cases"):
            signatures[issue_id] = issue_id
            continue
        encoded = json.dumps(
            semantic_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signatures[issue_id] = hashlib.sha256(encoded).hexdigest()
    return signatures


def _cases_with_ids(cases: list[dict[str, Any]], case_ids: set[str]) -> list[dict[str, Any]]:
    """Keep source order while selecting active cases by identifier."""
    return [case for case in cases if str(case.get("case_id", "") or "") in case_ids]


def _sync_retained_case_ids(
    retained_case_ids: set[str],
    evaluated_case_scores: dict[str, float],
) -> None:
    """Replace retention state for every case observed in a fresh evaluation."""
    retained_case_ids.difference_update(evaluated_case_scores)
    retained_case_ids.update(case_id for case_id, score in evaluated_case_scores.items() if score >= 1.0)


def _rejected_capability_history(candidate_gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    copied_fields = (
        "target_confirmation",
        "epoch_checkpoint_outcome",
        "verifier_deltas_by_case",
        "candidate_failure_diagnoses",
    )
    for gate in candidate_gates:
        if gate.get("status") != "rejected":
            continue
        for capability in gate.get("capabilities", []):
            if not isinstance(capability, dict):
                continue
            item = dict(capability)
            item["rejection_reason"] = str(gate.get("reason", ""))
            item["failure_class"] = str(gate.get("failure_class", ""))
            for field in copied_fields:
                value = gate.get(field, {})
                item[field] = dict(value) if isinstance(value, dict) else {}
            rejected.append(item)
    return rejected


def _capability_action_ids(gates: list[dict[str, Any]]) -> list[str]:
    action_ids: set[str] = set()
    for gate in gates:
        for capability in gate.get("capabilities", []):
            action_id = str(capability.get("action_id", "") or "")
            if action_id:
                action_ids.add(action_id)
    return sorted(action_ids)


def _expected_runtime_names(
    capabilities: list[dict[str, Any]],
    *,
    action_group: str,
) -> list[str]:
    names: set[str] = set()
    for capability in capabilities:
        if capability.get("action_group") != action_group:
            continue
        if capability.get("operation") not in {"add", "modify"}:
            continue
        runtime_name = str(capability.get("runtime_name", ""))
        if runtime_name:
            names.add(runtime_name)
    return sorted(names)


def _compact_analysis_diagnoses(
    analysis_ref: str | Path,
) -> dict[str, list[dict[str, Any]]]:
    """Load the candidate analyzer's causal result, not just its score."""
    analysis = _read_yaml(analysis_ref)
    metadata = analysis.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    diagnoses_path = Path(str(metadata.get("per_case_diagnoses_path", "") or ""))
    if not diagnoses_path.is_file():
        return {}
    try:
        payload = json.loads(diagnoses_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    compact: dict[str, list[dict[str, Any]]] = {}
    for diagnosis in payload.get("per_case_diagnoses", []):
        if not isinstance(diagnosis, dict):
            continue
        case_id = str(diagnosis.get("case_id", "") or "")
        if not case_id:
            continue
        compact_diagnosis: dict[str, Any] = {}
        for key in (
            "summary",
            "root_cause",
            "critical_mistake",
            "general_mechanism",
            "recommendation",
            "decision_contract",
            "target_ref",
            "confidence",
            "diagnosis_status",
            "analysis_failed",
            "evidence_status",
            "hypothesis_assessment",
            "causal_coverage",
            "prior_experiment_assessment",
        ):
            if key in diagnosis:
                compact_diagnosis[key] = diagnosis.get(key)
        compact.setdefault(case_id, []).append(compact_diagnosis)
    return compact


def _refine_paired_candidate_activation(
    activation: dict[str, Any],
    diagnoses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Add paired behavioral evidence when a surface has no direct call event."""
    assessments = [
        dict(item["prior_experiment_assessment"])
        for item in diagnoses
        if isinstance(item, dict) and isinstance(item.get("prior_experiment_assessment"), dict)
    ]
    if not assessments:
        return activation
    values = {str(item.get("intervention_activated", "") or "").strip().casefold() for item in assessments}
    values.discard("")
    state = "triggered" if values == {"yes"} else "not_triggered" if "no" in values else "unknown"
    refined = dict(activation)
    refined.update(
        {
            "availability": "observed",
            "state": state,
            "observation_source": "candidate_failure_analysis",
            "behavior_activation": {
                "availability": "observed",
                "state": state,
                "assessment_count": len(assessments),
                "predicted_behavior_occurred": _uniform_assessment_value(assessments, "predicted_behavior_occurred"),
                "predicted_outcome_occurred": _uniform_assessment_value(assessments, "predicted_outcome_occurred"),
            },
        }
    )
    return refined


def _uniform_assessment_value(assessments: list[dict[str, Any]], key: str) -> str:
    values = {str(item.get(key, "") or "").strip().casefold() for item in assessments}
    values.discard("")
    return next(iter(values)) if len(values) == 1 else "mixed" if values else "unknown"


def _causal_candidate_failure_classification(activation_by_case: dict[str, dict[str, Any]]) -> str:
    """Route a rejected candidate by realized action, not score alone."""
    states = {
        str(item.get("state", "") or "").strip().casefold()
        for item in activation_by_case.values()
        if isinstance(item, dict)
    }
    behavioral = [
        item.get("behavior_activation", {})
        for item in activation_by_case.values()
        if isinstance(item, dict) and isinstance(item.get("behavior_activation"), dict)
    ]
    behavior_values = {
        str(item.get("predicted_behavior_occurred", "") or "").strip().casefold() for item in behavioral
    } - {""}
    outcome_values = {
        str(item.get("predicted_outcome_occurred", "") or "").strip().casefold() for item in behavioral
    } - {""}
    if "not_triggered" in states or "no" in behavior_values:
        return "intervention_not_activated"
    if behavior_values == {"yes"} and "no" in outcome_values:
        return "action_occurred_but_hypothesis_refuted"
    if "no" in outcome_values:
        return "outcome_not_improved"
    return ""


def _materialize_candidate_activation_repair(
    analysis_ref_path: str | Path,
    *,
    capabilities: list[dict[str, Any]],
    causal_intervention_contracts: list[dict[str, Any]],
    diagnoses_by_case: dict[str, list[dict[str, Any]]],
) -> None:
    """Keep a failed causal intervention repairable without inventing task semantics."""
    analysis_path = Path(analysis_ref_path).expanduser().resolve()
    analysis = _read_yaml(analysis_path)
    if analysis.get("issues"):
        return
    raw_issues_path = str(analysis.get("issues_path", "") or "").strip()
    issues_path = Path(raw_issues_path) if raw_issues_path else None
    if issues_path is not None and not issues_path.is_absolute():
        issues_path = analysis_path.parent / issues_path
    if issues_path is not None and issues_path.is_file() and _read_yaml(issues_path).get("issues"):
        return

    capability = next((item for item in capabilities if isinstance(item, dict)), None)
    contract = next((item for item in causal_intervention_contracts if isinstance(item, dict)), None)
    if capability is None or contract is None:
        return
    affected_cases: list[str] = []
    assessments: list[dict[str, Any]] = []
    for case_id, diagnoses in diagnoses_by_case.items():
        for diagnosis in diagnoses:
            assessment = diagnosis.get("prior_experiment_assessment", {})
            if not isinstance(assessment, dict):
                continue
            if str(assessment.get("predicted_behavior_occurred", "") or "").casefold() != "no":
                continue
            affected_cases.append(case_id)
            assessments.append(dict(assessment))
    if not affected_cases:
        return

    role = str(capability.get("role", "") or "solver")
    surface = str(capability.get("action_group", "") or "prompt").casefold()
    variable = {"prompt": "prompt_section", "skill": "skill", "tool": "tool", "rail": "rail"}.get(surface, "config")
    target_ref = f"member_harness.{role}.{variable}"
    predicted = str(contract.get("predicted_behavior_and_outcome", "") or "").strip()
    intervention = str(contract.get("intervention", "") or "").strip()
    digest = hashlib.sha256(
        json.dumps(
            {"target_ref": target_ref, "cases": sorted(set(affected_cases)), "predicted": predicted},
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    hypothesis_id = f"h_activation_{digest}"
    evidence = [
        {
            "case_id": case_id,
            "failure_mode": "candidate_intervention_behavior_not_reproduced",
            "affected_component": role,
        }
        for case_id in sorted(set(affected_cases))
    ]
    issue = {
        "issue_id": f"issue_activation_{digest}",
        "category": "member_harness",
        "severity": "high",
        "summary": "The deployed candidate did not produce its pre-registered behavior on the paired target run.",
        "affected_cases": sorted(set(affected_cases)),
        "evidence": evidence,
        "suspected_team_scope": "member",
        "optimization_target": "member_harness",
        "target_members": [role],
        "recommendation": (
            "Revise the activation and operational wording of the same intervention so its pre-registered "
            "behavior becomes observable; do not change the task-semantic conclusion or add case literals."
        ),
        "metadata": {
            "attribution": {
                "evidence_status": "confirmed",
                "selected_hypothesis_id": hypothesis_id,
                "target_ref": target_ref,
                "general_mechanism": (
                    "When a deployed intervention does not cause its pre-registered behavior, strengthen its "
                    "trigger, executable action, and observable completion condition on the same surface."
                ),
                "decision_contract": {
                    "wrong_decision": (
                        "The candidate intervention was delivered but its predicted behavior did not occur."
                    ),
                    "causal_distinction": (
                        "This repair changes intervention activation and operationalization, not the unresolved "
                        "task-semantic answer."
                    ),
                    "required_action": (
                        f"Operationalize the existing intervention ({intervention}) so the recorded behavior occurs."
                    ),
                    "acceptance_observable": predicted,
                    "scope_boundary": [
                        "Preserve the original semantic decision contract.",
                        "Do not add benchmark identifiers, evaluator answers, or case-specific literals.",
                    ],
                    "activation_phase": "task_execution",
                },
                "causal_coverage": {
                    "explained_requirement_ids": ["candidate_intervention_behavior"],
                    "residual_requirement_ids": [],
                    "unexplained_observations": [],
                    "sufficiency_status": "cluster_sufficient",
                },
                "hypothesis_assessment": [
                    {
                        "hypothesis_id": hypothesis_id,
                        "status": "supported",
                        "verification_status": "verified",
                        "verification_basis": "paired_candidate_experiment",
                        "falsifying_condition_status": "not_observed",
                        "claim_follows_from_evidence": "yes",
                        "reason": "The paired Analyzer observed that the pre-registered behavior did not occur.",
                        "evidence_refs": evidence,
                    }
                ],
                "prior_experiment_assessment": assessments[0],
                "source_causal_intervention_contract": dict(contract),
            }
        },
    }
    analysis["issues"] = [issue]
    _write_yaml_atomic(analysis_path, analysis)
    if issues_path is not None:
        _write_yaml_atomic(issues_path, {"issues": [issue]})


def _causal_intervention_contracts(capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve what each candidate predicted before its outcome was observed."""
    contracts: list[dict[str, Any]] = []
    for capability in capabilities:
        if not isinstance(capability, dict):
            continue
        expected_effect = _bounded_candidate_text(str(capability.get("expected_effect", "") or ""), 2_000)
        if not expected_effect:
            continue
        decision_contracts = [dict(item) for item in capability.get("decision_contracts", []) if isinstance(item, dict)]
        acceptance_observables = [
            str(item.get("acceptance_observable", "") or "").strip()
            for item in decision_contracts
            if str(item.get("acceptance_observable", "") or "").strip()
        ]
        contracts.append(
            {
                "action_id": str(capability.get("action_id", "") or ""),
                "action_group": str(capability.get("action_group", "") or ""),
                "operation": str(capability.get("operation", "") or ""),
                "attributed_issue_ids": [
                    str(issue_id) for issue_id in capability.get("attributed_issue_ids", []) if str(issue_id)
                ],
                "target_case_ids": [str(case_id) for case_id in capability.get("target_case_ids", []) if str(case_id)],
                "intervention": _bounded_candidate_text(
                    str(
                        capability.get("intervention")
                        or capability.get("description")
                        or capability.get("rationale")
                        or ""
                    ),
                    2_000,
                ),
                "target_object": str(
                    capability.get("runtime_name") or capability.get("target_path") or capability.get("action_group")
                ),
                "expected_observable_actions_or_state_changes": acceptance_observables or [expected_effect],
                "success_condition": (
                    "The candidate trajectory or artifact contains the pre-registered observable change, "
                    "and the paired target outcome improves without regression."
                ),
                "refutation_condition": (
                    "The pre-registered action occurs but its predicted outcome does not; this refutes the "
                    "source causal hypothesis rather than requesting another wording-only repair."
                ),
                "predicted_behavior_and_outcome": expected_effect,
                "analyzer_counterfactual_predictions": [
                    str(item) for item in capability.get("analyzer_counterfactual_predictions", []) if str(item)
                ],
                "source_causal_hypothesis_id": str(capability.get("source_causal_hypothesis_id", "") or ""),
                "source_causal_hypothesis_ids": [
                    str(item) for item in capability.get("source_causal_hypothesis_ids", []) if str(item)
                ],
                "source_causal_hypothesis_semantic_id": str(
                    capability.get("source_causal_hypothesis_semantic_id", "") or ""
                ),
                "source_causal_hypothesis_semantic_ids": [
                    str(item) for item in capability.get("source_causal_hypothesis_semantic_ids", []) if str(item)
                ],
                "prediction_recorded_before_evaluation": True,
            }
        )
    return contracts


def _paired_candidate_activation(
    capabilities: list[dict[str, Any]],
    *,
    case_id: str,
    pre_edit_tools_by_case: dict[str, set[str]],
    pre_edit_skills_by_case: dict[str, set[str]],
) -> dict[str, Any]:
    """Separate observed candidate delivery from surface behavior activation."""
    delivery = {
        "availability": "observed",
        "state": "executed",
        "evidence": "candidate_harness_was_used_for_paired_evaluation",
    }
    surfaces = {
        str(capability.get("action_group", "") or "")
        for capability in capabilities
        if str(capability.get("action_group", "") or "")
    }
    expected: list[dict[str, Any]] = []
    missing_runtime_name = False
    for capability in capabilities:
        action_group = str(capability.get("action_group", "") or "")
        operation = str(capability.get("operation", "") or "")
        if action_group not in {"skill", "tool"} or operation not in {"add", "modify", "update"}:
            continue
        target_case_ids = {
            str(target_case_id) for target_case_id in capability.get("target_case_ids", []) if str(target_case_id)
        }
        if target_case_ids and case_id not in target_case_ids:
            continue
        runtime_name = str(capability.get("runtime_name", "") or "")
        if not runtime_name:
            missing_runtime_name = True
            continue
        observed_names = (
            pre_edit_tools_by_case.get(case_id, set())
            if action_group == "tool"
            else pre_edit_skills_by_case.get(case_id, set())
        )
        names_match = _tool_names_match if action_group == "tool" else _skill_names_match
        expected.append(
            {
                "surface": action_group,
                "runtime_name": runtime_name,
                "triggered_pre_edit": any(names_match(runtime_name, name) for name in observed_names),
            }
        )

    if missing_runtime_name:
        return {
            "schema_version": 1,
            "delivery": delivery,
            "availability": "missing_artifact",
            "state": "unknown",
            "surfaces": sorted(surfaces),
            "reason": "expected_runtime_name_missing",
            "expected": expected,
            "behavior_activation": {
                "availability": "missing_artifact",
                "state": "unknown",
                "reason": "expected_runtime_name_missing",
            },
        }
    if not expected:
        availability = "not_instrumented" if surfaces & {"prompt", "rail", "config", "control"} else "not_applicable"
        return {
            "schema_version": 1,
            "delivery": delivery,
            "availability": availability,
            "state": "unknown",
            "surfaces": sorted(surfaces),
            "reason": "surface_has_no_observable_activation_event",
            "expected": [],
            "behavior_activation": {
                "availability": availability,
                "state": "unknown",
                "reason": "surface_has_no_observable_activation_event",
            },
        }

    observed_count = sum(item["triggered_pre_edit"] is True for item in expected)
    state = (
        "triggered" if observed_count == len(expected) else "partially_triggered" if observed_count else "not_triggered"
    )
    return {
        "schema_version": 1,
        "delivery": delivery,
        "availability": "observed",
        "state": state,
        "surfaces": sorted(surfaces & {"skill", "tool"}),
        "expected_count": len(expected),
        "observed_pre_edit_count": observed_count,
        "trigger_rate": observed_count / len(expected),
        "expected": expected,
        "behavior_activation": {
            "availability": "observed",
            "state": state,
            "expected_count": len(expected),
            "observed_pre_edit_count": observed_count,
            "trigger_rate": observed_count / len(expected),
        },
    }


def _prior_candidate_feedback(
    state: dict[str, Any],
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Select recent paired candidate evidence for cases being re-diagnosed."""
    active_case_ids = {str(case.get("case_id", "") or "") for case in cases if str(case.get("case_id", "") or "")}
    by_case: dict[str, list[dict[str, Any]]] = {case_id: [] for case_id in active_case_ids}
    for record in state.get("optimization_journal", []):
        if not isinstance(record, dict):
            continue
        deltas = record.get("verifier_deltas_by_case", {})
        diagnoses = record.get("candidate_failure_diagnoses", {})
        patches = record.get("candidate_patch_excerpts_by_case", {})
        causal_contracts = record.get("causal_intervention_contracts", [])
        deltas = deltas if isinstance(deltas, dict) else {}
        diagnoses = diagnoses if isinstance(diagnoses, dict) else {}
        patches = patches if isinstance(patches, dict) else {}
        causal_contracts = [dict(item) for item in causal_contracts if isinstance(item, dict)]
        for case_id in active_case_ids & set(deltas):
            diagnosis_value = diagnoses.get(case_id, {})
            if isinstance(diagnosis_value, list):
                case_diagnoses = [dict(item) for item in diagnosis_value if isinstance(item, dict)]
            elif isinstance(diagnosis_value, dict):
                case_diagnoses = [dict(diagnosis_value)]
            else:
                case_diagnoses = []
            by_case[case_id].append(
                {
                    "experiment_id": str(record.get("experiment_id", "") or ""),
                    "surface": str(record.get("surface", "") or ""),
                    "outcome": str(record.get("outcome", "") or ""),
                    "status": str(record.get("status", "") or ""),
                    "reason": str(record.get("reason", "") or ""),
                    "failure_class": str(record.get("failure_class", "") or ""),
                    "predicted_rank": record.get("predicted_rank"),
                    "predicted_score": record.get("predicted_score"),
                    "source_target_score": record.get("source_target_score"),
                    "candidate_target_score": record.get("candidate_target_score"),
                    "target_score_delta": record.get("target_score_delta"),
                    "selected_for_promotion": record.get("selected_for_promotion"),
                    "verifier_delta": dict(deltas.get(case_id, {})),
                    "candidate_patch_excerpt": str(patches.get(case_id, "") or ""),
                    # Preserve the singular field for older analyzer prompts.
                    "candidate_failure_diagnosis": (dict(case_diagnoses[0]) if case_diagnoses else {}),
                    "candidate_failure_diagnoses": case_diagnoses,
                    "causal_intervention_contracts": [
                        dict(contract)
                        for contract in causal_contracts
                        if case_id in set(contract.get("target_case_ids", []))
                    ],
                }
            )
    return {"by_case": {case_id: records[-3:] for case_id, records in by_case.items() if records}}


def _action_target_case_ids(
    action: dict[str, Any],
    *,
    issue_case_ids: dict[str, set[str]],
    target_case_ids_by_role: dict[str, set[str]],
) -> set[str]:
    action_issue_ids = {str(issue_id) for issue_id in action.get("attributed_issue_ids", []) if str(issue_id)}
    if action_issue_ids:
        target_case_ids: set[str] = set()
        for issue_id in action_issue_ids:
            target_case_ids.update(issue_case_ids.get(issue_id, set()))
        return target_case_ids
    return set(target_case_ids_by_role.get(str(action.get("role", "") or ""), set()))


def _bind_task_acceptance_contracts(
    cases: list[dict[str, Any]],
    *,
    analysis_ref: str,
    target_case_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Attach current-case verifier feedback without freezing it into a Skill."""
    if not analysis_ref or not target_case_ids:
        return [dict(case) for case in cases], {}
    analysis = _read_yaml(analysis_ref)
    metadata = analysis.get("metadata") if isinstance(analysis, dict) else {}
    diagnoses_path = str(
        (metadata if isinstance(metadata, dict) else {}).get(
            "per_case_diagnoses_path",
            "",
        )
        or ""
    )
    if not diagnoses_path or not Path(diagnoses_path).is_file():
        return [dict(case) for case in cases], {}
    try:
        payload = json.loads(Path(diagnoses_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [dict(case) for case in cases], {}
    records = payload.get("per_case_diagnoses", []) if isinstance(payload, dict) else []
    diagnoses = {
        str(record.get("case_id", "") or ""): record
        for record in records
        if isinstance(record, dict) and str(record.get("case_id", "") or "")
    }
    contracts: dict[str, dict[str, Any]] = {}
    bound_cases: list[dict[str, Any]] = []
    for case in cases:
        cloned = dict(case)
        case_id = str(case.get("case_id", "") or "")
        diagnosis = diagnoses.get(case_id)
        if case_id in target_case_ids and diagnosis:
            verifier = diagnosis.get("verifier_observations")
            verifier = verifier if isinstance(verifier, dict) else {}
            contract = {
                "case_id": case_id,
                "failed_fail_to_pass_tests": list(verifier.get("failed_fail_to_pass_tests", []) or []),
                "failed_pass_to_pass_tests": list(verifier.get("failed_pass_to_pass_tests", []) or []),
                "verifier_failure_output_excerpt": str(diagnosis.get("verifier_failure_output_excerpt", "") or ""),
                "semantic_invariant": str(diagnosis.get("root_cause", "") or ""),
                "required_correction": str(diagnosis.get("recommendation", "") or ""),
                "local_official_contradiction": str(
                    (diagnosis.get("validation_observations") or {}).get(
                        "contradiction_explanation",
                        "",
                    )
                    if isinstance(diagnosis.get("validation_observations"), dict)
                    else ""
                ),
            }
            contracts[case_id] = contract
            _append_task_contract(cloned, contract)
        bound_cases.append(cloned)
    return bound_cases, contracts


def _append_task_contract(case: dict[str, Any], contract: dict[str, Any]) -> None:
    rendered = (
        "<task-acceptance-contract>\n"
        "This optimizer feedback is authoritative only for the current training case; "
        "do not copy it into a reusable Skill. The named FAIL_TO_PASS operation may not "
        "be replaced by a nearby convenience behavior. A locally available suite that "
        "does not contain the named test is regression evidence, not proof of this contract.\n"
        f"{json.dumps(contract, ensure_ascii=False, indent=2)}\n"
        "Before completion, run the exact named test when available. If it is unavailable, "
        "use the supplied authoritative failure output to enumerate competing semantics, "
        "including positive and negative/order/lifecycle boundaries when relevant. A "
        "self-authored probe may falsify a hypothesis only when its expected result is "
        "grounded in the task, verifier output, or repository history; it cannot certify "
        "equivalence to an unavailable official test. State unresolved verifier-only "
        "semantics explicitly instead of choosing the probe expectation yourself.\n"
        "</task-acceptance-contract>"
    )
    key = next(
        (name for name in ("input", "inputs", "task_input", "query", "prompt") if name in case),
        "input",
    )
    value = case.get(key, "")
    if key == "input" and isinstance(value, dict) and set(value) == {"user_message"}:
        copied = dict(value)
        copied["user_message"] = f"{copied.get('user_message', '')}\n\n{rendered}"
        case[key] = copied
        return
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    case[key] = f"{value}\n\n{rendered}".strip()


def _persist_promotion(
    member_ref_path: str,
    candidate_refs_path: str,
    gate: dict[str, Any],
) -> None:
    status = str(gate.get("status", "") or "")
    accepted = bool(gate.get("accepted")) and status == "accepted"
    promotion_status = "promoted" if accepted else status
    candidate_manifest_paths: set[str] = set()
    for raw_path in (member_ref_path, candidate_refs_path):
        path = Path(raw_path).expanduser()
        if not path.is_file():
            continue
        payload = _read_yaml(path)
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            manifest_path = str(metadata.get("candidate_manifest_path", "") or "")
            if manifest_path:
                candidate_manifest_paths.add(manifest_path)
        roles = payload.get("candidate_ready_roles", payload.get("published_roles", []))
        roles = [str(role) for role in roles] if isinstance(roles, list) else []
        payload["candidate_ready_roles"] = roles
        payload["promoted_roles"] = roles if accepted else []
        payload["promotion_status"] = promotion_status
        payload["candidate_gate"] = {
            "status": gate.get("status"),
            "reason": gate.get("reason"),
            "source_score": gate.get("source_score"),
            "candidate_score": gate.get("candidate_score"),
        }
        _write_yaml_atomic(path, payload)
    for raw_manifest_path in candidate_manifest_paths:
        manifest_path = Path(raw_manifest_path).expanduser()
        if not manifest_path.is_file():
            continue
        manifest = _read_yaml(manifest_path)
        manifest.update(
            {
                "status": "accepted" if accepted else status,
                "candidate_gate": {
                    "status": gate.get("status"),
                    "reason": gate.get("reason"),
                    "source_score": gate.get("source_score"),
                    "candidate_score": gate.get("candidate_score"),
                    "score_delta": gate.get("score_delta"),
                    "target_case_ids": gate.get("target_case_ids", []),
                    "target_confirmation": gate.get("target_confirmation", {}),
                    "epoch_checkpoint_outcome": gate.get(
                        "epoch_checkpoint_outcome",
                        {},
                    ),
                },
            }
        )
        _write_yaml_atomic(manifest_path, manifest)


def _update_batch_attempt_record(
    completed_batch: dict[str, Any],
    gate: dict[str, Any],
) -> None:
    """Keep per-issue attempt status aligned with the epoch checkpoint result."""
    member_ref = str(gate.get("member_optimization_ref_path", "") or "")
    for attempt in completed_batch.get("candidate_attempts", []):
        if not isinstance(attempt, dict):
            continue
        if str(attempt.get("member_optimization_ref_path", "") or "") != member_ref:
            continue
        attempt["candidate_gate_status"] = gate.get("status", "")
        attempt["candidate_gate_reason"] = gate.get("reason", "")
        return


def _eval_score(eval_ref_path: str | Path) -> float:
    payload = _read_yaml(eval_ref_path)
    official_metrics = payload.get("official_metrics", {})
    if isinstance(official_metrics, dict):
        primary_score = _number(official_metrics.get("primary_score"))
        if primary_score is not None:
            return primary_score
    scores = [_number(case.get("score")) for case in payload.get("cases", []) if isinstance(case, dict)]
    numeric = [score for score in scores if score is not None]
    return sum(numeric) / len(numeric) if numeric else 0.0


def _eval_case_scores(eval_ref_path: str | Path) -> dict[str, float]:
    payload = _read_yaml(eval_ref_path)
    scores: dict[str, float] = {}
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "")
        score = _number(case.get("score"))
        if case_id and score is not None:
            scores[case_id] = score
    return scores


def _eval_case_native_signals(eval_ref_path: str | Path) -> dict[str, dict[str, Any]]:
    """Read the evaluator's generic search signals, falling back to strict score."""
    payload = _read_yaml(eval_ref_path)
    signals: dict[str, dict[str, Any]] = {}
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "")
        fallback_score = _number(case.get("score"))
        if not case_id:
            continue
        result = _read_json(str(case.get("result_path", "") or ""))
        evaluation = result.get("evaluation", {})
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        metadata = evaluation.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        contract = evaluation_optimization_signals(metadata)
        continuous = contract["continuous_score"]
        native_score = _number(continuous.get("value")) if continuous.get("availability") == "available" else None
        source = str(continuous.get("source") or "")
        dimensions: dict[str, float] = {}
        for name, dimension in contract["dimensions"].items():
            if not isinstance(dimension, dict) or dimension.get("availability") != "available":
                continue
            score = _number(dimension.get("value"))
            if score is not None:
                dimensions[str(name)] = score
        if native_score is None:
            native_score = fallback_score
            source = "eval_ref_score_fallback"
        if native_score is None:
            continue
        signals[case_id] = {
            "score": native_score,
            "source": source,
            "dimensions": dimensions,
        }
    return signals


def _native_case_scores(signals: dict[str, dict[str, Any]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for case_id, signal in signals.items():
        score = _number(signal.get("score"))
        if score is not None:
            scores[case_id] = score
    return scores


def _paired_score_delta(
    source_scores: dict[str, float],
    candidate_scores: dict[str, float],
    case_id: str,
) -> float | None:
    source_score = _number(source_scores.get(case_id))
    candidate_score = _number(candidate_scores.get(case_id))
    if source_score is None or candidate_score is None:
        return None
    return candidate_score - source_score


def _native_dimension_deltas(
    source_signals: dict[str, dict[str, Any]],
    candidate_signals: dict[str, dict[str, Any]],
    target_case_ids: set[str],
) -> tuple[dict[str, dict[str, float]], float | None]:
    by_case: dict[str, dict[str, float]] = {}
    all_deltas: list[float] = []
    for case_id in sorted(target_case_ids):
        source_dimensions = source_signals.get(case_id, {}).get("dimensions", {})
        candidate_dimensions = candidate_signals.get(case_id, {}).get("dimensions", {})
        if not isinstance(source_dimensions, dict) or not isinstance(candidate_dimensions, dict):
            continue
        case_deltas: dict[str, float] = {}
        for name in sorted(set(source_dimensions) & set(candidate_dimensions)):
            source_score = _number(source_dimensions.get(name))
            candidate_score = _number(candidate_dimensions.get(name))
            if source_score is None or candidate_score is None:
                continue
            delta = candidate_score - source_score
            case_deltas[str(name)] = delta
            all_deltas.append(delta)
        if case_deltas:
            by_case[case_id] = case_deltas
    aggregate = sum(all_deltas) / len(all_deltas) if all_deltas else None
    return by_case, aggregate


def _eval_official_metrics(eval_ref_path: str | Path) -> dict[str, Any]:
    payload = _read_yaml(eval_ref_path)
    metrics = payload.get("official_metrics", {})
    return dict(metrics) if isinstance(metrics, dict) else {}


def _epoch_full_source_eval_ref(
    state: dict[str, Any],
    *,
    epoch: int,
    expected_case_ids: set[str],
) -> str:
    """Find an epoch source evaluation that covers the full frozen suite."""
    for key, record in state.get("completed_batches", {}).items():
        if not key.startswith(f"epoch_{epoch:03d}:") or not isinstance(record, dict):
            continue
        candidate = str(record.get("source_eval_ref_path", "") or "")
        if candidate and set(_eval_case_scores(candidate)) == expected_case_ids:
            return candidate
    return ""


def _eval_verifier_statuses(
    eval_ref_path: str | Path,
) -> dict[str, dict[str, Any]]:
    """Read dataset-neutral per-requirement outcomes without score collapse."""
    payload = _read_yaml(eval_ref_path)
    statuses: dict[str, dict[str, Any]] = {}
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "")
        if not case_id:
            continue
        result = _read_json(str(case.get("result_path", "") or ""))
        evaluation = result.get("evaluation", {})
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        metadata = evaluation.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        requirements = evaluation_requirement_results(metadata, case_id=case_id)
        instance_report = metadata.get("instance_report", {})
        instance_report = instance_report if isinstance(instance_report, dict) else {}
        report = instance_report.get(case_id, {})
        if not isinstance(report, dict) and len(instance_report) == 1:
            report = next(iter(instance_report.values()))
        report = report if isinstance(report, dict) else {}
        normalized: dict[str, Any] = {
            "patch_successfully_applied": report.get("patch_successfully_applied"),
            "resolved": report.get("resolved"),
            "empty_patch": metadata.get("empty_patch"),
            "requirement_results": requirements,
        }
        statuses[case_id] = normalized
    return statuses


def _verifier_deltas_by_case(
    source_eval_ref: str | Path,
    candidate_eval_ref: str | Path,
    target_case_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Compare paired official test operations for each target case."""
    source = _eval_verifier_statuses(source_eval_ref)
    candidate = _eval_verifier_statuses(candidate_eval_ref)
    deltas: dict[str, dict[str, Any]] = {}
    for case_id in sorted(target_case_ids):
        source_status = source.get(case_id, {})
        candidate_status = candidate.get(case_id, {})
        source_requirements = _requirement_result_index(source_status.get("requirement_results"))
        candidate_requirements = _requirement_result_index(candidate_status.get("requirement_results"))
        source_passed = {key for key, item in source_requirements.items() if item.get("passed") is True}
        candidate_passed = {key for key, item in candidate_requirements.items() if item.get("passed") is True}
        candidate_failed = {key for key, item in candidate_requirements.items() if item.get("passed") is False}
        newly_passed_keys = candidate_passed - source_passed
        regressed_keys = source_passed & candidate_failed

        source_f2p_success = _requirement_ids(source_passed, group=FAIL_TO_PASS_GROUP)
        candidate_f2p_success = _requirement_ids(candidate_passed, group=FAIL_TO_PASS_GROUP)
        candidate_f2p_failure = _requirement_ids(candidate_failed, group=FAIL_TO_PASS_GROUP)
        newly_passed = sorted(candidate_f2p_success - source_f2p_success)
        regressed_f2p = sorted(source_f2p_success - candidate_f2p_success)
        source_p2p_success = _requirement_ids(source_passed, group=PASS_TO_PASS_GROUP)
        candidate_p2p_failure = _requirement_ids(candidate_failed, group=PASS_TO_PASS_GROUP)
        regressed_p2p = sorted(source_p2p_success & candidate_p2p_failure)
        source_atomic_success = _requirement_ids(source_passed, group=ATOMIC_CHECK_GROUP)
        candidate_atomic_success = _requirement_ids(candidate_passed, group=ATOMIC_CHECK_GROUP)
        candidate_atomic_failure = _requirement_ids(candidate_failed, group=ATOMIC_CHECK_GROUP)
        newly_passed_atomic = sorted(candidate_atomic_success - source_atomic_success)
        regressed_atomic = sorted(source_atomic_success - candidate_atomic_success)
        newly_passed_requirements = _requirement_snapshots(candidate_requirements, newly_passed_keys)
        remaining_failed_requirements = _requirement_snapshots(candidate_requirements, candidate_failed)
        regressed_requirements = _requirement_snapshots(candidate_requirements, regressed_keys)
        deltas[case_id] = {
            "source_requirement_results": list(source_requirements.values()),
            "candidate_requirement_results": list(candidate_requirements.values()),
            "newly_passed_requirements": newly_passed_requirements,
            "remaining_failed_requirements": remaining_failed_requirements,
            "regressed_requirements": regressed_requirements,
            "source_fail_to_pass_success": sorted(source_f2p_success),
            "candidate_fail_to_pass_success": sorted(candidate_f2p_success),
            "newly_passed_fail_to_pass": newly_passed,
            "remaining_failed_fail_to_pass": sorted(candidate_f2p_failure),
            "regressed_fail_to_pass": regressed_f2p,
            "regressed_pass_to_pass": regressed_p2p,
            "source_passed_atomic_checks": sorted(source_atomic_success),
            "candidate_passed_atomic_checks": sorted(candidate_atomic_success),
            "newly_passed_atomic_checks": newly_passed_atomic,
            "remaining_failed_atomic_checks": sorted(candidate_atomic_failure),
            "regressed_atomic_checks": regressed_atomic,
            "partial_progress": bool(
                newly_passed_requirements and remaining_failed_requirements and not regressed_requirements
            ),
            "source_patch_successfully_applied": source_status.get("patch_successfully_applied"),
            "candidate_patch_successfully_applied": candidate_status.get("patch_successfully_applied"),
            "candidate_empty_patch": candidate_status.get("empty_patch"),
        }
    return deltas


def _requirement_result_index(value: Any) -> dict[tuple[str, str], dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, dict):
            continue
        requirement_id = str(raw.get("requirement_id", "") or "").strip()
        group = str(raw.get("group", "requirement") or "requirement").strip()
        if requirement_id:
            index[(group, requirement_id)] = dict(raw)
    return index


def _requirement_ids(keys: set[tuple[str, str]], *, group: str) -> set[str]:
    return {requirement_id for result_group, requirement_id in keys if result_group == group}


def _requirement_snapshots(
    index: dict[tuple[str, str], dict[str, Any]],
    keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    return [dict(index[key]) for key in sorted(keys) if key in index]


def _candidate_patch_excerpts_by_case(
    eval_ref_path: str | Path,
    target_case_ids: set[str],
) -> dict[str, str]:
    payload = _read_yaml(eval_ref_path)
    excerpts: dict[str, str] = {}
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "")
        if case_id not in target_case_ids:
            continue
        result = _read_json(str(case.get("result_path", "") or ""))
        evaluation = result.get("evaluation", {})
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        metadata = evaluation.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        patch_path = Path(str(metadata.get("model_patch_path", "") or ""))
        if not patch_path.is_file():
            excerpts[case_id] = ""
            continue
        try:
            patch = patch_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            patch = ""
        excerpts[case_id] = _bounded_candidate_text(patch)
    return excerpts


def _candidate_intervention_excerpts_by_case(
    capabilities: list[dict[str, Any]],
    target_case_ids: set[str],
) -> dict[str, str]:
    """Return the actual Harness mutations used in a paired evaluation.

    ``model_patch_path`` describes a task-agent workspace patch and is commonly
    absent for document or prompt-driven tasks.  Candidate feedback instead
    needs the Harness intervention that was under test.  Optimizers publish that
    immutable mutation on each capability before evaluation, so use it as the
    cross-domain source of truth.
    """
    excerpts: dict[str, list[str]] = {case_id: [] for case_id in target_case_ids}
    for capability in capabilities:
        if not isinstance(capability, dict):
            continue
        intervention = str(capability.get("intervention", "") or "").strip()
        if not intervention:
            continue
        capability_case_ids = {str(case_id) for case_id in capability.get("target_case_ids", []) if str(case_id)}
        applicable_case_ids = (capability_case_ids or target_case_ids) & target_case_ids
        label_parts = [
            str(capability.get("action_group", "") or ""),
            str(capability.get("target_path", "") or ""),
        ]
        label = ":".join(filter(None, label_parts))
        block = f"[{label}]\n{intervention}" if label else intervention
        for case_id in applicable_case_ids:
            excerpts.setdefault(case_id, []).append(block)
    return {case_id: _bounded_candidate_text("\n\n".join(blocks)) for case_id, blocks in excerpts.items() if blocks}


def _bounded_candidate_text(value: str, limit: int = 6000) -> str:
    if len(value) <= limit:
        return value
    half = limit // 2
    return f"{value[:half]}\n... candidate patch omitted ...\n{value[-half:]}"


def _average_case_scores(scores: dict[str, float], case_ids: set[str]) -> float:
    values = [scores[case_id] for case_id in case_ids if case_id in scores]
    return sum(values) / len(values) if values else 0.0


def _capability_target_case_ids(
    capabilities: list[dict[str, Any]],
    *,
    fallback_target_case_ids: set[str],
) -> set[str]:
    target_case_ids: set[str] = set()
    for capability in capabilities:
        if capability.get("operation") not in {"add", "modify"}:
            continue
        for case_id in capability.get("target_case_ids") or []:
            if str(case_id):
                target_case_ids.add(str(case_id))
    return target_case_ids or set(fallback_target_case_ids)


def _eval_ref_complete(path: Path) -> bool:
    if not path.is_file():
        return False
    payload = _read_yaml(path)
    cases = payload.get("cases", [])
    return bool(cases) and all(
        isinstance(case, dict) and Path(str(case.get("result_path", ""))).is_file() for case in cases
    )


def _eval_has_errors(eval_ref_path: str) -> bool:
    return bool(_error_case_ids(eval_ref_path))


def _error_case_ids(eval_ref_path: str | Path) -> set[str]:
    payload = _read_yaml(eval_ref_path)
    error_case_ids: set[str] = set()
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "")
        if case_id and str(case.get("status", "")).lower() == "error":
            error_case_ids.add(case_id)
    return error_case_ids


def _skipped_case_ids(eval_ref_path: str | Path) -> set[str]:
    payload = _read_yaml(eval_ref_path)
    skipped_case_ids: set[str] = set()
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "")
        metadata = case.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        skipped = str(case.get("status", "")).lower() == "skipped" or metadata.get("infrastructure_skip") is True
        if case_id and skipped:
            skipped_case_ids.add(case_id)
    return skipped_case_ids


def _invoked_tool_names(eval_ref_path: str) -> set[str]:
    invoked_names: set[str] = set()
    for names in _invoked_tool_names_by_case(eval_ref_path).values():
        invoked_names.update(names)
    return invoked_names


def _invoked_tool_names_by_case(eval_ref_path: str) -> dict[str, set[str]]:
    names_by_case: dict[str, set[str]] = {}
    payload = _read_yaml(eval_ref_path)
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "")
        if not case_id:
            continue
        names = names_by_case.setdefault(case_id, set())
        if _task_start_triggered_skill_names(case):
            names.add("skill_tool")
        for trace in _case_usage_traces(case):
            _collect_tool_names(trace, names)
            _collect_trajectory_dir_usage(trace, tool_names=names)
    return names_by_case


def _invoked_skill_names(eval_ref_path: str) -> set[str]:
    invoked_names: set[str] = set()
    for names in _invoked_skill_names_by_case(eval_ref_path).values():
        invoked_names.update(names)
    return invoked_names


def _invoked_skill_names_by_case(eval_ref_path: str) -> dict[str, set[str]]:
    names_by_case: dict[str, set[str]] = {}
    payload = _read_yaml(eval_ref_path)
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "")
        if not case_id:
            continue
        names = names_by_case.setdefault(case_id, set())
        names.update(_task_start_triggered_skill_names(case))
        for trace in _case_usage_traces(case):
            _collect_skill_names(trace, names)
            _collect_trajectory_dir_usage(trace, skill_names=names)
    return names_by_case


def _pre_edit_invoked_names_by_case(
    eval_ref_path: str,
    *,
    action_group: str,
) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Return capability completions that preceded the first persistent edit."""
    names_by_case: dict[str, set[str]] = {}
    first_edit_steps_by_case: dict[str, int] = {}
    payload = _read_yaml(eval_ref_path)
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "")
        if not case_id:
            continue
        names = names_by_case.setdefault(case_id, set())
        triggered_skills = _task_start_triggered_skill_names(case)
        if action_group == "skill":
            names.update(triggered_skills)
        elif triggered_skills:
            names.add("skill_tool")
        kwargs = {
            "tool_names": names if action_group == "tool" else None,
            "skill_names": names if action_group == "skill" else None,
        }
        edit_steps: list[int] = []
        for trace in _case_usage_traces(case):
            step = collect_pre_edit_successful_usage(trace, **kwargs)
            if step is not None:
                edit_steps.append(step)
            trajectory_dir = Path(str(trace.get("trajectory_dir", "") or ""))
            if trajectory_dir.is_dir():
                for member_trace_path in trajectory_dir.glob("*.jsonl"):
                    step = collect_jsonl_pre_edit_successful_usage(
                        member_trace_path,
                        **kwargs,
                    )
                    if step is not None:
                        edit_steps.append(step)
        if edit_steps:
            first_edit_steps_by_case[case_id] = min(edit_steps)
    return names_by_case, first_edit_steps_by_case


def _case_usage_traces(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Load the adapter trace plus any structured normalized trace it references."""
    trace = _read_json(str(case.get("trace_path", "") or ""))
    traces = [trace]
    behavior = trace.get("behavior_trace", {}) if isinstance(trace.get("behavior_trace"), dict) else {}
    normalized_path = str(behavior.get("normalized_trace_path", "") or "")
    if normalized_path:
        normalized = _read_json(normalized_path)
        if normalized and normalized != trace:
            traces.append(normalized)
    return traces


def _task_start_triggered_skill_names(case: dict[str, Any]) -> set[str]:
    """Read successful natural task-start Skill delivery from case metadata."""
    result = _read_json(str(case.get("result_path", "") or ""))
    execution = result.get("metadata", {}).get("execution", {}) if isinstance(result.get("metadata"), dict) else {}
    triggers = execution.get("skill_triggers", []) if isinstance(execution, dict) else []
    names: set[str] = set()
    for trigger in triggers if isinstance(triggers, list) else []:
        if not isinstance(trigger, dict) or trigger.get("delivered") is not True:
            continue
        name = str(trigger.get("selected_skill_name", "") or "").strip()
        if name:
            names.add(name)
    return names


def _nonpassing_case_ids(eval_ref_path: str) -> set[str]:
    payload = _read_yaml(eval_ref_path)
    case_ids: set[str] = set()
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", "") or "")
        score = _number(case.get("score"))
        status = str(case.get("status", "") or "").lower()
        metadata = case.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        if status == "skipped" or metadata.get("infrastructure_skip") is True:
            continue
        explicit_passed = _eval_case_explicit_passed(case)
        status_failed = status in {"failed", "error"}
        score_failed = explicit_passed is False or (explicit_passed is None and score is not None and score < 1.0)
        if case_id and (status_failed or score_failed):
            case_ids.add(case_id)
    return case_ids


def _eval_case_explicit_passed(case: dict[str, Any]) -> bool | None:
    metadata = case.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    value = metadata.get("evaluation_passed")
    return value if isinstance(value, bool) else None


def _classify_gate_failure(
    *,
    accepted: bool,
    reason: str,
    target_case_ids: set[str],
    candidate_case_scores: dict[str, float],
    first_edit_steps_by_case: dict[str, int],
    missing_skill_invocations: list[dict[str, str]],
    verifier_deltas_by_case: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Classify why a candidate failed so the next surface can change."""
    if accepted:
        return ""
    if "inconclusive" in reason or "error_cases" in reason:
        return "infrastructure_or_evidence_failure"
    if missing_skill_invocations:
        return "natural_skill_activation_failure"
    if any(
        delta.get("newly_passed_requirements")
        or delta.get("newly_passed_fail_to_pass")
        or delta.get("newly_passed_atomic_checks")
        for delta in (verifier_deltas_by_case or {}).values()
    ):
        return "partial_contract_progress"

    failed_targets = {case_id for case_id in target_case_ids if candidate_case_scores.get(case_id, 0.0) < 1.0}
    if failed_targets and failed_targets - set(first_edit_steps_by_case):
        return "execution_convergence_failure"
    if failed_targets:
        return "semantic_non_reproduction"
    if "retention" in reason:
        return "regression_or_retention_failure"
    if "not_invoked" in reason or "capability_not_invoked" in reason:
        return "capability_activation_failure"
    return "score_non_improvement"


def _classify_replay_outcome(
    gate: dict[str, Any],
    eval_ref_path: str,
) -> dict[str, Any]:
    """Compare a provisional target with the epoch replay trajectory."""
    target_case_ids = {str(case_id) for case_id in gate.get("target_case_ids", []) if str(case_id)}
    scores = _eval_case_scores(eval_ref_path)
    failed_target_case_ids = sorted(case_id for case_id in target_case_ids if scores.get(case_id, 0.0) < 1.0)
    _, first_edit_steps = _pre_edit_invoked_names_by_case(
        eval_ref_path,
        action_group="skill",
    )
    if set(failed_target_case_ids) - set(first_edit_steps):
        failure_class = "execution_convergence_failure"
    elif failed_target_case_ids:
        failure_class = "semantic_non_reproduction"
    else:
        failure_class = "score_non_improvement"
    return {
        "status": "failed",
        "eval_ref_path": eval_ref_path,
        "failed_target_case_ids": failed_target_case_ids,
        "first_persistent_edit_step_by_case": first_edit_steps,
        "failure_class": failure_class,
    }


def _select_gate_from_epoch_checkpoint(
    gate: dict[str, Any],
    *,
    full_eval_ref: str,
    error_case_ids: set[str],
    machine_evidence_case_ids: set[str],
) -> dict[str, Any]:
    """Select one provisional change from the single epoch-wide replay.

    The epoch replay evaluates the cumulative Harness once. Selection is then
    candidate-local: a change survives only when its own targets still pass and
    its runtime capability was actually used where applicable. Unrelated case
    outcomes remain audit evidence; they cannot establish that this candidate
    caused a regression. No post-pruning replay is run.
    """
    target_case_ids = {str(case_id) for case_id in gate.get("target_case_ids", []) if str(case_id)}
    inconclusive_target_case_ids = sorted(target_case_ids & (error_case_ids | machine_evidence_case_ids))
    if inconclusive_target_case_ids:
        return {
            "retained": False,
            "reason": "candidate_target_inconclusive_at_epoch_checkpoint",
            "failure_class": "infrastructure_or_evidence_failure",
            "failed_target_case_ids": [],
            "inconclusive_target_case_ids": inconclusive_target_case_ids,
            "missing_runtime_invocations": [],
            "correlated_regression_case_ids": [],
        }
    scores = _eval_case_scores(full_eval_ref)
    failed_target_case_ids = sorted(case_id for case_id in target_case_ids if scores.get(case_id, 0.0) < 1.0)
    if failed_target_case_ids:
        replay = _classify_replay_outcome(gate, full_eval_ref)
        return {
            "retained": False,
            "reason": "candidate_failed_target_replay_checkpoint",
            "failure_class": replay["failure_class"],
            "failed_target_case_ids": failed_target_case_ids,
            "missing_runtime_invocations": [],
            "correlated_regression_case_ids": [],
        }

    invoked_by_group = {
        "skill": _invoked_skill_names_by_case(full_eval_ref),
        "tool": _invoked_tool_names_by_case(full_eval_ref),
    }
    missing_runtime_invocations: list[dict[str, str]] = []
    for capability in gate.get("capabilities", []):
        if not isinstance(capability, dict):
            continue
        action_group = str(capability.get("action_group", "") or "")
        runtime_name = str(capability.get("runtime_name", "") or "")
        capability_targets = {
            str(case_id) for case_id in capability.get("target_case_ids", []) if str(case_id)
        } or target_case_ids
        if action_group == "prompt":
            continue
        if action_group not in invoked_by_group or not runtime_name:
            continue
        names_match = _skill_names_match if action_group == "skill" else _tool_names_match
        invoked_names_by_case = invoked_by_group[action_group]
        for case_id in sorted(capability_targets):
            if not any(names_match(runtime_name, invoked) for invoked in invoked_names_by_case.get(case_id, set())):
                missing_runtime_invocations.append(
                    {
                        "runtime_name": runtime_name,
                        "target_case_id": case_id,
                    }
                )
    if missing_runtime_invocations:
        return {
            "retained": False,
            "reason": "candidate_capability_not_used_at_epoch_checkpoint",
            "failure_class": "capability_activation_failure",
            "failed_target_case_ids": [],
            "missing_runtime_invocations": missing_runtime_invocations,
            "correlated_regression_case_ids": [],
        }
    return {
        "retained": True,
        "reason": "candidate_effect_retained_after_epoch_checkpoint",
        "failure_class": "",
        "failed_target_case_ids": [],
        "missing_runtime_invocations": [],
        "correlated_regression_case_ids": [],
    }


def _reject_mixed_opaque_snapshot_selection(
    gates: list[dict[str, Any]],
    selections: list[dict[str, Any]],
) -> bool:
    """Reject an opaque epoch when only some cumulative snapshots survive."""
    if len(gates) != len(selections):
        raise ValueError("opaque snapshot gates and selections must be paired")
    opaque = any(str(gate.get("composition_mode", "") or "") == "opaque_snapshot" for gate in gates)
    mixed = bool(
        opaque
        and any(bool(selection.get("retained")) for selection in selections)
        and any(not bool(selection.get("retained")) for selection in selections)
    )
    if not mixed:
        return False
    # PolicyHarness candidates are cumulative filesystem snapshots. Removing
    # one action while keeping a later snapshot would retain rejected bytes.
    # Reject the epoch atomically until an opaque composer/replay exists.
    for selection in selections:
        selection.update(
            {
                "retained": False,
                "reason": "epoch_opaque_snapshot_partial_retention_unsupported",
                "failure_class": "regression_or_retention_failure",
            }
        )
    return True


def _materialize_checkpoint_filtered_harness(
    *,
    output_dir: Path,
    epoch: int,
    base_harness_refs_path: str,
    replayed_harness_refs_path: str,
    retained_gates: list[dict[str, Any]],
    removed_gates: list[dict[str, Any]],
    full_eval_ref_path: str,
) -> str:
    """Compose retained changes onto the epoch's safe starting Harness."""
    base_refs = Path(base_harness_refs_path).expanduser().resolve()
    replayed_refs = Path(replayed_harness_refs_path).expanduser().resolve()
    base_payload = _read_yaml(base_refs)
    base_roles = base_payload.get("harness_refs", {})
    if not isinstance(base_roles, dict) or not base_roles:
        raise RuntimeError("Cannot filter epoch checkpoint: base refs contain no harness_refs")

    selection_root = output_dir / "epoch_selections" / f"e{epoch:03d}"
    staging_root = selection_root.with_name(f"{selection_root.name}.filter_tmp")
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True, exist_ok=True)
    staged_roles: dict[str, Path] = {}
    try:
        for role, raw_source in sorted(base_roles.items()):
            source = Path(str(raw_source)).expanduser().resolve()
            if not source.is_dir():
                raise RuntimeError(
                    f"Cannot filter epoch checkpoint role '{role}': source package does not exist: {source}"
                )
            role_dir = staging_root / "harnesses" / _checkpoint_role_dir_name(str(role))
            shutil.copytree(source, role_dir)
            staged_roles[str(role)] = role_dir

        retained_records: list[dict[str, str]] = []
        for gate in retained_gates:
            candidate_roles = _checkpoint_harness_role_paths(str(gate.get("candidate_harness_refs_path", "") or ""))
            for capability in gate.get("capabilities", []):
                if not isinstance(capability, dict):
                    continue
                role = str(capability.get("role", "") or "")
                if role not in staged_roles and len(staged_roles) == 1:
                    role = next(iter(staged_roles))
                target_rel = _checkpoint_relative_path(str(capability.get("target_path", "") or ""))
                if not role or role not in staged_roles or not target_rel:
                    raise RuntimeError(
                        "Cannot filter epoch checkpoint capability without a "
                        f"resolvable role and target path: {capability}"
                    )
                operation = str(capability.get("operation", "") or "")
                action_group = str(capability.get("action_group", "") or "")
                runtime_name = str(capability.get("runtime_name", "") or "")
                candidate_role = candidate_roles.get(role)
                if candidate_role is None and len(candidate_roles) == 1:
                    candidate_role = next(iter(candidate_roles.values()))
                if operation in {"add", "modify"}:
                    if candidate_role is None:
                        raise RuntimeError(
                            "Cannot compose retained capability without its "
                            f"candidate role package: {role}:{target_rel}"
                        )
                    _checkpoint_copy_capability(
                        staged_roles[role],
                        candidate_role,
                        action_group=action_group,
                        target_rel=target_rel,
                        runtime_name=runtime_name,
                    )
                elif operation == "remove":
                    _checkpoint_remove_added_capability(
                        staged_roles[role],
                        action_group=action_group,
                        target_rel=target_rel,
                        runtime_name=runtime_name,
                    )
                else:
                    raise RuntimeError(
                        "Checkpoint filtering supports retained add/modify/remove "
                        f"capabilities, got {action_group}:{operation}"
                    )
                retained_records.append(
                    {
                        "action_id": str(capability.get("action_id", "") or ""),
                        "role": role,
                        "action_group": action_group,
                        "operation": operation,
                        "target_path": target_rel,
                    }
                )

        if selection_root.exists():
            shutil.rmtree(selection_root)
        staging_root.replace(selection_root)
    except Exception:
        if staging_root.exists():
            shutil.rmtree(staging_root)
        raise

    removed_records: list[dict[str, str]] = []
    for gate in removed_gates:
        for capability in gate.get("capabilities", []):
            if not isinstance(capability, dict):
                continue
            removed_records.append(
                {
                    "action_id": str(capability.get("action_id", "") or ""),
                    "role": str(capability.get("role", "") or ""),
                    "action_group": str(capability.get("action_group", "") or ""),
                    "operation": str(capability.get("operation", "") or ""),
                    "target_path": _checkpoint_relative_path(str(capability.get("target_path", "") or "")),
                }
            )
    selected_roles = {role: str(selection_root / "harnesses" / path.name) for role, path in staged_roles.items()}
    selected_payload = dict(base_payload)
    role_results = {
        role: {
            "status": "checkpoint_filtered",
            "before_harness_ref_path": str(base_roles[role]),
            "after_harness_ref_path": selected_roles[role],
        }
        for role in sorted(selected_roles)
    }
    role_identities: list[dict[str, Any]] = []
    for raw_role in base_payload.get("roles", []):
        if not isinstance(raw_role, dict):
            continue
        identity = dict(raw_role)
        role_name = str(identity.get("member_name") or identity.get("role") or "")
        if role_name in selected_roles:
            identity["harness_ref_path"] = selected_roles[role_name]
        role_identities.append(identity)
    selected_payload.update(
        {
            "source_harness_refs_path": str(base_refs),
            "harness_refs": selected_roles,
            "promotion_status": "checkpoint_filtered",
            "role_results": role_results,
            "checkpoint_filter": {
                "full_eval_ref_path": full_eval_ref_path,
                "replayed_harness_refs_path": str(replayed_refs),
                "post_checkpoint_replay_performed": False,
                "retained_action_ids": _capability_action_ids(retained_gates),
                "retained_actions": retained_records,
                "removed_actions": removed_records,
            },
        }
    )
    if role_identities:
        selected_payload["roles"] = role_identities
    selected_ref_path = selection_root / "harness_refs.yaml"
    _write_yaml_atomic(selected_ref_path, selected_payload)
    _validate_single_harness_refs(str(selected_ref_path))
    return str(selected_ref_path)


def _checkpoint_harness_role_paths(refs_path: str) -> dict[str, Path]:
    payload = _read_yaml(refs_path)
    refs = payload.get("harness_refs", {})
    if not isinstance(refs, dict):
        return {}
    return {str(role): Path(str(path)).expanduser().resolve() for role, path in refs.items() if str(role) and str(path)}


def _checkpoint_role_dir_name(role: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "_" for char in role).strip("_")
    digest = hashlib.sha256(role.encode("utf-8")).hexdigest()[:8]
    return f"{normalized or 'role'}_{digest}"


def _checkpoint_relative_path(raw_path: str) -> str:
    normalized = raw_path.strip().replace("\\", "/")
    path = Path(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        return ""
    return Path(*path.parts).as_posix()


def _checkpoint_remove_added_capability(
    harness_root: Path,
    *,
    action_group: str,
    target_rel: str,
    runtime_name: str,
) -> None:
    if action_group == "skill":
        parts = Path(target_rel).parts
        skill_root = Path(parts[0], parts[1]).as_posix() if len(parts) >= 2 and parts[0] == "skills" else target_rel
        _checkpoint_remove_path(harness_root, skill_root)
        _checkpoint_remove_manifest_entries(
            harness_root / "skills" / "skills.yaml",
            list_key="skills",
            target_rel=skill_root,
            runtime_name=runtime_name,
            exact_only=True,
        )
        return
    if action_group == "prompt":
        _checkpoint_remove_path(harness_root, target_rel)
        _checkpoint_remove_prompt_entry(
            harness_root / "prompt_sections" / "sections.yaml",
            target_rel=target_rel,
            section_name=runtime_name,
        )
        return
    if action_group == "tool":
        if target_rel == "tools/tools.yaml":
            raise RuntimeError("Cannot remove an added tool whose target is only tools/tools.yaml")
        _checkpoint_remove_path(harness_root, target_rel)
        _checkpoint_remove_manifest_entries(
            harness_root / "tools" / "tools.yaml",
            list_key="tools",
            target_rel=target_rel,
            runtime_name=runtime_name,
        )
        return
    raise RuntimeError(f"Checkpoint filtering does not support action_group={action_group!r}")


def _checkpoint_copy_capability(
    harness_root: Path,
    candidate_harness_root: Path,
    *,
    action_group: str,
    target_rel: str,
    runtime_name: str,
) -> None:
    copy_rel = target_rel
    manifest_name = ""
    manifest_key = ""
    if action_group == "skill":
        parts = Path(target_rel).parts
        copy_rel = Path(parts[0], parts[1]).as_posix() if len(parts) >= 2 and parts[0] == "skills" else target_rel
        manifest_name = "skills/skills.yaml"
        manifest_key = "skills"
    elif action_group == "prompt":
        manifest_name = "prompt_sections/sections.yaml"
        manifest_key = "sections"
    elif action_group == "tool":
        manifest_name = "tools/tools.yaml"
        manifest_key = "tools"
    else:
        raise RuntimeError(f"Checkpoint filtering does not support action_group={action_group!r}")

    if target_rel != manifest_name:
        _checkpoint_copy_path(
            harness_root,
            candidate_harness_root,
            copy_rel,
        )
    _checkpoint_copy_manifest_entries(
        harness_root / manifest_name,
        candidate_harness_root / manifest_name,
        list_key=manifest_key,
        target_rel=copy_rel,
        runtime_name=runtime_name,
    )


def _checkpoint_copy_path(
    harness_root: Path,
    source_harness_root: Path,
    target_rel: str,
) -> None:
    destination = harness_root / target_rel
    source = source_harness_root / target_rel
    if not source.exists():
        raise RuntimeError(f"Cannot compose retained capability: source path is missing: {source}")
    _checkpoint_remove_path(harness_root, target_rel)
    if source.is_dir():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _checkpoint_copy_manifest_entries(
    destination: Path,
    source: Path,
    *,
    list_key: str,
    target_rel: str,
    runtime_name: str,
) -> None:
    if not source.is_file():
        return
    source_data = yaml.safe_load(source.read_text(encoding="utf-8"))
    source_entries = source_data.get(list_key, []) if isinstance(source_data, dict) else source_data
    if not isinstance(source_entries, list):
        source_entries = [source_entries]

    def matches(entry: Any) -> bool:
        if isinstance(entry, str):
            return _checkpoint_manifest_path_matches(
                entry,
                target_rel,
                exact_only=True,
            )
        if not isinstance(entry, dict):
            return False
        if runtime_name and str(entry.get("name", "") or "") == runtime_name:
            return True
        return any(
            _checkpoint_manifest_path_matches(
                str(entry.get(key, "") or ""),
                target_rel,
                exact_only=True,
            )
            for key in ("file", "file_path", "path")
        )

    selected = [entry for entry in source_entries if matches(entry)]
    if not selected:
        raise RuntimeError(
            "Cannot compose retained capability: candidate manifest has no "
            f"matching entry for {runtime_name or target_rel}"
        )

    if destination.is_file():
        destination_data = yaml.safe_load(destination.read_text(encoding="utf-8"))
    else:
        destination_data = {list_key: []}
    if isinstance(destination_data, dict):
        destination_entries = destination_data.get(list_key, [])
    elif isinstance(destination_data, list):
        destination_entries = destination_data
        destination_data = {list_key: destination_entries}
    else:
        destination_entries = []
        destination_data = {list_key: destination_entries}
    if not isinstance(destination_entries, list):
        destination_entries = [destination_entries]
    for entry in selected:
        if entry not in destination_entries:
            destination_entries.append(entry)
    destination_data[list_key] = destination_entries
    _write_yaml_atomic(destination, destination_data)


def _checkpoint_remove_path(harness_root: Path, target_rel: str) -> None:
    target = harness_root / target_rel
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def _checkpoint_remove_prompt_entry(
    manifest: Path,
    *,
    target_rel: str,
    section_name: str,
) -> None:
    if not manifest.is_file():
        return
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        key = "sections" if "sections" in data else "prompt_sections"
        entries = data.get(key, [])
    elif isinstance(data, list):
        key = "sections"
        entries = data
        data = {key: entries}
    else:
        return
    if not isinstance(entries, list):
        entries = [entries]
    kept = []
    for entry in entries:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        name_matches = section_name and str(entry.get("name", "") or "") == section_name
        path_matches = _checkpoint_manifest_path_matches(
            str(entry.get("file") or entry.get("path") or ""),
            target_rel,
            exact_only=True,
        )
        if not name_matches and not path_matches:
            kept.append(entry)
    data[key] = kept
    _write_yaml_atomic(manifest, data)


def _checkpoint_remove_manifest_entries(
    manifest: Path,
    *,
    list_key: str,
    target_rel: str,
    runtime_name: str,
    exact_only: bool = False,
) -> None:
    if not manifest.is_file():
        return
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        entries = data.get(list_key, [])
    elif isinstance(data, list):
        entries = data
        data = {list_key: entries}
    else:
        return
    if not isinstance(entries, list):
        entries = [entries]

    def matches(entry: Any) -> bool:
        if isinstance(entry, str):
            return _checkpoint_manifest_path_matches(
                entry,
                target_rel,
                exact_only=exact_only,
            )
        if not isinstance(entry, dict):
            return False
        if runtime_name and str(entry.get("name", "") or "") == runtime_name:
            return True
        return any(
            _checkpoint_manifest_path_matches(
                str(entry.get(key, "") or ""),
                target_rel,
                exact_only=exact_only,
            )
            for key in ("file", "file_path", "path")
        )

    data[list_key] = [entry for entry in entries if not matches(entry)]
    _write_yaml_atomic(manifest, data)


def _checkpoint_manifest_path_matches(
    raw_path: str,
    target_rel: str,
    *,
    exact_only: bool,
) -> bool:
    normalized = _checkpoint_relative_path(raw_path)
    target = _checkpoint_relative_path(target_rel)
    if not normalized or not target:
        return False
    if normalized == target:
        return True
    if exact_only:
        return False
    return normalized.startswith(f"{target}/") or target.startswith(f"{normalized}/")


def _missing_capability_invocations(
    capabilities: list[dict[str, Any]],
    *,
    action_group: str,
    invoked_names_by_case: dict[str, set[str]],
    fallback_target_case_ids: set[str],
    names_match: Any,
) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    for capability in capabilities:
        runtime_name = str(capability.get("runtime_name", "") or "")
        if (
            capability.get("action_group") != action_group
            or capability.get("operation") not in {"add", "modify"}
            or not runtime_name
        ):
            continue
        target_case_ids = {
            str(case_id) for case_id in capability.get("target_case_ids", []) if str(case_id)
        } or fallback_target_case_ids
        for case_id in sorted(target_case_ids):
            invoked_names = invoked_names_by_case.get(case_id, set())
            if not any(names_match(runtime_name, invoked) for invoked in invoked_names):
                missing.append(
                    {
                        "runtime_name": runtime_name,
                        "target_case_id": case_id,
                    }
                )
    return missing


def _missing_invocations_were_late(
    missing_invocations: list[dict[str, str]],
    invoked_names_by_case: dict[str, set[str]],
    *,
    names_match: Any,
) -> bool:
    return any(
        any(
            names_match(item["runtime_name"], invoked)
            for invoked in invoked_names_by_case.get(item["target_case_id"], set())
        )
        for item in missing_invocations
    )


def _collect_tool_names(value: Any, names: set[str]) -> None:
    collect_successful_tool_names(value, names)


def _collect_skill_names(value: Any, names: set[str]) -> None:
    collect_successful_skill_names(value, names)


def _collect_trajectory_dir_usage(
    trace: dict[str, Any],
    *,
    tool_names: set[str] | None = None,
    skill_names: set[str] | None = None,
) -> None:
    trajectory_dir = Path(str(trace.get("trajectory_dir", "") or ""))
    if not trajectory_dir.is_dir():
        return
    for member_trace_path in trajectory_dir.glob("*.jsonl"):
        collect_jsonl_successful_usage(
            member_trace_path,
            tool_names=tool_names,
            skill_names=skill_names,
        )


def _capability_runtime_name(action_group: str, target_path: str) -> str:
    if not target_path:
        return ""
    normalized = Path(target_path.replace("\\", "/"))
    if action_group == "skill" and normalized.name.lower() == "skill.md":
        return normalized.parent.name
    return normalized.stem


def _failed_machine_evidence(eval_ref_path: str) -> list[str]:
    failures: list[str] = []
    payload = _read_yaml(eval_ref_path)
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        result = _read_json(str(case.get("result_path", "") or ""))
        metadata = (result.get("evaluation") or {}).get("metadata") or {}
        evidence = metadata.get("artifact_runtime_evidence") or {}
        for observation in evidence.get("observations", []) if isinstance(evidence, dict) else []:
            if isinstance(observation, dict) and str(observation.get("status", "")).lower() in {"failed", "error"}:
                failures.append(f"{case.get('case_id', 'unknown')}:{observation.get('type', 'machine_evidence')}")
    return sorted(set(failures))


def _machine_evidence_case_ids(failures: list[str]) -> set[str]:
    return {failure.split(":", 1)[0] for failure in failures if failure.split(":", 1)[0]}


def _tool_names_match(planned: str, invoked: str) -> bool:
    def canonical(value: str) -> str:
        return value.strip().lower().replace("-", "_").removesuffix("_tool")

    return bool(canonical(planned)) and canonical(planned) == canonical(invoked)


def _skill_names_match(planned: str, invoked: str) -> bool:
    def canonical(value: str) -> str:
        return value.strip().lower().replace("-", "_")

    return bool(canonical(planned)) and canonical(planned) == canonical(invoked)


def _build_report(state: dict[str, Any], dataset: DatasetArtifact) -> dict[str, Any]:
    accepted = [gate for gate in state["candidate_gates"] if gate.get("accepted") and gate.get("status") == "accepted"]
    return {
        "mode": "single_harness_benchmark",
        "status": state["status"],
        "dataset_id": dataset.dataset_id,
        "dataset_cases": dataset.cases,
        "allowed_action_groups": state["allowed_action_groups"],
        "allowed_prompt_surfaces": state["allowed_prompt_surfaces"],
        "completed_batch_count": len(state["completed_batches"]),
        "candidate_count": len(state["candidate_gates"]),
        "accepted_candidate_count": len(accepted),
        "baseline_score": state.get("baseline_score"),
        "baseline_eval_ref_path": state.get("baseline_eval_ref_path", ""),
        "best_score": state.get("best_score"),
        "best_eval_ref_path": state.get("best_eval_ref_path", ""),
        "best_harness_refs_path": state.get("best_harness_refs_path"),
        "published_harness_refs_path": state.get("published_harness_refs_path"),
        "publication_status": state.get("publication_status", "not_published"),
        "current_harness_refs_path": state.get("current_harness_refs_path"),
        "working_harness_refs_path": state.get("working_harness_refs_path"),
        "retained_case_ids": state.get("retained_case_ids", []),
        "epoch_checkpoints": state["epoch_checkpoints"],
        "candidate_gates": state["candidate_gates"],
        "improvement_instances": state.get("improvement_instances", {}),
        "candidate_feedback_ledger_path": state.get("candidate_feedback_ledger_path", ""),
        "improver_policy": state.get("improver_policy", {}),
        "optimization_journal_path": state.get("optimization_journal_path", ""),
        "lever_scoreboard_path": state.get("lever_scoreboard_path", ""),
        "lever_scoreboard": state.get("lever_scoreboard", {}),
    }


def _ensure_final_publication(*, state: dict[str, Any], output_dir: Path) -> None:
    """Copy the best gated harness to the stable standalone publish location."""
    accepted_gates = [
        gate for gate in state.get("candidate_gates", []) if gate.get("accepted") and gate.get("status") == "accepted"
    ]
    if not accepted_gates:
        state["published_harness_refs_path"] = ""
        state["publication_status"] = "not_published_no_improvement"
        return

    existing_ref = Path(str(state.get("published_harness_refs_path", "") or ""))
    if existing_ref.is_file():
        _validate_single_harness_refs(str(existing_ref))
        state["publication_status"] = "published"
        return

    best_refs_path = Path(str(state["best_harness_refs_path"])).expanduser().resolve()
    best_payload = _read_yaml(best_refs_path)
    harness_refs = best_payload.get("harness_refs", {})
    if not isinstance(harness_refs, dict) or not harness_refs:
        raise RuntimeError("Cannot publish final single harness: best refs contain no harness_refs")

    member_output_root = output_dir / "member_optimizations"
    path_layout = MemberOptimizerPathLayout.from_output_root(member_output_root)
    published_refs: dict[str, str] = {}
    for role, raw_source in sorted(harness_refs.items()):
        source = Path(str(raw_source)).expanduser().resolve()
        if not source.is_dir():
            raise RuntimeError(
                f"Cannot publish final single harness role '{role}': source package does not exist: {source}"
            )
        path_layout.write_role_mapping(str(role))
        destination = path_layout.current_harness_dir(str(role))
        staging = destination.with_name(f"{destination.name}.publish_tmp")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(source, staging)
        if destination.exists():
            shutil.rmtree(destination)
        staging.replace(destination)
        published_refs[str(role)] = str(destination)

    published_payload = dict(best_payload)
    published_roles = sorted(published_refs)
    published_role_results = {
        role: {
            "status": "published",
            "before_harness_ref_path": str(harness_refs[role]),
            "after_harness_ref_path": published_refs[role],
        }
        for role in published_roles
    }
    role_identities = []
    for raw_role in best_payload.get("roles", []):
        if not isinstance(raw_role, dict):
            continue
        role_identity = dict(raw_role)
        role_name = str(role_identity.get("member_name") or role_identity.get("role") or "")
        if role_name in published_refs:
            role_identity["harness_ref_path"] = published_refs[role_name]
        role_identities.append(role_identity)
    published_payload.update(
        {
            "source_harness_refs_path": str(best_refs_path),
            "harness_refs": published_refs,
            "published_roles": published_roles,
            "candidate_ready_roles": published_roles,
            "staged_roles": published_roles,
            "verified_roles": published_roles,
            "promoted_roles": published_roles,
            "promotion_status": "published",
            "role_results": published_role_results,
            "last_attempt": {
                "published_roles": published_roles,
                "failed_roles": [],
                "skipped_roles": [],
                "role_results": published_role_results,
            },
            "published_from_harness_refs_path": str(best_refs_path),
            "published_best_score": state.get("best_score"),
        }
    )
    if role_identities:
        published_payload["roles"] = role_identities
    published_ref_path = member_output_root / "current_harness_refs.yaml"
    _write_yaml_atomic(published_ref_path, published_payload)
    _validate_single_harness_refs(str(published_ref_path))
    state["published_harness_refs_path"] = str(published_ref_path)
    state["publication_status"] = "published"


def _read_yaml(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser()
    if not target.is_file():
        return {}
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _read_json(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_yaml_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "IterativeSingleHarnessRequest",
    "IterativeSingleHarnessResult",
    "SingleHarnessIterativeOptimizationOrchestrator",
]
