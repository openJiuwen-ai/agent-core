"""Approval-bound activation for registered independent campaigns.

Retired shared-ledger transitions and historical adoption cannot activate a
campaign. Activation preserves the registered condition and grants no stage
execution authorization.
"""

from __future__ import annotations

from ..paths import PACKAGE_ROOT
from ..infrastructure.storage import load_checkpoint, load_ledger, file_sha256, strict_json_equal

import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from .experiment import _campaign_lock, _relative, _write_json_atomic, planning_hooks, prepare_experiment
from ..infrastructure.failure_policy import proposed_failure_policy

INDEPENDENT_PLAN_PROTOCOL = "independent_campaign_policy_plan_v2"
INDEPENDENT_AUTHORIZATION_PROTOCOL = "independent_campaign_policy_authorization_v2"
INDEPENDENT_ACTIVATION_PROTOCOL = "independent_campaign_policy_activation_v2"


def _read(path: str | Path) -> dict[str, Any]:
    return load_ledger(path)


def _sha(path: str | Path) -> str:
    return file_sha256(path)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _inside(path: str | Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(PACKAGE_ROOT):
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


def _inert_model(backend: Mapping[str, Any]) -> Any:
    async def unavailable(**_kwargs: Any) -> None:
        raise RuntimeError("offline policy activation cannot dispatch a model request")

    return SimpleNamespace(
        invoke=unavailable,
        model_client_config=SimpleNamespace(openrouter_role_output=backend.get("role_output")),
    )


def _require_independent(specification: Mapping[str, Any]) -> None:
    if (
        not specification.get("independent_campaign")
        or specification.get("policy_transition")
        or specification.get("revised_condition")
    ):
        raise ValueError(
            "historical shared-ledger policy workflows are retired; "
            "a registered independent campaign is required"
        )


def activate_policy_plan(plan_path: str | Path, authorization_path: str | Path) -> dict[str, Any]:
    """Activate the registered independent policy without generation permission."""
    if _read(plan_path).get("protocol") != INDEPENDENT_PLAN_PROTOCOL:
        raise ValueError("historical shared-ledger policy activation is retired")
    return _activate_independent_policy(plan_path, authorization_path)


def validate_policy_condition(specification: Mapping[str, Any], directory: Path) -> dict[str, Any]:
    """Validate an independent activation and its unchanged campaign identity."""
    _require_independent(specification)
    return _validate_independent_activation(specification, directory)


def registered_policy_directory(specification_path: str | Path) -> Path:
    source = Path(specification_path).resolve()
    return Path(validate_policy_condition(_read(source), source.parent)["state_directory"])


def bind_policy_dispatch(ledger: Any, source: str | Path, execution_guard: Callable[[], bool]) -> None:
    """Restore only the process-local campaign-lock capability; never approval."""
    path = Path(source).resolve()
    specification = _read(path)
    _require_independent(specification)
    from .regular_development import bind_amended_policy as bind_regular_policy
    if bind_regular_policy(ledger, path, execution_guard):
        return
    from .calibration_launch import bind_amended_policy
    if bind_amended_policy(ledger, path, execution_guard):
        return
    plan, _, prepared = _load_independent_plan(specification["independent_campaign"]["policy_plan_path"])
    receipt = _validate_independent_activation(specification, path.parent)
    authorization = _independent_approved(plan, Path(receipt["authorization"]["path"]))
    canonical = _read(Path(plan["campaign_directory"]) / "expenditure_ledger.json")
    if not ledger.entries and ledger.failure_policy is None:
        ledger.load_state_dict(canonical)
    if not strict_json_equal(ledger.state_dict(), canonical):
        raise ValueError("resumed independent ledger differs from its canonical durable state")
    ledger.activate_failure_policy(
        prepared.config.failure_policy,
        authorization=authorization,
        bindings=_independent_bindings(plan, authorization),
        execution_guard=execution_guard,
    )


def policy_response_identity(prepared: Any, source: str | Path) -> Any:
    from .campaigns import shared_compatibility_evidence
    from ..providers.identity import with_response_identity_evidence

    path = Path(source).resolve()
    specification = _read(path)
    _require_independent(specification)
    evidence = shared_compatibility_evidence(specification, path.parent)
    return with_response_identity_evidence(prepared, evidence["response_identity_evidence"])


def policy_compatibility_evidence(source: str | Path) -> dict[str, Any]:
    """Validate retained request-path evidence in its original scope."""
    from .campaigns import shared_compatibility_evidence

    path = Path(source).resolve()
    specification = _read(path)
    _require_independent(specification)
    return shared_compatibility_evidence(specification, path.parent)


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
    from ..providers.resources import ExpenditureLedger
    from .experiment import validate_journal_resume
    from ..infrastructure.persistence import copy_exact

    source, anchor = Path(manifest_path).resolve(), _inside(destination)
    specification = _read(source)
    _require_independent(specification)
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
        copy_exact(anchor / "accounting_progress.jsonl", initial_journal)
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
    _require_independent(specification)
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
    from ..providers.resources import ExpenditureLedger
    from ..infrastructure.persistence import copy_exact
    from ..infrastructure.persistence import DurableJournal, read_journal
    from ..learning.training import CheckpointManager

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
                copy_exact(Path(authorization_path), staged_authorization)
            # Bind the copied journal against its exact original snapshot first.
            # Publishing the amended ledger before binding would contradict the
            # already committed parent boundary, even when no receipts exist.
            staged_ledger = staging / "expenditure_ledger.json"
            if not staged_ledger.exists():
                copy_exact(Path(plan["initial_ledger"]["path"]), staged_ledger)
            payload = load_checkpoint(anchor / "checkpoint.pt")
            if payload.get("schema_version") != CheckpointManager.schema_version:
                raise ValueError("independent activation requires the current initial checkpoint schema")
            payload["hook_state"]["expenditure_ledger"] = ledger.state_dict()
            with (staging / "checkpoint.pt").open("wb") as stream:
                torch.save(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            staged_journal = staging / "accounting_progress.jsonl"
            if not staged_journal.exists():
                copy_exact(Path(plan["initial_journal"]["path"]), staged_journal)
            if _sha(staged_journal) == plan["initial_journal"]["sha256"]:
                if _sha(staged_ledger) != plan["initial_ledger"]["sha256"]:
                    raise ValueError("staged activation ledger differs from the copied parent boundary")
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
    from ..providers.resources import ExpenditureLedger

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
    if state is not None and state.get("condition_amendments"):
        # Validate the original activation against its preserved parent policy.
        # Current prospective dispatch authorization is independently checked by
        # calibration_launch.bind_amended_policy; old approvals grant no new scope.
        state = state["condition_amendments"][0]["parent_policy"]
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
