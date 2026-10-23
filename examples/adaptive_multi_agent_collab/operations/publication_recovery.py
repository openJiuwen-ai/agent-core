"""Audited recovery of an authorized, definitely offline policy publication.

This never settles receipts or resumes a workflow.  Every mutable scientific,
optimizer, RNG and execution coordinate must equal the preserved parent except
for the independently derived approved policy amendment.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from .supplement import _canonical, _check_reference, _digest, _inside, _reference, _sha

RECOVERY_PROTOCOL = "audited_publication_recovery_v1"
AUTHORITY_PROTOCOL = "offline_publication_recovery_authority_v1"
COPY_CHUNK = 8 * 1024 * 1024
COPY_DEADLINE = 5.0


def bounded_sha(path: Path) -> str:
    """Measured validation units do not hide a whole-file migration read."""
    digest = hashlib.sha256()
    with _inside(path).open("rb") as stream:
        while True:
            started = time.monotonic()
            raw = stream.read(COPY_CHUNK)
            if not raw:
                break
            digest.update(raw)
            if time.monotonic() - started > COPY_DEADLINE:
                raise ValueError("preserved file verification exceeded per-chunk deadline")
    return digest.hexdigest()


def bounded_copy(source: Path, target: Path) -> dict[str, Any]:
    """Explicit resumable migration, each hash/copy/verify/fsync chunk bounded."""
    from ..infrastructure.storage import _mkdir_durable
    source, target = _inside(source), _inside(target)
    if source == target:
        raise ValueError("preservation cannot overwrite its source")
    started = time.perf_counter()
    metrics = {"copied_bytes": 0, "read_bytes": 0, "chunks": 0,
               "write_seconds": 0., "fsync_seconds": 0., "hash_seconds": 0.}
    directory_started = time.monotonic()
    _mkdir_durable(target.parent)
    if time.monotonic() - directory_started > COPY_DEADLINE:
        raise ValueError("preservation directory creation exceeded five-second bound")
    temporary = target.with_name(target.name + ".pending")
    chosen = target if target.exists() else temporary
    original_stat = source.stat()
    digest = hashlib.sha256()
    with source.open("rb") as incoming, chosen.open("r+b" if chosen.exists() else "x+b") as output:
        existing = output.seek(0, os.SEEK_END)
        if existing > original_stat.st_size:
            raise ValueError("preservation collision exceeds original bytes")
        output.seek(0)
        position = 0
        while True:
            chunk_started = time.monotonic()
            data = incoming.read(COPY_CHUNK)
            if not data:
                break
            tick = time.perf_counter()
            digest.update(data)
            metrics["hash_seconds"] += time.perf_counter() - tick
            metrics["read_bytes"] += len(data)
            already = min(max(existing - position, 0), len(data))
            if output.read(already) != data[:already]:
                raise ValueError("preserved partial copy differs from original bytes")
            metrics["read_bytes"] += already
            if already < len(data):
                if target.exists():
                    raise ValueError("preserved publication is incomplete")
                tick = time.perf_counter()
                output.write(data[already:])
                output.flush()
                metrics["write_seconds"] += time.perf_counter() - tick
                tick = time.perf_counter()
                os.fsync(output.fileno())
                metrics["fsync_seconds"] += time.perf_counter() - tick
                output.seek(position)
                if output.read(len(data)) != data:
                    raise ValueError("preservation copy read-back failed")
                metrics["read_bytes"] += len(data)
                metrics["copied_bytes"] += len(data) - already
            position += len(data)
            metrics["chunks"] += 1
            if time.monotonic() - chunk_started > COPY_DEADLINE:
                raise ValueError("preservation chunk exceeded five-second bound; resumable partial retained")
    now = source.stat()
    if (now.st_size, now.st_mtime_ns, now.st_ino) != (original_stat.st_size, original_stat.st_mtime_ns, original_stat.st_ino):
        raise ValueError("preservation source changed during copy")
    if not target.exists():
        commit_started = time.monotonic()
        temporary.replace(target)
        fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if time.monotonic() - commit_started > COPY_DEADLINE:
            raise ValueError("preservation directory commit exceeded five-second bound")
    metrics["elapsed_seconds"] = time.perf_counter() - started
    return {"original_path": str(source), "preserved_path": str(target),
            "sha256": digest.hexdigest(), "bytes": original_stat.st_size, "metrics": metrics}


def preserve_interruption(directory: Path, destination: Path, *,
                          names: tuple[str, ...] = ("checkpoint.pt", "expenditure_ledger.json", "calibration/transition_intent.json"),
                          protocol: str = RECOVERY_PROTOCOL) -> dict[str, Any]:
    """One immutable copy of changed publications and a verified WAL prefix."""
    from .experiment import _write_json_atomic

    manifest_path = destination / "PRESERVATION.json"
    if manifest_path.exists():
        return verify_preservation(manifest_path)
    records = [bounded_copy(directory / name, destination / name) for name in names if (directory / name).is_file()]
    path = directory / "accounting_progress.jsonl"
    digest = hashlib.sha256()
    length = path.stat().st_size
    started = time.perf_counter()
    remaining = length
    with path.open("rb") as stream:
        while remaining:
            tick = time.monotonic()
            data = stream.read(min(COPY_CHUNK, remaining))
            if not data:
                raise ValueError("journal prefix disappeared during preservation")
            digest.update(data)
            remaining -= len(data)
            if time.monotonic() - tick > COPY_DEADLINE:
                raise ValueError("journal prefix verification exceeded per-chunk deadline")
    if path.stat().st_size != length:
        raise ValueError("journal advanced while campaign/writer locks were required")
    value = {"protocol": protocol, "files": records,
             "journal_prefix": {"path": str(path), "bytes": length, "sha256": digest.hexdigest()},
             "journal_hash_seconds": time.perf_counter() - started}
    _write_json_atomic(manifest_path, value)
    return value


def exact_equal(left: Any, right: Any) -> bool:
    """Exact typed numerical values; JSON sequence containers are equivalent."""
    import numpy as np
    import torch

    seen: set[tuple[int, int]] = set()

    def same(a: Any, b: Any) -> bool:
        if a is b:
            return True
        if type(a) in {str, int, float, bool, type(None)}:
            return type(a) is type(b) and a == b
        pair = (id(a), id(b))
        if pair in seen:
            return True
        seen.add(pair)
        if isinstance(a, dict):
            return isinstance(b, dict) and a.keys() == b.keys() and all(same(v, b[k]) for k, v in a.items())
        if isinstance(a, (list, tuple)):
            return isinstance(b, (list, tuple)) and len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
        if isinstance(a, torch.Tensor):
            return isinstance(b, torch.Tensor) and a.dtype == b.dtype and torch.equal(a, b)
        if isinstance(a, np.ndarray):
            return isinstance(b, np.ndarray) and a.dtype == b.dtype and np.array_equal(a, b)
        return type(a) is type(b) and a == b

    return same(left, right)


def expected_patch(parent: Mapping[str, Any], prepared: Any, plan: Mapping[str, Any],
                   lineage: Mapping[str, Any], authorization: Mapping[str, Any],
                   authorization_path: Path) -> list[dict[str, Any]]:
    """Derive the allowed changes without constructing models or copying history."""
    from .calibration_launch import policy_amendment, policy_authorization, policy_bindings
    from .experiment import planning_hooks
    from ..infrastructure.failure_policy import new_policy_state, validate_policy_state
    from ..infrastructure.reproducibility import scientific_configuration

    old = parent["hook_state"]["expenditure_ledger"]
    policy = new_policy_state(prepared.config.failure_policy,
                              policy_authorization(plan, authorization), policy_bindings(plan, prepared))
    policy["events"] = old["failure_policy"]["events"]
    policy["historical_adoptions"] = old["failure_policy"]["historical_adoptions"]
    policy["condition_amendments"] = [policy_amendment(plan, old["failure_policy"], authorization_path)]
    validate_policy_state(policy, old["entries"])
    condition = scientific_configuration(prepared.config, encoder_metadata=parent["encoder_metadata"],
                                         architecture_identifiers=parent["architecture_identifiers"]).to_dict()
    changes = {
        ("config",): prepared.config.to_dict(),
        ("declared_config",): prepared.config.to_dict(),
        ("scientific_configuration",): condition,
        ("hook_state", "condition"): planning_hooks(prepared)._condition(),
        ("hook_state", "calibration_lineage"): dict(lineage),
        ("hook_state", "evaluation_cache"): {},
        ("hook_state", "evaluation_query_cache"): {},
        ("hook_state", "stage_authorizations"): {"development": dict(authorization)},
        ("hook_state", "expenditure_ledger", "failure_policy"): policy,
    }
    return [{"path": list(path), "value": value} for path, value in changes.items()]


def apply_patch_values(parent: Mapping[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Persistent shallow path copies; unchanged tensors and evidence are shared."""
    result = dict(parent)
    for change in changes:
        cursor = result
        for key in change["path"][:-1]:
            cursor[key] = dict(cursor[key])
            cursor = cursor[key]
        cursor[change["path"][-1]] = change["value"]
    return result


def verify_patch(parent: Mapping[str, Any], current: Mapping[str, Any],
                 changes: list[dict[str, Any]], ledger: Mapping[str, Any]) -> None:
    expected = apply_patch_values(parent, changes)
    # Only these declared JSON storage boundaries use their existing canonical
    # serialization.  For example AgentCallKind.INITIAL is the JSON string
    # "initial"; amounts, unknowns, fields and sequence order remain exact.
    # Tensors, optimizer/RNG state and trainer counters retain typed comparison.
    json_fields = {"config", "declared_config", "scientific_configuration", "hook_state"}
    matches = expected.keys() == current.keys() and all(
        (_canonical(expected[key]) == _canonical(current[key]) if key in json_fields
         else exact_equal(expected[key], current[key])) for key in expected)
    if not matches:
        raise ValueError("interrupted publication differs from the exact approved parent amendment")
    if _canonical(current["hook_state"]["expenditure_ledger"]) != _canonical(ledger):
        raise ValueError("interrupted checkpoint, ledger and durable accounting disagree")
    if current.get("request_boundary_pending") is not False:
        raise ValueError("recovery cannot certify unfinished physical/workflow execution")


def journal_tail(path: Path) -> dict[str, Any]:
    """Read the final complete record without copying the entire WAL."""
    with path.open("rb") as stream:
        stream.seek(0, 2)
        end = stream.tell()
        if not end:
            raise ValueError("missing interrupted journal")
        stream.seek(end - 1)
        if stream.read(1) != b"\n":
            raise ValueError("torn journal tail requires separate review")
        size = min(end, 8192)
        while True:
            stream.seek(end - size)
            raw = stream.read(size)
            lines = raw.splitlines()
            if size == end or len(lines) > 1:
                return json.loads(lines[-1])
            size = min(end, size * 2)


def verify_tail(journal: Mapping[str, Any], tail: Mapping[str, Any], checkpoint: Path,
                sequence: int, *, recovery_fingerprint: str | None = None) -> None:
    data = tail.get("data", {})
    if (journal.get("boundary") is not None or tail.get("operation") != "checkpoint-intent"
            or tail.get("seq") != sequence or journal.get("sequence") != sequence + 1
            or journal.get("sha256") != tail.get("sha256")
            or data.get("checkpoint") != str(checkpoint.resolve()) or data.get("safe_requested") is not True
            or (recovery_fingerprint is not None and data.get("recovery_fingerprint") != recovery_fingerprint)):
        raise ValueError("recovery requires its exact final safe-requested checkpoint intent; intervening work is forbidden")


def verify_recovery_suffix(directory: Path, proof: Mapping[str, Any], preservation: Mapping[str, Any]) -> None:
    """Only this recovery's publication may follow the reviewed interrupted tail."""
    with (directory / "accounting_progress.jsonl").open("rb") as stream:
        stream.seek(preservation["journal_prefix"]["bytes"])
        intents = 0
        committed = False
        for raw in stream:
            if not raw.endswith(b"\n"):
                raise ValueError("torn recovery publication remains blocked")
            record = json.loads(raw)
            if committed:
                raise ValueError("work intervened after the recovery commit")
            if record["operation"] == "checkpoint-intent":
                if (record["data"].get("recovery_fingerprint") != proof["fingerprint"]
                        or record["data"].get("safe_requested") is not True
                        or record["data"].get("checkpoint") != proof["expected_intent_checkpoint"]):
                    raise ValueError("unrelated publication intervened during recovery")
                intents += 1
            elif record["operation"] == "safe-boundary" and intents:
                committed = True
            else:
                raise ValueError("dispatch, accounting or workflow progress intervened during recovery")


def verify_preservation(path: str | Path) -> dict[str, Any]:
    """Original bytes remain available; a WAL prefix is verified, never copied."""
    manifest = json.loads(_inside(path).read_text())
    for item in manifest["files"]:
        if bounded_sha(_inside(item["preserved_path"])) != item["sha256"]:
            raise ValueError("preserved publication bytes changed")
    prefix = manifest["journal_prefix"]
    digest = hashlib.sha256()
    remaining = prefix["bytes"]
    with _inside(prefix["path"]).open("rb") as stream:
        while remaining:
            started = time.monotonic()
            data = stream.read(min(1024 * 1024, remaining))
            if not data:
                raise ValueError("preserved journal prefix is missing")
            digest.update(data)
            remaining -= len(data)
            if time.monotonic() - started > COPY_DEADLINE:
                raise ValueError("preservation prefix verification exceeded per-chunk deadline")
    if digest.hexdigest() != prefix["sha256"]:
        raise ValueError("preserved journal prefix changed")
    return manifest


def recorded_interruption(plan_path: str | Path, parent: Mapping[str, Any],
                          sequence: int) -> dict[str, Any]:
    """Bind recovery to the independently recorded failed publication."""
    directory = _inside(plan_path).parent
    paths = {name: directory / name for name in (
        "state_verification.json", "preservation_verification.json", "transition_outcome.json")}
    records = {name: json.loads(path.read_text()) for name, path in paths.items()}
    state = records["state_verification.json"]
    preserved = records["preservation_verification.json"]
    outcome = records["transition_outcome.json"]
    checkpoint_hash = preserved.get("current_checkpoint_sha256")
    ledger_hash = preserved.get("current_ledger_sha256")
    if (not isinstance(checkpoint_hash, str) or len(checkpoint_hash) != 64
            or not isinstance(ledger_hash, str) or len(ledger_hash) != 64
            or state.get("checkpoint_sha256") != checkpoint_hash
            or state.get("source_checkpoint_sha256") != parent["sha256"]
            or state.get("verification_mode") != "offline_transition_failure"
            or state.get("no_live_calls_during_transition") is not True
            or state.get("actual_workflow_executions") != 0
            or outcome.get("status") != "blocked_partial_transition_publication"
            or outcome.get("last_observed_journal_operation") != "checkpoint-intent"
            or outcome.get("last_observed_journal_sequence") != sequence
            or outcome.get("new_provider_calls") != 0
            or outcome.get("new_metadata_lookups") != 0
            or outcome.get("transition_receipt_published") is not False
            or preserved.get("passed") is not True):
        raise ValueError("saved interrupted-publication evidence is inconsistent")
    return {"reports": {name: _reference(path) for name, path in paths.items()},
            "checkpoint_sha256": checkpoint_hash, "ledger_sha256": ledger_hash,
            "parent_checkpoint_sha256": parent["sha256"], "intent_sequence": sequence}


def verify_recorded_identity(record: Mapping[str, Any], checkpoint: Mapping[str, Any],
                             ledger: Mapping[str, Any]) -> None:
    if (checkpoint["sha256"] != record["checkpoint_sha256"]
            or ledger["sha256"] != record["ledger_sha256"]):
        raise ValueError("interrupted bytes differ from the saved failure evidence")


def inspect_recovery(specification_path: str | Path, plan_path: str | Path,
                     forecast_path: str | Path, authorization_path: str | Path,
                     authority_path: str | Path, *, expected_sequence: int = 78949) -> dict[str, Any]:
    """Read-only unique-state proof; callers must hold locks before publication."""
    from .calibration_launch import (LINEAGE_PROTOCOL, apply_prepared, auth_bindings,
                                     load_plan)
    from .experiment import prepare_experiment
    from ..infrastructure.persistence import read_journal, _json
    from .preflight import load_stage_authorization
    from .policy_activation import _validate_independent_activation
    from ..infrastructure.storage import load_checkpoint, load_ledger
    from .supplement import prepare_checkpoint_condition

    plan = load_plan(plan_path, specification_path)
    recorded = recorded_interruption(plan_path, plan["references"]["checkpoint"], expected_sequence)
    campaign = _inside(plan["campaign_directory"])
    _validate_independent_activation(json.loads(_inside(specification_path).read_text()),
                                     _inside(plan["campaign_directory"]))
    original_preservation = verify_preservation(campaign / "calibration/before/PRESERVATION.json")
    checkpoint = campaign / "checkpoint.pt"
    parent = load_checkpoint(_check_reference(plan["references"]["checkpoint"]))
    original = prepare_checkpoint_condition(prepare_experiment(specification_path), parent,
                                            specification_path=specification_path)
    prepared = apply_prepared(original, plan)
    bindings, _ = auth_bindings(original, checkpoint, plan_path, forecast_path)
    authorization = load_stage_authorization(authorization_path, **bindings)
    authority = json.loads(_inside(authority_path).read_text())
    if (authority.get("protocol") != AUTHORITY_PROTOCOL
            or authority.get("decision") != "authorize_offline_storage_migration_and_audited_recovery"
            or authority.get("campaign") != "Pilot_5"):
        raise ValueError("offline recovery requires the separately authorized exact operation")
    lineage = {"protocol": LINEAGE_PROTOCOL, "plan_path": str(_inside(plan_path)),
               "plan_sha256": _sha(_inside(plan_path)), "parent_checkpoint": plan["references"]["checkpoint"]}
    expected_intent = {"protocol": "calibration_limit_transition_v1", "lineage": lineage,
                       "authorization": _reference(authorization_path)}
    if json.loads((campaign / "calibration/transition_intent.json").read_text()) != expected_intent:
        raise ValueError("original transition intent changed")
    current, ledger = load_checkpoint(checkpoint), load_ledger(campaign / "expenditure_ledger.json")
    changes = expected_patch(parent, prepared, plan, lineage, authorization, _inside(authorization_path))
    verify_patch(parent, current, changes, ledger)
    # The authorized pre-publication prefix must be byte-identical, followed
    # only by its approved policy ledger delta and checkpoint intent.  Equal
    # spending totals cannot disguise intervening physical or workflow work.
    with (campaign / "accounting_progress.jsonl").open("rb") as stream:
        stream.seek(original_preservation["journal_prefix"]["bytes"])
        original_suffix = [json.loads(stream.readline()) for _ in range(2)]
    if ([item.get("seq") for item in original_suffix] != [expected_sequence - 1, expected_sequence]
            or [item.get("operation") for item in original_suffix] != ["ledger", "checkpoint-intent"]
            or original_suffix[0].get("data") != {"header": {"failure_policy": ledger["failure_policy"]},
                                                  "removed": [], "entries": []}
            or original_suffix[1].get("data") != {"checkpoint": str(checkpoint), "safe_requested": True}):
        raise ValueError("original publication suffix is not exactly its authorized policy-only amendment")
    journal = read_journal(campaign)
    if journal is None or _canonical(journal["state"]) != _canonical(ledger):
        raise ValueError("journal is missing, newer than or inconsistent with interrupted publication")
    tail = journal_tail(campaign / "accounting_progress.jsonl")
    saved_proof_path = campaign / "calibration/recovery/plan.json"
    saved_proof = None
    if saved_proof_path.exists():
        saved_proof = json.loads(saved_proof_path.read_text())
        if (saved_proof.get("protocol") != RECOVERY_PROTOCOL
                or saved_proof.get("fingerprint") != _digest({k: v for k, v in saved_proof.items() if k != "fingerprint"})
                or saved_proof.get("authority") != _reference(authority_path)
                or saved_proof.get("plan") != _reference(plan_path)
                or saved_proof.get("forecast") != _reference(forecast_path)
                or saved_proof.get("authorization") != _reference(authorization_path)
                or saved_proof.get("recorded_interruption") != recorded
                or saved_proof.get("lineage") != lineage
                or saved_proof.get("expected_intent_sequence") != expected_sequence):
            raise ValueError("recovery authorization or original interrupted identity changed")
        verify_recorded_identity(recorded, saved_proof["interrupted_checkpoint"],
                                 saved_proof["interrupted_ledger"])
        preserved = verify_preservation(campaign / "calibration/recovery/before/PRESERVATION.json")
        by_original = {item["original_path"]: item for item in preserved["files"]}
        for name in ("interrupted_checkpoint", "interrupted_ledger"):
            reference = saved_proof[name]
            if by_original[reference["path"]]["sha256"] != reference["sha256"]:
                raise ValueError("recovery preserved source differs from authorized interrupted publication")
        verify_recovery_suffix(campaign, saved_proof, preserved)
        if journal["boundary"] is None:
            verify_tail(journal, tail, checkpoint, tail["seq"],
                        recovery_fingerprint=None if tail["seq"] == expected_sequence else saved_proof["fingerprint"])
        else:
            from .experiment import validate_journal_resume
            validate_journal_resume(checkpoint, campaign, allow_halted=True)
    else:
        verify_tail(journal, tail, checkpoint, expected_sequence)
        verify_recorded_identity(recorded, _reference(checkpoint),
                                 _reference(campaign / "expenditure_ledger.json"))
    required = authority.get("required_preservation", {})
    unknown = [entry["dispatch_id"] for entry in ledger["entries"] if entry["charged"] is None]
    if (len(ledger["entries"]) != required.get("receipts")
            or unknown != required.get("unknown_dispatch_ids")
            or prepared.config.failure_policy.max_unresolved_requests != required.get("count_limit")
            or prepared.config.failure_policy.max_unresolved_reserved_usd != required.get("uncertainty_usd")):
        raise ValueError("recovery authority does not match the preserved receipt or policy identities")
    proof = {"protocol": RECOVERY_PROTOCOL, "authority": _reference(authority_path),
             "plan": _reference(plan_path), "forecast": _reference(forecast_path),
             "authorization": _reference(authorization_path), "lineage": lineage,
             "recorded_interruption": recorded,
             "parent_checkpoint": plan["references"]["checkpoint"],
             "interrupted_checkpoint": _reference(checkpoint),
             "interrupted_ledger": _reference(campaign / "expenditure_ledger.json"),
             "expected_sequence": journal["sequence"], "expected_previous": journal["sha256"],
             "expected_intent_sequence": expected_sequence, "expected_intent_checkpoint": str(checkpoint),
             "expected_state_sha256": hashlib.sha256(_json(ledger)).hexdigest(),
             "receipt_count": len(ledger["entries"]), "unknown_dispatch_ids": unknown,
             "generation_calls": 0, "spending_reset": False}
    proof["fingerprint"] = _digest(proof)
    if saved_proof is not None:
        proof = saved_proof
    return {"proof": proof, "patch": changes, "payload": current, "ledger": ledger,
            "journal": journal, "prepared": prepared, "plan": plan, "lineage": lineage}


def recover_interrupted(specification_path: str | Path, plan_path: str | Path,
                        forecast_path: str | Path, authorization_path: str | Path,
                        authority_path: str | Path) -> dict[str, Any]:
    """Complete the reviewed offline transaction exactly once, never run a stage."""
    import asyncio
    from .calibration_launch import TRANSITION_PROTOCOL
    from .experiment import _campaign_lock, _write_json_atomic, validate_journal_resume
    from ..infrastructure.persistence import DurableJournal, _json
    from ..infrastructure.storage import load_checkpoint, load_ledger

    source = _inside(specification_path)
    directory = source.parent
    recovery_dir = directory / "calibration/recovery"
    checkpoint = directory / "checkpoint.pt"
    with _campaign_lock(directory):
        context = inspect_recovery(source, plan_path, forecast_path, authorization_path, authority_path)
        proof, state = context["proof"], context["ledger"]
        recovery_receipt = recovery_dir / "receipt.json"
        if recovery_receipt.exists():
            receipt = json.loads(recovery_receipt.read_text())
            if (receipt.get("recovery_fingerprint") != proof["fingerprint"]
                    or receipt.get("checkpoint_sha256") != _sha(checkpoint)):
                raise ValueError("completed recovery receipt no longer matches its publication")
            validate_journal_resume(checkpoint, directory, allow_halted=True)
            return receipt
        journal = context["journal"]
        token = None
        if journal["boundary"] is None:
            tail = journal_tail(directory / "accounting_progress.jsonl")
            token = {**proof, "expected_sequence": journal["sequence"], "expected_previous": journal["sha256"],
                     "expected_intent_sequence": tail["seq"],
                     "expected_state_sha256": hashlib.sha256(_json(state)).hexdigest(),
                     "authorization_sha256": proof["authorization"]["sha256"],
                     "plan_fingerprint": context["plan"]["fingerprint"],
                     "parent_checkpoint_sha256": proof["parent_checkpoint"]["sha256"],
                     "interrupted_checkpoint_sha256": proof["interrupted_checkpoint"]["sha256"],
                     "validation_sha256": proof["fingerprint"]}
        writer = DurableJournal([directory], state, recovery_token=token)
        metrics: dict[str, Any] = {}

        async def publish() -> None:
            try:
                before = recovery_dir / "before/PRESERVATION.json"
                if before.exists():
                    verify_preservation(before)
                else:
                    preserve_interruption(directory, before.parent)
                proof_path = recovery_dir / "plan.json"
                if not proof_path.exists():
                    await writer.artifact(proof_path, proof)
                if journal["boundary"] is not None:
                    return
                metrics.update(await publish_policy_amendment(writer, directory, proof["parent_checkpoint"],
                    context["patch"], state, proof["fingerprint"], hook_state=context["payload"]["hook_state"]))
            finally:
                await writer.aclose()
                metrics["writer"] = dict(writer.metrics)

        asyncio.run(publish())
        validate_journal_resume(checkpoint, directory, allow_halted=True)
        restored = load_checkpoint(checkpoint)
        verify_patch(load_checkpoint(proof["parent_checkpoint"]["path"]), restored,
                     context["patch"], load_ledger(directory / "expenditure_ledger.json"))
        transition = {"protocol": TRANSITION_PROTOCOL, "lineage": context["lineage"],
                      "plan_fingerprint": context["plan"]["fingerprint"],
                      "authorization": proof["authorization"], "source_checkpoint": proof["parent_checkpoint"],
                      "checkpoint_at_publication": _reference(checkpoint), "generation_calls": 0, "spending_reset": False,
                      "preservation": str(directory / "calibration/before/PRESERVATION.json")}
        transition_path = directory / "calibration/transition.json"
        if transition_path.exists() and json.loads(transition_path.read_text()) != transition:
            raise ValueError("transition receipt collision; no historical publication is overwritten")
        if not transition_path.exists():
            _write_json_atomic(transition_path, transition)
        receipt = {"protocol": RECOVERY_PROTOCOL, "recovery_fingerprint": proof["fingerprint"],
                   "authority": proof["authority"], "checkpoint_sha256": _sha(checkpoint),
                   "ledger_sha256": _sha(directory / "expenditure_ledger.json"),
                   "transition": _reference(transition_path), "preservation": str(recovery_dir / "before/PRESERVATION.json"),
                   "receipt_count": len(state["entries"]), "unknown_dispatch_ids": proof["unknown_dispatch_ids"],
                   "generation_calls": 0, "spending_reset": False, "metrics": metrics}
        _write_json_atomic(recovery_receipt, receipt)
        return receipt


async def publish_policy_amendment(writer: Any, directory: Path, parent: Mapping[str, Any],
                                   changes: list[dict[str, Any]], state: Mapping[str, Any],
                                   fingerprint: str, *, hook_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Explicit bounded migration followed by a small atomic policy publication."""
    from ..infrastructure.storage import EvidenceStore, amendment_payload

    store = EvidenceStore(directory)
    started = time.perf_counter()
    entries = []
    for entry in state["entries"]:
        entries.append(await writer.prepare_checkpoint_state(lambda item=entry: store.put(item)))
    policy = {}
    for key, value in state.get("failure_policy", {}).items():
        if key in {"events", "historical_adoptions"}:
            policy[key] = [await writer.prepare_checkpoint_state(lambda event=event: store.put(event)) for event in value]
        else:
            policy[key] = await writer.prepare_checkpoint_state(lambda value=value: store.put(value))
    compact = {**{key: value for key, value in state.items() if key not in {"entries", "failure_policy"}},
               "entries": entries, **({"failure_policy": policy} if "failure_policy" in state else {})}
    patches = []
    for item in changes:
        value = policy if item["path"] == ["hook_state", "expenditure_ledger", "failure_policy"] else item["value"]
        patches.append({"path": item["path"], "value": await writer.prepare_checkpoint_state(lambda value=value: store.put(value))})
    patches.append({"path": ["hook_state", "expenditure_ledger", "entries"], "value": entries})
    if hook_state is not None:
        # These values do not change semantically.  Migrating them now gives
        # the first ordinary checkpoint verified references rather than a
        # hidden second bulk migration inside its five-second writer budget.
        for field in ("compatibility_records", "pilot_observations", "evaluation_records"):
            if field in hook_state:
                values = [await writer.prepare_checkpoint_state(lambda value=value: store.put(value))
                          for value in hook_state[field]]
                patches.append({"path": ["hook_state", field], "value": values})
        for field in ("safety_record", "termination_record"):
            if hook_state.get(field) is not None:
                value = await writer.prepare_checkpoint_state(lambda field=field: store.put(hook_state[field]))
                patches.append({"path": ["hook_state", field], "value": value})
        if "recovery_slots" in hook_state:
            values = {key: await writer.prepare_checkpoint_state(lambda value=value: store.put(value))
                      if value.get("status") == "complete" else value for key, value in hook_state["recovery_slots"].items()}
            patches.append({"path": ["hook_state", "recovery_slots"], "value": values})
    checkpoint = directory / "checkpoint.pt"
    envelope = amendment_payload(parent["path"], parent["sha256"], patches, target=checkpoint)
    metrics = {"migration_elapsed_seconds": time.perf_counter() - started, "evidence": dict(store.metrics)}
    tick = time.perf_counter()
    await writer.amendment(envelope, checkpoint, compact, safe=True, recovery_fingerprint=fingerprint)
    metrics["publication_acknowledgement_seconds"] = time.perf_counter() - tick
    return metrics
