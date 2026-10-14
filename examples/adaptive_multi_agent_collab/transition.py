"""Audited fresh-condition boundaries and one frozen training-side recheck.

The historical checkpoint is evidence, never a source of mutable training state.
All spending remains attached to the original campaign lock and ledger.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, Mapping

import torch

from .experiment import (
    _campaign_lock,
    _relative,
    _write_json_atomic,
    build_experiment,
    planning_hooks,
    prepare_experiment,
    validate_journal_resume,
    wire_durable_journal,
)
from .preflight import PreflightIssue, PreflightReport, inspect_preflight
from .training import _module_fingerprint


AUTHORIZATION_PROTOCOL = "revised_condition_six_probe_authorization_v4"
TRANSITION_PROTOCOL = "fresh_condition_shared_ledger_boundary_v3"
RECHECK_PROTOCOL = "frozen_training_side_six_probe_execution_v1"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _read(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _copy_exact(source: str | Path, target: Path) -> None:
    with target.open("xb") as stream:
        stream.write(Path(source).read_bytes())
        stream.flush()
        os.fsync(stream.fileno())


def _anchor(specification_path: str | Path) -> Path:
    source = Path(specification_path).resolve()
    specification = _read(source)
    anchor = _relative(specification["campaign_directory"], source.parent).resolve()
    if not anchor.is_relative_to(Path(__file__).resolve().parent / "runs"):
        raise ValueError("revised execution must retain a registered campaign inside the example runs directory")
    lineage = specification.get("revised_condition", {})
    if _relative(lineage.get("source_campaign_directory", ""), source.parent).resolve() != anchor:
        raise ValueError("revised execution must retain its original durable ledger anchor")
    return anchor


def authorization_template(
    prepared: Any,
    plan: Mapping[str, Any],
    *,
    reconciliation_sha256: str | None,
    source_lineage: Mapping[str, Any],
    user_message_sha256: str,
    published_evidence_path: str | Path,
    user_message_path: str | Path | None = None,
    response_identity_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    retained_reservation_exception_sha256: str | None = None,
) -> dict[str, Any]:
    from .continuation import recheck_limits

    if (reconciliation_sha256 is None) == (retained_reservation_exception_sha256 is None):
        raise ValueError("exactly one complete reconciliation or retained-reservation exception must be bound")
    return {
        "protocol": AUTHORIZATION_PROTOCOL,
        "decision": "pending",
        "scope": "fresh_condition_and_one_frozen_six_probe_recheck_only",
        "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
        "condition_fingerprint": planning_hooks(prepared).condition_fingerprint,
        "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
        "plan_fingerprint": plan["fingerprint"],
        "source_lineage_fingerprint": _digest(source_lineage),
        "reconciliation_sha256": reconciliation_sha256,
        "accounting_mode": (
            "retained_reservation_exception"
            if retained_reservation_exception_sha256 is not None
            else "fully_reconciled"
        ),
        "retained_reservation_exception_sha256": retained_reservation_exception_sha256,
        "user_message_sha256": user_message_sha256,
        "user_message_path": str(Path(user_message_path).resolve()) if user_message_path is not None else None,
        "published_evidence": {
            "path": str(Path(published_evidence_path).resolve()),
            "sha256": _sha(published_evidence_path),
            "verified_bindings": _read(published_evidence_path),
        },
        "response_identity_evidence": copy.deepcopy(dict(response_identity_evidence or {})),
        "response_identity_evidence_sha256": _digest(response_identity_evidence or {}),
        **recheck_limits(plan),
        "pilot_authorized": False,
        "development_authorized": False,
        "test_authorized": False,
        "reviewer": "",
        "rationale": "",
    }


def validate_authorization(value: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    if set(value) != set(expected) or value.get("decision") != "approve_transition_and_recheck":
        raise ValueError("an explicit revised-condition transition and frozen recheck authorization is required")
    if any(value[key] != expected[key] for key in set(expected) - {"decision", "reviewer", "rationale"}):
        raise ValueError(
            "revised authorization differs from the exact condition, plan, source, reconciliation or spending scope"
        )
    for key in ("reviewer", "rationale"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError("revised authorization must record its reviewer and rationale")
    for key in (
        "manifest_fingerprint",
        "condition_fingerprint",
        "resource_profile_fingerprint",
        "plan_fingerprint",
        "source_lineage_fingerprint",
        "user_message_sha256",
    ):
        if (
            not isinstance(value[key], str)
            or len(value[key]) != 64
            or any(c not in "0123456789abcdef" for c in value[key])
        ):
            raise ValueError("revised authorization requires exact SHA256 bindings")
    for key in ("reconciliation_sha256", "retained_reservation_exception_sha256"):
        digest = value.get(key)
        if digest is not None and (
            not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("accounting authorization requires an exact artifact SHA256")


def _inputs(
    specification_path: str | Path,
    plan_path: str | Path,
    authorization_path: str | Path,
    reconciliation_path: str | Path | None,
    retained_reservation_path: str | Path | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    from .probes import validate_probe_plan
    from .resources import ExpenditureLedger, validate_reconciliation_receipt

    prepared = prepare_experiment(specification_path)
    accounting_path = _accounting_path(reconciliation_path, retained_reservation_path)
    specification, plan, authorization, reconciliation = (
        _read(path) for path in (specification_path, plan_path, authorization_path, accounting_path)
    )
    anchor = _anchor(specification_path)
    validate_probe_plan(plan, prepared)
    ledger = _read(anchor / "expenditure_ledger.json")
    if retained_reservation_path is not None:
        from .continuation import validate_historical_exception_receipt

        validate_historical_exception_receipt(
            ledger,
            reconciliation,
            condition_fingerprint=planning_hooks(prepared).condition_fingerprint,
            plan_fingerprint=plan["fingerprint"],
        )
    else:
        validate_reconciliation_receipt(ledger, reconciliation)
    current = ExpenditureLedger(prepared.config.resources)
    current.load_state_dict(ledger)
    if retained_reservation_path is None:
        current.assert_dispatch_allowed()
    if retained_reservation_path is None and any(
        current.stage_summary(stage)["unresolved_dispatch_ids"]
        for stage in ("compatibility", "pilot", "development", "test")
    ):
        raise ValueError("current campaign has unresolved reservations after the reconciled prefix")
    lineage = specification["revised_condition"]
    if retained_reservation_path is not None:
        if reconciliation.get("authorization", {}).get("source_lineage") != lineage:
            raise ValueError("retained-reservation exception differs from the exact historical source lineage")
    elif reconciliation.get("source_ledger_sha256") != lineage["source_ledger_sha256"]:
        raise ValueError("reconciliation is not bound to the historical ledger in this condition")
    for filename, field in (
        ("checkpoint.pt", "source_checkpoint_sha256"),
        ("experiment_manifest.json", "source_manifest_sha256"),
    ):
        if _sha(anchor / filename) != lineage[field]:
            raise ValueError("historical checkpoint or manifest changed before revised transition")
    publication = authorization.get("published_evidence", {})
    publication_path = Path(publication.get("path", ""))
    evidence = _read(publication_path)
    if evidence.get("all_checks_pass") is not True or evidence.get("bindings_unchanged") is not True:
        raise ValueError("recheck requires verified current public route and tariff evidence")
    if {row["backend"] for row in evidence.get("routes", ())} != set(prepared.backends):
        raise ValueError("published evidence does not cover the fixed backend pool")
    for row in evidence["routes"]:
        backend = prepared.backends[row["backend"]]
        gateway, profile = backend["gateway"], backend["resources"]
        if (row["model"], row["route"], row["provider"]) != (
            backend["model_name"],
            gateway["provider_endpoint"],
            gateway["served_provider"],
        ):
            raise ValueError("published route evidence differs from the fixed provider/model binding")
        if (float(row["pricing"]["prompt"]), float(row["pricing"]["completion"])) != (
            profile["input_price"],
            profile["output_price"],
        ):
            raise ValueError("published tariffs differ from the fixed resource profile")
        if not row.get("checks") or not all(value is True for value in row["checks"].values()):
            raise ValueError("published route/resource checks did not all pass")
        if (
            _sha(publication_path.parent / (row["model"].replace("/", "_") + "_endpoints.json"))
            != row["catalog_sha256"]
        ):
            raise ValueError("published route catalog source changed")
    expected = authorization_template(
        prepared,
        plan,
        reconciliation_sha256=_sha(reconciliation_path) if reconciliation_path is not None else None,
        retained_reservation_exception_sha256=(
            _sha(retained_reservation_path) if retained_reservation_path is not None else None
        ),
        source_lineage=lineage,
        user_message_sha256=authorization.get("user_message_sha256", ""),
        published_evidence_path=publication_path,
        response_identity_evidence=authorization.get("response_identity_evidence", {}),
    )
    user_source = authorization.get("user_message_path")
    if not user_source or _sha(user_source) != authorization.get("user_message_sha256"):
        raise ValueError("revised authorization must bind the preserved current user instruction")
    expected["user_message_path"] = str(Path(user_source).resolve())
    validate_authorization(authorization, expected)
    prepared = with_response_identity_evidence(prepared, authorization["response_identity_evidence"])
    return prepared, plan, authorization, reconciliation, anchor


def _accounting_path(
    reconciliation_path: str | Path | None, retained_reservation_path: str | Path | None
) -> str | Path:
    if (reconciliation_path is None) == (retained_reservation_path is None):
        raise ValueError("choose exactly one reconciliation receipt or retained-reservation exception")
    if retained_reservation_path is not None:
        return retained_reservation_path
    assert reconciliation_path is not None
    return reconciliation_path


def _saved_accounting_paths(directory: Path) -> tuple[Path | None, Path | None]:
    receipt = _read(directory / "transition.json")
    if receipt.get("accounting_mode") == "retained_reservation_exception":
        return None, directory / "retained_reservation_exception.json"
    return directory / "reconciliation_receipt.json", None


def _activate_exception(
    ledger: Any, accounting: Mapping[str, Any], *, condition: str, plan: str, guard: Callable[[], bool]
) -> None:
    ledger.activate_historical_exception(
        accounting["exception_id"],
        condition_fingerprint=condition,
        plan_fingerprint=plan,
        scope_id="recheck:" + plan,
        execution_guard=guard,
    )


def _register_recheck_scope(ledger: Any, *, condition: str, plan: str, authorization_path: str | Path) -> str:
    authorization = _read(authorization_path)
    ledger.stage = "compatibility"
    scope_id = "recheck:" + plan
    ledger.register_scope(
        scope_id,
        stage="compatibility",
        max_attempts=authorization.get("additional_physical_attempts", 6),
        budget=authorization.get("additional_budget_usd", 0.25),
        bindings={
            "condition_fingerprint": condition,
            "authorization_sha256": _sha(authorization_path),
            "plan_fingerprint": plan,
        },
    )
    ledger.activate_scope(scope_id)
    return scope_id


def with_response_identity_evidence(prepared: Any, evidence: Mapping[str, Mapping[str, Any]]) -> Any:
    """Validate exact public identity evidence without changing scientific settings."""
    from .gateway import OpenRouterBinding
    from .identity import validate_model_identity_evidence

    if not isinstance(evidence, Mapping) or not set(evidence).issubset(prepared.backends):
        raise ValueError("response identity evidence must refer only to the frozen backend pool")
    for key, item in evidence.items():
        binding = OpenRouterBinding.from_dict(prepared.backends[key]["gateway"])
        binding.validate()
        canonical = item.get("metadata_model")
        if not isinstance(canonical, str) or not canonical.strip():
            raise ValueError("response identity evidence requires an exact nonempty canonical model identifier")
        validate_model_identity_evidence(binding, canonical, item)
    return replace(prepared, response_identity_evidence=copy.deepcopy(dict(evidence)))


def inspect_recheck_preflight(
    specification_path: str | Path,
    *,
    plan_path: str | Path,
    authorization_path: str | Path,
    reconciliation_path: str | Path | None = None,
    retained_reservation_path: str | Path | None = None,
) -> PreflightReport:
    """Reuse backend/resource readiness without requiring a later pilot approval."""
    base = inspect_preflight(specification_path, operation="compatibility")
    issues = [
        issue
        for issue in base.issues
        if issue.code not in {"stage_authorization", "revised_condition_lineage", "campaign_halted"}
    ]
    checks = dict(base.checks)
    checks.pop("compatibility_plan", None)
    checks.pop("stage_authorization_template", None)
    try:
        _, plan, authorization, reconciliation, _ = _inputs(
            specification_path, plan_path, authorization_path, reconciliation_path, retained_reservation_path
        )
        checks.update(
            frozen_recheck_plan_fingerprint=plan["fingerprint"],
            reconciliation_sha256=_sha(reconciliation_path) if reconciliation_path is not None else None,
            retained_reservation_exception_sha256=(
                _sha(retained_reservation_path) if retained_reservation_path is not None else None
            ),
            retained_reservation_verified_as_zero=False,
            historical_halt_release=reconciliation.get("release_halt_with_provenance"),
            requested_stage="targeted_recheck",
            additional_physical_attempts=authorization.get("additional_physical_attempts", 6),
            additional_budget_usd=authorization.get("additional_budget_usd", 0.25),
            pilot_authorized=False,
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        issues.append(PreflightIssue("revised_recheck_binding", str(error)))
    return PreflightReport("targeted_recheck", base.approval_fingerprint, tuple(issues), checks)


def _parameters(trainer: Any) -> dict[str, str]:
    return {
        "alpha": _module_fingerprint(trainer.alpha),
        "weighting": _module_fingerprint(trainer.weighting),
        "gamma": _digest(trainer.gamma.to_dict()),
    }


def _frozen_files(
    specification_path: str | Path,
    plan_path: str | Path,
    authorization_path: str | Path,
    reconciliation_path: str | Path,
    plan: Mapping[str, Any],
    authorization: Mapping[str, Any],
    anchor: Path,
) -> dict[str, str]:
    paths = [Path(value) for value in (specification_path, plan_path, authorization_path, reconciliation_path)]
    paths.extend(anchor / name for name in ("checkpoint.pt", "experiment_manifest.json"))
    for item in (plan.get("source_artifact"), plan.get("original_compatibility_baseline", {}).get("artifact")):
        if item:
            paths.append(Path(item["path"]))
    for item in (
        plan.get("prior_plan"),
        plan.get("synthetic_fixtures"),
        plan.get("consumed_compatibility_baseline", {}).get("artifact"),
    ):
        if item:
            paths.append(Path(item["path"]))
    for row in plan.get("requests", ()):
        profile = row.get("role_output_profile") or {}
        for item in (profile.get("endpoint_catalog"), *profile.get("documentation", ())):
            if item:
                paths.append(Path(item["path"]))
    publication = authorization.get("published_evidence")
    if publication:
        path = Path(publication["path"])
        paths.append(path)
        paths.extend(
            path.parent / (row["model"].replace("/", "_") + "_endpoints.json")
            for row in publication["verified_bindings"]["routes"]
        )
    if authorization.get("user_message_path"):
        paths.append(Path(authorization["user_message_path"]))
    for item in authorization.get("response_identity_evidence", {}).values():
        paths.extend(Path(item[kind]["path"]) for kind in ("model_catalog", "endpoint_catalog"))
    return {str(path): _sha(path) for path in paths}


def _verify_probe_request(runtime: Any, probe: Mapping[str, Any]) -> None:
    """Verify the exact current composition and runtime identity before dispatch."""
    from .prompts import compose_backend_input, protocol_instructions
    from .randomness import GenerationContext, LogicalCallIdentity

    identity = runtime.config.agent_pool[probe["agent_id"]]
    instructions = "\n".join(value for value in (identity.system_configuration, identity.role_configuration) if value)
    context = dict(probe["context"])
    context["agent_instructions"] = instructions
    actual = compose_backend_input(instructions, protocol_instructions(probe["component"], context), probe["prompt"])
    logical = LogicalCallIdentity(
        probe["agent_id"],
        int(context.get("session", 0)),
        probe["call_kind"],
        probe["call_kind"],
        int(context.get("round", 0)),
        int(context.get("repair_attempt", 0)),
    )
    generation = GenerationContext(**probe["generation_context"])
    if (
        _digest(actual) != _digest(probe["effective_input"])
        or asdict(logical) != probe["logical_call"]
        or generation.seed_for(logical) != probe["generation_seed"]
    ):
        raise ValueError("runtime request composition or logical seed differs from its frozen probe")
    if "wire_body" in probe:
        from .gateway import OpenRouterBinding, preview_request_body

        adapter = runtime.agents[probe["agent_id"]]
        schema = adapter.response_format(probe["call_kind"])
        if schema != probe.get("response_format") or adapter.role_output != probe.get("role_output_profile"):
            raise ValueError("runtime native schema differs from the exact frozen role profile")
        client = adapter.model.model_client_config
        settings: dict[str, Any] = {"max_tokens": runtime.config.resources.caps(probe["call_kind"])[1]}
        if adapter.supports_seed:
            settings["seed"] = generation.seed_for(logical)
        if probe["call_kind"] == "role":
            settings["workflow_call_kind"] = "role"
        if schema is not None:
            settings["response_format"] = schema
        body = preview_request_body(
            OpenRouterBinding.from_dict(client.openrouter_binding),
            adapter.model.model_config.temperature,
            actual,
            settings,
            role_output=adapter.role_output,
            response_identity_evidence=getattr(client, "openrouter_response_identity_evidence", None),
        )
        body_hash = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if body != probe["wire_body"] or body_hash != probe.get("wire_body_sha256"):
            raise ValueError("runtime serialized request differs from its frozen exact wire body")


def _verify_fresh(trainer: Any, hooks: Any, historical_path: Path) -> None:
    from .prompts import seed_prompt_collection

    historical = torch.load(historical_path, map_location="cpu", weights_only=False)
    if any(historical["trainer_state"]["updates"].values()) or any(
        historical[name]["state"] for name in ("alpha_optimizer", "weighting_optimizer")
    ):
        raise ValueError("source numerical state is not a verified untrained initialization")
    if trainer.state.phase != "unstarted" or trainer.alpha_optimizer.state or trainer.weighting_optimizer.state:
        raise ValueError("revised condition requires fresh untrained numerical state")
    for name, module in (("alpha", trainer.alpha), ("weighting", trainer.weighting)):
        fresh, old = module.state_dict(), historical[name]
        if set(fresh) != set(old) or any(
            not torch.equal(value.detach().cpu(), old[key]) for key, value in fresh.items()
        ):
            raise ValueError(
                "fresh initialized numerical parameters differ from the verified historical initialization"
            )
    if trainer.gamma.to_dict() != seed_prompt_collection().to_dict():
        raise ValueError("revised condition requires the current seed prompts, not old accepted prompts")
    if (
        hooks.pilot_observations
        or hooks.safety_record is not None
        or hooks.termination_record is not None
        or hooks.evaluation_records
        or hooks.evaluation_cache
        or hooks.stage_authorizations
        or hooks.targeted_recheck is not None
    ):
        raise ValueError("revised condition inherited historical workflow, score, cache or approval state")


def _validated_registry_history(anchor: Path, registry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    path = anchor / "condition_registry_history.json"
    if not path.exists():
        return None
    history = _read(path)
    records = history.get("conditions", [])
    if (
        registry is None
        or history.get("protocol") != "append_only_condition_registry_v1"
        or not records
        or records[-1] != registry
        or len({item["condition_fingerprint"] for item in records}) != len(records)
        or any(
            Path(item["state_directory"]).resolve() != anchor / "conditions" / item["condition_fingerprint"]
            or _read(Path(item["state_directory"]) / "transition.json") != item
            for item in records
        )
    ):
        raise ValueError("successive condition registry history changed or transition is incomplete")
    return history


def initialize_revised_condition(
    specification_path: str | Path,
    *,
    plan_path: str | Path,
    authorization_path: str | Path,
    reconciliation_path: str | Path | None = None,
    retained_reservation_path: str | Path | None = None,
    builder: Callable[..., Any] = build_experiment,
) -> dict[str, Any]:
    report = inspect_recheck_preflight(
        specification_path,
        plan_path=plan_path,
        authorization_path=authorization_path,
        reconciliation_path=reconciliation_path,
        retained_reservation_path=retained_reservation_path,
    )
    report.require_ready()
    _, _, _, _, anchor = _inputs(
        specification_path, plan_path, authorization_path, reconciliation_path, retained_reservation_path
    )
    with _campaign_lock(anchor) as guard:
        prepared, plan, authorization, reconciliation, _ = _inputs(
            specification_path, plan_path, authorization_path, reconciliation_path, retained_reservation_path
        )
        registry_path = anchor / "active_condition.json"
        prior_registry = _read(registry_path) if registry_path.exists() else None
        prior_history = _validated_registry_history(anchor, prior_registry)
        if prior_registry is not None:
            from .continuation import EXTENSION_PROTOCOL

            if (
                reconciliation.get("protocol") != EXTENSION_PROTOCOL
                or reconciliation["authorization"]["previous_boundary"]["registry"] != prior_registry
            ):
                raise ValueError(
                    "a revised execution boundary already exists; only its audited successive exception can replace the pointer"
                )
        condition = planning_hooks(prepared).condition_fingerprint
        if prior_registry is not None and condition == prior_registry["condition_fingerprint"]:
            raise ValueError("a successive condition cannot reset the existing scientific condition")
        directory = anchor / "conditions" / condition
        if directory.exists():
            raise ValueError("an incomplete revised transition exists; review its durable artifacts before retrying")
        trainer, hooks = builder(prepared)
        _verify_fresh(trainer, hooks, anchor / "checkpoint.pt")
        hooks.expenditure_ledger.load_state_dict(_read(anchor / "expenditure_ledger.json"))
        if retained_reservation_path is not None:
            _register_recheck_scope(
                hooks.expenditure_ledger,
                condition=condition,
                plan=plan["fingerprint"],
                authorization_path=authorization_path,
            )
            _activate_exception(
                hooks.expenditure_ledger, reconciliation, condition=condition, plan=plan["fingerprint"], guard=guard
            )
        hooks.expenditure_ledger.assert_dispatch_allowed()
        if retained_reservation_path is None and any(
            hooks.expenditure_ledger.stage_summary(stage)["unresolved_dispatch_ids"]
            for stage in ("compatibility", "pilot", "development", "test")
        ):
            raise ValueError("all historical reservations must be authoritatively resolved before transition")
        receipt = {
            "protocol": TRANSITION_PROTOCOL,
            "condition_fingerprint": condition,
            "manifest_fingerprint": prepared.authorization_manifest_fingerprint,
            "resource_profile_fingerprint": prepared.resource_profile_fingerprint,
            "plan_fingerprint": plan["fingerprint"],
            "campaign_directory": str(anchor),
            "state_directory": str(directory),
            "checkpoint_path": str(directory / "checkpoint.pt"),
            "authorization_sha256": _sha(authorization_path),
            "reconciliation_sha256": _sha(reconciliation_path) if reconciliation_path is not None else None,
            "accounting_mode": (
                "retained_reservation_exception" if retained_reservation_path is not None else "fully_reconciled"
            ),
            "retained_reservation_exception_sha256": (
                _sha(retained_reservation_path) if retained_reservation_path is not None else None
            ),
            "historical_reservation_remains_unknown": retained_reservation_path is not None,
            "response_identity_evidence": copy.deepcopy(dict(prepared.response_identity_evidence)),
            "response_identity_evidence_sha256": _digest(prepared.response_identity_evidence),
            "source_lineage": _read(specification_path)["revised_condition"],
            "parameter_receipts": _parameters(trainer),
            "imported_state": "physical_expenditure_only",
            "pilot_authorized": False,
            "development_authorized": False,
            "test_authorized": False,
            "previous_condition": copy.deepcopy(prior_registry),
        }
        # Every validation above is read-only. The registry is the final commit
        # marker; no old checkpoint, report, manifest or approval is overwritten.
        directory.mkdir(parents=True)
        _copy_exact(authorization_path, directory / "transition_authorization.json")
        if retained_reservation_path is not None:
            _copy_exact(retained_reservation_path, directory / "retained_reservation_exception.json")
        else:
            _copy_exact(_accounting_path(reconciliation_path, None), directory / "reconciliation_receipt.json")
        _copy_exact(plan_path, directory / "probe_plan.json")
        _write_json_atomic(directory / "experiment_manifest.json", prepared.manifest())
        _write_json_atomic(directory / "preflight.json", report.to_dict())
        _write_json_atomic(directory / "transition.json", receipt)
        trainer.hooks, trainer.checkpoint_path = hooks, directory / "checkpoint.pt"
        hooks.execution_session_guard = guard
        wire_durable_journal(trainer, hooks, directory, campaign_directory=anchor)
        trainer._checkpoint()
        if prior_registry is not None:
            history_path = anchor / "condition_registry_history.json"
            history = prior_history or {"protocol": "append_only_condition_registry_v1", "conditions": [prior_registry]}
            history["conditions"].append(receipt)
            _write_json_atomic(history_path, history)
        _write_json_atomic(registry_path, receipt)
        return receipt


def registered_state_directory(specification_path: str | Path) -> Path | None:
    """Resolve only an audited pointer; changing output cannot establish a new budget."""
    if _read(specification_path).get("policy_transition"):
        from .policy_activation import registered_policy_directory

        return registered_policy_directory(specification_path)
    anchor = _anchor(specification_path)
    registry_path = anchor / "active_condition.json"
    if not registry_path.is_file():
        return None
    registry = _read(registry_path)
    prepared = prepare_experiment(specification_path)
    condition = planning_hooks(prepared).condition_fingerprint
    directory = anchor / "conditions" / condition
    if (
        registry.get("protocol") != TRANSITION_PROTOCOL
        or registry.get("condition_fingerprint") != condition
        or Path(registry.get("state_directory", "")).resolve() != directory
    ):
        raise ValueError("registered revised boundary differs from the active condition")
    if _read(directory / "transition.json") != registry:
        raise ValueError("registered revised boundary differs from its durable transition receipt")
    _validated_registry_history(anchor, registry)
    return directory


def validate_revised_recheck_evidence(specification_path: str | Path) -> dict[str, Any]:
    """Combine unchanged historical backend evidence with only the six affected probes.

    This does not approve a pilot and does not assert that a full compatibility
    suite or TextGrad execution ran under the revised collaboration prompts.
    """
    if _read(specification_path).get("policy_transition"):
        from .policy_activation import policy_compatibility_evidence

        return policy_compatibility_evidence(specification_path)
    directory = registered_state_directory(specification_path)
    if directory is None:
        raise ValueError("the revised condition has no registered fresh execution boundary")
    prepared, plan, _, _, anchor = _inputs(
        specification_path,
        directory / "probe_plan.json",
        directory / "transition_authorization.json",
        *_saved_accounting_paths(directory),
    )
    if _saved_accounting_paths(directory)[1] is not None:
        raise ValueError(
            "retained historical reservation requires separate audited pilot disposition and approval; the recheck exception cannot authorize later stages"
        )
    validate_journal_resume(directory / "checkpoint.pt", directory, campaign_directory=anchor)
    payload = torch.load(directory / "checkpoint.pt", map_location="cpu", weights_only=False)
    report = _read(directory / "recheck_report.json")
    if payload.get("request_boundary_pending") is not False or _digest(
        payload.get("hook_state", {}).get("targeted_recheck")
    ) != _digest(report):
        raise ValueError("targeted recheck is not at its exact durable recorded boundary")
    if (
        report.get("status") != "complete"
        or report.get("stage_complete") is not True
        or report.get("parameter_receipts", {}).get("unchanged") is not True
        or report.get("condition_fingerprint") != planning_hooks(prepared).condition_fingerprint
        or report.get("plan_fingerprint") != plan["fingerprint"]
    ):
        raise ValueError("the six affected probes did not all complete under the fixed revised condition")
    if [item["probe_id"] for item in report.get("probes", ())] != [
        item["probe_id"] for item in plan["requests"]
    ] or any(not row.get("valid_output") or not row.get("native_evidence_valid") for row in report["probes"]):
        raise ValueError("targeted recheck has incomplete structured or native receipt evidence")
    historical = _read(anchor / "compatibility_report.json")
    old_checkpoint = torch.load(anchor / "checkpoint.pt", map_location="cpu", weights_only=False)
    if not any(
        _digest(record) == _digest(historical)
        for record in old_checkpoint.get("hook_state", {}).get("compatibility_records", ())
    ):
        raise ValueError("historical backend evidence differs from its preserved recorded checkpoint")
    assessment = historical.get("assessment", {})
    if (
        historical.get("status") != "complete"
        or assessment.get("passed") is not True
        or assessment.get("textgrad", {}).get("status") != "validated"
        or set(assessment.get("backends", {})) != {"0", "1", "2"}
    ):
        raise ValueError("unchanged historical backend and TextGrad evidence is incomplete")
    dimensions = ("route_identity", "non_thinking", "token_counter", "native_usage", "billing")
    if any(
        row.get(name, {}).get("status") != "validated" for row in assessment["backends"].values() for name in dimensions
    ):
        raise ValueError("unchanged historical backend evidence is incomplete")
    return {
        "scope": "historical_backend_and_textgrad_evidence_plus_six_affected_training_probes",
        "recheck_report_sha256": _sha(directory / "recheck_report.json"),
        "historical_compatibility_sha256": _sha(anchor / "compatibility_report.json"),
        "pilot_authorized": False,
    }


def with_registered_response_identity(prepared: Any, specification_path: str | Path) -> Any:
    """Use only the map recorded by an audited new-state transition; legacy builds stay strict."""
    source = Path(specification_path).resolve()
    specification = _read(source)
    if specification.get("policy_transition") or specification.get("independent_campaign"):
        from .policy_activation import policy_response_identity

        return policy_response_identity(prepared, source)
    anchor = _relative(specification["campaign_directory"], source.parent).resolve()
    if not (anchor / "active_condition.json").is_file():
        if prepared.response_identity_evidence:
            raise ValueError("response identity normalization requires its registered revised boundary")
        return prepared
    directory = registered_state_directory(source)
    if directory is None:
        raise ValueError("registered response identity evidence has no revised state directory")
    audited, _, authorization, _, _ = _inputs(
        source,
        directory / "probe_plan.json",
        directory / "transition_authorization.json",
        *_saved_accounting_paths(directory),
    )
    if _saved_accounting_paths(directory)[1] is not None:
        raise ValueError("the retained-reservation exception is limited to transition and targeted recheck")
    receipt = _read(directory / "transition.json")
    evidence = authorization["response_identity_evidence"]
    if receipt.get("response_identity_evidence_sha256") != _digest(evidence) or _digest(
        receipt.get("response_identity_evidence")
    ) != _digest(evidence):
        raise ValueError("registered response identity evidence differs from its approved transition")
    if planning_hooks(prepared).condition_fingerprint != planning_hooks(audited).condition_fingerprint:
        raise ValueError("registered response identity evidence belongs to another scientific condition")
    return with_response_identity_evidence(prepared, evidence)


async def execute_recheck(
    specification_path: str | Path,
    *,
    plan_path: str | Path,
    authorization_path: str | Path,
    reconciliation_path: str | Path | None = None,
    retained_reservation_path: str | Path | None = None,
    checkpoint_path: str | Path,
    builder: Callable[..., Any] = build_experiment,
) -> dict[str, Any]:
    """Execute the frozen six-row plan once; repairs/retries use the same scope budget."""
    from .calibration import compatibility_attempt_assessment
    from .probes import request_from_probe
    from .randomness import GenerationContext
    from .schemas import AgentOutput, RoleOutput, TieOutput, QueryExample
    from .resources import ResourceFailure

    preflight = inspect_recheck_preflight(
        specification_path,
        plan_path=plan_path,
        authorization_path=authorization_path,
        reconciliation_path=reconciliation_path,
        retained_reservation_path=retained_reservation_path,
    )
    preflight.require_ready()
    prepared, plan, authorization, accounting, anchor = _inputs(
        specification_path, plan_path, authorization_path, reconciliation_path, retained_reservation_path
    )
    with _campaign_lock(anchor) as guard:
        prepared, plan, authorization, accounting, _ = _inputs(
            specification_path, plan_path, authorization_path, reconciliation_path, retained_reservation_path
        )
        directory = registered_state_directory(specification_path)
        if directory is None or Path(checkpoint_path).resolve() != directory / "checkpoint.pt":
            raise ValueError("recheck requires the registered revised checkpoint, not historical ordinary resume")
        transition = _read(directory / "transition.json")
        if (
            transition["authorization_sha256"] != _sha(authorization_path)
            or transition["plan_fingerprint"] != plan["fingerprint"]
        ):
            raise ValueError("recheck authorization/plan differs from its initialized boundary")
        if transition.get("response_identity_evidence_sha256") != _digest(prepared.response_identity_evidence):
            raise ValueError("recheck response identity evidence differs from its initialized boundary")
        if transition.get("retained_reservation_exception_sha256") != (
            _sha(retained_reservation_path) if retained_reservation_path is not None else None
        ):
            raise ValueError("recheck accounting exception differs from the initialized boundary")
        validate_journal_resume(
            checkpoint_path, directory, campaign_directory=anchor, allow_halted=retained_reservation_path is not None
        )
        report_path = directory / "recheck_report.json"
        record = (
            _read(report_path)
            if report_path.exists()
            else {
                "protocol": RECHECK_PROTOCOL,
                "plan_fingerprint": plan["fingerprint"],
                "condition_fingerprint": transition["condition_fingerprint"],
                "authorization_sha256": _sha(authorization_path),
                "status": "running",
                "pending_probe": None,
                "probes": [],
                "parameter_receipts": {"before": transition["parameter_receipts"]},
                "pilot_authorized": False,
                "response_identity_evidence_sha256": _digest(prepared.response_identity_evidence),
                "retained_reservation_exception_sha256": (
                    _sha(retained_reservation_path) if retained_reservation_path is not None else None
                ),
                "historical_reservation_remains_unknown": retained_reservation_path is not None,
            }
        )
        if record.get("status") != "running":
            raise ValueError("this frozen recheck already has a terminal result; duplicate execution is disabled")
        if record.get("pending_probe") is not None:
            raise ValueError("recheck has an unresolved request boundary; reconcile before any replay")
        if record.get("plan_fingerprint") != plan["fingerprint"] or record.get("authorization_sha256") != _sha(
            authorization_path
        ):
            raise ValueError("saved recheck belongs to a different frozen plan or authorization")
        if [item["probe_id"] for item in record["probes"]] != [
            item["probe_id"] for item in plan["requests"][: len(record["probes"])]
        ]:
            raise ValueError("saved recheck does not contain the ordered completed prefix")
        trainer, hooks = builder(prepared)
        hooks.execution_session_guard = guard
        if retained_reservation_path is not None:
            hooks.recheck_exception_context = {
                "receipt": copy.deepcopy(accounting),
                "condition_fingerprint": transition["condition_fingerprint"],
                "plan_fingerprint": plan["fingerprint"],
            }
        trainer.resume_from_checkpoint(checkpoint_path, hooks=hooks)
        if hooks.targeted_recheck is not None and _digest(hooks.targeted_recheck) != _digest(record):
            raise ValueError("saved recheck sidecar differs from its durable checkpoint boundary")
        if hooks.targeted_recheck is None and record["probes"]:
            raise ValueError("saved recheck prefix is missing from its durable checkpoint boundary")
        if (
            trainer.state.phase != "unstarted"
            or trainer.state.pilot_complete
            or any(trainer.state.updates.values())
            or any(trainer.state.attempts.values())
        ):
            raise ValueError("targeted recheck cannot run a pilot or mutate an already trained condition")
        if _parameters(trainer) != transition["parameter_receipts"]:
            raise ValueError("recheck changed the initialized numerical parameters or revised seed prompts")
        trainer.hooks, trainer.checkpoint_path = hooks, Path(checkpoint_path)
        hooks.execution_session_guard = guard
        ledger = hooks.expenditure_ledger
        wire_durable_journal(trainer, hooks, directory, campaign_directory=anchor)
        scope_id = _register_recheck_scope(
            ledger,
            condition=transition["condition_fingerprint"],
            plan=plan["fingerprint"],
            authorization_path=authorization_path,
        )
        if retained_reservation_path is not None:
            _activate_exception(
                ledger, accounting, condition=transition["condition_fingerprint"], plan=plan["fingerprint"], guard=guard
            )
        ledger.assert_dispatch_allowed()
        _write_json_atomic(report_path, record)
        trainer._checkpoint()
        runtime = hooks._runtime()
        frozen_files = _frozen_files(
            specification_path,
            plan_path,
            authorization_path,
            _accounting_path(reconciliation_path, retained_reservation_path),
            plan,
            authorization,
            anchor,
        )

        def persist() -> None:
            record["scope_expenditure"] = ledger.scope_summary(scope_id)
            record["expenditure_by_stage"] = {
                stage: ledger.stage_summary(stage) for stage in ("compatibility", "pilot", "development", "test")
            }
            _write_json_atomic(report_path, record)
            hooks.targeted_recheck = copy.deepcopy(record)
            trainer._checkpoint()

        for probe in plan["requests"][len(record["probes"]) :]:
            if guard() is not True:
                raise ValueError("recheck lost its original campaign execution lock")
            ledger.assert_dispatch_allowed()
            if any(_sha(path) != digest for path, digest in frozen_files.items()):
                raise ValueError("a frozen execution input or source artifact changed before dispatch")
            if ledger.scope_summary(scope_id)["attempts"] >= ledger.scopes[scope_id]["max_attempts"]:
                record["status"] = "incomplete"
                record["completion_reason"] = "additional physical attempt ceiling reached"
                break
            record["pending_probe"] = probe["probe_id"]
            _write_json_atomic(report_path, record)
            trainer._request_checkpoint()
            entry_start, call_start = len(ledger.entries), len(runtime._call_records)
            outcome: dict[str, Any] = {
                "probe_id": probe["probe_id"],
                "source_evidence": copy.deepcopy(probe["source_evidence"]),
                "deliberate_repair_shape": probe["deliberate_repair_shape"],
            }
            try:
                request = request_from_probe(probe)
                _verify_probe_request(runtime, probe)
                runtime._generation_context = GenerationContext(**probe["generation_context"])
                if probe["source_evidence"].get("partition") == "synthetic":
                    fixture = probe.get("runtime_example", {})
                    if set(fixture) != {"example_id", "query", "candidates"}:
                        raise ValueError("synthetic probe must contain only its unlabelled fixed runtime example")
                    example = QueryExample(**fixture)
                else:
                    example = replace(
                        prepared.dataset.select("train", (probe["source_evidence"]["query_id"],))[0], gold_answer=None
                    )
                allowed = tuple(request.context.get("tied_answers", example.candidates))
                expected_type = {"role": RoleOutput, "tie": TieOutput, "revision": AgentOutput}[probe["call_kind"]]
                validator = (
                    runtime._validate_role_output
                    if probe["call_kind"] == "role"
                    else runtime._answer_context_validator(example, allowed, "frozen recheck")
                )
                if probe["deliberate_repair_shape"]:
                    output = await runtime._call(request)
                    _, validation_error = validator(output)
                else:
                    output = await runtime._validated_call(request, expected_type, validator)
                    validation_error = output.error
                outcome["output"] = asdict(output)
                outcome["valid_output"] = (
                    output.structured_output is not None
                    and validation_error is None
                    and not output.validation_exhausted
                    and isinstance(output, expected_type)
                )
                outcome["error"] = validation_error
            except (ResourceFailure, RuntimeError, ValueError, TypeError) as error:
                outcome.update(valid_output=False, error=f"{type(error).__name__}: {error}")
                record["status"] = "failed"
            finally:
                outcome["call_records"] = copy.deepcopy(runtime._call_records[call_start:])
                outcome["physical_entries"] = copy.deepcopy(ledger.entries[entry_start:])
                outcome["native_receipts"] = [
                    compatibility_attempt_assessment(entry) for entry in ledger.entries[entry_start:]
                ]
            receipts = outcome["native_receipts"]
            if (
                not receipts
                or any(receipt["checks"].get("finish") == "unavailable" for receipt in receipts)
                or any(
                    value not in {"validated", "not_generated"}
                    for receipt in receipts
                    for name, value in receipt["checks"].items()
                    if name != "finish"
                )
            ):
                record["status"] = "failed"
                outcome["error"] = (
                    outcome.get("error") or "native usage, identity, finish or billing evidence unavailable"
                )
            outcome["native_evidence_valid"] = (
                bool(receipts)
                and all(receipt["checks"].get("finish") != "unavailable" for receipt in receipts)
                and all(
                    value in {"validated", "not_generated"}
                    for receipt in receipts
                    for name, value in receipt["checks"].items()
                    if name != "finish"
                )
            )
            try:
                ledger.assert_dispatch_allowed()
            except ResourceFailure:
                record["status"] = "failed"
            record["probes"].append(outcome)
            record["pending_probe"] = None
            record["operational_halt"] = copy.deepcopy(ledger.blocking_halt)
            record["retained_historical_halt"] = (
                copy.deepcopy(ledger.halted) if retained_reservation_path is not None else None
            )
            record["parameter_receipts"]["after"] = _parameters(trainer)
            record["parameter_receipts"]["unchanged"] = (
                record["parameter_receipts"]["after"] == record["parameter_receipts"]["before"]
            )
            if not record["parameter_receipts"]["unchanged"]:
                ledger.halt("targeted recheck altered initialized parameters or prompts")
                record["status"] = "failed"
            persist()
            if record["status"] == "failed":
                break
        if record["status"] == "running":
            record["status"] = (
                "complete"
                if all(item.get("valid_output") and item.get("native_evidence_valid") for item in record["probes"])
                else "failed"
            )
        record["unattempted_probe_ids"] = [probe["probe_id"] for probe in plan["requests"][len(record["probes"]) :]]
        record["stage_complete"] = record["status"] == "complete" and len(record["probes"]) == 6
        record["semantic_assessment"] = (
            "outputs retained for human review; structured validity is not correctness or verified progress"
        )
        # End only the active selection, retaining the registered limits and all
        # scoped receipts. A later independently authorized pilot uses its own stage.
        ledger.activate_scope(None)
        persist()
        return copy.deepcopy(record)
