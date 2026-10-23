"""Audited prospective uncertainty amendment and calibration-only publication.

Historical scientific conditions, policy approvals and expenditure stay intact.
This operation changes exactly two admission limits; it grants no training scope.
"""
from __future__ import annotations

from ..infrastructure.storage import read_json, strict_json_equal

import asyncio
import copy
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Mapping

from .supplement import _canonical, _check_reference, _digest, _inside, _reference, _sha

PLAN_PROTOCOL = "calibration_limit_plan_v1"
LINEAGE_PROTOCOL = "audited_calibration_limits_v1"
TRANSITION_PROTOCOL = "calibration_limit_transition_v1"


def amendment() -> dict[str, Any]:
    return {"max_unresolved_requests": 32, "max_unresolved_reserved_usd": 5.0}


def _baseline(payload: Mapping[str, Any]) -> dict[str, Any]:
    state, hooks = payload["trainer_state"], payload["hook_state"]
    ledger = hooks["expenditure_ledger"]
    from ..infrastructure.failure_policy import FailurePolicyConfig, logical_summary
    from ..providers.resources import ExpenditureLedger
    from ..config import Config
    config = Config.from_dict(payload["declared_config"])
    if (payload.get("request_boundary_pending") is not False
            or state.get("phase") != "calibration" or state.get("phase_progress")
            or state.get("calibration_complete") or state.get("outer_cycle")
            or not state.get("pilot_complete") or state.get("system_validation_records")
            or state.get("bootstrap_complete") != {"weighting": True, "routing": True}
            or state.get("attempts") != {"weighting": 43, "routing": 10, "prompt": 0}
            or state.get("updates") != {"weighting": 10, "routing": 10, "prompt": 0}
            or config.failure_policy != FailurePolicyConfig(enabled=True, max_unresolved_requests=8)
            or hooks.get("termination_record") is not None
            or not hooks.get("supplement_lineage") or hooks.get("calibration_lineage")
            or ledger.get("halted")):
        raise ValueError("calibration amendment requires the completed W10/43 alpha10 pre-calibration boundary")
    policy = ledger.get("failure_policy")
    if not policy or policy["config"] != asdict(config.failure_policy):
        raise ValueError("calibration parent policy does not match its recorded condition")
    if logical_summary(policy)["pending_logical_ids"] or any(e["event"] == "drain" for e in policy["events"]):
        raise ValueError("calibration amendment cannot waive unfinished requests or a persistent pause")
    probe = ExpenditureLedger(config.resources)
    probe.load_state_dict(ledger)
    if probe.uncertainty_summary()["inflight_ids"]:
        raise ValueError("calibration amendment requires a completed physical-request boundary")
    return {"trainer_state_sha256": _digest(state), "ledger_sha256": _digest(ledger),
            "receipt_count": len(ledger["entries"]), "parent_policy_sha256": _digest(policy),
            "sample_counters": copy.deepcopy(hooks["sample_counters"])}


def prepare_plan(specification_path: str | Path, checkpoint_snapshot_path: str | Path,
                 formal_source_path: str | Path, approval_source_path: str | Path,
                 forecast_path: str | Path | None = None) -> dict[str, Any]:
    from ..infrastructure.storage import load_checkpoint
    from .experiment import prepare_experiment, planning_hooks
    from .supplement import prepare_checkpoint_condition as parent_condition
    source, snapshot = _inside(specification_path), _inside(checkpoint_snapshot_path)
    if snapshot == source.parent / "checkpoint.pt":
        raise ValueError("calibration source must be an immutable preserved checkpoint")
    payload = load_checkpoint(snapshot)
    parent = parent_condition(prepare_experiment(source), payload, specification_path=source)
    if parent.extension_registration:
        raise ValueError("this narrow calibration amendment applies to the registered primary campaign")
    plan = {"protocol": PLAN_PROTOCOL, "campaign_id": parent.campaign_binding["campaign_id"],
            "campaign_directory": parent.campaign_binding["directory"],
            "references": {"manifest": _reference(source), "checkpoint": _reference(snapshot),
                "formal_specification": _reference(formal_source_path), "approval_source": _reference(approval_source_path),
                "forecast": None if forecast_path is None else _reference(forecast_path)},
            "parent": {"condition_fingerprint": planning_hooks(parent).condition_fingerprint,
                "manifest_fingerprint": parent.authorization_manifest_fingerprint,
                "resource_profile_fingerprint": parent.resource_profile_fingerprint,
                "specification_sha256": parent.specification_sha256},
            "amendment": amendment(), "baseline": _baseline(payload)}
    plan["fingerprint"] = _digest(plan)
    return plan


def load_plan(path: str | Path, specification_path: str | Path) -> dict[str, Any]:
    plan = read_json(_inside(path))
    if (set(plan) != {"protocol", "campaign_id", "campaign_directory", "references", "parent", "amendment", "baseline", "fingerprint"}
            or plan.get("protocol") != PLAN_PROTOCOL or plan.get("amendment") != amendment()
            or _digest({k: v for k, v in plan.items() if k != "fingerprint"}) != plan.get("fingerprint")):
        raise ValueError("invalid calibration amendment plan")
    refs = plan["references"]
    if set(refs) != {"manifest", "checkpoint", "formal_specification", "approval_source", "forecast"}:
        raise ValueError("calibration plan lacks immutable source references")
    checked = {k: _check_reference(v) for k, v in refs.items()}
    if checked["manifest"] != _inside(specification_path):
        raise ValueError("calibration plan belongs to another campaign manifest")
    expected = prepare_plan(checked["manifest"], checked["checkpoint"], checked["formal_specification"],
                            checked["approval_source"], checked["forecast"])
    if plan != expected:
        raise ValueError("calibration plan differs from its immutable source boundary")
    return plan


def apply_prepared(parent: Any, plan: Mapping[str, Any]) -> Any:
    from ..infrastructure.failure_policy import FailurePolicyConfig
    from .preflight import gateway_fingerprint_fields
    from .experiment import planning_hooks
    expected = {"condition_fingerprint": planning_hooks(parent).condition_fingerprint,
                "manifest_fingerprint": parent.authorization_manifest_fingerprint,
                "resource_profile_fingerprint": parent.resource_profile_fingerprint,
                "specification_sha256": parent.specification_sha256}
    if (plan.get("protocol") != PLAN_PROTOCOL or plan.get("amendment") != amendment()
            or plan["parent"] != expected or parent.extension_registration
            or parent.config.failure_policy != FailurePolicyConfig(enabled=True, max_unresolved_requests=8)):
        raise ValueError("calibration amendment does not match its exact parent condition")
    config = replace(parent.config, failure_policy=replace(parent.config.failure_policy, **amendment()))
    record = {"protocol": PLAN_PROTOCOL, "parent": copy.deepcopy(expected), "amendment": amendment(),
              "formal_specification_sha256": plan["references"]["formal_specification"]["sha256"]}
    scientific_hash = _digest(record)
    record["target_specification_sha256"] = scientific_hash
    resource_hash = _digest({"resources": asdict(config.resources), "failure_policy": asdict(config.failure_policy),
        "backends": {key: {name: gateway_fingerprint_fields(backend.get(name)) if name == "gateway" else backend.get(name)
                     for name in ("model_name", "revision", "resources", "gateway", "billing_verification", "role_output")}
                     for key, backend in parent.backends.items()}})
    plans = {key: replace(value, execution_configuration={**dict(value.execution_configuration),
              "specification_sha256": scientific_hash}) for key, value in parent.plans.items()}
    return replace(parent, config=config, plans=plans,
        termination_plan=replace(parent.termination_plan, validation=plans["system"]),
        specification_sha256=scientific_hash, resource_profile_fingerprint=resource_hash,
        authorization_manifest_fingerprint=_digest({"parent": parent.authorization_manifest_fingerprint, **record}),
        specification_amendments=(*parent.specification_amendments, record))


def reconstruct_calibration_lineage(lineage: Mapping[str, Any], *, specification_path: str | Path | None = None) -> tuple[Any, Any, dict[str, Any]]:
    from ..infrastructure.storage import load_checkpoint
    from .experiment import prepare_experiment
    from .supplement import prepare_checkpoint_condition as parent_condition
    if (not isinstance(lineage, Mapping) or set(lineage) != {"protocol", "plan_path", "plan_sha256", "parent_checkpoint"}
            or lineage["protocol"] != LINEAGE_PROTOCOL):
        raise ValueError("unregistered calibration condition lineage")
    path = _inside(lineage["plan_path"])
    if _sha(path) != lineage["plan_sha256"]:
        raise ValueError("calibration plan bytes changed")
    source = read_json(path)["references"]["manifest"]["path"]
    if specification_path is not None and _inside(source) != _inside(specification_path):
        raise ValueError("calibration lineage belongs to another manifest")
    plan = load_plan(path, source)
    if lineage["parent_checkpoint"] != plan["references"]["checkpoint"]:
        raise ValueError("calibration parent checkpoint changed")
    payload = load_checkpoint(_check_reference(plan["references"]["checkpoint"]))
    parent = parent_condition(prepare_experiment(source), payload, specification_path=source)
    return parent, apply_prepared(parent, plan), plan


def prepare_checkpoint_condition(prepared: Any, payload: Mapping[str, Any], *, specification_path: str | Path | None = None) -> Any:
    from ..config import Config
    from .experiment import planning_hooks
    lineage = payload.get("hook_state", {}).get("calibration_lineage")
    parent, amended, _ = reconstruct_calibration_lineage(lineage, specification_path=specification_path)
    # The unamended manifest is still the immutable source; its exact ancestry
    # was reconstructed above. No current runtime setting can substitute for it.
    if prepared.campaign_binding != amended.campaign_binding:
        raise ValueError("calibration checkpoint belongs to another campaign")
    saved = Config.from_dict(payload["declared_config"])
    saved = replace(saved, training=replace(saved.training, max_outer_cycles=amended.config.training.max_outer_cycles))
    if (saved != amended.config or _canonical(payload["hook_state"].get("condition")) != _canonical(planning_hooks(amended)._condition())):
        raise ValueError("calibration checkpoint differs from its independently reconstructed condition")
    del parent
    return amended


def auth_bindings(prepared: Any, checkpoint_path: str | Path, plan_path: str | Path,
                  forecast_path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from ..infrastructure.storage import load_checkpoint
    from .experiment import prepare_experiment, planning_hooks
    from .supplement import prepare_checkpoint_condition as condition
    from .preflight import authorization_bindings, CALIBRATION_SCOPE, _positive_money
    raw = read_json(_inside(plan_path))
    source = raw["references"]["manifest"]["path"]
    plan = load_plan(plan_path, source)
    parent_payload = load_checkpoint(_check_reference(plan["references"]["checkpoint"]))
    parent = condition(prepare_experiment(source), parent_payload, specification_path=source)
    amended = apply_prepared(parent, plan)
    if prepared.campaign_binding != amended.campaign_binding:
        raise ValueError("calibration authorization belongs to another campaign")
    current = load_checkpoint(checkpoint_path)
    if _sha(Path(checkpoint_path)) == plan["references"]["checkpoint"]["sha256"]:
        bindings, _ = authorization_bindings(parent, "development", checkpoint_path)
        bindings.update(manifest_fingerprint=amended.authorization_manifest_fingerprint,
                        resource_profile_fingerprint=amended.resource_profile_fingerprint,
                        condition_fingerprint=planning_hooks(amended).condition_fingerprint)
    else:
        condition(prepared, current, specification_path=source)
        bindings, _ = authorization_bindings(amended, "development", checkpoint_path)
    forecast = read_json(_inside(forecast_path))
    keys = ("manifest_fingerprint", "condition_fingerprint", "resource_profile_fingerprint", "pilot_fingerprint")
    if (_reference(forecast_path) != plan["references"]["forecast"]
            or forecast.get("protocol") != "calibration_forecast_v1"
            or forecast.get("bindings") != {k: bindings[k] for k in keys}
            or forecast.get("development_ceiling_usd") != bindings["approved_budget_usd"] or not forecast.get("basis")
            or not _positive_money(forecast.get("estimated_cost_usd")) or not _positive_money(forecast.get("headroom_usd"))
            or forecast["estimated_cost_usd"] + forecast["headroom_usd"] > bindings["approved_budget_usd"]):
        raise ValueError("calibration forecast differs from its exact condition or cumulative budget")
    state = current["trainer_state"]
    if (state.get("phase") not in {"calibration", "system_baseline", "weighting"} or state.get("outer_cycle")
            or state.get("attempts") != plan["baseline"].get("attempts", parent_payload["trainer_state"]["attempts"])
            or state.get("updates") != parent_payload["trainer_state"]["updates"]):
        raise ValueError("calibration-only approval cannot authorize numerical or later-cycle work")
    bindings.update(execution_scope=CALIBRATION_SCOPE, calibration_plan_sha256=_sha(_inside(plan_path)),
                    calibration_forecast_sha256=_sha(_inside(forecast_path)))
    return bindings, forecast


def policy_bindings(plan: Mapping[str, Any], prepared: Any) -> dict[str, Any]:
    from .experiment import planning_hooks
    return {"condition_fingerprint": planning_hooks(prepared).condition_fingerprint,
            "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
            "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
            "campaign_id": plan["campaign_id"], "plan_fingerprint": plan["fingerprint"],
            "formal_specification_sha256": plan["references"]["formal_specification"]["sha256"],
            "stage_authorization_required": True}


def policy_amendment(plan: Mapping[str, Any], parent_policy: Mapping[str, Any], authorization_path: str | Path) -> dict[str, Any]:
    from ..infrastructure.failure_policy import fingerprint
    return {"protocol": TRANSITION_PROTOCOL, "plan_fingerprint": plan["fingerprint"],
            "authorization": _reference(authorization_path),
            "parent_policy": copy.deepcopy(dict(parent_policy)),
            "parent_policy_sha256": fingerprint(parent_policy), "receipt_count": plan["baseline"]["receipt_count"]}


def policy_authorization(plan: Mapping[str, Any], validated_stage: Mapping[str, Any]) -> dict[str, Any]:
    """The validated scoped approval expressly includes these two policy limits."""
    from .preflight import CALIBRATION_SCOPE
    if validated_stage.get("decision") != "approve_stage" or validated_stage.get("scope") != CALIBRATION_SCOPE:
        raise ValueError("calibration policy amendment requires its explicit scoped stage approval")
    return {"approved": True, "scope": "calibration_limit_policy_only",
            "stage_authorization": copy.deepcopy(dict(validated_stage)),
            "approval_source": copy.deepcopy(plan["references"]["approval_source"]),
            "formal_specification": copy.deepcopy(plan["references"]["formal_specification"])}


def bind_amended_policy(ledger: Any, source: str | Path, execution_guard: Any) -> bool:
    """Validate the durable prospective policy, retaining its original activation."""
    directory = Path(source).resolve().parent
    receipt_path = directory / "calibration" / "transition.json"
    if not receipt_path.exists():
        return False
    from .policy_activation import _validate_independent_activation
    _validate_independent_activation(read_json(source), directory)
    receipt = read_json(receipt_path)
    parent, prepared, plan = reconstruct_calibration_lineage(receipt["lineage"], specification_path=source)
    if (receipt.get("protocol") != TRANSITION_PROTOCOL or receipt.get("plan_fingerprint") != plan["fingerprint"]
            or receipt.get("source_checkpoint") != plan["references"]["checkpoint"]
            or receipt.get("generation_calls") != 0 or receipt.get("spending_reset") is not False):
        raise ValueError("calibration policy publication receipt changed")
    from .preflight import load_stage_authorization
    bindings, _ = auth_bindings(parent, directory / "checkpoint.pt", receipt["lineage"]["plan_path"], plan["references"]["forecast"]["path"])
    authorization_path = _check_reference(receipt["authorization"])
    authorization = load_stage_authorization(authorization_path, **bindings)
    from ..infrastructure.storage import load_checkpoint
    payload = load_checkpoint(plan["references"]["checkpoint"]["path"])
    audit = policy_amendment(plan, payload["hook_state"]["expenditure_ledger"]["failure_policy"], authorization_path)
    from ..infrastructure.storage import load_ledger
    canonical = load_ledger(directory / "expenditure_ledger.json")
    if not ledger.entries and ledger.failure_policy is None:
        ledger.load_state_dict(canonical)
    if not strict_json_equal(ledger.state_dict(), canonical):
        raise ValueError("amended policy ledger differs from its canonical durable state")
    ledger.activate_failure_policy(prepared.config.failure_policy, authorization=policy_authorization(plan, authorization),
        bindings=policy_bindings(plan, prepared), execution_guard=execution_guard, condition_amendments=[audit])
    return True


def apply_transition(specification_path: str | Path, plan_path: str | Path,
                     authorization_path: str | Path, forecast_path: str | Path, *, build_factory: Any = None) -> dict[str, Any]:
    from ..infrastructure.storage import load_checkpoint
    from .experiment import _campaign_lock, _write_json_atomic, prepare_experiment, build_experiment, validate_journal_resume, wire_durable_journal
    from .policy_activation import _inert_model, bind_policy_dispatch
    from .preflight import load_stage_authorization
    from .supplement import prepare_checkpoint_condition as condition
    plan = load_plan(plan_path, specification_path)
    parent_payload = load_checkpoint(plan["references"]["checkpoint"]["path"])
    parent = condition(prepare_experiment(specification_path), parent_payload, specification_path=specification_path)
    parent_policy = copy.deepcopy(parent_payload["hook_state"]["expenditure_ledger"]["failure_policy"])
    del parent_payload
    prepared = apply_prepared(parent, plan)
    directory = _inside(plan["campaign_directory"])
    checkpoint, storage = directory / "checkpoint.pt", directory / "calibration"
    receipt_path = storage / "transition.json"
    lineage = {"protocol": LINEAGE_PROTOCOL, "plan_path": str(_inside(plan_path)), "plan_sha256": _sha(_inside(plan_path)),
               "parent_checkpoint": copy.deepcopy(plan["references"]["checkpoint"])}
    with _campaign_lock(directory) as guard:
        validate_journal_resume(checkpoint, directory, allow_halted=True)
        bindings, _ = auth_bindings(parent, checkpoint, plan_path, forecast_path)
        authorization = load_stage_authorization(authorization_path, **bindings)
        payload = load_checkpoint(checkpoint)
        existing = payload["hook_state"].get("calibration_lineage")
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            if (receipt.get("protocol") != TRANSITION_PROTOCOL or receipt.get("lineage") != lineage
                    or receipt.get("authorization") != _reference(authorization_path) or existing != lineage):
                raise ValueError("calibration transition cannot be replaced")
            from ..providers.resources import ExpenditureLedger
            bind_amended_policy(ExpenditureLedger(prepared.config.resources), specification_path, guard)
            return receipt
        if existing:
            if existing != lineage or payload["declared_config"] != prepared.config.to_dict():
                raise ValueError("unfinished calibration transition has different bindings")
        else:
            if _sha(checkpoint) != plan["references"]["checkpoint"]["sha256"] or _baseline(payload) != plan["baseline"]:
                raise ValueError("campaign advanced beyond the approved pre-calibration source")
            from .publication_recovery import expected_patch, preserve_interruption, publish_policy_amendment
            changes = expected_patch(payload, prepared, plan, lineage, authorization, _inside(authorization_path))
            immutable_hook_evidence = payload["hook_state"]
            # Policy-only publication references the exact preserved parent;
            # unchanged tensors, RNG and old completed evidence are not saved again.
            del payload
            intent = {"protocol": TRANSITION_PROTOCOL, "lineage": lineage, "authorization": _reference(authorization_path)}
            intent_path = storage / "transition_intent.json"
            if intent_path.exists() and json.loads(intent_path.read_text()) != intent:
                raise ValueError("unfinished calibration intent changed")
            trainer, hooks = (build_factory or build_experiment)(parent, model_factory=_inert_model)
            hooks.authorization_required = True
            hooks.execution_session_guard = guard
            bind_policy_dispatch(hooks.expenditure_ledger, specification_path, guard)
            trainer.resume_from_checkpoint(checkpoint, hooks=hooks)
            trainer.hooks, trainer.checkpoint_path = hooks, checkpoint
            wire_durable_journal(trainer, hooks, directory)
            async def publish() -> None:
                try:
                    preserve_interruption(directory, storage / "before", protocol=TRANSITION_PROTOCOL,
                        names=("checkpoint.pt", "expenditure_ledger.json", "result.json", "training_diagnostics.json",
                               "experiment_manifest.json", "preflight.json"))
                    if not intent_path.exists():
                        await trainer.durable_journal.artifact(intent_path, intent)
                    hooks.apply_calibration(prepared, plan, lineage)
                    hooks.authorize_stage(authorization)
                    audit = policy_amendment(plan, parent_policy, authorization_path)
                    hooks.expenditure_ledger.amend_failure_policy(prepared.config.failure_policy,
                        authorization=policy_authorization(plan, authorization), bindings=policy_bindings(plan, prepared),
                        execution_guard=guard, amendment=audit)
                    ledger = hooks.expenditure_ledger
                    state = {**ledger.state_dict(include_entries=False), "entries": ledger.entries}
                    await publish_policy_amendment(trainer.durable_journal, directory,
                        plan["references"]["checkpoint"], changes, state, plan["fingerprint"],
                        hook_state=immutable_hook_evidence)
                finally:
                    await trainer.durable_journal.aclose()
            asyncio.run(publish())
            validate_journal_resume(checkpoint, directory, allow_halted=True)
        receipt = {"protocol": TRANSITION_PROTOCOL, "lineage": lineage, "plan_fingerprint": plan["fingerprint"],
                   "authorization": _reference(authorization_path), "source_checkpoint": plan["references"]["checkpoint"],
                   "checkpoint_at_publication": _reference(checkpoint), "generation_calls": 0, "spending_reset": False,
                   "preservation": str(storage / "before" / "PRESERVATION.json")}
        _write_json_atomic(receipt_path, receipt)
        return json.loads(_canonical(receipt))
