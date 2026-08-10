# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Iterative benchmark optimization for one standalone Expert Harness."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.config import AutoCoordinatingHarnessConfig
from openjiuwen.rsi.data_loader import DataLoader
from openjiuwen.rsi.evaluation_result_analyzer import (
    EvaluationResultAnalyzer,
)
from openjiuwen.rsi.evaluator import TeamEvaluator
from openjiuwen.rsi.evaluator.trajectory_usage import (
    collect_jsonl_pre_edit_successful_usage,
    collect_jsonl_successful_usage,
    collect_pre_edit_successful_usage,
    collect_successful_skill_names,
    collect_successful_tool_names,
)
from openjiuwen.rsi.member_optimizer import MemberOptimizer
from openjiuwen.rsi.member_optimizer.hypothesis import (
    compile_optimization_hypotheses,
    load_optimization_hypotheses,
)
from openjiuwen.rsi.member_optimizer.path_layout import (
    MemberOptimizerPathLayout,
)
from openjiuwen.rsi.schema import (
    DatasetArtifact,
    EvaluationResultAnalysisInvocation,
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
            max_actions_per_plan=1,
        )
        self.config = replace(config, member_optimizer=restricted_member_config)
        self.evaluator = evaluator or TeamEvaluator(self.config.evaluator)
        self.analyzer = analyzer or EvaluationResultAnalyzer(self.config.evaluation_result_analyzer)
        self.member_optimizer = member_optimizer or MemberOptimizer(restricted_member_config)
        self.data_loader = data_loader or DataLoader(self.config.data_loader)

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
        )
        state = _load_or_create_state(
            state_path=state_path,
            resume=request.resume,
            fingerprint=fingerprint,
            source_harness_refs_path=source_refs,
            dataset=dataset,
        )
        if request.resume and state.get("status") == "completed":
            _ensure_final_publication(state=state, output_dir=output_dir)
            _write_yaml_atomic(state_path, state)
            _write_yaml_atomic(report_path, _build_report(state, dataset))
            return _result_from_state(state, state_path, report_path)

        all_cases = _load_cases(dataset.dataset_files)
        all_case_ids = {str(case.get("case_id", "") or "") for case in all_cases if str(case.get("case_id", "") or "")}
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
            batches = list(self.data_loader.load(str(dataset_dir), epoch=epoch))
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
                analysis_ref = await self._analyze(
                    eval_ref_path=source_eval_ref,
                    harness_refs_path=current_refs,
                    output_dir=batch_dir / "analysis",
                    prior_candidate_feedback=_prior_candidate_feedback(
                        state,
                        batch,
                    ),
                )
                hypotheses_ref = compile_optimization_hypotheses(
                    analysis_ref_path=analysis_ref,
                    cases=batch,
                    output_path=batch_dir / "analysis" / "optimization_hypotheses.yaml",
                )
                hypotheses = load_optimization_hypotheses(hypotheses_ref)
                issue_attempt_ids: list[str | None] = [
                    str(item.get("source_issue_id", "")) for item in hypotheses if str(item.get("source_issue_id", ""))
                ] or [None]
                issue_case_ids = _analysis_issue_case_ids(analysis_ref)
                batch_before_refs = current_refs
                attempt_records: list[dict[str, Any]] = []
                accepted_target_case_ids: set[str] = set()
                last_member_ref = ""
                last_gate: dict[str, Any] | None = None

                for attempt_index, issue_id in enumerate(issue_attempt_ids, start=1):
                    attempt_dir = batch_dir / "attempts" / f"a{attempt_index:03d}"
                    attempt_source_eval_ref = source_eval_ref
                    if attempt_index > 1:
                        attempt_source_eval_ref = await self._evaluate(
                            cases=batch,
                            harness_refs_path=current_refs,
                            output_dir=attempt_dir / "source",
                            dataset=dataset,
                        )
                    before_attempt_refs = current_refs
                    target_case_ids = issue_case_ids.get(issue_id or "", set())
                    source_case_scores = _eval_case_scores(attempt_source_eval_ref)
                    target_already_resolved = (
                        issue_id
                        and target_case_ids
                        and target_case_ids.issubset(source_case_scores)
                        and all(source_case_scores[case_id] >= 1.0 for case_id in target_case_ids)
                    )
                    if target_already_resolved:
                        reason = "issue_already_resolved_in_latest_source"
                        attempt_records.append(
                            {
                                "attempt_index": attempt_index,
                                "source_issue_id": issue_id,
                                "source_eval_ref_path": attempt_source_eval_ref,
                                "member_optimization_ref_path": "",
                                "before_harness_refs_path": before_attempt_refs,
                                "after_harness_refs_path": current_refs,
                                "candidate_gate_status": "skipped",
                                "candidate_gate_reason": reason,
                                "accepted_target_case_ids": [],
                            }
                        )
                        last_gate = {"status": "skipped", "reason": reason}
                        continue
                    rejected_capabilities = _rejected_capability_history(state["candidate_gates"])
                    member_ref = await self.member_optimizer.optimize(
                        eval_ref_path=attempt_source_eval_ref,
                        analysis_result_path=analysis_ref,
                        harness_refs_path=before_attempt_refs,
                        output_dir=str(output_dir / "member_optimizations"),
                        defer_publish=True,
                        rejected_capabilities=rejected_capabilities,
                        single_harness=True,
                        optimization_hypotheses_path=hypotheses_ref,
                        optimization_issue_ids=([issue_id] if issue_id else None),
                        optimization_experience={
                            "journal": list(state.get("optimization_journal", [])),
                            "lever_scoreboard": dict(state.get("lever_scoreboard", {})),
                        },
                    )
                    member_info = _read_yaml(member_ref)
                    candidate_refs = str(member_info.get("optimized_harness_refs_path", "") or before_attempt_refs)
                    capabilities = _candidate_capabilities(member_info)
                    gate = await self._candidate_gate(
                        cases=batch,
                        source_eval_ref=attempt_source_eval_ref,
                        analysis_ref=analysis_ref,
                        before_harness_refs_path=before_attempt_refs,
                        candidate_harness_refs_path=candidate_refs,
                        member_status=str(member_info.get("status", "") or ""),
                        capabilities=capabilities,
                        output_dir=attempt_dir / "candidate",
                        dataset=dataset,
                    )
                    primary_accepted = bool(gate["accepted"])
                    gate.update(
                        {
                            "epoch": epoch,
                            "batch_index": batch_index,
                            "batch_attempt_index": attempt_index,
                            "source_issue_id": issue_id or "",
                            "member_optimization_ref_path": member_ref,
                            "primary_gate_accepted": primary_accepted,
                            "primary_gate_reason": str(gate.get("reason", "")),
                            "evaluation_input_mode": "original_task",
                        }
                    )
                    if primary_accepted:
                        gate["status"] = "provisional"
                        gate["reason"] = "candidate_passed_batch_gate_pending_epoch_checkpoint"
                        current_refs = candidate_refs
                        accepted_target_case_ids.update(
                            str(case_id) for case_id in gate.get("target_case_ids", []) if str(case_id)
                        )
                        working_retained_case_ids.update(
                            str(case_id) for case_id in gate.get("target_case_ids", []) if str(case_id)
                        )
                    _persist_promotion(
                        member_ref,
                        (candidate_refs if candidate_refs != before_attempt_refs else ""),
                        gate,
                    )
                    state["candidate_gates"].append(gate)
                    _refresh_optimization_experience(state, output_dir)
                    attempt_records.append(
                        {
                            "attempt_index": attempt_index,
                            "source_issue_id": issue_id or "",
                            "source_eval_ref_path": attempt_source_eval_ref,
                            "member_optimization_ref_path": member_ref,
                            "before_harness_refs_path": before_attempt_refs,
                            "after_harness_refs_path": current_refs,
                            "candidate_gate_status": gate["status"],
                            "candidate_gate_reason": gate["reason"],
                            "accepted_target_case_ids": (
                                list(gate.get("target_case_ids", [])) if primary_accepted else []
                            ),
                        }
                    )
                    last_member_ref = member_ref
                    last_gate = gate

                batch_accepted = bool(accepted_target_case_ids)
                batch_gate_status = (
                    "provisional" if batch_accepted else str((last_gate or {}).get("status", "rejected"))
                )
                batch_gate_reason = (
                    "candidate_passed_batch_gate_pending_epoch_checkpoint"
                    if batch_accepted
                    else str((last_gate or {}).get("reason", ""))
                )
                state["working_harness_refs_path"] = current_refs
                state["completed_batches"][batch_key] = {
                    "epoch": epoch,
                    "batch_index": batch_index,
                    "source_eval_ref_path": source_eval_ref,
                    "analysis_ref_path": analysis_ref,
                    "optimization_hypotheses_path": hypotheses_ref,
                    "member_optimization_ref_path": last_member_ref,
                    "candidate_attempts": attempt_records,
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
            previous_best_case_scores = _eval_case_scores(previous_best_eval_ref) if previous_best_eval_ref else {}
            retained_case_ids = set(state.get("retained_case_ids", []))
            regressed_best_case_ids = []
            for case_id, previous_score in previous_best_case_scores.items():
                if case_id in retained_case_ids and full_case_scores.get(case_id, 0.0) < previous_score:
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
            if retained_gates and removed_gates:
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
                    "regressed_best_case_ids": regressed_best_case_ids,
                    "failed_retention_case_ids": failed_retention_case_ids,
                    "failed_case_ids": full_failed_case_ids,
                    "failed_target_case_ids": failed_target_case_ids,
                    "failed_machine_evidence": full_failed_machine_evidence,
                    "error_case_ids": sorted(full_error_case_ids),
                    "retained_candidate_action_ids": _capability_action_ids(retained_gates),
                    "removed_candidate_action_ids": _capability_action_ids(removed_gates),
                    "selected_harness_refs_path": (selected_refs if retained_gates else epoch_start_refs),
                    "post_checkpoint_replay_performed": False,
                }
            )
            state["epoch_checkpoints"].append(checkpoint)
            if retained_gates or (not epoch_provisional_gates and checkpoint_status == "verified"):
                current_refs = selected_refs
                state["best_score"] = full_score
                state["best_eval_ref_path"] = full_eval_ref
                state["best_harness_refs_path"] = current_refs
                state["retained_case_ids"] = sorted(full_passing_case_ids)
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
        output_dir: Path,
        dataset: DatasetArtifact,
    ) -> dict[str, Any]:
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
        if _eval_has_errors(source_eval_ref):
            return {
                "accepted": False,
                "status": "inconclusive",
                "reason": "source_gate_inconclusive_due_to_error_cases",
                **base,
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
            _capability_target_case_ids(
                capabilities,
                fallback_target_case_ids=fallback_target_case_ids,
            )
            & batch_case_ids
        )
        if not target_case_ids:
            target_case_ids = set(fallback_target_case_ids) & batch_case_ids
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
        candidate_eval_ref = await self._evaluate(
            cases=candidate_cases,
            harness_refs_path=candidate_harness_refs_path,
            output_dir=output_dir,
            dataset=dataset,
        )
        candidate_case_scores = _eval_case_scores(candidate_eval_ref)
        verifier_deltas_by_case = _verifier_deltas_by_case(
            paired_source_eval_ref,
            candidate_eval_ref,
            target_case_ids,
        )
        candidate_patch_excerpts_by_case = _candidate_patch_excerpts_by_case(
            candidate_eval_ref,
            target_case_ids,
        )
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
        invoked_tools = sorted({name for names in invoked_tools_by_case.values() for name in names})
        expected_skills = _expected_runtime_names(capabilities, action_group="skill")
        invoked_skills_by_case = _invoked_skill_names_by_case(candidate_eval_ref)
        pre_edit_skills_by_case, skill_first_edit_steps_by_case = _pre_edit_invoked_names_by_case(
            candidate_eval_ref, action_group="skill"
        )
        for case_id, step in skill_first_edit_steps_by_case.items():
            current = first_edit_steps_by_case.get(case_id)
            if current is None or (step is not None and step < current):
                first_edit_steps_by_case[case_id] = step
        invoked_skills = sorted({name for names in invoked_skills_by_case.values() for name in names})
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
            case_id
            for case_id, delta in verifier_deltas_by_case.items()
            if delta.get("newly_passed_fail_to_pass") or delta.get("newly_passed_atomic_checks")
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
        if missing_tools:
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
        elif _eval_has_errors(candidate_eval_ref):
            reason = "candidate_gate_inconclusive_due_to_error_cases"
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
        candidate_failure_diagnoses: dict[str, dict[str, Any]] = {}
        if not accepted and not _eval_has_errors(candidate_eval_ref):
            paired_feedback = {
                "by_case": {
                    case_id: [
                        {
                            "kind": "paired_source_candidate_delta",
                            "verifier_delta": dict(delta),
                            "candidate_patch_excerpt": str(candidate_patch_excerpts_by_case.get(case_id, "")),
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
            **base,
            "candidate_eval_ref_path": candidate_eval_ref,
            "candidate_score": candidate_score,
            "score_delta": score_delta,
            "target_case_ids": sorted(target_case_ids),
            "source_target_score": source_target_score,
            "candidate_target_score": candidate_target_score,
            "target_score_delta": target_score_delta,
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
    return cases


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
) -> dict[str, Any]:
    return {
        "optimization_chain_version": 10,
        "dataset_files": [str(Path(path).expanduser().resolve()) for path in request.dataset_files],
        "dataset_sha256": [_file_sha256(path) for path in request.dataset_files],
        "source_harness_refs_path": source_harness_refs_path,
    }


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().resolve().open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        if state.get("fingerprint") != fingerprint:
            raise ValueError("resume inputs do not match single-harness state")
        state.setdefault(
            "working_harness_refs_path",
            state.get("current_harness_refs_path", source_harness_refs_path),
        )
        state.setdefault("retained_case_ids", [])
        state.setdefault("best_eval_ref_path", "")
        state.setdefault("publication_status", "not_published")
        state.setdefault("optimization_journal", [])
        state.setdefault("lever_scoreboard", {})
        return state
    return {
        "version": 5,
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
        "retained_case_ids": [],
        "batch_plan_paths": {},
        "completed_batches": {},
        "candidate_gates": [],
        "epoch_checkpoints": [],
        "optimization_journal": [],
        "lever_scoreboard": {},
    }


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
                    "surface": str(capability.get("action_group", "") or ""),
                    "lever": str(decision.get("selected_lever", "unresolved") or "unresolved"),
                    "lever_decision": decision,
                    "target_case_ids": list(gate.get("target_case_ids", [])),
                    "source_target_score": _number(gate.get("source_target_score")),
                    "candidate_target_score": _number(gate.get("candidate_target_score")),
                    "target_score_delta": _number(gate.get("target_score_delta")),
                    "non_target_score_delta": _number(gate.get("non_target_score_delta")),
                    "status": str(gate.get("status", "") or ""),
                    "reason": str(gate.get("reason", "") or ""),
                    "failure_class": str(gate.get("failure_class", "") or ""),
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
        isinstance(delta, dict) and delta.get("newly_passed_fail_to_pass") for delta in verifier_deltas.values()
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
        capabilities.append(
            {
                "action_id": str(action.get("action_id", "") or ""),
                "role": str(action.get("role", "") or ""),
                "action_group": str(action.get("action_group", "") or ""),
                "operation": str(action.get("operation", "") or ""),
                "target_path": target_path,
                "runtime_name": _capability_runtime_name(
                    str(action.get("action_group", "") or ""),
                    target_path,
                ),
                "expected_effect": str(action.get("expected_effect", "") or ""),
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
) -> dict[str, dict[str, Any]]:
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
    compact: dict[str, dict[str, Any]] = {}
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
        ):
            if key in diagnosis:
                compact_diagnosis[key] = diagnosis.get(key)
        compact[case_id] = compact_diagnosis
    return compact


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
        deltas = deltas if isinstance(deltas, dict) else {}
        diagnoses = diagnoses if isinstance(diagnoses, dict) else {}
        patches = patches if isinstance(patches, dict) else {}
        for case_id in active_case_ids & set(deltas):
            by_case[case_id].append(
                {
                    "experiment_id": str(record.get("experiment_id", "") or ""),
                    "surface": str(record.get("surface", "") or ""),
                    "outcome": str(record.get("outcome", "") or ""),
                    "failure_class": str(record.get("failure_class", "") or ""),
                    "verifier_delta": dict(deltas.get(case_id, {})),
                    "candidate_patch_excerpt": str(patches.get(case_id, "") or ""),
                    "candidate_failure_diagnosis": dict(
                        diagnoses.get(case_id, {}) if isinstance(diagnoses.get(case_id), dict) else {}
                    ),
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
        return {case_id for issue_id in action_issue_ids for case_id in issue_case_ids.get(issue_id, set())}
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


def _eval_verifier_statuses(
    eval_ref_path: str | Path,
) -> dict[str, dict[str, Any]]:
    """Read official per-test outcomes without collapsing them to case score."""
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
        instance_report = metadata.get("instance_report", {})
        instance_report = instance_report if isinstance(instance_report, dict) else {}
        report = instance_report.get(case_id, {})
        if not isinstance(report, dict) and len(instance_report) == 1:
            report = next(iter(instance_report.values()))
        report = report if isinstance(report, dict) else {}
        tests_status = report.get("tests_status", {})
        tests_status = tests_status if isinstance(tests_status, dict) else {}
        normalized: dict[str, Any] = {
            "patch_successfully_applied": report.get("patch_successfully_applied"),
            "resolved": report.get("resolved"),
            "empty_patch": metadata.get("empty_patch"),
        }
        raw_atomic_checks = metadata.get("atomic_checks", [])
        atomic_passed: set[str] = set()
        atomic_failed: set[str] = set()
        if isinstance(raw_atomic_checks, list):
            for check in raw_atomic_checks:
                if not isinstance(check, dict):
                    continue
                name = str(check.get("name", "") or "").strip()
                if not name:
                    continue
                if check.get("passed") is True:
                    atomic_passed.add(name)
                else:
                    atomic_failed.add(name)
        normalized["atomic_checks"] = {
            "success": sorted(atomic_passed),
            "failure": sorted(atomic_failed),
        }
        for group in ("FAIL_TO_PASS", "PASS_TO_PASS"):
            group_status = tests_status.get(group, {})
            group_status = group_status if isinstance(group_status, dict) else {}
            normalized[group] = {
                "success": sorted({str(item) for item in group_status.get("success", []) or [] if str(item)}),
                "failure": sorted({str(item) for item in group_status.get("failure", []) or [] if str(item)}),
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
        source_f2p = source_status.get("FAIL_TO_PASS", {})
        candidate_f2p = candidate_status.get("FAIL_TO_PASS", {})
        source_p2p = source_status.get("PASS_TO_PASS", {})
        candidate_p2p = candidate_status.get("PASS_TO_PASS", {})
        source_f2p_success = set(source_f2p.get("success", []) or [])
        candidate_f2p_success = set(candidate_f2p.get("success", []) or [])
        candidate_f2p_failure = set(candidate_f2p.get("failure", []) or [])
        source_p2p_success = set(source_p2p.get("success", []) or [])
        candidate_p2p_failure = set(candidate_p2p.get("failure", []) or [])
        newly_passed = sorted(candidate_f2p_success - source_f2p_success)
        regressed_f2p = sorted(source_f2p_success - candidate_f2p_success)
        regressed_p2p = sorted(source_p2p_success & candidate_p2p_failure)
        source_atomic = source_status.get("atomic_checks", {})
        candidate_atomic = candidate_status.get("atomic_checks", {})
        source_atomic_success = set(source_atomic.get("success", []) or [])
        candidate_atomic_success = set(candidate_atomic.get("success", []) or [])
        candidate_atomic_failure = set(candidate_atomic.get("failure", []) or [])
        newly_passed_atomic = sorted(candidate_atomic_success - source_atomic_success)
        regressed_atomic = sorted(source_atomic_success - candidate_atomic_success)
        atomic_partial_progress = bool(newly_passed_atomic and candidate_atomic_failure and not regressed_atomic)
        deltas[case_id] = {
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
                atomic_partial_progress
                or (newly_passed and candidate_f2p_failure and not regressed_f2p and not regressed_p2p)
            ),
            "source_patch_successfully_applied": source_status.get("patch_successfully_applied"),
            "candidate_patch_successfully_applied": candidate_status.get("patch_successfully_applied"),
            "candidate_empty_patch": candidate_status.get("empty_patch"),
        }
    return deltas


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


def _invoked_tool_names(eval_ref_path: str) -> set[str]:
    return {name for names in _invoked_tool_names_by_case(eval_ref_path).values() for name in names}


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
        trace = _read_json(str(case.get("trace_path", "") or ""))
        _collect_tool_names(trace, names)
        _collect_trajectory_dir_usage(trace, tool_names=names)
    return names_by_case


def _invoked_skill_names(eval_ref_path: str) -> set[str]:
    return {name for names in _invoked_skill_names_by_case(eval_ref_path).values() for name in names}


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
        trace = _read_json(str(case.get("trace_path", "") or ""))
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
        trace = _read_json(str(case.get("trace_path", "") or ""))
        kwargs = {
            "tool_names": names if action_group == "tool" else None,
            "skill_names": names if action_group == "skill" else None,
        }
        edit_steps = [step for step in [collect_pre_edit_successful_usage(trace, **kwargs)] if step is not None]
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
        status_failed = status in {"failed", "error"}
        score_failed = score is not None and score < 1.0
        if case_id and (status_failed or score_failed):
            case_ids.add(case_id)
    return case_ids


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
    if any(delta.get("newly_passed_fail_to_pass") for delta in (verifier_deltas_by_case or {}).values()):
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
