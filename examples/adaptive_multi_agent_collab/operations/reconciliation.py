"""Bounded, durable accounting recovery; never a generation retry or answer repair."""

from __future__ import annotations

from ..paths import PACKAGE_ROOT
from ..infrastructure.storage import load_ledger

import asyncio
import copy
import fcntl
import hashlib
import json
import math
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..config import ResourceConfig
from ..providers.gateway import METADATA_LOOKUP_TIMEOUT, OpenRouterBinding, generation_usage
from ..providers.identity import validate_model_identity_evidence
from ..providers.resources import ExpenditureLedger

RECONCILIATION_PROTOCOL = "bounded_generation_accounting_audit_1"
ACCOUNTING_HALT = "generation charge unresolved; retain reservation and review before dispatch"


def _bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def digest(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _write(path: Path, value: Any, *, immutable: bool = False) -> None:
    content = _bytes(value)
    if immutable:
        try:
            with path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError("immutable accounting evidence already exists with different contents") from None
    else:
        temporary = path.with_name(path.name + ".pending")
        with temporary.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read(path: Path) -> dict[str, Any]:
    value = load_ledger(path)
    if not isinstance(value, dict):
        raise ValueError("accounting artifact must be an object")
    return value


def _snapshot_bytes(path: Path, content: bytes) -> None:
    """Preserve exact bytes, refusing to replace an earlier immutable snapshot."""
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError("immutable accounting snapshot already exists with different contents") from None
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def generation_identity(entry: Mapping[str, Any]) -> str:
    usage = entry.get("usage", {})
    generation_id = usage.get("generation_id") or usage.get("header_generation_id")
    if not isinstance(generation_id, str) or not generation_id.startswith("gen-"):
        raise ValueError("historical dispatch has no recorded generation identity")
    if usage.get("header_generation_id") not in (None, generation_id):
        raise ValueError("historical generation identities disagree")
    return generation_id


def validate_lookup_authorization(
    authorization: Mapping[str, Any],
    campaign: Path,
    source_bytes: bytes,
    ids: Sequence[int],
) -> None:
    """Bind the user's finite metadata-only approval to exact historical work."""
    source = json.loads(source_bytes)
    required = {
        "protocol": "historical_accounting_lookup_authorization_v1",
        "decision": "approve_bounded_accounting_lookup_and_evidence_backed_settlement",
        "campaign_directory": str(campaign.resolve()),
        "original_ledger_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "maximum_additional_http_attempts": 9,
        "maximum_additional_http_attempts_per_generation": 3,
        "stop_on_credential_rejection": True,
        "new_generation_authorized_by_this_artifact": False,
        "pilot_authorized": False,
        "development_authorized": False,
        "dispatch_generation_ids": {str(index): generation_identity(source["entries"][index]) for index in ids},
    }
    if any(
        authorization.get(key) != value or type(authorization.get(key)) is not type(value)
        for key, value in required.items()
    ):
        raise ValueError("accounting authorization does not match the exact bounded historical lookup scope")
    transcript = authorization.get("source_transcription_path")
    if not isinstance(transcript, str) or hashlib.sha256(
        Path(transcript).read_bytes()
    ).hexdigest() != authorization.get("source_transcription_sha256"):
        raise ValueError("accounting authorization has no matching user-instruction provenance")


def billing_mode_evidence(entries: Sequence[Mapping[str, Any]], index: int) -> dict[str, Any]:
    """Retain observed billing evidence from earlier successful identical bindings."""
    binding = entries[index].get("usage", {}).get("binding", {})
    fields = ("model", "provider_endpoint", "revision_binding", "served_model", "served_provider", "immutable_revision")
    observations = []
    for item in entries[:index]:
        usage = item.get("usage", {})
        candidate = usage.get("binding", {})
        if (
            item.get("status") == "completed"
            and item.get("charged") is not None
            and usage.get("response_identity_verified") is True
            and isinstance(usage.get("is_byok"), bool)
            and all(candidate.get(key) == binding.get(key) for key in fields)
        ):
            observations.append(item)
    modes = {item["usage"]["is_byok"] for item in observations}
    if len(modes) > 1:
        raise ValueError("historical native billing-mode evidence conflicts for the same serving binding")
    if not observations:
        return {"is_byok": None, "dispatch_id": None, "original_entry_sha256": None}
    witness = observations[0]
    return {
        "is_byok": witness["usage"]["is_byok"],
        "dispatch_id": witness["dispatch_id"],
        "original_entry_sha256": digest(witness),
    }


def validate_generation_receipt(
    entry: Mapping[str, Any],
    payload: Mapping[str, Any],
    billing_evidence: Mapping[str, Any] | None = None,
    model_identity_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate native debit evidence against the original frozen request."""
    old_usage = entry.get("usage", {})
    binding = OpenRouterBinding.from_dict(old_usage.get("binding", {}))
    binding.validate()
    identity = generation_identity(entry)
    if payload.get("id") != identity:
        raise ValueError("accounting metadata returned a different generation identity")
    expected_model = binding.served_model or binding.model
    if entry["model"] != binding.model:
        raise ValueError("accounting metadata returned a different served model")
    if payload.get("model") != expected_model:
        validate_model_identity_evidence(binding, payload.get("model"), model_identity_evidence)
    if not binding.served_provider or payload.get("provider_name") != binding.served_provider:
        raise ValueError("accounting metadata returned a different served provider")
    for old, new in (("served_model", "model"), ("served_provider", "provider_name")):
        allowed = (None, payload[new], expected_model) if old == "served_model" else (None, payload[new])
        if old_usage.get(old) not in allowed:
            raise ValueError("accounting metadata conflicts with the original serving identity")
    if payload.get("service_tier") not in (None, "default", "standard"):
        raise ValueError("accounting metadata reports an unapproved service tier")
    usage = generation_usage(payload)
    for name in ("input_tokens", "output_tokens", "cache_tokens", "reasoning_tokens"):
        value = usage.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("native accounting metadata is missing or invalid: " + name)
    charge = usage.get("total_cost")
    if isinstance(charge, bool) or not isinstance(charge, (int, float)) or not math.isfinite(charge) or charge < 0:
        raise ValueError("actual gateway account debit is missing or invalid")
    if not isinstance(usage.get("is_byok"), bool):
        raise ValueError("generation metadata lacks the native billing-mode field")
    expected_billing = (billing_evidence or {}).get("is_byok")
    if expected_billing is not None and usage["is_byok"] != expected_billing:
        raise ValueError("recovered native billing mode conflicts with the preserved same-binding receipts")
    if usage["is_byok"] is True:
        raise ValueError("BYOK gateway debit does not establish the full external provider account debit")
    if charge > entry["reserved"] + 1e-12:
        raise ValueError("actual account debit exceeds the original reservation")
    if usage["input_tokens"] > entry["reserved_input_tokens"] or usage["output_tokens"] > entry["output_cap"]:
        raise ValueError("native token usage exceeds the original request reservation")
    if entry.get("input_count_method") == "estimated" and usage["input_tokens"] > entry["input_count"]:
        raise ValueError("native input exceeds the original conservative bound")
    if usage["cache_tokens"] > usage["input_tokens"] or usage["reasoning_tokens"] != 0:
        raise ValueError("native cache or non-thinking accounting is inconsistent")
    for name in ("input_tokens", "output_tokens", "cache_tokens", "reasoning_tokens", "total_cost", "is_byok"):
        if old_usage.get(name) is not None and old_usage[name] != usage[name]:
            raise ValueError("recovered accounting conflicts with an original known coordinate")
    tariff = entry["billing_tariff"]
    usage.update(
        generation_id=identity,
        served_model=payload["model"],
        served_provider=payload["provider_name"],
        response_identity_verified=True,
        finish_reason=payload.get("finish_reason"),
        native_finish_reason=payload.get("native_finish_reason"),
        system_fingerprint=payload.get("system_fingerprint"),
        reference_cost=(
            usage["input_tokens"] * tariff["reference_input_price"]
            + usage["output_tokens"] * tariff["reference_output_price"]
        ),
    )
    return usage


def validate_accounting_history(state: Mapping[str, Any]) -> None:
    """Validate append-only overlays and narrowly scoped halt-release provenance."""
    entries = state["entries"]
    records = state.get("accounting_reconciliations", [])
    registered = state.get("reconciliation_audit", {})
    intents = state.get("accounting_lookup_intents", [])
    if len(intents) > 9:
        raise ValueError("accounting lookup attempt ceiling exceeded")
    for number, intent in enumerate(intents, 1):
        index = intent.get("dispatch_id")
        if (
            intent.get("sequence") != number
            or index not in registered.get("dispatch_ids", [])
            or intent.get("generation_id") != generation_identity(entries[index])
            or intent.get("timeout_seconds") != METADATA_LOOKUP_TIMEOUT
            or intent.get("status") != "intent_reserved"
            or intent.get("generation_attempt") != sum(item["dispatch_id"] == index for item in intents[:number])
            or intent["generation_attempt"] > 3
        ):
            raise ValueError("corrupt canonical metadata lookup intent history")
    seen: set[int] = set()
    for record in records:
        index = record["dispatch_id"]
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(entries) or index in seen:
            raise ValueError("invalid or duplicate reconciled dispatch")
        seen.add(index)
        entry = entries[index]
        if entry["charged"] is not None or digest(entry) != record.get("original_entry_sha256"):
            raise ValueError("reconciliation rewrites an original settled receipt")
        if record.get("protocol") != RECONCILIATION_PROTOCOL or not record.get("authorization_sha256"):
            raise ValueError("missing accounting reconciliation authorization provenance")
        attempt = record.get("lookup_attempt")
        if (
            isinstance(attempt, bool)
            or not isinstance(attempt, int)
            or not 1 <= attempt <= len(intents)
            or intents[attempt - 1]["dispatch_id"] != index
        ):
            raise ValueError("settlement has no corresponding durable metadata lookup intent")
        if any(record.get(name) != registered.get(name) for name in ("source_ledger_sha256", "authorization_sha256")):
            raise ValueError("settlement evidence disagrees with the registered accounting audit")
        result = record["lookup_result"]
        if result.get("http_status") != 200 or digest(result) != record.get("lookup_result_sha256"):
            raise ValueError("invalid authenticated metadata lookup evidence")
        expected_billing = billing_mode_evidence(entries, index)
        if record.get("billing_mode_evidence") != expected_billing:
            raise ValueError("reconciliation billing provenance differs from retained native receipts")
        if (
            validate_generation_receipt(entry, result["data"], expected_billing, record.get("model_identity_evidence"))
            != record["usage"]
        ):
            raise ValueError("reconciliation usage differs from native metadata evidence")
        if record.get("cached_adjudication") is not None:
            reference = record["cached_adjudication"]
            path = Path(reference["path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != reference["sha256"]:
                raise ValueError("cached accounting adjudication changed")
            adjudication = _read(path)
            for artifact in ("before_ledger", "before_unresolved_receipt"):
                if (
                    hashlib.sha256(Path(adjudication[artifact + "_path"]).read_bytes()).hexdigest()
                    != adjudication[artifact + "_sha256"]
                ):
                    raise ValueError("pre-adjudication accounting evidence changed")
            if (
                adjudication.get("additional_http_attempts") != 0
                or adjudication.get("source_ledger_sha256") != record["source_ledger_sha256"]
            ):
                raise ValueError("cached settlement does not match its zero-lookup audit")
    for release in state.get("halt_releases", []):
        prior = release.get("prior_halt", {})
        ids = release.get("dispatch_ids")
        if prior.get("reason") != ACCOUNTING_HALT or not release.get("disposition"):
            raise ValueError("only a dispositioned unresolved-accounting halt may be released")
        boundary = release.get("entry_count")
        if isinstance(boundary, bool) or not isinstance(boundary, int) or not 0 <= boundary <= len(entries):
            raise ValueError("invalid halt-release boundary")
        unresolved = {item["dispatch_id"] for item in entries[:boundary] if item["charged"] is None}
        if not isinstance(ids, list) or set(ids) != unresolved or not unresolved.issubset(seen):
            raise ValueError("all original accounting blockers must be reconciled before halt release")
        matching = [record for record in records if record["dispatch_id"] in unresolved]
        if not matching or any(
            record["source_ledger_sha256"] != release.get("source_ledger_sha256")
            or record["authorization_sha256"] != release.get("authorization_sha256")
            for record in matching
        ):
            raise ValueError("halt-release provenance does not match its accounting evidence")


def validate_reconciliation_receipt(state: Mapping[str, Any], receipt: Mapping[str, Any]) -> None:
    """Verify immutable evidence and allow only an append-only campaign suffix."""
    if receipt.get("protocol") != RECONCILIATION_PROTOCOL or receipt.get("fully_resolved") is not True:
        raise ValueError("the accounting reconciliation is not fully resolved")
    source_path = Path(receipt["source_snapshot_path"])
    reconciled_path = Path(receipt["reconciled_snapshot_path"])
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != receipt["source_ledger_sha256"]:
        raise ValueError("the original accounting snapshot changed")
    if hashlib.sha256(reconciled_path.read_bytes()).hexdigest() != receipt["reconciled_ledger_sha256"]:
        raise ValueError("the reconciled accounting snapshot changed")
    source, baseline = _read(source_path), _read(reconciled_path)
    authorization = receipt["authorization"]
    validate_lookup_authorization(
        authorization, Path(authorization["campaign_directory"]), source_path.read_bytes(), receipt["dispatch_ids"]
    )
    if receipt["authorization_sha256"] != digest(authorization):
        raise ValueError("reconciliation authorization digest changed")
    if source["entries"] != baseline["entries"] or receipt["original_entry_count"] != len(source["entries"]):
        raise ValueError("reconciliation changed historical physical receipts")
    validate_accounting_history(baseline)
    if baseline["halted"] is not None or receipt["prior_halt"] != source["halted"]:
        raise ValueError("accounting halt disposition does not match original evidence")
    release = receipt["release_halt_with_provenance"]
    if release not in baseline.get("halt_releases", []) or release["prior_halt"] != source["halted"]:
        raise ValueError("accounting halt has no matching authenticated release")
    if any(release[name] != receipt[name] for name in ("source_ledger_sha256", "authorization_sha256", "dispatch_ids")):
        raise ValueError("accounting release differs from the authorized audit")
    audit_directory = Path(baseline["reconciliation_audit"]["audit_directory"])
    for record in baseline.get("accounting_reconciliations", []):
        evidence = _read(audit_directory / f"lookup-{record['lookup_attempt']:03d}.json")
        if (
            evidence.get("result") != record["lookup_result"]
            or evidence.get("dispatch_id") != record["dispatch_id"]
            or evidence.get("generation_id") != record["generation_id"]
        ):
            raise ValueError("the immutable metadata lookup evidence changed")
    for name in ("entries", "accounting_reconciliations", "halt_releases", "accounting_lookup_intents"):
        if state.get(name, [])[: len(baseline.get(name, []))] != baseline.get(name, []):
            raise ValueError("campaign rewrites the reconciled accounting prefix")
    for name in ("protocol", "limits"):
        if state[name] != source[name]:
            raise ValueError("reconciliation changed cumulative campaign limits")
    if state.get("reconciliation_audit") != baseline.get("reconciliation_audit"):
        raise ValueError("the registered accounting audit identity changed")
    validate_accounting_history(state)


async def reconcile_campaign(
    campaign_directory: str | Path,
    audit_directory: str | Path,
    *,
    lookup: Callable[[str], Awaitable[Mapping[str, Any]]],
    authorization: Mapping[str, Any],
    dispatch_ids: Sequence[int],
    disposition: str,
    model_identity_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    cached_only: bool = False,
) -> dict[str, Any]:
    """Audit at most nine extra GETs, under the canonical campaign process lock.

    Callers inject the documented one-GET helper. Intent persistence precedes
    calling it; interrupted intents consume the limit and are never replayed.
    """
    campaign, audit = Path(campaign_directory).resolve(), Path(audit_directory).resolve()
    target = PACKAGE_ROOT
    if not campaign.is_relative_to(target) or not audit.is_relative_to(target):
        raise ValueError("accounting artifacts must stay within this example directory")
    ids = list(dispatch_ids)
    if not ids or len(ids) > 3 or len(set(ids)) != len(ids) or not disposition.strip() or not authorization:
        raise ValueError("a bounded explicit accounting audit authorization is required")
    if any(isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in ids):
        raise ValueError("invalid accounting dispatch identities")
    initial = _read(campaign / "expenditure_ledger.json")
    registered = initial.get("reconciliation_audit")
    if registered is not None and registered.get("audit_directory") != str(audit):
        raise ValueError("the canonical campaign already has a registered accounting audit; lookup limits cannot reset")
    if registered is not None and not (audit / "source_ledger.json").is_file():
        raise ValueError("registered accounting audit evidence is missing; lookup limits cannot reset")
    pre_path = (
        audit / "source_ledger.json" if initial.get("reconciliation_audit") else campaign / "expenditure_ledger.json"
    )
    validate_lookup_authorization(authorization, campaign, pre_path.read_bytes(), ids)
    audit.mkdir(parents=True, exist_ok=True)
    with (campaign / ".campaign.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another process holds the canonical campaign lock") from None
        try:
            return await _reconcile_locked(
                campaign,
                audit,
                lookup,
                authorization,
                ids,
                disposition,
                model_identity_evidence=model_identity_evidence,
                cached_only=cached_only,
            )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


async def _reconcile_locked(
    campaign: Path,
    audit: Path,
    lookup: Callable[[str], Awaitable[Mapping[str, Any]]],
    authorization: Mapping[str, Any],
    ids: list[int],
    disposition: str,
    *,
    model_identity_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    cached_only: bool = False,
) -> dict[str, Any]:
    ledger_path, journal_path = campaign / "expenditure_ledger.json", audit / "lookup_journal.json"
    current = _read(ledger_path)
    registered = current.get("reconciliation_audit")
    if registered is not None and registered.get("audit_directory") != str(audit):
        raise ValueError("the canonical campaign already has a registered accounting audit; lookup limits cannot reset")
    snapshot_path = audit / "source_ledger.json"
    if registered is not None and (not snapshot_path.is_file() or not journal_path.is_file()):
        raise ValueError("registered accounting audit evidence is missing; lookup limits cannot reset")
    if not snapshot_path.exists():
        # Preserve the exact pre-operation bytes, including historical formatting.
        _snapshot_bytes(snapshot_path, ledger_path.read_bytes())
    source = _read(snapshot_path)
    validate_lookup_authorization(authorization, campaign, snapshot_path.read_bytes(), ids)
    source_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    if source["entries"] != current["entries"]:
        raise ValueError("historical dispatches changed during the accounting audit")
    if any(source[name] != current[name] for name in ("protocol", "limits")):
        raise ValueError("cumulative accounting configuration changed during the audit")
    if source.get("halted", {}).get("reason") != ACCOUNTING_HALT:
        raise ValueError("this audit can only resolve an unknown-accounting halt")
    if {item["dispatch_id"] for item in source["entries"] if item["charged"] is None} != set(ids):
        raise ValueError("the authorized dispatches do not match all original accounting blockers")
    authorization_hash = digest(authorization)
    identity = {
        "protocol": RECONCILIATION_PROTOCOL,
        "source_ledger_sha256": source_hash,
        "source_snapshot_path": str(snapshot_path),
        "dispatch_ids": ids,
        "authorization": dict(authorization),
        "authorization_sha256": authorization_hash,
        "disposition": disposition,
    }
    if journal_path.exists():
        journal = _read(journal_path)
        if any(journal.get(key) != value for key, value in identity.items()):
            raise ValueError("accounting audit authorization or immutable baseline changed")
    else:
        journal = {**identity, "attempts": [], "credential_rejected": False}
        _write(journal_path, journal)
    # The canonical ledger independently retains every pre-GET intent. A model
    # checkpoint or an older lookup-journal copy cannot replenish this budget.
    canonical_intents = current.get("accounting_lookup_intents", [])
    if len(journal["attempts"]) > len(canonical_intents):
        raise ValueError("metadata lookup journal is ahead of its canonical durable intent history")
    for number, canonical in enumerate(canonical_intents):
        if number < len(journal["attempts"]):
            if any(
                journal["attempts"][number].get(key) != value for key, value in canonical.items() if key != "status"
            ):
                raise ValueError("metadata lookup journal conflicts with its canonical durable intent")
        else:
            journal["attempts"].append(copy.deepcopy(canonical))
    if len(journal["attempts"]) > 9 or any(
        sum(item["dispatch_id"] == index for item in journal["attempts"]) > 3 for index in ids
    ):
        raise ValueError("corrupt accounting lookup attempt budget")
    for number, item in enumerate(journal["attempts"], 1):
        if (
            item.get("sequence") != number
            or item.get("dispatch_id") not in ids
            or item.get("generation_id") != generation_identity(source["entries"][item["dispatch_id"]])
            or item.get("timeout_seconds") != METADATA_LOOKUP_TIMEOUT
        ):
            raise ValueError("corrupt durable metadata lookup identity")
        saved_path = audit / f"lookup-{number:03d}.json"
        if saved_path.exists():
            saved = _read(saved_path)
            if any(saved.get(key) != item.get(key) for key in item if key not in ("status", "result")):
                raise ValueError("immutable metadata receipt conflicts with its reserved intent")
            if "result" in item and item != saved:
                raise ValueError("durable metadata receipt changed")
            item.update(saved)
    if any(item.get("result", {}).get("http_status") in (401, 403) for item in journal["attempts"]):
        journal["credential_rejected"] = True
    _write(journal_path, journal)
    ledger = ExpenditureLedger(ResourceConfig(**current["limits"]))
    ledger.load_state_dict(current)
    ledger.on_change = lambda: _write(ledger_path, ledger.state_dict())
    adjudication = None
    if cached_only:
        evidence_hash = digest(model_identity_evidence or {})
        adjudication_directory = audit / ("cached-adjudication-" + evidence_hash)
        adjudication_path = adjudication_directory / "adjudication.json"
        if adjudication_path.is_file():
            adjudication = _read(adjudication_path)
            if adjudication.get("model_identity_evidence") != model_identity_evidence:
                raise ValueError("cached settlement interpretation evidence changed")
        else:
            adjudication_directory.mkdir(exist_ok=True)
            before_ledger = adjudication_directory / "before_ledger.json"
            before_receipt = adjudication_directory / "before_unresolved_receipt.json"
            _snapshot_bytes(before_ledger, ledger_path.read_bytes())
            _snapshot_bytes(before_receipt, (audit / "unresolved_receipt.json").read_bytes())
            adjudication = {
                "protocol": "cached_accounting_identity_adjudication_1",
                "source_ledger_sha256": source_hash,
                "authorization_sha256": authorization_hash,
                "before_ledger_path": str(before_ledger),
                "before_ledger_sha256": hashlib.sha256(before_ledger.read_bytes()).hexdigest(),
                "before_unresolved_receipt_path": str(before_receipt),
                "before_unresolved_receipt_sha256": hashlib.sha256(before_receipt.read_bytes()).hexdigest(),
                "model_identity_evidence": copy.deepcopy(model_identity_evidence),
                "additional_http_attempts": 0,
                "reason": "Revalidate immutable authenticated metadata using explicit catalog id-to-canonical_slug and exact direct endpoint evidence; preserve original failures and live bindings.",
            }
            _write(adjudication_path, adjudication, immutable=True)
    audit_binding = {
        "audit_directory": str(audit),
        "source_ledger_sha256": source_hash,
        "authorization_sha256": authorization_hash,
        "dispatch_ids": ids,
    }
    if ledger.reconciliation_audit not in (None, audit_binding):
        raise ValueError("the registered accounting audit identity changed")
    ledger.reconciliation_audit = audit_binding
    ledger.persist()

    def accept(index: int, result: Mapping[str, Any], attempt_number: int) -> bool:
        if result.get("http_status") != 200:
            return False
        try:
            billing_evidence = billing_mode_evidence(source["entries"], index)
            identity_evidence = (model_identity_evidence or {}).get(source["entries"][index]["model"])
            usage = validate_generation_receipt(
                source["entries"][index], result["data"], billing_evidence, identity_evidence
            )
        except (ValueError, KeyError, TypeError):
            return False
        record = {
            "protocol": RECONCILIATION_PROTOCOL,
            "dispatch_id": index,
            "generation_id": generation_identity(source["entries"][index]),
            "source_ledger_sha256": source_hash,
            "authorization_sha256": authorization_hash,
            "original_entry_sha256": digest(source["entries"][index]),
            "lookup_attempt": attempt_number,
            "lookup_result": copy.deepcopy(dict(result)),
            "lookup_result_sha256": digest(result),
            "usage": usage,
            "billing_mode_evidence": billing_evidence,
        }
        if identity_evidence is not None:
            record["model_identity_evidence"] = copy.deepcopy(dict(identity_evidence))
            record["identity_resolution_scope"] = "accounting receipt only; original generation binding unchanged"
        if adjudication is not None:
            record["cached_adjudication"] = {"path": str(adjudication_path), "sha256": digest(adjudication)}
        _write(audit / f"settlement-{index}.json", record, immutable=True)
        ledger.record_reconciliation(record)
        return True

    # A crash after receipt persistence and before ledger persistence can settle
    # from that receipt, without spending another GET or replacing its evidence.
    for item in journal["attempts"]:
        if ledger.effective_charge(ledger.entries[item["dispatch_id"]]) is None and "result" in item:
            accept(item["dispatch_id"], item["result"], item["sequence"])
    for index in ids:
        if journal["credential_rejected"] or cached_only:
            break
        entry = ledger.entries[index]
        if ledger.effective_charge(entry) is not None:
            continue
        count = sum(item["dispatch_id"] == index for item in journal["attempts"])
        while count < 3 and len(journal["attempts"]) < 9:
            count += 1
            item = {
                "sequence": len(journal["attempts"]) + 1,
                "dispatch_id": index,
                "generation_id": generation_identity(entry),
                "generation_attempt": count,
                "timeout_seconds": METADATA_LOOKUP_TIMEOUT,
                "status": "intent_reserved",
            }
            ledger.accounting_lookup_intents.append(copy.deepcopy(item))
            ledger.persist()  # Canonical monotonic intent survives lookup-journal rollback.
            journal["attempts"].append(item)
            _write(journal_path, journal)  # Irrevocably consume the GET before invoking transport.
            try:
                async with asyncio.timeout(METADATA_LOOKUP_TIMEOUT):
                    returned = await lookup(item["generation_id"])
                result = {key: returned[key] for key in ("http_status", "data", "error_type") if key in returned}
            except Exception as error:
                result = {"error_type": type(error).__name__}
            item.update(status="returned", result=result)
            if result.get("http_status") == 200:
                try:
                    validate_generation_receipt(entry, result["data"], billing_mode_evidence(source["entries"], index))
                except ValueError as error:
                    item["validation_error"] = str(error)
                except (KeyError, TypeError):
                    item["validation_error"] = "generation metadata schema is incomplete or invalid"
            if result.get("http_status") in (401, 403):
                journal["credential_rejected"] = True
            _write(audit / f"lookup-{item['sequence']:03d}.json", item, immutable=True)
            _write(journal_path, journal)
            if accept(index, result, item["sequence"]) or journal["credential_rejected"]:
                break
    fully_resolved = all(ledger.effective_charge(item) is not None for item in ledger.entries)
    release = {
        "source_ledger_sha256": source_hash,
        "authorization_sha256": authorization_hash,
        "prior_halt": source["halted"],
        "dispatch_ids": ids,
        "entry_count": len(source["entries"]),
        "disposition": disposition,
    }
    if fully_resolved:
        ledger.release_reconciled_halt(release)
    receipt: dict[str, Any] = {
        **identity,
        "original_entry_count": len(source["entries"]),
        "prior_halt": source["halted"],
        "fully_resolved": fully_resolved,
        "additional_lookup_attempts": len(journal["attempts"]),
        "credential_rejected": journal["credential_rejected"],
        "release_halt_with_provenance": release if fully_resolved else None,
        "unresolved_dispatch_ids": [
            item["dispatch_id"] for item in ledger.entries if ledger.effective_charge(item) is None
        ],
    }
    if fully_resolved:
        reconciled_path = audit / "reconciled_ledger.json"
        _write(reconciled_path, ledger.state_dict(), immutable=True)
        receipt.update(
            reconciled_snapshot_path=str(reconciled_path),
            reconciled_ledger_sha256=hashlib.sha256(reconciled_path.read_bytes()).hexdigest(),
        )
        _write(audit / "reconciliation_receipt.json", receipt, immutable=True)
        validate_reconciliation_receipt(ledger.state_dict(), receipt)
    else:
        previous = audit / "unresolved_receipt.json"
        if previous.is_file():
            old = _read(previous)
            _write(audit / ("unresolved_receipt-" + digest(old) + ".json"), old, immutable=True)
        _write(audit / "unresolved_receipt.json", receipt)
    return receipt


async def settle_cached_receipts(
    campaign_directory: str | Path,
    audit_directory: str | Path,
    *,
    model_catalog_path: str | Path,
    endpoint_catalog_paths: Mapping[str, str | Path],
) -> dict[str, Any]:
    """Revalidate existing authenticated receipts with hashed public catalog evidence.

    This entry point cannot make a network call, reset lookup counters, or change
    any generation binding. Original failed validation receipts remain immutable.
    """
    audit = Path(audit_directory).resolve()
    journal = _read(audit / "lookup_journal.json")
    source = _read(audit / "source_ledger.json")
    model_path = Path(model_catalog_path).resolve()
    models = _read(model_path)["data"]
    identity_evidence = {}
    for index in journal["dispatch_ids"]:
        entry = source["entries"][index]
        binding = OpenRouterBinding.from_dict(entry["usage"]["binding"])
        if binding.model not in endpoint_catalog_paths:
            continue
        rows = [row for row in models if row.get("id") == binding.model]
        if len(rows) != 1 or not isinstance(rows[0].get("canonical_slug"), str):
            raise ValueError("public model catalog must explicitly identify a unique canonical slug")
        endpoint_path = Path(endpoint_catalog_paths[binding.model]).resolve()
        identity_evidence[binding.model] = {
            "protocol": "documented_accounting_model_identity_1",
            "requested_model": binding.model,
            "metadata_model": rows[0]["canonical_slug"],
            "served_provider": binding.served_provider,
            "provider_endpoint": binding.provider_endpoint,
            "model_catalog": {
                "path": str(model_path),
                "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
                "url": "https://openrouter.ai/api/v1/models",
            },
            "endpoint_catalog": {
                "path": str(endpoint_path),
                "sha256": hashlib.sha256(endpoint_path.read_bytes()).hexdigest(),
                "url": "https://openrouter.ai/api/v1/models/" + binding.model + "/endpoints",
            },
        }

    async def no_lookup(generation_id: str) -> Mapping[str, Any]:
        raise AssertionError("cached accounting settlement cannot perform a metadata lookup")

    return await reconcile_campaign(
        campaign_directory,
        audit,
        lookup=no_lookup,
        authorization=journal["authorization"],
        dispatch_ids=journal["dispatch_ids"],
        disposition=journal["disposition"],
        model_identity_evidence=identity_evidence,
        cached_only=True,
    )
