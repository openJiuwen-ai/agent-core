"""Explicit prospective bootstrap amendment within one unchanged campaign.

This is not an ordinary-resume compatibility exception. The original checkpoint
is validated under its original condition before a separately authorized new
condition is published. Receipts, limits, optimizer state and historical records
are never converted or replenished by this operation.
"""

from __future__ import annotations

from ..paths import PACKAGE_ROOT
from ..infrastructure.storage import read_json, file_sha256, canonical_sha256

from ..infrastructure.storage import load_checkpoint

import asyncio
import copy
import json
import os
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Mapping

PLAN_PROTOCOL = "bootstrap_supplement_plan_v1"
TRANSITION_PROTOCOL = "bootstrap_supplement_transition_v1"
ROOT = PACKAGE_ROOT


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: Any) -> str:
    return canonical_sha256(value)


def _sha(path: Path) -> str:
    return file_sha256(path)

def _inside(value: str | Path) -> Path:
    path = Path(value).absolute()
    if not path.resolve().is_relative_to(ROOT) or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("supplement artifacts must stay inside the example without symlinks")
    return path.resolve()


def _reference(path: str | Path) -> dict[str, str]:
    source = _inside(path)
    return {"path": str(source), "sha256": _sha(source)}


def _check_reference(value: Mapping[str, Any]) -> Path:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise ValueError("supplement references require exact path and SHA-256")
    path = _inside(value["path"])
    if not path.is_file() or _sha(path) != value["sha256"]:
        raise ValueError("supplement immutable reference changed: " + str(path))
    return path


def amendment() -> dict[str, Any]:
    from ..workflows.recovery import WorkflowRecoveryConfig

    return {
        "weighting_bootstrap_max_slots": 50,
        "workflow_recovery": asdict(WorkflowRecoveryConfig(enabled=True)),
        "maximum_new_weighting_slots": 20,
        "maximum_new_weighting_updates": 2,
        "frozen_components": ["alpha", "gamma"],
        "final_boundary": "calibration",
        "unchanged_weighting_recovery_query": "1d33f1d899f3cbc6ba6f",
    }


def _baseline(payload: Mapping[str, Any]) -> dict[str, Any]:
    state = payload["trainer_state"]
    if (payload.get("request_boundary_pending") is not False
            or state.get("phase") != "calibration" or state.get("phase_progress")
            or state.get("weighting_supplement") or state.get("outer_cycle")
            or not state.get("pilot_complete") or state.get("calibration_complete")
            or state.get("bootstrap_complete") != {"weighting": True, "routing": True}
            or state.get("attempts") != {"weighting": 30, "routing": 10, "prompt": 0}
            or state.get("updates") != {"weighting": 8, "routing": 10, "prompt": 0}
            or state.get("workflow_attempt_counter") != 40
            or state.get("system_validation_records")):
        raise ValueError("supplement requires the exact completed W8/30 alpha10/10 boundary")
    phases = state.get("phase_records", [])
    if ([item.get("phase") for item in phases] != ["weighting_bootstrap", "routing_bootstrap"]
            or [(item.get("attempts"), item.get("updates")) for item in phases] != [(30, 8), (10, 10)]):
        raise ValueError("supplement original bootstrap phase records changed")
    ledger = payload["hook_state"]["expenditure_ledger"]
    if ledger.get("halted"):
        raise ValueError("supplement cannot waive a campaign halt")
    return {
        "phase": state["phase"], "attempts": copy.deepcopy(state["attempts"]),
        "updates": copy.deepcopy(state["updates"]), "workflow_attempt_counter": 40,
        "phase_records_sha256": _digest(phases),
        "sample_counters": copy.deepcopy(payload["hook_state"]["sample_counters"]),
        "ledger_sha256": _digest(ledger), "receipt_count": len(ledger["entries"]),
    }


def prepare_supplement_plan(
    specification_path: str | Path, checkpoint_snapshot_path: str | Path,
    formal_source_path: str | Path, approval_source_path: str | Path,
    forecast_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a pending plan; creation grants no authorization or mutable state."""
    from .experiment import planning_hooks, prepare_experiment

    source = _inside(specification_path)
    prepared = prepare_experiment(source)
    if prepared.continuation_registration or prepared.extension_registration:
        raise ValueError("bootstrap supplement requires the original primary condition")
    snapshot = _inside(checkpoint_snapshot_path)
    if snapshot == source.parent / "checkpoint.pt":
        raise ValueError("supplement source must be a preserved checkpoint snapshot")
    payload = load_checkpoint(snapshot)
    hooks = planning_hooks(prepared)
    plan = {
        "protocol": PLAN_PROTOCOL,
        "campaign_id": prepared.campaign_binding["campaign_id"],
        "campaign_directory": prepared.campaign_binding["directory"],
        "references": {
            "manifest": _reference(source), "checkpoint": _reference(snapshot),
            "formal_specification": _reference(formal_source_path),
            "approval_source": _reference(approval_source_path),
            "forecast": None if forecast_path is None else _reference(forecast_path),
        },
        "parent": {
            "condition_fingerprint": hooks.condition_fingerprint,
            "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
            "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
            "specification_sha256": prepared.specification_sha256,
        },
        "amendment": amendment(), "baseline": _baseline(payload),
    }
    plan["fingerprint"] = _digest(plan)
    return plan


def load_supplement_plan(path: str | Path, specification_path: str | Path) -> dict[str, Any]:
    source = _inside(path)
    plan = read_json(source)
    if (set(plan) != {"protocol", "campaign_id", "campaign_directory", "references",
                      "parent", "amendment", "baseline", "fingerprint"}
            or plan.get("protocol") != PLAN_PROTOCOL
            or plan.get("amendment") != amendment()
            or _digest({key: value for key, value in plan.items() if key != "fingerprint"}) != plan["fingerprint"]):
        raise ValueError("invalid or modified supplementary plan")
    references = plan["references"]
    if set(references) != {"manifest", "checkpoint", "formal_specification", "approval_source", "forecast"}:
        raise ValueError("supplement requires every immutable reference")
    checked = {key: _check_reference(value) for key, value in references.items()}
    if checked["manifest"] != _inside(specification_path):
        raise ValueError("supplement belongs to another registered manifest")
    expected = prepare_supplement_plan(
        checked["manifest"], checked["checkpoint"], checked["formal_specification"],
        checked["approval_source"], checked["forecast"],
    )
    if plan != expected:
        raise ValueError("supplement differs from the registered condition or preserved boundary")
    return plan


def apply_prepared(prepared: Any, plan: Mapping[str, Any]) -> Any:
    """Pure prospective configuration; do not alter original manifest or plans."""
    from ..workflows.recovery import WorkflowRecoveryConfig

    parent = plan["parent"]
    if (plan.get("protocol") != PLAN_PROTOCOL or plan.get("amendment") != amendment()
            or prepared.continuation_registration or prepared.extension_registration
            or parent["specification_sha256"] != prepared.specification_sha256
            or parent["manifest_fingerprint"] != prepared.authorization_manifest_fingerprint
            or parent["resource_profile_fingerprint"] != prepared.resource_profile_fingerprint
            or prepared.config.training.bootstrap_weighting_attempts != 30
            or prepared.config.workflow_recovery.enabled):
        raise ValueError("prospective amendment does not match its original condition")
    source_hash = plan["references"]["formal_specification"]["sha256"]
    condition = {"protocol": PLAN_PROTOCOL, "parent": copy.deepcopy(parent),
                 "amendment": amendment(), "formal_specification_sha256": source_hash}
    scientific_hash = _digest(condition)
    registration = {**condition, "target_specification_sha256": scientific_hash}
    # The source remains a read-only historical record. It cannot enter a new
    # validation unless the evaluator verifies the exact unchanged joint tuple.
    registration["supplement_routing_source"] = routing_source(plan)
    config = replace(
        prepared.config,
        training=replace(prepared.config.training, bootstrap_weighting_attempts=50),
        workflow_recovery=WorkflowRecoveryConfig(enabled=True),
    )
    plans = {
        key: replace(value, execution_configuration={
            **dict(value.execution_configuration), "specification_sha256": scientific_hash,
        })
        for key, value in prepared.plans.items()
    }
    return replace(
        prepared, config=config, plans=plans,
        termination_plan=replace(prepared.termination_plan, validation=plans["system"]),
        specification_sha256=scientific_hash,
        authorization_manifest_fingerprint=_digest({"parent": parent["manifest_fingerprint"], **condition}),
        specification_amendments=(*prepared.specification_amendments, registration),
        continuation_registration=registration,
    )


def reconstruct_condition_lineage(
    lineage: Mapping[str, Any], *, specification_path: str | Path | None = None,
) -> tuple[Any, Any, dict[str, Any]]:
    """Read an already published condition's exact ancestry; grant no execution scope."""
    from .experiment import prepare_experiment

    if (not isinstance(lineage, Mapping)
            or set(lineage) != {"protocol", "plan_path", "plan_sha256", "parent_checkpoint"}
            or lineage["protocol"] != "audited_weighting_supplement_v1"):
        raise ValueError("unregistered supplementary condition lineage")
    plan_path = _inside(lineage["plan_path"])
    if _sha(plan_path) != lineage["plan_sha256"]:
        raise ValueError("supplement plan bytes changed")
    raw = read_json(plan_path)
    source = raw["references"]["manifest"]["path"]
    if specification_path is not None and _inside(specification_path) != _inside(source):
        raise ValueError("checkpoint amendment belongs to another manifest")
    plan = load_supplement_plan(plan_path, source)
    if lineage["parent_checkpoint"] != plan["references"]["checkpoint"]:
        raise ValueError("supplement source checkpoint changed")
    original = prepare_experiment(source)
    return original, apply_prepared(original, plan), plan


def prepare_checkpoint_condition(
    prepared: Any, payload: Mapping[str, Any], *, specification_path: str | Path | None = None,
) -> Any:
    """Reconstruct only a checkpoint's published condition, independently of its approval.

    This cannot amend an original checkpoint or select the supplementary W
    operation. Ordinary checkpoint/authorization validators still run afterward.
    """
    from ..config import Config
    from .experiment import planning_hooks

    state = payload.get("hook_state") or {}
    if state.get("regular_lineage") is not None:
        from .regular_development import prepare_checkpoint_condition as regular_condition
        return regular_condition(prepared, payload, specification_path=specification_path)
    if (state.get("condition") or {}).get("execution_metadata", {}).get("registered_regular"):
        raise ValueError("regular checkpoint lacks its audited TextGrad amendment lineage")
    if state.get("calibration_lineage") is not None:
        from .calibration_launch import prepare_checkpoint_condition as calibration_condition
        return calibration_condition(prepared, payload, specification_path=specification_path)
    if (state.get("condition") or {}).get("execution_metadata", {}).get("registered_calibration"):
        raise ValueError("amended checkpoint lacks its audited calibration lineage")
    lineage = state.get("supplement_lineage")
    if lineage is None:
        recorded_amendment = (state.get("condition") or {}).get("execution_metadata", {}).get("registered_supplement")
        saved = Config.from_dict(payload["declared_config"]) if payload.get("declared_config") else None
        if (getattr(prepared, "continuation_registration", None) or recorded_amendment
                or (saved is not None and saved.workflow_recovery != prepared.config.workflow_recovery)):
            raise ValueError("amended checkpoint lacks its audited supplement lineage")
        return prepared
    original, amended, _plan = reconstruct_condition_lineage(lineage, specification_path=specification_path)
    if (prepared.extension_registration
            or prepared.authorization_manifest_fingerprint not in {
                original.authorization_manifest_fingerprint, amended.authorization_manifest_fingerprint}
            or prepared.campaign_binding != amended.campaign_binding):
        raise ValueError("checkpoint amendment differs from the prepared parent condition")
    saved = Config.from_dict(payload["declared_config"])
    # The existing checkpoint contract permits an explicit outer-cycle ceiling
    # extension; every scientific coordinate, including recovery, stays exact.
    saved = replace(saved, training=replace(saved.training,
        max_outer_cycles=amended.config.training.max_outer_cycles))
    if (saved != amended.config
            or _canonical(state.get("condition")) != _canonical(planning_hooks(amended)._condition())):
        raise ValueError("checkpoint differs from its independently reconstructed amended condition")
    return amended


def routing_source(plan: Mapping[str, Any]) -> dict[str, Any]:

    snapshot = _check_reference(plan["references"]["checkpoint"])
    payload = load_checkpoint(snapshot)
    _baseline(payload)
    phase = payload["trainer_state"]["phase_records"][-1]
    evaluation = phase["records"][-1]
    source_id = amendment()["unchanged_weighting_recovery_query"]
    if (evaluation["plan"]["domain"] != "alphaval"
            or len(evaluation["examples"]) != 20
            or sum(item["example_id"] == source_id for item in evaluation["examples"]) != 1):
        raise ValueError("supplement source is not the complete original routing exit record")
    entries = payload["hook_state"]["expenditure_ledger"]["entries"]
    failures = [entry for entry in entries if entry["dispatch_id"] == 883]
    if (len(failures) != 1 or failures[0]["usage"].get("failure_kind") != "provider-unavailable"
            or failures[0]["charged"] is not None or failures[0]["usage"].get("generation_id") is not None):
        raise ValueError("supplement original unavailable-usage evidence changed")
    return {
        "evaluation": copy.deepcopy(evaluation), "evaluation_sha256": _digest(evaluation),
        "alpha_fingerprint": phase["parameter_fingerprints_exit"]["alpha"],
        "weighting_fingerprint": phase["parameter_fingerprints_exit"]["weighting"],
        "gamma": copy.deepcopy(payload["gamma"]),
        "source_checkpoint_sha256": plan["references"]["checkpoint"]["sha256"],
        "failure_evidence": copy.deepcopy(failures[0]),
    }


def _preserve_publications(directory: Path, target: Path, *, protocol: str = TRANSITION_PROTOCOL) -> dict[str, Any]:
    """One verified copy of mutable publications; retain the append-only WAL by prefix."""
    from .experiment import _write_json_atomic

    target.mkdir(parents=True, exist_ok=True)
    records = []
    for name in ("checkpoint.pt", "expenditure_ledger.json", "result.json", "training_diagnostics.json",
                 "experiment_manifest.json", "preflight.json"):
        old = directory / name
        if not old.is_file():
            continue
        new = target / name
        digest = _sha(old)
        if new.exists():
            if _sha(new) != digest:
                raise ValueError("pre-supplement preservation collision: " + name)
        else:
            temporary = new.with_suffix(new.suffix + ".pending")
            if temporary.exists():
                if temporary.is_symlink() or not temporary.is_file():
                    raise ValueError("unsafe pre-supplement temporary publication")
                # Only this narrowly named unpublished copy is reproducible;
                # the source and any already published evidence stay intact.
                if _sha(temporary) == digest:
                    temporary.replace(new)
                    records.append({"original_path": str(old), "preserved_path": str(new), "sha256": digest})
                    continue
                temporary.unlink()
            with old.open("rb") as source, temporary.open("xb") as destination:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            if _sha(temporary) != digest:
                raise ValueError("pre-supplement copy verification failed")
            temporary.replace(new)
        records.append({"original_path": str(old), "preserved_path": str(new), "sha256": digest})
    journal = directory / "accounting_progress.jsonl"
    result = {"protocol": protocol, "files": records,
              "journal_prefix": {"path": str(journal), "bytes": journal.stat().st_size, "sha256": _sha(journal)}}
    manifest = target / "PRESERVATION.json"
    if manifest.exists() and json.loads(manifest.read_text()) != result:
        raise ValueError("pre-supplement preservation record changed")
    if not manifest.exists():
        _write_json_atomic(manifest, result)
    return result


def apply_transition(
    specification_path: str | Path, plan_path: str | Path,
    authorization_path: str | Path, forecast_path: str | Path,
    *, build_factory: Any = None,
) -> dict[str, Any]:
    """Offline audited publication. All generation boundaries are inert.

    ``build_factory`` exists for injected offline test wiring only. The default
    public construction uses a model that cannot invoke any provider.
    """
    from .experiment import (
        _campaign_lock, _write_json_atomic, build_experiment, prepare_experiment,
        validate_journal_resume, wire_durable_journal,
    )
    from .policy_activation import _inert_model, bind_policy_dispatch
    from .preflight import load_stage_authorization, supplement_forecast_bindings

    plan = load_supplement_plan(plan_path, specification_path)
    original = prepare_experiment(specification_path)
    prepared = apply_prepared(original, plan)
    directory = _inside(plan["campaign_directory"])
    checkpoint = directory / "checkpoint.pt"
    storage = directory / "supplement"
    receipt_path = storage / "transition.json"
    with _campaign_lock(directory) as guard:
        validate_journal_resume(checkpoint, directory, allow_halted=True)
        bindings, _ = supplement_forecast_bindings(original, checkpoint, plan_path, forecast_path)
        authorization = load_stage_authorization(authorization_path, **bindings)

        payload = load_checkpoint(checkpoint)
        existing = (payload.get("hook_state") or {}).get("supplement_lineage")
        expected_lineage = {
            "protocol": "audited_weighting_supplement_v1",
            "plan_path": str(_inside(plan_path)), "plan_sha256": _sha(_inside(plan_path)),
            "parent_checkpoint": copy.deepcopy(plan["references"]["checkpoint"]),
        }
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if (receipt.get("protocol") != TRANSITION_PROTOCOL
                    or receipt.get("plan_fingerprint") != plan["fingerprint"]
                    or receipt.get("authorization") != _reference(authorization_path)
                    or _canonical(receipt.get("registration")) != _canonical(prepared.continuation_registration)
                    or existing != expected_lineage
                    or payload.get("declared_config") != prepared.config.to_dict()):
                raise ValueError("completed supplement transition has different authorization")
            return receipt
        if existing is not None:
            # A crash after publication but before the receipt may finish only
            # this identical transaction; it never reloads pre-transition state.
            if (existing != expected_lineage or payload.get("declared_config") != prepared.config.to_dict()):
                raise ValueError("unfinished transition belongs to a different condition")
        else:
            if _sha(checkpoint) != plan["references"]["checkpoint"]["sha256"] or _baseline(payload) != plan["baseline"]:
                raise ValueError("campaign advanced or differs from the pre-supplement boundary")
            # Normal restoration loads the same full receipt graph below. Do
            # not retain a redundant parent graph throughout that operation.
            del payload
            _preserve_publications(directory, storage / "before")
            storage.mkdir(parents=True, exist_ok=True)
            intent = {"protocol": TRANSITION_PROTOCOL, "plan_fingerprint": plan["fingerprint"],
                      "source_checkpoint_sha256": _sha(checkpoint), "authorization": _reference(authorization_path)}
            intent_path = storage / "transition_intent.json"
            if intent_path.exists() and json.loads(intent_path.read_text()) != intent:
                raise ValueError("unfinished supplementary transition differs")
            if not intent_path.exists():
                _write_json_atomic(intent_path, intent)
            trainer, hooks = (build_factory or build_experiment)(original, model_factory=_inert_model)
            hooks.authorization_required = True
            hooks.execution_session_guard = guard
            bind_policy_dispatch(hooks.expenditure_ledger, specification_path, guard)
            trainer.resume_from_checkpoint(checkpoint, hooks=hooks)
            hooks.apply_supplement(prepared, plan, expected_lineage)
            hooks.authorize_stage(authorization)
            trainer.hooks, trainer.checkpoint_path = hooks, checkpoint
            wire_durable_journal(trainer, hooks, directory)

            async def publish() -> None:
                await trainer.checkpoint_async(safe_boundary=True)
                await trainer.durable_journal.aclose()

            asyncio.run(publish())
            validate_journal_resume(checkpoint, directory, allow_halted=True)
        receipt = {
            "protocol": TRANSITION_PROTOCOL, "plan_fingerprint": plan["fingerprint"],
            "source_checkpoint": plan["references"]["checkpoint"],
            "authorization": _reference(authorization_path),
            "checkpoint_at_publication": _reference(checkpoint),
            "registration": prepared.continuation_registration,
            "preservation": str(storage / "before" / "PRESERVATION.json"),
            "generation_calls": 0, "spending_reset": False,
        }
        _write_json_atomic(receipt_path, receipt)
        # The publication is JSON. Return that same representation on both
        # first application and idempotent restart, without altering payloads
        # kept in the numerical checkpoint.
        return json.loads(_canonical(receipt))
