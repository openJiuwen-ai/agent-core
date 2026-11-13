"""Audited recovery of one proved, unexecuted shared-query workflow intent.

This is not provider recovery. The prior checkpoint is republished unchanged;
its deterministic query identity, seed and replacement allowance are retained.
The original intent and all subsequent recovery publication records remain.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .supplement import _check_reference, _digest, _inside, _reference

PROTOCOL = "audited_predispatch_recovery_v1"
AUTHORITY_PROTOCOL = "predispatch_recovery_authority_v1"


def _suffix(path: Path, start: int) -> list[dict[str, Any]]:
    """Inspect only the complete authorized suffix after full WAL verification."""
    with path.open("rb") as stream:
        stream.seek(start)
        result = []
        for raw in stream:
            if not raw.endswith(b"\n"):
                raise ValueError("torn predispatch recovery suffix")
            result.append(json.loads(raw))
    return result


def _last_pair(path: Path, *, end: int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    with path.open("rb") as stream:
        stream.seek(0, 2)
        end = stream.tell() if end is None else end
        size = min(8192, end)
        while size:
            stream.seek(end - size)
            raw = stream.read(size)
            if not raw.endswith(b"\n"):
                raise ValueError("torn workflow intent")
            lines = raw.splitlines()
            if len(lines) >= 3 or size == end:
                if len(lines) < 2:
                    break
                return json.loads(lines[-2]), json.loads(lines[-1])
            size = min(end, size * 2)
    raise ValueError("missing predecessor safe boundary")


def validate_intent(payload: Mapping[str, Any], ledger: Mapping[str, Any],
                    journal: Mapping[str, Any], previous: Mapping[str, Any],
                    intent: Mapping[str, Any], authority: Mapping[str, Any]) -> None:
    """No dispatch is proved by the exact safe->intent suffix, not zero costs."""
    from ..infrastructure.storage import strict_json_equal
    from ..workflows.recovery import validate_recovery_records

    if (intent.get("seq") != authority["intent_sequence"]
            or intent.get("sha256") != authority["intent_sha256"]
            or journal["sequence"] != intent["seq"] + 1
            or journal["sha256"] != intent["sha256"]
            or journal["boundary"] is not None
            or previous.get("seq") != intent["seq"] - 1
            or previous.get("operation") != "safe-boundary"
            or intent.get("previous") != previous.get("sha256")
            or previous.get("data", {}).get("checkpoint_sha256") != authority["checkpoint_sha256"]
            or previous.get("data", {}).get("checkpoint") != authority["checkpoint_path"]
            or intent.get("operation") != "progress"
            or set(intent.get("data", {})) != {"kind", "payload"}
            or intent["data"]["kind"] != "workflow-recovery"):
        raise ValueError("recovery requires the exact final predispatch workflow intent")
    value = intent["data"]["payload"]
    if set(value) != {"slot", "sample_counters", "workflow_attempt_counter"}:
        raise ValueError("unexpected workflow progress or dispatch evidence")
    slot = value["slot"]
    validate_recovery_records({slot["slot_id"]: slot})
    if (slot["operation"] != "shared-prefix" or slot["status"] != "running"
            or slot["slot_id"] != authority["slot_id"]
            or slot["executions"] != [{"index": 0, "status": "started",
                                       "execution_id": authority["execution_id"]}]):
        raise ValueError("only the original untouched shared-query execution is recoverable")
    hooks, trainer = payload["hook_state"], payload["trainer_state"]
    if (payload.get("request_boundary_pending") is not False
            or trainer.get("phase") != "calibration" or trainer.get("calibration_complete")
            or trainer.get("outer_cycle") != 0
            or slot["slot_id"] in hooks.get("recovery_slots", {})
            or hooks.get("termination_record") is not None
            or hooks.get("sample_counters") != value["sample_counters"]
            or trainer.get("workflow_attempt_counter") != value["workflow_attempt_counter"]):
        raise ValueError("checkpoint progress differs from the unexecuted workflow intent")
    if (not strict_json_equal(hooks["expenditure_ledger"], ledger)
            or not strict_json_equal(journal["state"], ledger)
            or len(ledger["entries"]) != authority["receipt_count"]
            or [item["dispatch_id"] for item in ledger["entries"] if item["charged"] is None]
            != authority["unknown_dispatch_ids"]):
        raise ValueError("receipt identity, dispatch evidence or accounting changed")


def token_matches(saved: Mapping[str, Any], token: Mapping[str, Any]) -> bool:
    """Independent final gate under the writer lock; no generic unfinished bypass."""
    from ..infrastructure.persistence import _json
    tail = saved["tail_record"]
    expected = token.get("expected_tail")
    return bool(
        token.get("protocol") == PROTOCOL
        and saved["boundary"] is None
        and saved["sequence"] == token.get("expected_sequence")
        and saved["sha256"] == token.get("expected_previous")
        and tail == expected
        and hashlib.sha256(_json(saved["state"])).hexdigest() == token.get("expected_state_sha256")
        and all(isinstance(token.get(key), str) and len(token[key]) == 64
                for key in ("authority_sha256", "validation_sha256", "checkpoint_sha256"))
        and ((tail["operation"] == "progress"
              and tail["sequence"] == token.get("original_intent_sequence")
              and tail["sha256"] == token.get("original_intent_sha256")
              and tail["data"].get("kind") == "workflow-recovery")
             or (tail["operation"] == "checkpoint-intent"
                 and tail["data"] == {"checkpoint": token.get("checkpoint_path"),
                                      "safe_requested": True,
                                      "recovery_fingerprint": token.get("validation_sha256")})))


def _verify_authority(path: Path, campaign: Path) -> dict[str, Any]:
    authority = json.loads(path.read_text())
    if (authority.get("protocol") != AUTHORITY_PROTOCOL
            or authority.get("decision") != "recover_verified_predispatch_intent"
            or authority.get("campaign_directory") != str(campaign)
            or authority.get("checkpoint_path") != str(campaign / "checkpoint.pt")
            or not isinstance(authority.get("approval_source"), str)
            or not authority["approval_source"].strip()):
        raise ValueError("missing or mismatched explicit predispatch recovery authority")
    for name in ("manifest", "plan", "forecast", "authorization", "failure_trace"):
        _check_reference(authority["references"][name])
    return authority


def _verify_recovery_suffix(records: list[dict[str, Any]], proof: Mapping[str, Any]) -> None:
    committed = False
    for item in records:
        if committed:
            raise ValueError("work intervened after predispatch recovery")
        if item["operation"] == "checkpoint-intent":
            if item["data"] != {"checkpoint": proof["checkpoint_path"], "safe_requested": True,
                                "recovery_fingerprint": proof["fingerprint"]}:
                raise ValueError("unrelated intent intervened during recovery")
        elif item["operation"] == "safe-boundary" and records and item is not records[0]:
            committed = True
        else:
            raise ValueError("dispatch or progress intervened during predispatch recovery")


def recover(campaign: str | Path, authority_path: str | Path) -> dict[str, Any]:
    """Locked, audited, idempotent operation. Never calls a model or changes a slot."""
    import torch
    from .experiment import _campaign_lock, _write_json_atomic, validate_journal_resume
    from ..infrastructure.persistence import DurableJournal, read_journal, _json
    from .publication_recovery import exact_equal, preserve_interruption, verify_preservation
    from ..infrastructure.storage import (StartupVerification, load_checkpoint, load_ledger,
                           read_json, strict_json_equal)

    campaign, authority_path = Path(_inside(campaign)), Path(_inside(authority_path))
    target = campaign / "calibration/predispatch_recovery"
    checkpoint, ledger_path = campaign / "checkpoint.pt", campaign / "expenditure_ledger.json"
    with _campaign_lock(campaign), StartupVerification():
        authority = _verify_authority(authority_path, campaign)
        journal = read_journal(campaign, include_audit=False)
        if journal is None:
            raise ValueError("missing durable journal")
        payload, ledger = load_checkpoint(checkpoint), load_ledger(ledger_path)
        plan_path, receipt_path = target / "plan.json", target / "receipt.json"
        original = None
        if plan_path.exists():
            proof = json.loads(plan_path.read_text())
            if (proof.get("fingerprint") != _digest({k: v for k, v in proof.items() if k != "fingerprint"})
                    or proof.get("authority") != _reference(authority_path)):
                raise ValueError("predispatch recovery proof or authority changed")
            preservation = verify_preservation(target / "before/PRESERVATION.json")
            _verify_recovery_suffix(_suffix(campaign / "accounting_progress.jsonl",
                                           preservation["journal_prefix"]["bytes"]), proof)
            original = load_checkpoint(target / "before/checkpoint.pt")
            if _reference(target / "before/checkpoint.pt")["sha256"] != authority["checkpoint_sha256"]:
                raise ValueError("preserved checkpoint is not the authorized parent")
            previous, intent = _last_pair(campaign / "accounting_progress.jsonl",
                                          end=preservation["journal_prefix"]["bytes"])
            validate_intent(original, ledger, {"sequence": intent["seq"] + 1,
                            "sha256": intent["sha256"], "boundary": None, "state": ledger},
                            previous, intent, authority)
            if proof["original_intent"] != intent or proof["previous_safe_boundary"] != previous:
                raise ValueError("recovery proof differs from the preserved intent")
            if not exact_equal(original, payload):
                raise ValueError("checkpoint changed during predispatch recovery")
            if (not strict_json_equal(ledger, original["hook_state"]["expenditure_ledger"])
                    or not strict_json_equal(journal["state"], ledger)):
                raise ValueError("accounting changed during predispatch recovery")
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
                if (receipt.get("recovery_fingerprint") != proof["fingerprint"]
                        or receipt.get("checkpoint_sha256") != _reference(checkpoint)["sha256"]):
                    raise ValueError("recovery receipt publication changed")
                validate_journal_resume(checkpoint, campaign, allow_halted=True)
                return receipt
        else:
            if (_reference(checkpoint)["sha256"] != authority["checkpoint_sha256"]
                    or _reference(ledger_path)["sha256"] != authority["ledger_sha256"]):
                raise ValueError("authorized interrupted artifact bytes changed")
            previous, intent = _last_pair(campaign / "accounting_progress.jsonl")
            validate_intent(payload, ledger, journal, previous, intent, authority)
            proof = {"protocol": PROTOCOL, "authority": _reference(authority_path),
                     "checkpoint_path": str(checkpoint),
                     "checkpoint_sha256": authority["checkpoint_sha256"],
                     "original_intent": intent, "previous_safe_boundary": previous,
                     "slot_id": authority["slot_id"], "execution_id": authority["execution_id"],
                     "receipt_count": len(ledger["entries"]),
                     "unknown_dispatch_ids": authority["unknown_dispatch_ids"],
                     "disposition": "local predispatch defect; original execution not performed",
                     "provider_replacement": False, "replacement_allowance_changed": False}
            proof["fingerprint"] = _digest(proof)
        token = None
        if journal["boundary"] is None:
            token = {"protocol": PROTOCOL, "expected_sequence": journal["sequence"],
                     "expected_previous": journal["sha256"], "expected_tail": journal["tail_record"],
                     "expected_state_sha256": hashlib.sha256(_json(ledger)).hexdigest(),
                     "authority_sha256": proof["authority"]["sha256"],
                     "validation_sha256": proof["fingerprint"],
                     "checkpoint_sha256": proof["checkpoint_sha256"],
                     "checkpoint_path": str(checkpoint),
                     "original_intent_sequence": proof["original_intent"]["seq"],
                     "original_intent_sha256": proof["original_intent"]["sha256"]}
        writer = DurableJournal([campaign], ledger, recovery_token=token)
        # Independent writer ownership plus locked freshness precede preservation
        # or any publication. No running executor/writer can race this operation.
        async def publish() -> None:
            try:
                if not (target / "before/PRESERVATION.json").exists():
                    preserve_interruption(campaign, target / "before", names=(
                        "checkpoint.pt", "expenditure_ledger.json", "calibration/execution/stderr.txt",
                        "calibration/execution/guard/sitecustomize.py"), protocol=PROTOCOL)
                if not plan_path.exists():
                    await writer.artifact(plan_path, proof)
                if journal["boundary"] is not None:
                    return
                # Reuse the exact compact envelope: no evidence expansion, RNG
                # read/update, tensor mutation or history/cursor modification.
                stored = torch.load(checkpoint, map_location="cpu", weights_only=False)
                raw_ledger = read_json(ledger_path)
                compact = raw_ledger.get("state", raw_ledger)
                await writer.amendment(stored, checkpoint, compact, safe=True,
                                       recovery_fingerprint=proof["fingerprint"])
            finally:
                await writer.aclose()
        asyncio.run(publish())
        # Publication invalidates the pre-write snapshot. A fresh bounded
        # verification context retains immutable identity reuse for the committed
        # state; the old context must never authorize post-mutation reuse.
        with StartupVerification() as committed_verification:
            validate_journal_resume(checkpoint, campaign, allow_halted=True)
            after = load_checkpoint(checkpoint)
            if not exact_equal(payload, after) or not strict_json_equal(ledger, load_ledger(ledger_path)):
                raise ValueError("recovery did not preserve exact checkpoint/accounting state")
            final = read_journal(campaign, include_audit=False)
            receipt = {"protocol": PROTOCOL, "recovery_fingerprint": proof["fingerprint"],
                       "authority": proof["authority"], "checkpoint_sha256": _reference(checkpoint)["sha256"],
                       "ledger_sha256": _reference(ledger_path)["sha256"],
                       "journal_sequence": final["sequence"], "journal_sha256": final["sha256"],
                       "slot_id": proof["slot_id"], "execution_id": proof["execution_id"],
                       "provider_calls": 0, "metadata_lookups": 0, "provider_replacement": False,
                       "replacement_allowance_changed": False, "checkpoint_state_unchanged": True,
                       "receipts_unchanged": True, "writer_metrics": dict(writer.metrics)}
            committed_verification.assert_fresh()
        _write_json_atomic(receipt_path, receipt)
        return receipt


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign")
    parser.add_argument("--authority", required=True)
    args = parser.parse_args()
    # The journal worker lazily imports training/SDK classes at publication.
    # Configure the same no-file logging boundary as the public CLI first.
    from ..cli import configure_logging
    configure_logging()
    print(json.dumps(recover(args.campaign, args.authority), indent=2))


if __name__ == "__main__":
    main()
