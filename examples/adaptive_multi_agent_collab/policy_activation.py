"""Pending, separately approved operational-policy boundaries.

Independent registered campaigns activate only their own initial ledger and
checkpoint. The older shared-ledger path retains its historical-adoption
requirements. Neither path authorizes generation or imports workflow results.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from .experiment import (
    _campaign_lock,
    _relative,
    _write_json_atomic,
    build_experiment,
    planning_hooks,
    prepare_experiment,
    wire_durable_journal,
)
from .failure_policy import proposed_failure_policy

PLAN_PROTOCOL = "bounded_failure_policy_plan_v1"
AUTHORIZATION_PROTOCOL = "bounded_failure_policy_activation_authorization_v1"
TRANSITION_PROTOCOL = "bounded_failure_policy_fresh_boundary_v1"
INDEPENDENT_PLAN_PROTOCOL = "independent_campaign_policy_plan_v2"
INDEPENDENT_AUTHORIZATION_PROTOCOL = "independent_campaign_policy_authorization_v2"
INDEPENDENT_ACTIVATION_PROTOCOL = "independent_campaign_policy_activation_v2"


def _read(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _inside(path: str | Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(Path(__file__).resolve().parent):
        raise ValueError("policy artifacts must remain inside the permitted example directory")
    return resolved


def _reference(path: str | Path) -> dict[str, str]:
    path = Path(path).resolve()
    return {"path": str(path), "sha256": _sha(path)}


def _check_reference(item: Mapping[str, Any]) -> Path:
    path = Path(item["path"]).resolve()
    if _sha(path) != item.get("sha256"):
        raise ValueError("policy source artifact changed: " + str(path))
    return path


def _absolute_paths(value: Any, directory: Path, field: str = "") -> Any:
    """Rebase declared path fields; ordinary strings are never inferred as paths."""
    if isinstance(value, dict):
        return {key: _absolute_paths(item, directory, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_absolute_paths(item, directory, field) for item in value]
    path_fields = {
        "path",
        "local_source",
        "source_files",
        "evaluation_id_files",
        "cache_directory",
        "tokenizer_cache_directory",
        "asset_directory",
        "campaign_directory",
        "source_campaign_directory",
        "historical_manifest",
        "historical_campaign",
        "plan_path",
    }
    if (
        field in path_fields
        and isinstance(value, str)
        and value
        and not Path(value).is_absolute()
        and "://" not in value
    ):
        return str((directory / value).resolve())
    return value


def _accounting_approval() -> dict[str, Any]:
    from .continuation import EXCEPTED_DISPATCH, EXCEPTED_GENERATION, EXCEPTED_REQUEST, RETAINED_RESERVATION

    return {
        "approved": False,
        "dispatch_id": EXCEPTED_DISPATCH,
        "generation_id": EXCEPTED_GENERATION,
        "request_sha256": EXCEPTED_REQUEST,
        "retained_reservation_usd": RETAINED_RESERVATION,
        "charge_usage_reference_cost_remain_unknown": True,
        "reservation_is_verified_upper_bound": False,
        "stage": "pilot",
    }


def _authorization(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocol": AUTHORIZATION_PROTOCOL,
        "approved": False,
        "scope": "fresh_policy_boundary_only_no_generation",
        "plan_fingerprint": plan["fingerprint"],
        "condition_fingerprint": plan["condition_fingerprint"],
        "manifest_fingerprint": plan["manifest_fingerprint"],
        "resource_profile_fingerprint": plan["resource_profile_fingerprint"],
        "source_ledger_sha256": plan["sources"]["ledger"]["sha256"],
        "formal_specification": {"approved": False, "path": None, "sha256": None},
        "approval_source": {"path": None, "sha256": None},
        "historical_adoption": _accounting_approval(),
        "stage_authorizations": {name: False for name in ("compatibility", "pilot", "development", "test")},
        "reviewer": "",
        "rationale": "",
    }


def build_policy_plan(manifest_path: str | Path, destination: str | Path) -> dict[str, Any]:
    """Freeze an enabled candidate and pending approvals; perform no activation."""
    from .transition import _copy_exact, _validated_registry_history

    source = Path(manifest_path).resolve()
    destination = _inside(destination)
    if destination.exists():
        raise ValueError("policy planning destination already exists; preserved plans cannot be overwritten")
    specification = _read(source)
    if specification.get("config", {}).get("failure_policy", {}).get("enabled"):
        raise ValueError("policy planning requires the currently disabled source condition")
    anchor = _inside(_relative(specification["campaign_directory"], source.parent))
    registry = _read(anchor / "active_condition.json")
    _validated_registry_history(anchor, registry)
    prior = Path(registry["state_directory"]).resolve()
    if prior != anchor / "conditions" / registry["condition_fingerprint"]:
        raise ValueError("source condition is not anchored to its canonical campaign")
    source_files = {
        "manifest": source,
        "ledger": anchor / "expenditure_ledger.json",
        "registry": anchor / "active_condition.json",
        "checkpoint": prior / "checkpoint.pt",
        "original_checkpoint": anchor / "checkpoint.pt",
        "recheck_report": prior / "recheck_report.json",
        "compatibility_report": anchor / "compatibility_report.json",
        "transition": prior / "transition.json",
    }
    if (anchor / "condition_registry_history.json").exists():
        source_files["registry_history"] = anchor / "condition_registry_history.json"
    sources = {key: _reference(path) for key, path in source_files.items()}
    candidate = _absolute_paths(copy.deepcopy(specification), source.parent)
    candidate["config"]["failure_policy"] = asdict(proposed_failure_policy())
    candidate["approvals"] = {}
    candidate["policy_transition"] = {
        "protocol": PLAN_PROTOCOL,
        "plan_path": str(destination / "plan.json"),
        "source_condition_fingerprint": registry["condition_fingerprint"],
        "source_manifest_sha256": sources["manifest"]["sha256"],
        "source_ledger_sha256": sources["ledger"]["sha256"],
        "formal_approval_required": True,
        "historical_adoption_required": True,
        "stage_approval_required": True,
    }
    destination.mkdir(parents=True)
    snapshots = destination / "sources"
    snapshots.mkdir()
    immutable = {}
    for key, path in source_files.items():
        if key in {"manifest", "ledger", "registry", "registry_history"}:
            snapshot = snapshots / (key + path.suffix)
            _copy_exact(path, snapshot)
            immutable[key] = _reference(snapshot)
        else:
            # Fresh boundaries never overwrite predecessor checkpoints or
            # reports. Preserve these existing immutable artifacts by reference.
            immutable[key] = _reference(path)
    candidate_path = destination / "candidate_manifest.json"
    _write_json_atomic(candidate_path, candidate)
    prepared = prepare_experiment(candidate_path)
    plan = {
        "protocol": PLAN_PROTOCOL,
        "candidate_manifest": _reference(candidate_path),
        "campaign_directory": str(anchor),
        "source_manifest_directory": str(source.parent),
        "source_condition": copy.deepcopy(registry),
        "sources": sources,
        "immutable_sources": immutable,
        "condition_fingerprint": planning_hooks(prepared).condition_fingerprint,
        "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
        "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
        "formal_specification_sha256": None,
        "policy": asdict(prepared.config.failure_policy),
        "stage_authorized": False,
    }
    plan["fingerprint"] = _digest(plan)
    _write_json_atomic(destination / "plan.json", plan)
    _write_json_atomic(destination / "authorization.json", _authorization(plan))
    from .preflight import stage_authorization_template
    from .resources import ExpenditureLedger

    ledger = ExpenditureLedger(prepared.config.resources)
    ledger.load_state_dict(_read(sources["ledger"]["path"]))
    pilot = ledger.stage_summary("pilot")
    pending_pilot = stage_authorization_template(**planning_hooks(prepared)._authorization_bindings("pilot"))
    pending_pilot["rationale"] = (
        "PENDING separate pilot approval after the formal amendment, policy activation and exact historical-hold "
        "adoption have been approved. Retain all historical pilot expenditure and unresolved holds; "
        f"recorded pilot paid USD {pilot['paid']:.10g}, outstanding reservations USD {pilot['unresolved']:.10g}. "
        f"The existing aggregate pilot ceiling remains USD {prepared.config.resources.pilot_budget:.10g}; "
        "the historical hold is not a verified upper bound on its unknown bill. This provides no fresh allowance, "
        "settles no unknown bill and does not approve development."
    )
    _write_json_atomic(destination / "pilot_authorization_pending.json", pending_pilot)
    return plan


def _load_plan(path: str | Path) -> tuple[dict[str, Any], dict[str, Any], Any]:
    plan = _read(path)
    if plan.get("protocol") != PLAN_PROTOCOL or plan.get("fingerprint") != _digest(
        {key: value for key, value in plan.items() if key != "fingerprint"}
    ):
        raise ValueError("policy plan fingerprint or protocol changed")
    candidate_path = _check_reference(plan["candidate_manifest"])
    candidate = _read(candidate_path)
    if candidate.get("policy_transition", {}).get("plan_path") != str(Path(path).resolve()):
        raise ValueError("candidate belongs to a different policy plan")
    for item in plan["immutable_sources"].values():
        _check_reference(item)
    old = _absolute_paths(
        _read(plan["immutable_sources"]["manifest"]["path"]), Path(plan["source_manifest_directory"])
    )
    revised = copy.deepcopy(candidate)
    revised.pop("policy_transition", None)
    revised["config"].pop("failure_policy", None)
    old["config"].pop("failure_policy", None)
    old.pop("approvals", None)
    revised.pop("approvals", None)
    if old != revised:
        raise ValueError("policy transition changed scientific settings, prompts, data or backend behavior")
    prepared = prepare_experiment(candidate_path)
    if (
        not prepared.config.failure_policy.enabled
        or asdict(prepared.config.failure_policy) != plan["policy"]
        or planning_hooks(prepared).condition_fingerprint != plan["condition_fingerprint"]
        or prepared.authorization_manifest_fingerprint != plan["manifest_fingerprint"]
        or prepared.resource_profile_fingerprint != plan["resource_profile_fingerprint"]
    ):
        raise ValueError("policy candidate no longer matches its frozen condition")
    return plan, candidate, prepared


def _approved(plan: Mapping[str, Any], authorization_path: str | Path) -> dict[str, Any]:
    value = _read(authorization_path)
    expected = _authorization(plan)
    mutable = {
        "approved",
        "formal_specification",
        "approval_source",
        "historical_adoption",
        "reviewer",
        "rationale",
    }
    if (
        set(value) != set(expected)
        or value.get("approved") is not True
        or any(value[key] != expected[key] for key in set(expected) - mutable)
    ):
        raise ValueError("pending or mismatched policy-boundary approval")
    formal = value["formal_specification"]
    if set(formal) != {"approved", "path", "sha256"} or formal["approved"] is not True:
        raise ValueError("the formal specification amendment requires separate explicit approval")
    _check_reference(formal)
    original_specification = _read(plan["immutable_sources"]["manifest"]["path"])
    if formal["sha256"] == original_specification.get("specification_sha256"):
        raise ValueError("formal policy amendment must differ from the superseded specification")
    source = value["approval_source"]
    if set(source) != {"path", "sha256"}:
        raise ValueError("policy approval requires its exact preserved source")
    _check_reference(source)
    adoption = copy.deepcopy(value["historical_adoption"])
    adoption["approved"] = False
    if adoption != _accounting_approval() or value["historical_adoption"].get("approved") is not True:
        raise ValueError("historical dispatch 400 requires a separate exact retained-hold adoption approval")
    if any(not isinstance(value[key], str) or not value[key].strip() for key in ("reviewer", "rationale")):
        raise ValueError("policy approval requires a reviewer and actual approval rationale")
    return value


def _bindings(plan: Mapping[str, Any], authorization: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: plan[name]
        for name in ("condition_fingerprint", "manifest_fingerprint", "resource_profile_fingerprint")
    } | {
        "plan_fingerprint": plan["fingerprint"],
        "source_ledger_sha256": plan["sources"]["ledger"]["sha256"],
        "formal_specification_sha256": authorization["formal_specification"]["sha256"],
        "stage_authorization_required": True,
    }


def _inert_model(backend: Mapping[str, Any]) -> Any:
    async def unavailable(**_kwargs: Any) -> None:
        raise RuntimeError("offline policy activation cannot dispatch a model request")

    return SimpleNamespace(
        invoke=unavailable,
        model_client_config=SimpleNamespace(openrouter_role_output=backend.get("role_output")),
    )


def activate_policy_plan(
    plan_path: str | Path,
    authorization_path: str | Path,
    *,
    builder: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Create one approved fresh boundary; never dispatch or approve a stage."""
    if _read(plan_path).get("protocol") == INDEPENDENT_PLAN_PROTOCOL:
        return _activate_independent_policy(plan_path, authorization_path)
    from .transition import _copy_exact, _parameters, _validated_registry_history, _verify_fresh

    plan, candidate, prepared = _load_plan(plan_path)
    anchor = _inside(plan["campaign_directory"])
    with _campaign_lock(anchor) as guard:
        plan, candidate, prepared = _load_plan(plan_path)
        authorization = _approved(plan, authorization_path)
        registry_path = anchor / "active_condition.json"
        registry = _read(registry_path)
        if registry.get("protocol") == TRANSITION_PROTOCOL:
            receipt = validate_policy_condition(candidate, Path(plan["candidate_manifest"]["path"]).parent)
            if receipt["authorization_sha256"] != _sha(authorization_path):
                raise ValueError("activated policy authorization cannot be replaced")
            return receipt
        history = _validated_registry_history(anchor, registry)
        if registry != plan["source_condition"]:
            raise ValueError("policy source condition changed before activation")
        for item in plan["sources"].values():
            _check_reference(item)
        directory = anchor / "conditions" / plan["condition_fingerprint"]
        if directory.exists():
            raise ValueError("incomplete policy transition exists; automatic replay is disabled")
        trainer, hooks = (
            builder(prepared)
            if builder is not None
            else build_experiment(prepared, model_factory=_inert_model)
        )
        _verify_fresh(trainer, hooks, Path(plan["sources"]["original_checkpoint"]["path"]))
        ledger = hooks.expenditure_ledger
        ledger.on_change = None
        ledger.load_state_dict(_read(anchor / "expenditure_ledger.json"))
        if ledger.active_scope is not None:
            prior_report = _read(plan["immutable_sources"]["recheck_report"]["path"])
            active_scope = ledger.scopes.get(ledger.active_scope, {})
            if (
                prior_report.get("status") not in {"complete", "failed"}
                or prior_report.get("pending_probe") is not None
                or active_scope.get("bindings", {}).get("plan_fingerprint")
                != registry.get("plan_fingerprint")
            ):
                raise ValueError("a previous generation scope is still active")
            ledger.activate_scope(None)
        bindings = _bindings(plan, authorization)
        ledger.activate_failure_policy(
            prepared.config.failure_policy,
            authorization=authorization,
            bindings=bindings,
            execution_guard=guard,
        )
        ledger.adopt_historical_hold(
            400, authorization=authorization["historical_adoption"], bindings=bindings, execution_guard=guard
        )
        ledger.assert_dispatch_allowed()
        # Validation is complete before writing any new execution state.
        directory.mkdir(parents=True)
        _copy_exact(authorization_path, directory / "policy_authorization.json")
        _copy_exact(plan_path, directory / "policy_plan.json")
        _write_json_atomic(directory / "experiment_manifest.json", prepared.manifest())
        trainer.hooks, trainer.checkpoint_path = hooks, directory / "checkpoint.pt"
        hooks.authorization_required = True
        hooks.execution_session_guard = guard
        wire_durable_journal(trainer, hooks, directory, campaign_directory=anchor)
        trainer._checkpoint()
        _copy_exact(directory / "checkpoint.pt", directory / "initial_checkpoint.pt")
        receipt = {
            "protocol": TRANSITION_PROTOCOL,
            **bindings,
            "campaign_directory": str(anchor),
            "state_directory": str(directory),
            "checkpoint_path": str(directory / "checkpoint.pt"),
            "initial_checkpoint": _reference(directory / "initial_checkpoint.pt"),
            "authorization_sha256": _sha(authorization_path),
            "plan_path": str(Path(plan_path).resolve()),
            "plan_sha256": _sha(plan_path),
            "parameter_receipts": _parameters(trainer),
            "previous_condition": copy.deepcopy(registry),
            "response_identity_evidence": copy.deepcopy(registry.get("response_identity_evidence", {})),
            "imported_state": "physical_expenditure_and_explicit_policy_only",
            "historical_accounting_fully_reconciled": False,
            "pilot_authorized": False,
            "development_authorized": False,
            "test_authorized": False,
        }
        _write_json_atomic(directory / "transition.json", receipt)
        history = history or {"protocol": "append_only_condition_registry_v1", "conditions": [registry]}
        history["conditions"].append(receipt)
        _write_json_atomic(anchor / "condition_registry_history.json", history)
        _write_json_atomic(registry_path, receipt)
        return receipt


def validate_policy_condition(specification: Mapping[str, Any], directory: Path) -> dict[str, Any]:
    """Validate the immutable activation and current non-resetting ledger identity."""
    if specification.get("independent_campaign"):
        return _validate_independent_activation(specification, directory)
    from .resources import ExpenditureLedger
    from .transition import _validated_registry_history

    marker = specification.get("policy_transition", {})
    if marker.get("protocol") != PLAN_PROTOCOL:
        raise ValueError("missing audited policy-transition lineage")
    plan, candidate, prepared = _load_plan(_relative(marker["plan_path"], directory))
    if candidate != dict(specification):
        raise ValueError("policy specification differs from its frozen candidate")
    anchor = _inside(plan["campaign_directory"])
    receipt = _read(anchor / "active_condition.json")
    state_directory = anchor / "conditions" / plan["condition_fingerprint"]
    if (
        receipt.get("protocol") != TRANSITION_PROTOCOL
        or receipt.get("condition_fingerprint") != plan["condition_fingerprint"]
        or receipt.get("plan_sha256") != _sha(marker["plan_path"])
        or receipt.get("state_directory") != str(state_directory)
        or _read(state_directory / "transition.json") != receipt
    ):
        raise ValueError("policy activation is pending or its registered boundary is incomplete")
    _validated_registry_history(anchor, receipt)
    authorization = _approved(plan, state_directory / "policy_authorization.json")
    if receipt["authorization_sha256"] != _sha(state_directory / "policy_authorization.json"):
        raise ValueError("policy activation approval changed")
    _check_reference(receipt["initial_checkpoint"])
    ledger = ExpenditureLedger(prepared.config.resources)
    ledger.load_state_dict(_read(anchor / "expenditure_ledger.json"))
    state = ledger.failure_policy
    if (
        state is None
        or state["bindings"] != _bindings(plan, authorization)
        or state["authorization"] != authorization
    ):
        raise ValueError("canonical ledger is missing its exact policy approval")
    if state["config"] != asdict(prepared.config.failure_policy) or len(state["historical_adoptions"]) != 1:
        raise ValueError("policy configuration or explicit historical adoption is incomplete")
    ledger._adopted_halt()
    return receipt


def registered_policy_directory(specification_path: str | Path) -> Path:
    source = Path(specification_path).resolve()
    return Path(validate_policy_condition(_read(source), source.parent)["state_directory"])


def bind_policy_dispatch(ledger: Any, source: str | Path, execution_guard: Callable[[], bool]) -> None:
    """Restore only the process-local campaign-lock capability; never an approval."""
    path = Path(source).resolve()
    if _read(path).get("independent_campaign"):
        plan, _, prepared = _load_independent_plan(_read(path)["independent_campaign"]["policy_plan_path"])
        receipt = _validate_independent_activation(_read(path), path.parent)
        authorization = _independent_approved(plan, Path(receipt["authorization"]["path"]))
        canonical = _read(Path(plan["campaign_directory"]) / "expenditure_ledger.json")
        if not ledger.entries and ledger.failure_policy is None:
            ledger.load_state_dict(canonical)
        if _digest(ledger.state_dict()) != _digest(canonical):
            raise ValueError("resumed independent ledger differs from its canonical durable state")
        ledger.activate_failure_policy(
            prepared.config.failure_policy,
            authorization=authorization,
            bindings=_independent_bindings(plan, authorization),
            execution_guard=execution_guard,
        )
        return
    receipt = validate_policy_condition(_read(path), path.parent)
    plan, _, prepared = _load_plan(receipt["plan_path"])
    authorization = _approved(plan, Path(receipt["state_directory"]) / "policy_authorization.json")
    canonical = _read(Path(receipt["campaign_directory"]) / "expenditure_ledger.json")
    if not ledger.entries and ledger.failure_policy is None:
        ledger.load_state_dict(canonical)
    if _digest(ledger.state_dict()) != _digest(canonical):
        raise ValueError("resumed policy ledger differs from its canonical durable state")
    ledger.activate_failure_policy(
        prepared.config.failure_policy,
        authorization=authorization,
        bindings=_bindings(plan, authorization),
        execution_guard=execution_guard,
    )


def policy_response_identity(prepared: Any, source: str | Path) -> Any:
    from .transition import with_response_identity_evidence

    path = Path(source).resolve()
    if _read(path).get("independent_campaign"):
        from .campaigns import shared_compatibility_evidence

        evidence = shared_compatibility_evidence(_read(path), path.parent)
        return with_response_identity_evidence(prepared, evidence["response_identity_evidence"])
    receipt = validate_policy_condition(_read(path), path.parent)
    if planning_hooks(prepared).condition_fingerprint != receipt["condition_fingerprint"]:
        raise ValueError("response identity evidence belongs to another policy condition")
    return with_response_identity_evidence(prepared, receipt["response_identity_evidence"])


def policy_compatibility_evidence(source: str | Path) -> dict[str, Any]:
    """Retain unchanged request-path evidence with its original condition scope."""
    path = Path(source).resolve()
    if _read(path).get("independent_campaign"):
        from .campaigns import shared_compatibility_evidence

        return shared_compatibility_evidence(_read(path), path.parent)
    receipt = validate_policy_condition(_read(path), path.parent)
    plan, _, _ = _load_plan(receipt["plan_path"])
    sources = plan["immutable_sources"]
    report = _read(_check_reference(sources["recheck_report"]))
    historical = _read(_check_reference(sources["compatibility_report"]))
    if (
        report.get("status") != "complete"
        or report.get("stage_complete") is not True
        or report.get("condition_fingerprint") != plan["source_condition"]["condition_fingerprint"]
        or report.get("parameter_receipts", {}).get("unchanged") is not True
        or len(report.get("probes", [])) != 6
        or any(
            not row.get("valid_output") or not row.get("native_evidence_valid") for row in report["probes"]
        )
        or historical.get("status") != "complete"
        or historical.get("assessment", {}).get("passed") is not True
    ):
        raise ValueError("retained compatibility evidence is incomplete under its original condition")
    return {
        "scope": "unchanged_request_paths_under_preserved_prior_conditions",
        "source_condition_fingerprint": plan["source_condition"]["condition_fingerprint"],
        "recheck_report_sha256": sources["recheck_report"]["sha256"],
        "historical_compatibility_sha256": sources["compatibility_report"]["sha256"],
        "failure_policy_live_validated": False,
        "pilot_authorized": False,
    }


def _independent_authorization(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocol": INDEPENDENT_AUTHORIZATION_PROTOCOL,
        "approved": False,
        "scope": "registered_independent_campaign_policy_only_no_generation",
        "campaign_id": plan["registration"]["campaign_id"],
        "plan_fingerprint": plan["fingerprint"],
        **{
            key: plan[key]
            for key in ("condition_fingerprint", "manifest_fingerprint", "resource_profile_fingerprint")
        },
        "policy": plan["policy"],
        "formal_specification": {"approved": False, "path": None, "sha256": None},
        "approval_source": {"path": None, "sha256": None},
        "stage_authorizations": {stage: False for stage in ("compatibility", "pilot", "development", "test")},
        "reviewer": "",
        "rationale": "",
    }


def build_independent_policy_plan(manifest_path: str | Path, destination: str | Path) -> dict[str, Any]:
    """Freeze only a registered campaign's own initial state and pending approval."""
    from .campaigns import validate_campaign
    from .resources import ExpenditureLedger
    from .experiment import validate_journal_resume
    from .transition import _copy_exact

    source, anchor = Path(manifest_path).resolve(), _inside(destination)
    specification = _read(source)
    registration = validate_campaign(specification, source.parent)
    if anchor != Path(registration["directory"]).resolve():
        raise ValueError("independent policy plan must remain in its registered campaign")
    plan_path = _inside(specification["independent_campaign"]["policy_plan_path"])
    if plan_path.parent != anchor:
        raise ValueError("independent policy plan must use its registered location")
    if plan_path.exists():
        plan, _, prepared = _load_independent_plan(plan_path)
        _independent_pending_artifacts(plan, prepared.config.resources.pilot_budget)
        return plan
    prepared = prepare_experiment(source)
    if prepared.config.failure_policy != proposed_failure_policy():
        raise ValueError("independent campaign requires its explicit eight-request candidate")
    ledger_path = anchor / "expenditure_ledger.json"
    ledger = ExpenditureLedger(prepared.config.resources)
    ledger.load_state_dict(_read(ledger_path))
    if ledger.entries or ledger.halted or ledger.failure_policy is not None or ledger.scopes:
        raise ValueError("independent policy planning requires its untouched zero-expenditure boundary")
    original_ledger = anchor / "initial_expenditure_ledger.json"
    if original_ledger.exists() and original_ledger.read_bytes() != ledger_path.read_bytes():
        raise ValueError("initial independent ledger cannot be overwritten")
    if not original_ledger.exists():
        original_ledger.write_bytes(ledger_path.read_bytes())
    validate_journal_resume(anchor / "checkpoint.pt", anchor)
    initial_journal = anchor / "initial_accounting_progress.jsonl"
    if initial_journal.exists():
        if _sha(initial_journal) != _sha(anchor / "accounting_progress.jsonl"):
            raise ValueError("initial independent journal cannot be overwritten")
    else:
        _copy_exact(anchor / "accounting_progress.jsonl", initial_journal)
    plan = {
        "protocol": INDEPENDENT_PLAN_PROTOCOL,
        "manifest": _reference(source),
        "campaign_directory": str(anchor),
        "registration": copy.deepcopy(registration),
        "initial_ledger": _reference(original_ledger),
        "initial_checkpoint": _reference(anchor / "initial_checkpoint.pt"),
        "initial_journal": _reference(initial_journal),
        "pilot_forecast": _reference(anchor / "pilot_forecast.json"),
        "condition_fingerprint": planning_hooks(prepared).condition_fingerprint,
        "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
        "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
        "policy": asdict(prepared.config.failure_policy),
        "stage_authorized": False,
    }
    plan["fingerprint"] = _digest(plan)
    _write_json_atomic(plan_path, plan)
    _independent_pending_artifacts(plan, prepared.config.resources.pilot_budget)
    return plan


def _independent_pending_artifacts(plan: Mapping[str, Any], pilot_budget_usd: float | None) -> None:
    """Finish interrupted paperwork creation without replacing any review decision."""
    from .preflight import independent_forecast_review_template

    anchor = Path(plan["campaign_directory"])
    for filename, value in (
        ("policy_authorization_pending.json", _independent_authorization(plan)),
        ("pilot_forecast_review_pending.json", independent_forecast_review_template(plan, pilot_budget_usd)),
    ):
        if not (anchor / filename).exists():
            _write_json_atomic(anchor / filename, value)


def _load_independent_plan(path: str | Path) -> tuple[dict[str, Any], dict[str, Any], Any]:
    from .campaigns import validate_campaign

    plan = _read(path)
    if plan.get("protocol") != INDEPENDENT_PLAN_PROTOCOL or plan.get("fingerprint") != _digest(
        {key: value for key, value in plan.items() if key != "fingerprint"}
    ):
        raise ValueError("independent policy plan fingerprint or protocol changed")
    source = _check_reference(plan["manifest"])
    specification = _read(source)
    if Path(specification["independent_campaign"]["policy_plan_path"]).resolve() != Path(path).resolve():
        raise ValueError("independent campaign belongs to another policy plan")
    if validate_campaign(specification, source.parent) != plan["registration"]:
        raise ValueError("independent campaign registration changed")
    for name in ("initial_ledger", "initial_checkpoint", "initial_journal", "pilot_forecast"):
        _check_reference(plan[name])
    prepared = prepare_experiment(source)
    if (
        asdict(prepared.config.failure_policy) != plan["policy"]
        or planning_hooks(prepared).condition_fingerprint != plan["condition_fingerprint"]
        or prepared.authorization_manifest_fingerprint != plan["manifest_fingerprint"]
        or prepared.resource_profile_fingerprint != plan["resource_profile_fingerprint"]
    ):
        raise ValueError("independent policy plan no longer matches its frozen condition")
    return plan, specification, prepared


def _independent_approved(plan: Mapping[str, Any], path: str | Path) -> dict[str, Any]:
    value, expected = _read(path), _independent_authorization(plan)
    mutable = {"approved", "formal_specification", "approval_source", "reviewer", "rationale"}
    if (
        set(value) != set(expected)
        or value.get("approved") is not True
        or any(value[key] != expected[key] for key in set(expected) - mutable)
    ):
        raise ValueError("independent campaign policy approval is pending or mismatched")
    formal = value["formal_specification"]
    if set(formal) != {"approved", "path", "sha256"} or formal["approved"] is not True:
        raise ValueError("independent policy requires explicit formal specification approval")
    _check_reference(formal)
    if set(value["approval_source"]) != {"path", "sha256"}:
        raise ValueError("independent policy approval requires its exact source")
    _check_reference(value["approval_source"])
    if any(not isinstance(value[key], str) or not value[key].strip() for key in ("reviewer", "rationale")):
        raise ValueError("independent policy approval requires a reviewer and rationale")
    return value


def _independent_bindings(plan: Mapping[str, Any], authorization: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **{
            key: plan[key]
            for key in ("condition_fingerprint", "manifest_fingerprint", "resource_profile_fingerprint")
        },
        "campaign_id": plan["registration"]["campaign_id"],
        "registration_fingerprint": _digest(plan["registration"]),
        "plan_fingerprint": plan["fingerprint"],
        "formal_specification_sha256": authorization["formal_specification"]["sha256"],
        "stage_authorization_required": True,
    }


def _activate_independent_policy(plan_path: str | Path, authorization_path: str | Path) -> dict[str, Any]:
    """Publish a restartable, approval-bound policy transaction without generation."""
    import os
    import asyncio
    import torch
    from .experiment import validate_journal_resume
    from .resources import ExpenditureLedger
    from .transition import _copy_exact
    from .persistence import DurableJournal, read_journal
    from .training import CheckpointManager

    plan, specification, prepared = _load_independent_plan(plan_path)
    anchor = _inside(plan["campaign_directory"])
    with _campaign_lock(anchor) as guard:
        plan, specification, prepared = _load_independent_plan(plan_path)
        authorization = _independent_approved(plan, authorization_path)
        receipt_path = anchor / "policy_activation.json"
        if receipt_path.exists():
            receipt = _validate_independent_activation(specification, Path(plan["manifest"]["path"]).parent)
            if receipt["authorization"]["sha256"] != _sha(authorization_path):
                raise ValueError("independent policy authorization cannot be replaced")
            return receipt
        staging = anchor / "policy_activation"
        transaction_path = staging / "transaction.json"
        if not transaction_path.exists():
            if (
                _sha(anchor / "checkpoint.pt") != plan["initial_checkpoint"]["sha256"]
                or _sha(anchor / "expenditure_ledger.json") != plan["initial_ledger"]["sha256"]
                or _sha(anchor / "accounting_progress.jsonl") != plan["initial_journal"]["sha256"]
            ):
                raise ValueError("unactivated independent campaign has changed from its initial boundary")
            validate_journal_resume(anchor / "checkpoint.pt", anchor)
            ledger = ExpenditureLedger(prepared.config.resources)
            ledger.load_state_dict(_read(anchor / "expenditure_ledger.json"))
            ledger.activate_failure_policy(
                prepared.config.failure_policy,
                authorization=authorization,
                bindings=_independent_bindings(plan, authorization),
                execution_guard=guard,
            )
            staging.mkdir(exist_ok=True)
            staged_authorization = staging / "authorization.json"
            if staged_authorization.exists():
                if _sha(staged_authorization) != _sha(authorization_path):
                    raise ValueError("staged independent authorization cannot be replaced")
            else:
                _copy_exact(Path(authorization_path), staged_authorization)
            _write_json_atomic(staging / "expenditure_ledger.json", ledger.state_dict())
            payload = torch.load(anchor / "checkpoint.pt", map_location="cpu", weights_only=False)
            if payload.get("schema_version") != CheckpointManager.schema_version:
                raise ValueError("independent activation requires the current initial checkpoint schema")
            payload["hook_state"]["expenditure_ledger"] = ledger.state_dict()
            with (staging / "checkpoint.pt").open("wb") as stream:
                torch.save(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            staged_journal = staging / "accounting_progress.jsonl"
            if not staged_journal.exists():
                _copy_exact(Path(plan["initial_journal"]["path"]), staged_journal)
            if _sha(staged_journal) == plan["initial_journal"]["sha256"]:
                journal = DurableJournal([staging], _read(plan["initial_ledger"]["path"]))
                try:
                    state = ledger.state_dict()
                    journal.changed({key: value for key, value in state.items() if key != "entries"}, [])
                    journal.publish(state, staging / "checkpoint.pt", safe=True)
                finally:
                    asyncio.run(journal.aclose())
            else:
                recovered = read_journal(staging)
                if recovered is None or recovered["state"] != ledger.state_dict():
                    raise ValueError("unfinished independent activation journal requires review")
            # A policy receipt may certify only a matching, fully published WAL,
            # ledger and checkpoint. Initial journal bytes remain its exact prefix.
            validate_journal_resume(staging / "checkpoint.pt", staging)
            receipt = {
                "protocol": INDEPENDENT_ACTIVATION_PROTOCOL,
                **_independent_bindings(plan, authorization),
                "plan_path": str(Path(plan_path).resolve()),
                "plan_sha256": _sha(plan_path),
                "campaign_directory": str(anchor),
                "state_directory": str(anchor),
                "authorization": _reference(staging / "authorization.json"),
                "activated_ledger": _reference(staging / "expenditure_ledger.json"),
                "activated_checkpoint": _reference(staging / "checkpoint.pt"),
                "activated_journal": _reference(staged_journal),
                "pilot_authorized": False,
                "imported_historical_state": False,
            }
            _write_json_atomic(transaction_path, receipt)
        receipt = _read(transaction_path)
        if (
            receipt.get("protocol") != INDEPENDENT_ACTIVATION_PROTOCOL
            or receipt.get("plan_sha256") != _sha(plan_path)
            or receipt.get("authorization", {}).get("sha256") != _sha(authorization_path)
        ):
            raise ValueError("pending independent activation transaction differs from approval")
        for name in ("authorization", "activated_ledger", "activated_checkpoint", "activated_journal"):
            _check_reference(receipt[name])
        # Each destination may be original or already-published after interruption.
        for filename, original, staged in (
            ("expenditure_ledger.json", "initial_ledger", "activated_ledger"),
            ("checkpoint.pt", "initial_checkpoint", "activated_checkpoint"),
            ("accounting_progress.jsonl", "initial_journal", "activated_journal"),
        ):
            target = anchor / filename
            if _sha(target) not in {plan[original]["sha256"], receipt[staged]["sha256"]}:
                raise ValueError("independent activation cannot overwrite subsequent campaign activity")
            if _sha(target) != receipt[staged]["sha256"]:
                temporary = anchor / (filename + ".activation.tmp")
                with temporary.open("wb") as stream:
                    stream.write(Path(receipt[staged]["path"]).read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                directory_fd = os.open(anchor, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        validate_journal_resume(anchor / "checkpoint.pt", anchor)
        _write_json_atomic(receipt_path, receipt)
        return _validate_independent_activation(specification, Path(plan["manifest"]["path"]).parent)


def _validate_independent_activation(specification: Mapping[str, Any], directory: Path) -> dict[str, Any]:
    from .resources import ExpenditureLedger

    plan, candidate, prepared = _load_independent_plan(
        _relative(specification["independent_campaign"]["policy_plan_path"], directory)
    )
    if candidate != dict(specification):
        raise ValueError("independent specification differs from its frozen policy plan")
    anchor = _inside(plan["campaign_directory"])
    receipt_path = anchor / "policy_activation.json"
    if not receipt_path.is_file():
        raise ValueError("independent campaign policy activation is pending")
    receipt = _read(receipt_path)
    if (
        receipt.get("protocol") != INDEPENDENT_ACTIVATION_PROTOCOL
        or receipt.get("plan_sha256") != _sha(specification["independent_campaign"]["policy_plan_path"])
        or receipt.get("campaign_directory") != str(anchor)
    ):
        raise ValueError("independent policy activation receipt differs from its registered plan")
    authorization = _independent_approved(plan, _check_reference(receipt["authorization"]))
    for key in ("activated_ledger", "activated_checkpoint", "activated_journal"):
        _check_reference(receipt[key])
    bindings = _independent_bindings(plan, authorization)
    if any(receipt.get(key) != value for key, value in bindings.items()):
        raise ValueError("independent policy activation bindings changed")
    ledger = ExpenditureLedger(prepared.config.resources)
    ledger.load_state_dict(_read(anchor / "expenditure_ledger.json"))
    state = ledger.failure_policy
    if (
        state is None
        or state["bindings"] != bindings
        or state["authorization"] != authorization
        or state["config"] != plan["policy"]
        or state["historical_adoptions"]
    ):
        raise ValueError(
            "independent ledger is missing its exact policy approval or imports historical adoption"
        )
    return receipt
