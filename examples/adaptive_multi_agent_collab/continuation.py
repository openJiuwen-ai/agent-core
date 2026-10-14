"""An explicit retained-reservation exception, scoped to one revised recheck.

Nothing here settles unknown usage, clears a halt, performs a lookup, or creates
a generation. The exception is evidence; dispatch also needs a process-local
context under the existing campaign lock and the frozen compatibility scope.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import ResourceConfig
from .reconciliation import (
    ACCOUNTING_HALT,
    _read,
    _snapshot_bytes,
    _write,
    digest,
    generation_identity,
    validate_accounting_history,
)
from .resources import ExpenditureLedger

AUTHORIZATION_PROTOCOL = "retained_historical_accounting_exception_authorization_1"
EXCEPTION_PROTOCOL = "retained_historical_accounting_exception_1"
EXTENSION_AUTHORIZATION_PROTOCOL = "retained_historical_accounting_exception_authorization_2"
EXTENSION_PROTOCOL = "retained_historical_accounting_exception_2"
LIMIT_AMENDMENT_PROTOCOL = "audited_compatibility_attempt_extension_1"
EXCEPTED_DISPATCH = 400
EXCEPTED_GENERATION = "gen-1789981189-6sOVOcpSKrVTJ64ySuuU"
EXCEPTED_REQUEST = "e926ac8cf9a858467a6a18b66859f45a477712ea17cd1ceaf91142d2a2eef8c0"
RETAINED_RESERVATION = 0.0018816


def _sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fingerprint(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _effective_charge(state: Mapping[str, Any], entry: Mapping[str, Any]) -> float | None:
    for record in state.get("accounting_reconciliations", []):
        if record["dispatch_id"] == entry["dispatch_id"]:
            return record["usage"]["total_cost"]
    return entry["charged"]


def _validate_historical_source(state: Mapping[str, Any]) -> None:
    validate_accounting_history(state)
    entries = state["entries"]
    if len(entries) != 403:
        raise ValueError("the historical exception requires the exact 403-dispatch boundary")
    entry = entries[EXCEPTED_DISPATCH]
    messages = entry.get("effective_input")
    if not isinstance(messages, list):
        raise ValueError("the original dispatch-400 effective request must remain available")
    request_sha = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if request_sha != EXCEPTED_REQUEST or request_sha != entry.get("request_sha256"):
        raise ValueError("the original dispatch-400 effective request body changed")
    if (
        entry.get("dispatch_id") != EXCEPTED_DISPATCH
        or entry.get("stage") != "pilot"
        or generation_identity(entry) != EXCEPTED_GENERATION
        or entry.get("request_sha256") != EXCEPTED_REQUEST
        or not math.isclose(entry["reserved"], RETAINED_RESERVATION, rel_tol=0, abs_tol=1e-15)
    ):
        raise ValueError("the historical exception cannot change the dispatch, generation, request, or reservation")
    if not isinstance(state.get("halted"), Mapping) or state["halted"].get("reason") != ACCOUNTING_HALT:
        raise ValueError("the original unresolved-accounting halt must remain present")
    if state.get("halt_releases") or state.get("halt_events"):
        raise ValueError("the historical exception cannot cover another released or newly recorded halt")
    if entry.get("charged") is not None or _effective_charge(state, entry) is not None:
        raise ValueError("dispatch 400 must retain its unknown actual charge")
    usage = entry.get("usage", {})
    if any(usage.get(key) is not None for key in ("input_tokens", "output_tokens", "reference_cost", "total_cost")):
        raise ValueError("unknown dispatch-400 usage and reference cost must not be fabricated")
    if {item["dispatch_id"] for item in entries if _effective_charge(state, item) is None} != {EXCEPTED_DISPATCH}:
        raise ValueError("this exception covers only the single unresolved dispatch 400")
    settled = {item["dispatch_id"] for item in state.get("accounting_reconciliations", [])}
    if settled != {401, 402}:
        raise ValueError("the authenticated settlements for 401 and 402 must remain complete")
    registered = state.get("reconciliation_audit", {})
    original_path = Path(registered["audit_directory"]) / "source_ledger.json"
    if _sha(original_path) != registered.get("source_ledger_sha256"):
        raise ValueError("the original pre-reconciliation ledger snapshot changed")
    original = _read(original_path)
    if (
        original["entries"] != entries
        or original["limits"] != state["limits"]
        or original["protocol"] != state["protocol"]
    ):
        raise ValueError("the original physical failures, requests, usage, or cumulative limits changed")
    intents = state.get("accounting_lookup_intents", [])
    if len(intents) != 9 or any(
        sum(item["dispatch_id"] == index for item in intents) != 3 for index in (400, 401, 402)
    ):
        raise ValueError("all nine exhausted historical accounting lookup intents must remain recorded")
    limits = state["limits"]
    if (
        limits.get("currency") != "USD"
        or limits.get("compatibility_budget") != 3
        or limits.get("compatibility_max_attempts") != 24
    ):
        raise ValueError("the exception cannot change cumulative compatibility spending limits")
    if sum(item["stage"] == "compatibility" for item in entries) != 18:
        raise ValueError("the original eighteen compatibility attempts must remain consumed")


def _source_state(campaign: Path) -> tuple[dict[str, Any], bytes]:
    state = _read(campaign / "expenditure_ledger.json")
    existing = state.get("historical_accounting_exceptions", [])
    if existing:
        raw = Path(existing[0]["source_snapshot_path"]).read_bytes()
        source = _read(Path(existing[0]["source_snapshot_path"]))
        return source, raw
    return state, (campaign / "expenditure_ledger.json").read_bytes()


def recheck_limits(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Only the two explicitly approved recheck profiles are representable."""
    names = (
        "additional_physical_attempts",
        "additional_budget_usd",
        "cumulative_compatibility_attempts",
        "cumulative_compatibility_budget_usd",
    )
    execution = plan.get("execution", {})
    legacy = (
        execution.get("additional_physical_attempt_ceiling", 6),
        execution.get("additional_spending_ceiling_usd", 0.25),
        execution.get("cumulative_physical_attempt_ceiling", 24),
        execution.get("cumulative_spending_ceiling_usd", 3.0),
    )
    values = tuple(plan.get(name, legacy[index]) for index, name in enumerate(names))
    if values not in ((6, 0.25, 24, 3.0), (24, 0.5, 48, 3.0)) or any(isinstance(value, bool) for value in values):
        raise ValueError("unsupported explicitly authorized recheck limits")
    if any(name in plan for name in names) and not all(name in plan for name in names):
        raise ValueError("the frozen plan must declare every recheck limit together")
    if execution and values != legacy:
        raise ValueError("frozen plan execution limits disagree")
    return dict(zip(names, values))


def _previous_boundary(campaign: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    registry = _read(campaign / "active_condition.json")
    directory = Path(registry["state_directory"])
    if directory.resolve() != campaign / "conditions" / registry["condition_fingerprint"]:
        raise ValueError("previous condition left the canonical campaign")
    result = {"registry": registry}
    for name in ("transition.json", "checkpoint.pt", "recheck_report.json"):
        path = directory / name
        result[name] = {"path": str(path), "sha256": _sha(path)}
    _validate_previous_boundary(result, state)
    return result


def _validate_previous_boundary(boundary: Mapping[str, Any], source: Mapping[str, Any]) -> None:
    registry = boundary["registry"]
    for name in ("transition.json", "checkpoint.pt", "recheck_report.json"):
        item = boundary[name]
        if _sha(item["path"]) != item["sha256"]:
            raise ValueError("previous condition evidence changed")
        if Path(item["path"]).resolve() != Path(registry["state_directory"]).resolve() / name:
            raise ValueError("previous condition evidence path changed")
    if _read(Path(boundary["transition.json"]["path"])) != registry:
        raise ValueError("previous condition registry differs from its immutable transition")
    report = _read(Path(boundary["recheck_report.json"]["path"]))
    if (
        report.get("status") not in {"complete", "failed", "incomplete"}
        or report.get("pending_probe") is not None
        or report.get("condition_fingerprint") != registry["condition_fingerprint"]
        or report.get("parameter_receipts", {}).get("unchanged") is not True
        or report.get("operational_halt") is not None
    ):
        raise ValueError("previous recheck is not a safe unchanged terminal boundary")
    import torch

    payload = torch.load(boundary["checkpoint.pt"]["path"], map_location="cpu", weights_only=False)
    if (
        payload.get("request_boundary_pending") is not False
        or digest(payload.get("hook_state", {}).get("targeted_recheck")) != digest(report)
        or digest(payload.get("hook_state", {}).get("expenditure_ledger")) != digest(source)
    ):
        raise ValueError("previous recheck checkpoint and ledger are not the same durable boundary")


def _validate_extension_source(source: Mapping[str, Any]) -> None:
    records = source.get("historical_accounting_exceptions", [])
    if len(records) != 1 or records[0].get("protocol") != EXCEPTION_PROTOCOL:
        raise ValueError("the successive exception requires its one preserved original exception")
    validate_historical_exception_receipt(
        source,
        records[0],
        condition_fingerprint=records[0]["condition_fingerprint"],
        plan_fingerprint=records[0]["plan_fingerprint"],
    )
    if (
        source.get("active_scope") is not None
        or source.get("limit_amendments")
        or len(source["entries"]) != 409
        or sum(entry["stage"] == "compatibility" for entry in source["entries"]) != 24
    ):
        raise ValueError("the extension requires the completed original 24-attempt accounting boundary")


def validate_limit_amendments(state: Mapping[str, Any]) -> None:
    """Bind the only permitted 24-to-48 change to its new immutable exception."""
    amendments = state.get("limit_amendments", [])
    if not amendments:
        return
    records = state.get("historical_accounting_exceptions", [])
    if len(amendments) != 1 or len(records) != 2 or records[-1].get("protocol") != EXTENSION_PROTOCOL:
        raise ValueError("limit extension has no exact successive exception")
    amendment, receipt = amendments[0], records[-1]
    source = _read(Path(receipt["source_snapshot_path"]))
    expected_limits = {**source["limits"], "compatibility_max_attempts": 48}
    expected = {
        "protocol": LIMIT_AMENDMENT_PROTOCOL,
        "exception_id": receipt["exception_id"],
        "source_ledger_sha256": receipt["source_ledger_sha256"],
        "authorization_sha256": receipt["authorization_sha256"],
        "before_limits": source["limits"],
        "after_limits": expected_limits,
        "after_entries": len(source["entries"]),
    }
    if (
        amendment != expected
        or state["limits"] != expected_limits
        or source["limits"]["compatibility_max_attempts"] != 24
    ):
        raise ValueError("cumulative limits differ from the exact audited 24-to-48 amendment")


def historical_exception_authorization_template(
    campaign_directory: str | Path,
    *,
    condition_fingerprint: str,
    plan_fingerprint: str,
    source_lineage: Mapping[str, Any],
    user_message_path: str | Path,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe the exact already-requested exception without granting it."""
    campaign = Path(campaign_directory).resolve()
    extended = plan is not None and recheck_limits(plan)["additional_physical_attempts"] == 24
    if extended:
        if (
            plan is None
            or plan.get("fingerprint") != plan_fingerprint
            or plan.get("condition_fingerprint") != condition_fingerprint
        ):
            raise ValueError("the successive exception must bind the exact frozen plan and condition")
        raw = (campaign / "expenditure_ledger.json").read_bytes()
        state = json.loads(raw)
        _validate_extension_source(state)
        previous = _previous_boundary(campaign, state)
        if (
            condition_fingerprint == previous["registry"]["condition_fingerprint"]
            or plan_fingerprint == previous["registry"]["plan_fingerprint"]
        ):
            raise ValueError("a successive exception requires a new condition and new frozen plan")
    else:
        state, raw = _source_state(campaign)
        _validate_historical_source(state)
    if not _fingerprint(condition_fingerprint) or not _fingerprint(plan_fingerprint):
        raise ValueError("the exception requires exact new-condition and frozen-plan fingerprints")
    entry = state["entries"][EXCEPTED_DISPATCH]
    result = {
        "protocol": EXTENSION_AUTHORIZATION_PROTOCOL if extended else AUTHORIZATION_PROTOCOL,
        "decision": "pending",
        "campaign_directory": str(campaign),
        "pre_exception_ledger_sha256": hashlib.sha256(raw).hexdigest(),
        "dispatch_id": EXCEPTED_DISPATCH,
        "generation_id": EXCEPTED_GENERATION,
        "request_sha256": EXCEPTED_REQUEST,
        "dispatch_receipt_sha256": digest(entry),
        "retained_reservation": entry["reserved"],
        "retained_unknowns": {
            "actual_charge": None,
            "input_tokens": None,
            "output_tokens": None,
            "reference_cost": None,
        },
        "reservation_is_verified_upper_bound": False,
        "uncertainty_disposition": "Explicitly accept unknown historical charge and native/reference usage for dispatch 400 while retaining its reservation solely for the fresh revised transition and frozen recheck; the hold is not a verified upper bound.",
        "settlement_evidence": {str(item["dispatch_id"]): digest(item) for item in state["accounting_reconciliations"]},
        "source_lineage": copy.deepcopy(dict(source_lineage)),
        "condition_fingerprint": condition_fingerprint,
        "plan_fingerprint": plan_fingerprint,
        "scope_id": "recheck:" + plan_fingerprint,
        "additional_physical_attempts": 6,
        "additional_budget_usd": 0.25,
        "cumulative_compatibility_attempts": 24,
        "cumulative_compatibility_budget_usd": 3.0,
        "historical_metadata_lookups_authorized": False,
        "pilot_authorized": False,
        "development_authorized": False,
        "test_authorized": False,
        "scope": "fresh_revised_transition_and_same_frozen_recheck_only",
        "user_message_path": str(Path(user_message_path).resolve()),
        "user_message_sha256": _sha(user_message_path),
        "reviewer": "",
        "rationale": "",
    }
    if extended:
        result.update(recheck_limits(plan or {}))
        result.update(
            previous_boundary=previous,
            previous_exception_id=state["historical_accounting_exceptions"][-1]["exception_id"],
            prior_limits=copy.deepcopy(state["limits"]),
            authorized_limits={**state["limits"], "compatibility_max_attempts": 48},
        )
    return result


def _validate_authorization(authorization: Mapping[str, Any], source: Mapping[str, Any], raw: bytes) -> None:
    campaign = Path(authorization["campaign_directory"])
    extended = authorization.get("protocol") == EXTENSION_AUTHORIZATION_PROTOCOL
    if (
        authorization.get("protocol") not in {AUTHORIZATION_PROTOCOL, EXTENSION_AUTHORIZATION_PROTOCOL}
        or authorization.get("decision") != "approve_retained_reservation_for_revised_recheck_only"
    ):
        raise ValueError("an explicit retained-reservation historical exception authorization is required")
    if not _fingerprint(authorization.get("condition_fingerprint")) or not _fingerprint(
        authorization.get("plan_fingerprint")
    ):
        raise ValueError("the exception requires exact condition and plan fingerprints")
    if _sha(authorization["user_message_path"]) != authorization.get("user_message_sha256"):
        raise ValueError("historical exception user-instruction evidence changed")
    for name in ("reviewer", "rationale"):
        if not isinstance(authorization.get(name), str) or not authorization[name].strip():
            raise ValueError("historical exception requires its explicit reviewer and rationale")
    expected = {
        "pre_exception_ledger_sha256": hashlib.sha256(raw).hexdigest(),
        "dispatch_id": EXCEPTED_DISPATCH,
        "generation_id": EXCEPTED_GENERATION,
        "request_sha256": EXCEPTED_REQUEST,
        "dispatch_receipt_sha256": digest(source["entries"][EXCEPTED_DISPATCH]),
        "retained_reservation": source["entries"][EXCEPTED_DISPATCH]["reserved"],
        "retained_unknowns": {
            "actual_charge": None,
            "input_tokens": None,
            "output_tokens": None,
            "reference_cost": None,
        },
        "reservation_is_verified_upper_bound": False,
        "uncertainty_disposition": "Explicitly accept unknown historical charge and native/reference usage for dispatch 400 while retaining its reservation solely for the fresh revised transition and frozen recheck; the hold is not a verified upper bound.",
        "settlement_evidence": {
            str(item["dispatch_id"]): digest(item) for item in source["accounting_reconciliations"]
        },
        "scope_id": "recheck:" + authorization["plan_fingerprint"],
        "additional_physical_attempts": 6,
        "additional_budget_usd": 0.25,
        "cumulative_compatibility_attempts": 24,
        "cumulative_compatibility_budget_usd": 3.0,
        "historical_metadata_lookups_authorized": False,
        "pilot_authorized": False,
        "development_authorized": False,
        "test_authorized": False,
        "scope": "fresh_revised_transition_and_same_frozen_recheck_only",
    }
    if extended:
        _validate_extension_source(source)
        _validate_previous_boundary(authorization["previous_boundary"], source)
        previous = source["historical_accounting_exceptions"][-1]
        registry = authorization["previous_boundary"]["registry"]
        if (
            registry.get("condition_fingerprint") != previous["condition_fingerprint"]
            or registry.get("plan_fingerprint") != previous["plan_fingerprint"]
        ):
            raise ValueError("previous boundary differs from its preserved exception")
        if (
            authorization["condition_fingerprint"] == previous["condition_fingerprint"]
            or authorization["plan_fingerprint"] == previous["plan_fingerprint"]
        ):
            raise ValueError("the old exception cannot authorize the new condition or plan")
        expected.update(
            additional_physical_attempts=24,
            additional_budget_usd=0.5,
            cumulative_compatibility_attempts=48,
            previous_exception_id=previous["exception_id"],
            prior_limits=source["limits"],
            authorized_limits={**source["limits"], "compatibility_max_attempts": 48},
        )
    if any(
        authorization.get(key) != value or type(authorization.get(key)) is not type(value)
        for key, value in expected.items()
    ):
        raise ValueError("historical exception authority differs from the exact retained receipt or bounded scope")
    lineage = authorization.get("source_lineage", {})
    if lineage.get("protocol") != "revised_condition_lineage_v1":
        raise ValueError("the exception requires its original revised-condition lineage")
    original = Path(lineage.get("source_campaign_directory", ""))
    if not original.is_absolute():
        # Registered manifests commonly retain a path relative to this example.
        original = Path(__file__).resolve().parent / original
    if original.resolve() != campaign.resolve():
        raise ValueError("the historical exception cannot move its campaign anchor")
    for filename, key in (
        ("checkpoint.pt", "source_checkpoint_sha256"),
        ("experiment_manifest.json", "source_manifest_sha256"),
    ):
        if _sha(campaign / filename) != lineage.get(key):
            raise ValueError("the historical checkpoint or manifest lineage changed")
    if lineage.get("source_ledger_sha256") != source.get("reconciliation_audit", {}).get("source_ledger_sha256"):
        raise ValueError("the exception changed the original pre-reconciliation ledger lineage")


def _validate_receipt_history(
    state: Mapping[str, Any], receipt: Mapping[str, Any], *, require_registered: bool
) -> dict[str, Any]:
    extended = receipt.get("protocol") == EXTENSION_PROTOCOL
    if receipt.get("protocol") not in {EXCEPTION_PROTOCOL, EXTENSION_PROTOCOL}:
        raise ValueError("unsupported historical exception receipt")
    source_path = Path(receipt["source_snapshot_path"])
    raw = source_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt.get("source_ledger_sha256"):
        raise ValueError("the immutable pre-exception ledger changed")
    source = _read(source_path)
    if extended:
        _validate_extension_source(source)
    else:
        _validate_historical_source(source)
    validate_accounting_history(state)
    authorization = receipt["authorization"]
    _validate_authorization(authorization, source, raw)
    if extended != (authorization["protocol"] == EXTENSION_AUTHORIZATION_PROTOCOL):
        raise ValueError("exception receipt and authorization versions disagree")
    if receipt.get("exception_id") != digest(authorization) or receipt.get("authorization_sha256") != digest(
        authorization
    ):
        raise ValueError("the historical exception authorization identity changed")
    expected = {
        "entry_count": len(source["entries"]),
        "prior_halt": source["halted"],
        "halt_event_count": len(source.get("halt_events", [])),
        "dispatch_id": EXCEPTED_DISPATCH,
        "condition_fingerprint": authorization["condition_fingerprint"],
        "plan_fingerprint": authorization["plan_fingerprint"],
        "scope_id": authorization["scope_id"],
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("historical exception receipt differs from its authorized source")
    if state["entries"][: len(source["entries"])] != source["entries"]:
        raise ValueError("the original physical receipts changed under the exception")
    for key in ("protocol", "reconciliation_audit", "accounting_lookup_intents"):
        if state.get(key) != source.get(key):
            raise ValueError("the historical exception changed accounting history or cumulative limits")
    if state["limits"] != source["limits"]:
        validate_limit_amendments(state)
        if not state.get("limit_amendments"):
            raise ValueError("cumulative limits changed without an audited amendment")
    original_settlements = source.get("accounting_reconciliations", [])
    later_settlements = state.get("accounting_reconciliations", [])
    if later_settlements[: len(original_settlements)] != original_settlements or any(
        item["dispatch_id"] != EXCEPTED_DISPATCH for item in later_settlements[len(original_settlements) :]
    ):
        raise ValueError("the historical exception cannot rewrite its original settlements")
    original_releases = source.get("halt_releases", [])
    releases = state.get("halt_releases", [])
    if releases[: len(original_releases)] != original_releases:
        raise ValueError("the historical exception cannot rewrite accounting release history")
    if state.get("halted") != source["halted"]:
        newer_events = state.get("halt_events", [])[receipt["halt_event_count"] :]
        later_halt = state.get("halted")
        valid_later_halt = any(
            {name: event[name] for name in ("reason", "stage", "after_entries")} == later_halt for event in newer_events
        )
        if (
            _effective_charge(state, state["entries"][EXCEPTED_DISPATCH]) is None
            or not any(item["prior_halt"] == source["halted"] for item in releases)
            or (newer_events and not valid_later_halt)
            or (not newer_events and later_halt is not None)
        ):
            raise ValueError("the retained halt can change only after authoritative settlement and audited release")
    if state.get("halt_events", [])[: receipt["halt_event_count"]] != source.get("halt_events", []):
        raise ValueError("historical halt event history changed")
    if require_registered:
        records = state.get("historical_accounting_exceptions", [])
        prior = source.get("historical_accounting_exceptions", [])
        if records[: len(prior) + 1] != [*prior, dict(receipt)]:
            raise ValueError("historical exception is not its exact append-only ledger record")
    return source


def validate_exception_history(state: Mapping[str, Any]) -> None:
    records = state.get("historical_accounting_exceptions", [])
    if not 1 <= len(records) <= 2:
        raise ValueError("only the original and explicitly authorized successive exceptions may be recorded")
    if records[0].get("protocol") != EXCEPTION_PROTOCOL or (
        len(records) == 2 and records[1].get("protocol") != EXTENSION_PROTOCOL
    ):
        raise ValueError("historical exception version sequence changed")
    for receipt in records:
        _validate_receipt_history(state, receipt, require_registered=True)
    validate_limit_amendments(state)


def validate_historical_exception_receipt(
    state: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    condition_fingerprint: str,
    plan_fingerprint: str,
    require_registered: bool = True,
    allow_owned_inflight: bool = False,
    owned_after: int | None = None,
) -> None:
    """Validate eligibility without activating dispatch permission or clearing a halt."""
    source = _validate_receipt_history(state, receipt, require_registered=require_registered)
    if require_registered and state["historical_accounting_exceptions"][-1] != receipt:
        raise ValueError("a superseded historical exception cannot authorize another execution")
    if receipt["condition_fingerprint"] != condition_fingerprint or receipt["plan_fingerprint"] != plan_fingerprint:
        raise ValueError("historical exception does not authorize this condition or frozen probe plan")
    if len(state.get("halt_events", [])) != receipt["halt_event_count"]:
        raise ValueError("a new fatal halt event cannot be covered by the historical exception")
    if _effective_charge(state, state["entries"][EXCEPTED_DISPATCH]) is not None:
        raise ValueError("the historical charge is now known; use the normal audited settlement and halt disposition")
    for entry in state["entries"][len(source["entries"]) :]:
        if (
            entry.get("scope_id") != receipt["scope_id"]
            or entry.get("historical_exception_id") != receipt["exception_id"]
            or entry["stage"] != "compatibility"
        ):
            raise ValueError("the exception cannot cover a request outside its frozen recheck scope")
        if _effective_charge(state, entry) is None:
            if (
                allow_owned_inflight
                and owned_after is not None
                and entry["dispatch_id"] >= owned_after
                and entry["status"] in {"reserved", "dispatched"}
            ):
                continue
            raise ValueError("a new unresolved request cannot be covered by the historical exception")


def apply_historical_exception(
    campaign_directory: str | Path,
    audit_directory: str | Path,
    *,
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the existing user authorization under the canonical lock, with no I/O to providers."""
    campaign, audit = Path(campaign_directory).resolve(), Path(audit_directory).resolve()
    target = Path(__file__).resolve().parent
    if not campaign.is_relative_to(target) or not audit.is_relative_to(target):
        raise ValueError("historical exception artifacts must remain within this example")
    if authorization.get("campaign_directory") != str(campaign):
        raise ValueError("historical exception authority belongs to a different campaign")
    extended = authorization.get("protocol") == EXTENSION_AUTHORIZATION_PROTOCOL
    current = _read(campaign / "expenditure_ledger.json")
    prior_match = next(
        (
            item
            for item in current.get("historical_accounting_exceptions", [])
            if item["authorization"] == dict(authorization)
        ),
        None,
    )
    if prior_match is not None:
        raw = Path(prior_match["source_snapshot_path"]).read_bytes()
        source = json.loads(raw)
    elif extended:
        raw = (campaign / "expenditure_ledger.json").read_bytes()
        source = json.loads(raw)
    else:
        source, raw = _source_state(campaign)
    if extended:
        _validate_extension_source(source)
    else:
        _validate_historical_source(source)
    _validate_authorization(authorization, source, raw)
    with (campaign / ".campaign.lock").open("a+b") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another process holds the canonical campaign lock") from None
        try:
            current = _read(campaign / "expenditure_ledger.json")
            existing = current.get("historical_accounting_exceptions", [])
            matching = next((item for item in existing if item["authorization"] == dict(authorization)), None)
            if matching is not None:
                receipt = matching
                if (
                    receipt["authorization"] != dict(authorization)
                    or Path(receipt["source_snapshot_path"]).parent != audit
                ):
                    raise ValueError("the historical exception cannot be reauthorized or moved")
                validate_historical_exception_receipt(
                    current,
                    receipt,
                    condition_fingerprint=receipt["condition_fingerprint"],
                    plan_fingerprint=receipt["plan_fingerprint"],
                )
                if _read(audit / "historical_exception_receipt.json") != receipt:
                    raise ValueError("the immutable historical exception receipt changed")
                return copy.deepcopy(receipt)
            if existing and not extended:
                raise ValueError("the historical exception cannot be reauthorized or moved")
            if (campaign / "expenditure_ledger.json").read_bytes() != raw:
                raise ValueError("campaign accounting changed before the historical exception lock was acquired")
            audit.mkdir(parents=True, exist_ok=True)
            before = audit / "before_ledger.json"
            _snapshot_bytes(before, raw)
            receipt = {
                "protocol": EXTENSION_PROTOCOL if extended else EXCEPTION_PROTOCOL,
                "exception_id": digest(authorization),
                "authorization": copy.deepcopy(dict(authorization)),
                "authorization_sha256": digest(authorization),
                "source_snapshot_path": str(before),
                "source_ledger_sha256": hashlib.sha256(raw).hexdigest(),
                "entry_count": len(source["entries"]),
                "prior_halt": copy.deepcopy(source["halted"]),
                "halt_event_count": len(source.get("halt_events", [])),
                "dispatch_id": EXCEPTED_DISPATCH,
                "condition_fingerprint": authorization["condition_fingerprint"],
                "plan_fingerprint": authorization["plan_fingerprint"],
                "scope_id": authorization["scope_id"],
            }
            _write(audit / "historical_exception_receipt.json", receipt, immutable=True)
            ledger = ExpenditureLedger(ResourceConfig(**current["limits"]))
            ledger.load_state_dict(current)
            ledger.register_historical_exception(receipt)
            after = ledger.state_dict()
            _write(audit / "after_ledger.json", after, immutable=True)
            _write(
                audit / "application_audit.json",
                {
                    "exception_id": receipt["exception_id"],
                    "before_ledger_sha256": _sha(before),
                    "after_ledger_sha256": _sha(audit / "after_ledger.json"),
                    "ledger_entries_changed": False,
                    "historical_halt_cleared": False,
                    "unknown_charge_settled": False,
                    "new_generation_attempts": 0,
                    "historical_metadata_attempts": 0,
                    "compatibility_attempt_limit_before": source["limits"]["compatibility_max_attempts"],
                    "compatibility_attempt_limit_after": after["limits"]["compatibility_max_attempts"],
                },
                immutable=True,
            )
            _write(campaign / "expenditure_ledger.json", after)
            return receipt
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
