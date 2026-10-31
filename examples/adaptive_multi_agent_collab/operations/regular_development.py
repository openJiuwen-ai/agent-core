"""Prospective TextGrad repair amendment and bounded regular-development scope.

Inference, calibration, learned numerical state and the active accounting policy
are preserved.  The new approval governs future prompt optimization only.
"""
from __future__ import annotations

import asyncio
import copy
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ..infrastructure.storage import load_checkpoint, read_json, strict_json_equal
from .supplement import _check_reference, _digest, _inside, _reference, _sha

PLAN_PROTOCOL = "regular_textgrad_repair_plan_v1"
LINEAGE_PROTOCOL = "audited_regular_textgrad_repair_v1"
TRANSITION_PROTOCOL = "regular_textgrad_repair_transition_v1"
FORECAST_PROTOCOL = "regular_development_forecast_v1"
SCOPE = "development_regular_only"


def amendment() -> dict[str, Any]:
    return {"textgrad_output_repair_limit": 1,
            "preserve_inference_and_scoring": True, "calibration_reexecution": False}


def _parent_condition(source: str | Path, payload: Mapping[str, Any]) -> Any:
    from .experiment import prepare_experiment
    from .supplement import prepare_checkpoint_condition
    return prepare_checkpoint_condition(prepare_experiment(source), payload, specification_path=source)


def _baseline(payload: Mapping[str, Any]) -> dict[str, Any]:
    from ..config import Config
    from ..infrastructure.failure_policy import logical_summary
    from ..providers.resources import ExpenditureLedger

    state, hooks = payload["trainer_state"], payload["hook_state"]
    ledger = hooks["expenditure_ledger"]
    if (payload.get("request_boundary_pending") is not False
            or state.get("phase") != "weighting" or state.get("phase_progress")
            or state.get("outer_cycle") != 0 or not state.get("calibration_complete")
            or not state.get("pilot_complete") or state.get("eligible_checkpoints") != [0]
            or len(state.get("system_validation_records", [])) != 1
            or state.get("bootstrap_complete") != {"weighting": True, "routing": True}
            or state.get("attempts", {}).get("prompt") != 0
            or payload.get("best_system") is None or hooks.get("termination_record") is None
            or hooks.get("regular_lineage") or ledger.get("halted")):
        raise ValueError("regular amendment requires the safe completed checkpoint0 boundary")
    policy = ledger.get("failure_policy")
    if (not policy or logical_summary(policy)["pending_logical_ids"]
            or any(event["event"] == "drain" for event in policy["events"])):
        raise ValueError("regular amendment requires its active settled logical-request boundary")
    probe = ExpenditureLedger(Config.from_dict(payload["declared_config"]).resources)
    probe.load_state_dict(ledger)
    if probe.uncertainty_summary()["inflight_ids"]:
        raise ValueError("regular amendment cannot adopt unfinished physical requests")
    return {"trainer_state_sha256": _digest(state), "ledger_sha256": _digest(ledger),
            "receipt_count": len(ledger["entries"]), "policy_sha256": _digest(policy),
            "termination_record_sha256": _digest(hooks["termination_record"]),
            "baseline_record_sha256": _digest(state["system_validation_records"][0]),
            "sample_counters": copy.deepcopy(hooks["sample_counters"])}


def _bindings(prepared: Any) -> dict[str, str]:
    from .experiment import planning_hooks
    return {"condition_fingerprint": planning_hooks(prepared).condition_fingerprint,
            "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
            "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
            "specification_sha256": prepared.specification_sha256}


def prepare_plan(specification_path: str | Path, checkpoint_snapshot_path: str | Path,
                 approval_source_path: str | Path, forecast_path: str | Path | None = None) -> dict[str, Any]:
    source, snapshot = _inside(specification_path), _inside(checkpoint_snapshot_path)
    if snapshot == source.parent / "checkpoint.pt":
        raise ValueError("regular amendment requires a preserved immutable checkpoint0 source")
    payload = load_checkpoint(snapshot)
    parent = _parent_condition(source, payload)
    if parent.extension_registration:
        raise ValueError("registered extensions require their separately scoped adaptation path")
    plan = {"protocol": PLAN_PROTOCOL, "campaign_id": parent.campaign_binding["campaign_id"],
            "campaign_directory": parent.campaign_binding["directory"],
            "references": {"manifest": _reference(source), "checkpoint": _reference(snapshot),
                "approval_source": _reference(approval_source_path),
                "forecast": None if forecast_path is None else _reference(forecast_path)},
            "parent": _bindings(parent), "amendment": amendment(), "baseline": _baseline(payload)}
    plan["fingerprint"] = _digest(plan)
    return plan


def load_plan(path: str | Path, specification_path: str | Path) -> dict[str, Any]:
    plan = read_json(_inside(path))
    if (set(plan) != {"protocol", "campaign_id", "campaign_directory", "references", "parent",
                     "amendment", "baseline", "fingerprint"}
            or plan.get("protocol") != PLAN_PROTOCOL or plan.get("amendment") != amendment()
            or _digest({key: value for key, value in plan.items() if key != "fingerprint"}) != plan.get("fingerprint")):
        raise ValueError("invalid regular-development amendment plan")
    refs = plan["references"]
    if set(refs) != {"manifest", "checkpoint", "approval_source", "forecast"}:
        raise ValueError("regular plan requires its exact immutable references")
    checked = {key: None if value is None else _check_reference(value) for key, value in refs.items()}
    if checked["manifest"] != _inside(specification_path):
        raise ValueError("regular plan belongs to another campaign manifest")
    expected = prepare_plan(checked["manifest"], checked["checkpoint"], checked["approval_source"], checked["forecast"])
    if not strict_json_equal(plan, expected):
        raise ValueError("regular plan differs from its immutable checkpoint0 source")
    return plan


def apply_prepared(parent: Any, plan: Mapping[str, Any]) -> Any:
    if (plan.get("protocol") != PLAN_PROTOCOL or plan.get("amendment") != amendment()
            or plan.get("parent") != _bindings(parent) or parent.extension_registration
            or parent.textgrad["config"].get("output_repair_limit", 0) != 0):
        raise ValueError("regular amendment differs from its exact TextGrad parent condition")
    textgrad = copy.deepcopy(dict(parent.textgrad))
    textgrad["config"]["output_repair_limit"] = 1
    record = {"protocol": PLAN_PROTOCOL, "parent": copy.deepcopy(dict(plan["parent"])),
              "amendment": amendment(), "approval_source_sha256": plan["references"]["approval_source"]["sha256"]}
    # No query seed, inference plan, termination, scoring or scientific config
    # changes. The exact calibrated prefixes remain valid evidence.
    return replace(parent, textgrad=textgrad,
        authorization_manifest_fingerprint=_digest({"parent": parent.authorization_manifest_fingerprint, **record}),
        resource_profile_fingerprint=_digest({"parent": parent.resource_profile_fingerprint,
                                             "textgrad_output_repair_limit": 1}),
        specification_amendments=(*parent.specification_amendments, record))


def reconstruct_lineage(lineage: Mapping[str, Any], *, specification_path: str | Path | None = None) -> tuple[Any, Any, dict[str, Any]]:
    if (not isinstance(lineage, Mapping) or set(lineage) != {"protocol", "plan_path", "plan_sha256", "parent_checkpoint"}
            or lineage.get("protocol") != LINEAGE_PROTOCOL):
        raise ValueError("unregistered regular-development lineage")
    path = _inside(lineage["plan_path"])
    if _sha(path) != lineage["plan_sha256"]:
        raise ValueError("regular amendment plan bytes changed")
    source = read_json(path)["references"]["manifest"]["path"]
    if specification_path is not None and _inside(source) != _inside(specification_path):
        raise ValueError("regular amendment belongs to another manifest")
    plan = load_plan(path, source)
    if lineage["parent_checkpoint"] != plan["references"]["checkpoint"]:
        raise ValueError("regular checkpoint0 source changed")
    parent = _parent_condition(source, load_checkpoint(_check_reference(plan["references"]["checkpoint"])))
    return parent, apply_prepared(parent, plan), plan


def prepare_checkpoint_condition(prepared: Any, payload: Mapping[str, Any], *, specification_path: str | Path | None = None) -> Any:
    from .experiment import planning_hooks
    _, amended, _ = reconstruct_lineage(payload.get("hook_state", {}).get("regular_lineage"),
                                       specification_path=specification_path)
    if (prepared.campaign_binding != amended.campaign_binding
            or not strict_json_equal(payload["declared_config"], amended.config.to_dict())
            or not strict_json_equal(payload["hook_state"]["condition"], planning_hooks(amended)._condition())):
        raise ValueError("regular checkpoint differs from independently reconstructed bindings")
    return amended


def auth_bindings(prepared: Any, checkpoint_path: str | Path, plan_path: str | Path,
                  forecast_path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from .preflight import authorization_bindings
    from .experiment import planning_hooks
    source = read_json(_inside(plan_path))["references"]["manifest"]["path"]
    plan = load_plan(plan_path, source)
    parent_payload = load_checkpoint(_check_reference(plan["references"]["checkpoint"]))
    parent = _parent_condition(source, parent_payload)
    amended = apply_prepared(parent, plan)
    if prepared.campaign_binding != amended.campaign_binding:
        raise ValueError("regular authorization belongs to another campaign")
    current = load_checkpoint(checkpoint_path)
    if _sha(_inside(checkpoint_path)) == plan["references"]["checkpoint"]["sha256"]:
        bindings, _ = authorization_bindings(parent, "development", checkpoint_path)
        bindings.update(manifest_fingerprint=amended.authorization_manifest_fingerprint,
            resource_profile_fingerprint=amended.resource_profile_fingerprint,
            condition_fingerprint=planning_hooks(amended).condition_fingerprint)
    else:
        prepare_checkpoint_condition(prepared, current, specification_path=source)
        bindings, _ = authorization_bindings(amended, "development", checkpoint_path)
    forecast = read_json(_inside(forecast_path))
    keys = ("manifest_fingerprint", "condition_fingerprint", "resource_profile_fingerprint", "pilot_fingerprint")
    if (_reference(forecast_path) != plan["references"]["forecast"]
            or forecast.get("protocol") != FORECAST_PROTOCOL
            or forecast.get("bindings") != {key: bindings[key] for key in keys}
            or forecast.get("development_ceiling_usd") != bindings["approved_budget_usd"]
            or not forecast.get("basis") or forecast.get("completion_guaranteed") is not False
            or not all(isinstance(forecast.get(key), (float, int)) and not isinstance(forecast.get(key), bool)
                       and math.isfinite(forecast[key]) and forecast[key] > 0
                       for key in ("estimated_cost_usd", "headroom_usd"))):
        raise ValueError("regular forecast differs from its exact condition or cumulative budget")
    # The approved finite workload can exceed the remaining budget. Existing
    # per-dispatch accounting stops safely; a forecast grants no replenishment.
    state = current["trainer_state"]
    if (not state.get("calibration_complete") or not state.get("eligible_checkpoints")
            or state.get("phase") not in {"weighting", "routing", "prompt", "system", "complete"}):
        raise ValueError("regular approval cannot authorize bootstrap or calibration")
    bindings.update(execution_scope=SCOPE, regular_plan_sha256=_sha(_inside(plan_path)),
                    regular_forecast_sha256=_sha(_inside(forecast_path)))
    return bindings, forecast


def policy_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in {"events", "historical_adoptions"}}


def bind_amended_policy(ledger: Any, source: str | Path, execution_guard: Any) -> bool:
    """Retain the already active policy; validate the new stage authority separately."""
    from ..infrastructure.storage import load_ledger
    from .preflight import load_stage_authorization
    directory = _inside(source).parent
    path = directory / "regular_development" / "transition.json"
    if not path.exists():
        return False
    from .policy_activation import _validate_independent_activation
    _validate_independent_activation(read_json(source), directory)
    receipt = read_json(path)
    _, prepared, plan = reconstruct_lineage(receipt["lineage"], specification_path=source)
    if (receipt.get("protocol") != TRANSITION_PROTOCOL or receipt.get("plan_fingerprint") != plan["fingerprint"]
            or receipt.get("source_checkpoint") != plan["references"]["checkpoint"]
            or receipt.get("generation_calls") != 0 or receipt.get("spending_reset") is not False):
        raise ValueError("regular transition receipt differs from its approved publication")
    bindings, _ = auth_bindings(prepared, directory / "checkpoint.pt", receipt["lineage"]["plan_path"],
                                plan["references"]["forecast"]["path"])
    load_stage_authorization(_check_reference(receipt["authorization"]), **bindings)
    original = load_checkpoint(plan["references"]["checkpoint"]["path"])["hook_state"]["expenditure_ledger"]
    canonical = load_ledger(directory / "expenditure_ledger.json")
    if not strict_json_equal(policy_metadata(canonical["failure_policy"]), policy_metadata(original["failure_policy"])):
        raise ValueError("regular development cannot replace its activated failure policy")
    if not ledger.entries and ledger.failure_policy is None:
        ledger.load_state_dict(canonical)
    if not strict_json_equal(ledger.state_dict(), canonical):
        raise ValueError("regular policy binding differs from current durable accounting")
    policy = canonical["failure_policy"]
    ledger.activate_failure_policy(prepared.config.failure_policy, authorization=policy["authorization"],
        bindings=policy["bindings"], execution_guard=execution_guard,
        condition_amendments=policy.get("condition_amendments"))
    return True


def forecast(prepared: Any, parent_payload: Mapping[str, Any], pilot_projection: Mapping[str, Any],
             bindings: Mapping[str, Any], *, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Finite scheduled workload with full replacement and request-repair allowances.

The saved Pilot5-derived profile already enlarges routing, repairs, token
lengths, retries and unobserved tie calls. Only its collaboration horizon is
scaled from three to the selected one; initialization is never scaled down.
"""
    from ..providers.resources import ExpenditureLedger
    training = prepared.config.training
    selected = parent_payload["config"]["termination"]
    if selected["max_sessions"] != 1 or prepared.textgrad["config"].get("output_repair_limit") != 1:
        raise ValueError("this forecast requires the approved one-session repaired TextGrad condition")
    validations = lambda phase: 1 + math.ceil(phase.max_updates / phase.validation_interval)
    cycles = training.max_outer_cycles - parent_payload["trainer_state"]["outer_cycle"]
    per_cycle = {
        "weighting_training_slots": training.weighting_phase.max_attempts,
        "routing_training_slots": training.routing_phase.max_attempts,
        "weighting_validation_slots": validations(training.weighting_phase) * training.weighting_validation_batch_size,
        "routing_validation_slots": validations(training.routing_phase) * training.routing_validation_batch_size,
        "prompt_evidence_slots": training.prompt_phase.max_iterations * training.prompt_train_batch_size,
        "prompt_validation_slots": (training.prompt_phase.max_iterations + 1) * training.prompt_validation_batch_size,
        "system_validation_slots": training.system_validation_batch_size,
    }
    slots = sum(per_cycle.values()) * cycles
    workflows = slots * (1 + prepared.config.workflow_recovery.max_replacements)
    iterations = cycles * training.prompt_phase.max_iterations
    logical_tg = iterations * 5  # up to two evaluation + two backward + one proposal
    # Safe transport recovery is shared across a logical TextGrad call, not
    # replenished for its one output repair.
    physical_tg = logical_tg * (2 + prepared.config.resources.transport_retries)
    ledger = ExpenditureLedger(prepared.config.resources)
    ledger.load_state_dict(parent_payload["hook_state"]["expenditure_ledger"])
    stage = ledger.stage_summary("development")
    stage["remaining_usd"] = stage["budget"] - stage["occupied"]
    tg_profile = prepared.backends[prepared.textgrad["optimizer_backend"]]["resources"]
    tg_one = ((prepared.config.resources.textgrad_input * tg_profile["input_price"]
               + prepared.config.resources.textgrad_output * tg_profile["output_price"])
              * tg_profile["billing_fee_multiplier"] + tg_profile["request_fee"])
    tg_paid = physical_tg * tg_one
    scenarios = []
    for original in pilot_projection["scenarios"]:
        by_backend = []
        for backend in original["by_backend"]:
            components = []
            for component in backend["components"]:
                # Ordinary tie allowance was declared per workflow, not
                # measured across three sessions; it remains one per backend.
                horizon_factor = (1.0 if component["kind"] == "initial"
                                  or (component["kind"] == "tie" and original is pilot_projection["scenarios"][0])
                                  else 1 / 3)
                amount = component["estimated_paid_usd"] / original["workflows"] * horizon_factor * workflows
                components.append({"kind": component["kind"], "estimated_paid_usd": amount,
                                   "horizon_factor": horizon_factor,
                                   "source_component_paid_usd": component["estimated_paid_usd"],
                                   "source_workflows": original["workflows"]})
            by_backend.append({"backend_key": backend["backend_key"], "model": backend["model"],
                               "components": components,
                               "estimated_paid_usd": sum(item["estimated_paid_usd"] for item in components)})
        workflow_paid = sum(item["estimated_paid_usd"] for item in by_backend)
        cost = workflow_paid + tg_paid
        # Existing holds already reduce the remaining ceiling. Only unused
        # uncertainty capacity enters additional headroom; never count twice.
        retained = stage["unresolved"]
        headroom = .25 * cost + max(0., prepared.config.failure_policy.max_unresolved_reserved_usd - retained)
        scenarios.append({"name": original["name"], "by_backend": by_backend,
                          "workflow_expenditure_usd": workflow_paid, "textgrad_expenditure_usd": tg_paid,
                          "estimated_paid_usd": cost, "headroom_usd": headroom,
                          "with_headroom_usd": cost + headroom,
                          "fits_remaining_budget": cost + headroom <= stage["remaining_usd"],
                          "is_full_thirty_round_bound": False})
    ordinary = scenarios[0]
    return {"protocol": FORECAST_PROTOCOL,
            "bindings": {key: bindings[key] for key in ("manifest_fingerprint", "condition_fingerprint",
                                                        "resource_profile_fingerprint", "pilot_fingerprint")},
            "basis": "Registered finite regular W/alpha/gamma schedule; saved Pilot5 workload expansion with one-session collaboration, unchanged initialization, every slot replacement, every TextGrad output repair and permitted safe transport retry, positive tie allowance and explicit uncertainty headroom.",
            "evidence": copy.deepcopy(dict(evidence)), "development_ceiling_usd": prepared.config.resources.development_budget,
            "estimated_cost_usd": ordinary["estimated_paid_usd"], "headroom_usd": ordinary["headroom_usd"],
            "estimated_with_headroom_usd": ordinary["with_headroom_usd"], "completion_guaranteed": False,
            "starting_accounting": stage, "selected_termination": selected,
            "schedule": {"maximum_cycles": cycles, "per_cycle": per_cycle,
                         "scheduled_workflow_slots": slots, "maximum_actual_workflow_executions": workflows,
                         "maximum_prompt_iterations": iterations, "maximum_textgrad_logical_calls": logical_tg,
                         "maximum_textgrad_physical_attempts": physical_tg, "calibration_workflows": 0,
                         "checkpoint0_baseline_regenerations": 0, "final_test_workflows": 0},
            "scenarios": scenarios,
            "forecast_assumptions": {"source_collaboration_sessions": 3, "selected_sessions": 1,
                "preserved_source_workload_assumptions": pilot_projection["scenarios"][0]["assumptions"],
                "full_cap_textgrad": True, "max_feedback_records": 2,
                "uncertainty_headroom_contains_inflight_reservations": True,
                "additional_funding_fee_applied_to_inference": False},
            "scientific_costs": "Physical expenditure from both slot executions and TextGrad overhead is separate from selected-trajectory reference-priced rewards and selection scores.",
            "stop_behavior": "Existing cumulative ceiling, reservations and policy pauses stop safely; forecast does not reset spending or guarantee all scheduled work."}


def expected_patch(parent: Mapping[str, Any], prepared: Any, lineage: Mapping[str, Any],
                   authorization: Mapping[str, Any]) -> list[dict[str, Any]]:
    from .experiment import planning_hooks
    planned = planning_hooks(prepared)
    condition = planned._condition()
    updates = {"condition": condition, "regular_lineage": dict(lineage),
               "stage_authorizations": {"development": dict(authorization)}}
    changes = [{"path": ["hook_state", key], "value": value} for key, value in updates.items()]
    changes.append({"path": ["prompt_backend_metadata"], "value": planned.execution_metadata["textgrad"]})
    if parent.get("best_system") is not None:
        # Preserve numerical/RNG/score/prefix fields; the independently validated
        # provenance records why checkpoint0 can be selected under this policy.
        changes.extend({"path": ["best_system", "hook_state", key], "value": value}
                       for key, value in updates.items())
    return changes


def apply_transition(specification_path: str | Path, plan_path: str | Path,
                     authorization_path: str | Path, forecast_path: str | Path, *, build_factory: Any = None) -> dict[str, Any]:
    from ..infrastructure.storage import amendment_payload, compact_references, StartupVerification
    from .experiment import (_campaign_lock, _write_json_atomic, build_experiment,
                             validate_journal_resume, wire_durable_journal)
    from .policy_activation import _inert_model, bind_policy_dispatch
    from .preflight import load_stage_authorization
    from .publication_recovery import preserve_interruption

    with StartupVerification():
        plan = load_plan(plan_path, specification_path)
        source = _check_reference(plan["references"]["checkpoint"])
        parent_payload = load_checkpoint(source)
        parent = _parent_condition(specification_path, parent_payload)
        prepared = apply_prepared(parent, plan)
        directory = _inside(plan["campaign_directory"])
        checkpoint, storage = directory / "checkpoint.pt", directory / "regular_development"
        receipt_path = storage / "transition.json"
        lineage = {"protocol": LINEAGE_PROTOCOL, "plan_path": str(_inside(plan_path)),
                   "plan_sha256": _sha(_inside(plan_path)), "parent_checkpoint": copy.deepcopy(plan["references"]["checkpoint"])}
        with _campaign_lock(directory) as guard:
            validate_journal_resume(checkpoint, directory)
            bindings, _ = auth_bindings(parent, checkpoint, plan_path, forecast_path)
            authorization = load_stage_authorization(authorization_path, **bindings)
            current = load_checkpoint(checkpoint)
            existing = current["hook_state"].get("regular_lineage")
            if receipt_path.exists():
                receipt = read_json(receipt_path)
                if (receipt.get("lineage") != lineage or receipt.get("authorization") != _reference(authorization_path)
                        or existing != lineage):
                    raise ValueError("regular transition cannot replace its existing publication")
                from ..providers.resources import ExpenditureLedger
                bind_amended_policy(ExpenditureLedger(prepared.config.resources), specification_path, guard)
                return receipt
            if existing:
                if existing != lineage:
                    raise ValueError("unfinished regular transition belongs to another approval")
                from .publication_recovery import verify_patch
                from ..infrastructure.storage import load_ledger
                verify_patch(parent_payload, current,
                    expected_patch(parent_payload, prepared, lineage, authorization),
                    load_ledger(directory / "expenditure_ledger.json"))
            else:
                if _sha(checkpoint) != plan["references"]["checkpoint"]["sha256"] or _baseline(current) != plan["baseline"]:
                    raise ValueError("campaign advanced beyond approved checkpoint0")
                trainer, hooks = (build_factory or build_experiment)(parent, model_factory=_inert_model)
                hooks.authorization_required, hooks.execution_session_guard = True, guard
                bind_policy_dispatch(hooks.expenditure_ledger, specification_path, guard)
                trainer.resume_from_checkpoint(checkpoint, hooks=hooks)
                trainer.hooks, trainer.checkpoint_path = hooks, checkpoint
                wire_durable_journal(trainer, hooks, directory)
                async def publish() -> None:
                    try:
                        preserve_interruption(directory, storage / "before", protocol=TRANSITION_PROTOCOL,
                            names=("checkpoint.pt", "expenditure_ledger.json", "result.json", "training_diagnostics.json",
                                   "experiment_manifest.json", "preflight.json", "data_manifest.json"))
                        hooks.apply_regular(prepared, plan, lineage)
                        hooks.authorize_stage(authorization)
                        changes = expected_patch(parent_payload, prepared, lineage, authorization)
                        patches = [{"path": item["path"], "value": compact_references(item["value"], directory)}
                                   for item in changes]
                        envelope = amendment_payload(source, plan["references"]["checkpoint"]["sha256"], patches, target=checkpoint)
                        ledger_state = await trainer.durable_journal.prepare_checkpoint_state(
                            lambda: hooks.expenditure_ledger.checkpoint_state_dict(trainer.durable_journal.evidence_store))
                        await trainer.durable_journal.amendment(envelope, checkpoint, ledger_state, safe=True,
                                                               recovery_fingerprint=plan["fingerprint"])
                    finally:
                        await trainer.durable_journal.aclose()
                asyncio.run(publish())
            # A committed write invalidates the old startup verification context.
            receipt = {"protocol": TRANSITION_PROTOCOL, "lineage": lineage, "plan_fingerprint": plan["fingerprint"],
                       "authorization": _reference(authorization_path), "source_checkpoint": plan["references"]["checkpoint"],
                       "checkpoint_at_publication": _reference(checkpoint), "generation_calls": 0, "spending_reset": False,
                       "preservation": str(storage / "before" / "PRESERVATION.json")}
            _write_json_atomic(receipt_path, receipt)
    with StartupVerification():
        validate_journal_resume(checkpoint, directory)
    return receipt
