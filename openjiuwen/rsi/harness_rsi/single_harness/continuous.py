# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-target improvement followed by exactly one independent full evaluation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.rsi.events import NodeStageEvent, emit
from openjiuwen.rsi.harness_rsi.evaluator.task_identity import case_identity
from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError
from openjiuwen.rsi.harness_rsi.member_optimizer.hypothesis import compile_optimization_hypotheses, load_optimization_hypotheses
from openjiuwen.rsi.harness_rsi.single_harness.events_translate import (
    active_epoch_node_event, epoch_node_event, generate_stage_payload, progress_event,
)
from openjiuwen.rsi.harness_rsi.single_harness.promotion_policy import (
    EPSILON, complete_scores, digest, full_evaluation_decision, mean_score, package_fingerprint, targets_improved,
)
from openjiuwen.rsi.harness_rsi.single_harness.source_evidence import materialize_source
from openjiuwen.rsi.usage import model_usage_stage, set_usage_node


async def _subset(owner: Any, cases: list[dict[str, Any]], source_ref: str, refs: str, directory: Path) -> str:
    from .iterative import _eval_ref_complete, _read_yaml

    if _eval_ref_complete(directory / "eval_ref.yaml"):
        return str(directory / "eval_ref.yaml")
    rows = {str(row["case_id"]): (source_ref, row) for row in _read_yaml(source_ref)["cases"]}
    return await materialize_source(
        cases=cases, selected=rows, output_dir=directory, harness_refs_path=refs,
        context=owner._evaluation_context(cases, refs), reused_case_ids={str(case["case_id"]) for case in cases},
    )


def _full_engineering(gate: dict[str, Any], eval_ref: str) -> bool:
    from .iterative import (
        _failed_machine_evidence,
        _missing_capability_invocations, _pre_edit_invoked_names_by_case, _skill_names_match, _tool_names_match,
    )

    if _failed_machine_evidence(eval_ref):
        return False
    for group, matcher in (
        ("tool", _tool_names_match),
        ("skill", _skill_names_match),
    ):
        early, _ = _pre_edit_invoked_names_by_case(eval_ref, action_group=group)
        later, _ = _pre_edit_invoked_names_by_case(eval_ref, action_group=group, allow_later_edit=True)
        if _missing_capability_invocations(
            gate.get("capabilities", []), action_group=group, invoked_names_by_case=early,
            later_edit_names_by_case=later, fallback_target_case_ids=set(gate["target_case_ids"]),
            names_match=matcher,
        ):
            return False
    return True


async def run_continuous(
    owner: Any, *, request: Any, state: dict[str, Any], dataset: Any, all_cases: list[dict[str, Any]],
    output_dir: Path, max_epochs: int, on_event: Any,
) -> Any:
    """Require every target margin, then per-case retention and a positive full gain."""
    from .iterative import (
        _analysis_issue_case_ids, _build_report, _candidate_can_continue_locally, _candidate_capabilities,
        _ensure_final_publication, _merge_repair_capabilities, _persist_promotion, _prior_candidate_feedback,
        _raise_if_cancelled, _read_yaml, _refresh_optimization_experience, _rejected_capabilities,
        _result_from_state, _validate_and_filter_planned_batches, _write_yaml_atomic,
    )

    state_path = output_dir / "single_harness_state.yaml"
    report_path = output_dir / "single_harness_report.yaml"
    ids = {str(case["case_id"]) for case in all_cases}
    cases_by_id = {str(case["case_id"]): case for case in all_cases}
    baseline_scores = complete_scores(state["baseline_eval_ref_path"], ids)
    if _read_yaml(state["baseline_eval_ref_path"]).get("evaluation_context") != owner._evaluation_context(
        all_cases, state["source_harness_refs_path"],
    ):
        raise EvaluationInfrastructureError("Initial continuous baseline protocol or dataset does not match")
    state.setdefault("best_reference_eval_ref_path", state["baseline_eval_ref_path"])
    state.setdefault("best_reference_score", mean_score(baseline_scores))
    state.setdefault("historical_peak_score", state["best_reference_score"])
    attempts = state.setdefault("continuous_attempts", {})
    trials = state.setdefault("continuous_trials", {})
    batch_states = state.setdefault("continuous_batches", {})

    def save() -> None:
        _write_yaml_atomic(state_path, state)

    for epoch in range(1, max_epochs + 1):
        _raise_if_cancelled()
        if any(int(item["epoch"]) == epoch for item in state["epoch_checkpoints"]):
            continue
        current_scores = complete_scores(state["best_eval_ref_path"], ids)
        if not any(1 - score > owner.config.promotion.min_improvement + EPSILON for score in current_scores.values()):
            state["stop_reason"] = "target_threshold_saturated"
            break
        epoch_refs = state["best_harness_refs_path"]
        node = f"epoch-{epoch:03d}"
        set_usage_node(node)
        state["active_epoch"] = epoch
        state["active_epoch_before_harness_refs_path"] = epoch_refs
        save()
        await emit(on_event, active_epoch_node_event(state))
        if callable(getattr(type(owner.data_loader), "load_files", None)):
            planned = list(owner.data_loader.load_files(request.dataset_files, epoch=epoch))
        else:
            planned = list(owner.data_loader.load(dataset.dataset_dir, epoch=epoch))
        batches = _validate_and_filter_planned_batches(planned, expected_case_ids=ids)
        epoch_promoted = False
        for batch_index, planned_batch in enumerate(batches, 1):
            batch_key = f"epoch_{epoch:03d}:batch_{batch_index:03d}"
            if batch_key in state["completed_batches"]:
                epoch_promoted = epoch_promoted or bool(state["completed_batches"][batch_key].get("promotion_applied"))
                continue
            frozen_batch = batch_states.get(batch_key)
            if frozen_batch is None:
                frozen_batch = {
                    "reference_eval": state["best_eval_ref_path"],
                    "parent_refs": state["best_harness_refs_path"], "parent_score": state["best_score"],
                }
                batch_states[batch_key] = frozen_batch
                save()
            reference_eval = frozen_batch["reference_eval"]
            scores = complete_scores(reference_eval, ids)
            cases = [cases_by_id[str(case["case_id"])] for case in planned_batch
                     if 1 - scores[str(case["case_id"])] > owner.config.promotion.min_improvement + EPSILON]
            if not cases:
                continue
            parent_refs = frozen_batch["parent_refs"]
            parent_score = frozen_batch["parent_score"]
            recorded_context = _read_yaml(reference_eval).get("evaluation_context", {})
            current_context = owner._evaluation_context(all_cases, parent_refs)
            if any(recorded_context.get(field) != current_context.get(field) for field in ("signature", "cases")):
                raise EvaluationInfrastructureError("Comparison parent no longer matches its recorded full evaluation")
            directory = output_dir / "evaluations" / f"e{epoch:03d}" / f"b{batch_index:03d}"
            source = await _subset(owner, cases, reference_eval, parent_refs, directory / "source")
            repair_refs, repair_source, repair_caps = parent_refs, source, []
            promoted = False
            last_gate: dict[str, Any] = {}
            budget = owner.config.member_optimizer.max_repair_rounds_per_batch
            if owner.config.member_optimizer.max_issue_attempts_per_batch:
                budget = min(budget, owner.config.member_optimizer.max_issue_attempts_per_batch)
            for attempt_index in range(1, budget + 1):
                _raise_if_cancelled()
                key = f"{batch_key}:attempt_{attempt_index:03d}"
                record = attempts.setdefault(key, {})
                if record.get("done"):
                    last_gate = record["gate"]
                    if last_gate.get("accepted"):
                        promoted = True
                        break
                    if record.get("repair_refs"):
                        repair_refs, repair_source = record["repair_refs"], record["repair_source"]
                        repair_caps = record["repair_capabilities"]
                    continue
                attempt_dir = directory / "attempts" / f"a{attempt_index:03d}"
                analysis = record.get("analysis_ref")
                if not analysis:
                    analysis = await owner._analyze(
                        eval_ref_path=repair_source, harness_refs_path=repair_refs,
                        output_dir=attempt_dir / "analysis", node_ref=node, on_event=on_event,
                        prior_candidate_feedback=_prior_candidate_feedback(state, cases),
                    )
                    record["analysis_ref"] = analysis
                    save()
                hypotheses = compile_optimization_hypotheses(
                    analysis_ref_path=analysis, cases=cases, output_path=attempt_dir / "optimization_hypotheses.yaml",
                )
                if not _analysis_issue_case_ids(analysis) or not load_optimization_hypotheses(hypotheses):
                    state["stop_reason"] = "no_executable_hypotheses"
                    break
                member_ref = record.get("member_ref")
                if not member_ref:
                    await emit(on_event, NodeStageEvent(node_ref=node, stage=generate_stage_payload(1, 1, "running")))
                    member_ref = await model_usage_stage("optimize")(owner.member_optimizer.optimize)(
                        eval_ref_path=repair_source, analysis_result_path=analysis, harness_refs_path=repair_refs,
                        output_dir=str(output_dir / "member_optimizations"), defer_publish=True,
                        rejected_capabilities=_rejected_capabilities(state), single_harness=True,
                        optimization_hypotheses_path=hypotheses,
                        optimization_experience={"journal": list(state.get("optimization_journal", [])),
                                                 "lever_scoreboard": dict(state.get("lever_scoreboard", {}))},
                    )
                    record["member_ref"] = member_ref
                    save()
                    await emit(on_event, NodeStageEvent(node_ref=node, stage=generate_stage_payload(1, 1, "done")))
                member = _read_yaml(member_ref)
                candidate_refs = str(member.get("optimized_harness_refs_path") or repair_refs)
                capabilities = _merge_repair_capabilities(repair_caps, _candidate_capabilities(member))
                identity_data = {
                    "package": package_fingerprint(candidate_refs), "parent": package_fingerprint(parent_refs),
                    "baseline_eval": reference_eval, "baseline_score": parent_score,
                    "cases": {str(case["case_id"]): case_identity(case) for case in all_cases},
                    # The initial protocol signature is stable across candidate directory aliases.
                    "protocol_signature": state["evaluation_protocol_signature"],
                    "promotion_policy": state["promotion_policy"],
                }
                identity = digest(identity_data)
                prior_trial = trials.get(identity)
                if prior_trial is None:
                    trials[identity] = {"attempt_key": key}
                    save()
                elif not prior_trial.get("done") and prior_trial["attempt_key"] != key:
                    raise EvaluationInfrastructureError("Resume the original unfinished candidate trial")
                gate = record.get("target_gate")
                if gate is None:
                    if prior_trial is not None and prior_trial.get("done"):
                        gate = dict(prior_trial["gate"])
                        gate["decision_reused_from"] = prior_trial["attempt_key"]
                        # Successful trials have already changed Best; they are not a new proposal.
                        gate["accepted"] = False
                        gate["status"] = "rejected"
                        gate["reason"] = "duplicate_candidate_decision"
                        gate["candidate_harness_refs_path"] = candidate_refs
                    elif identity_data["package"] == identity_data["parent"]:
                        gate = {
                            "accepted": False, "status": "rejected", "reason": "candidate_content_unchanged",
                            "source_eval_ref_path": source, "before_harness_refs_path": parent_refs,
                            "candidate_harness_refs_path": candidate_refs, "capabilities": capabilities,
                            "target_case_ids": [str(case["case_id"]) for case in cases],
                        }
                    else:
                        gate = await owner._candidate_gate(
                            cases=cases, source_eval_ref=source, analysis_ref=analysis,
                            before_harness_refs_path=parent_refs, candidate_harness_refs_path=candidate_refs,
                            member_status=str(member.get("status", "")), capabilities=capabilities,
                            output_dir=attempt_dir / "target", dataset=dataset, node_ref=node, on_event=on_event,
                        )
                    record["target_gate"] = dict(gate)
                    save()
                else:
                    gate = dict(gate)
                target_accepted = bool(gate.get("accepted"))
                gate.update({"epoch": epoch, "batch_index": batch_index, "batch_attempt_index": attempt_index,
                             "analysis_ref_path": analysis,
                             "member_optimization_ref_path": member_ref, "primary_gate_accepted": target_accepted,
                             "comparison_baseline_score": parent_score, "comparison_baseline_eval_ref_path": reference_eval,
                             "policy_version": owner.config.promotion.policy_version})
                if target_accepted:
                    gate.update(accepted=False, status="pending_full_evaluation")
                    try:
                        trial = trials.setdefault(identity, {"attempt_key": key})
                        if trial["attempt_key"] != key:
                            raise EvaluationInfrastructureError("Resume the original unfinished candidate trial")
                        save()
                        target_ids = set(gate["target_case_ids"])
                        target_cases = [case for case in all_cases if str(case["case_id"]) in target_ids]
                        target_ref = gate["candidate_eval_ref_path"]
                        if _read_yaml(target_ref).get("evaluation_context") != owner._evaluation_context(
                            target_cases, candidate_refs,
                        ):
                            raise EvaluationInfrastructureError("Target evaluation does not match the frozen candidate and baseline")
                        target_scores = complete_scores(target_ref, target_ids)
                        proof = record.get("improvement_proof")
                        if proof is None:
                            proof = {"identity": identity, "package_fingerprint": identity_data["package"],
                                     "scope": "target_cases", "policy_version": owner.config.promotion.policy_version,
                                     "baseline_score": parent_score, "baseline_scores_digest": digest(scores),
                                     "target_case_ids": sorted(target_ids), "target_scores": target_scores,
                                     "target_baseline_scores": {cid: scores[cid] for cid in sorted(target_ids)},
                                     "target_scores_digest": digest(target_scores), "target_eval_ref_path": target_ref,
                                     "qualified": targets_improved(target_scores, scores, target_ids, owner.config.promotion.min_improvement)}
                            record["improvement_proof"] = proof
                            save()
                        if (proof["identity"] != identity or proof.get("target_scores_digest") != digest(target_scores)
                                or proof.get("baseline_scores_digest") != digest(scores)
                                or set(proof.get("target_case_ids", [])) != target_ids or not proof.get("qualified")):
                            raise EvaluationInfrastructureError("Frozen candidate or baseline identity changed")
                        gate.update(improvement_proof=proof, reason="target_improvement_observed")
                        record["gate"] = gate
                        save()
                        # The full stage reruns all cases, including the targets. There is no third trial.
                        full_ref = await owner._evaluate(
                            cases=all_cases, harness_refs_path=candidate_refs,
                            output_dir=attempt_dir / "full", dataset=dataset, node_ref=node,
                            on_event=on_event,
                            case_concurrency=owner.config.scheduling.full_evaluation_concurrency,
                        )
                        full_scores = complete_scores(full_ref, ids)
                        if (package_fingerprint(candidate_refs) != identity_data["package"]
                                or digest(complete_scores(target_ref, target_ids)) != proof["target_scores_digest"]
                                or digest(complete_scores(reference_eval, ids)) != proof["baseline_scores_digest"]):
                            raise EvaluationInfrastructureError("Candidate or frozen score evidence changed during full evaluation")
                        checks = full_evaluation_decision(
                            full_scores, scores, target_ids, max_regression=owner.config.promotion.non_target_max_regression,
                            proof=proof, identity=identity,
                        )
                        engineering = _full_engineering(gate, full_ref)
                        accepted = checks["accepted"] and engineering
                        gate.update(accepted=accepted, status="accepted" if accepted else "rejected",
                                    candidate_full_score=checks["score"], latest_evaluated_score=checks["score"],
                                    full_eval_ref_path=full_ref, full_scores_digest=digest(full_scores),
                                    full_engineering_passed=engineering, full_checks=checks,
                                    reason=checks["reason"] if engineering else "engineering_failed")
                        state["historical_peak_score"] = max(state["historical_peak_score"], checks["score"])
                        if accepted:
                            state.update(best_score=checks["score"], best_reference_score=checks["score"],
                                         best_eval_ref_path=full_ref, best_reference_eval_ref_path=full_ref,
                                         best_harness_refs_path=candidate_refs, best_improvement_proof=proof,
                                         current_harness_refs_path=candidate_refs, working_harness_refs_path=candidate_refs,
                                         published_harness_refs_path="", publication_status="not_published")
                            promoted = True
                    except EvaluationInfrastructureError as exc:
                        gate.update(accepted=False, status="inconclusive", reason="evaluation_invalid", evaluation_error=str(exc))
                if not gate.get("accepted") and gate.get("status") == "pending_full_evaluation":
                    gate["status"] = "rejected"
                if not promoted and _candidate_can_continue_locally(gate, cases):
                    repair_refs, repair_source, repair_caps = candidate_refs, gate["candidate_eval_ref_path"], capabilities
                    record.update(repair_refs=repair_refs, repair_source=repair_source, repair_capabilities=repair_caps)
                record.update(done=True, gate=gate)
                if trials[identity]["attempt_key"] == key:
                    trials[identity].update(done=True, gate=dict(gate))
                state["candidate_gates"].append(gate)
                _persist_promotion(member_ref, candidate_refs if candidate_refs != parent_refs else "", gate)
                _refresh_optimization_experience(state, output_dir)
                save()
                last_gate = gate
                if promoted:
                    break
            state["completed_batches"][batch_key] = {
                "epoch": epoch, "batch_index": batch_index, "source_eval_ref_path": source,
                "analysis_ref_path": last_gate.get("analysis_ref_path", ""),
                "before_harness_refs_path": parent_refs, "after_harness_refs_path": state["best_harness_refs_path"],
                "candidate_gate_status": last_gate.get("status", "skipped"),
                "candidate_gate_reason": last_gate.get("reason", "no_actionable_analysis_issues"),
                "promotion_applied": promoted,
            }
            epoch_promoted = epoch_promoted or promoted
            save()
        state["active_epoch"] = 0
        checkpoint = {
            "epoch": epoch, "before_harness_refs_path": epoch_refs,
            "harness_refs_path": state["best_harness_refs_path"], "selected_harness_refs_path": state["best_harness_refs_path"],
            "eval_ref_path": state["best_eval_ref_path"], "score": state["best_score"],
            "promotion_applied": epoch_promoted, "promotion_reason": "two_stage_candidate_accepted" if epoch_promoted else "no_accepted_improvement",
            "status": "accepted" if epoch_promoted else "rejected", "policy_version": owner.config.promotion.policy_version,
        }
        state["epoch_checkpoints"].append(checkpoint)
        save()
        await emit(on_event, epoch_node_event(state, checkpoint))
        await emit(on_event, progress_event(state, total_iterations=max_epochs))
    _ensure_final_publication(state=state, output_dir=output_dir)
    state["status"] = "completed"
    save()
    _write_yaml_atomic(report_path, _build_report(state, dataset))
    return _result_from_state(state, state_path, report_path)
