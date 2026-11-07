"""Exact recovery guards; every file and ledger is a disposable offline fixture."""
from __future__ import annotations

import copy
import asyncio
from contextlib import contextmanager
import hashlib
import json

import numpy as np
import pytest
import torch

from examples.adaptive_multi_agent_collab.operations import publication_recovery as recovery


def payload():
    return {"request_boundary_pending": False, "alpha": {"row": torch.tensor([1., 2.])},
            "rng": np.array([42, 43]), "counter": 3,
            "hook_state": {"expenditure_ledger": {"entries": [{"dispatch_id": 0, "charged": None,
                "reserved": .1, "frames": ({"choices": []}, {"usage": {"input": 4}})}],
                "failure_policy": {"max_unresolved_requests": 8}}, "records": [{"id": "historic"}]}}


def fixture():
    parent = payload()
    patch = [{"path": ["hook_state", "expenditure_ledger", "failure_policy"],
              "value": {"max_unresolved_requests": 32}}]
    current = recovery.apply_patch_values(parent, patch)
    return parent, patch, current


def test_patch_shares_unchanged_tensors_history_and_preserves_parent():
    parent, patch, current = fixture()
    assert current["alpha"] is parent["alpha"]
    assert current["hook_state"]["records"] is parent["hook_state"]["records"]
    assert parent["hook_state"]["expenditure_ledger"]["failure_policy"]["max_unresolved_requests"] == 8
    ledger = json.loads(json.dumps(current["hook_state"]["expenditure_ledger"]))
    recovery.verify_patch(parent, current, patch, ledger)


@pytest.mark.parametrize("change", ["tensor", "rng", "counter", "receipt", "frame_order", "field", "pending"])
def test_recovery_rejects_every_nonapproved_coordinate(change):
    parent, patch, current = fixture()
    current = copy.deepcopy(current)
    if change == "tensor":
        current["alpha"]["row"][0] += 1
    elif change == "rng":
        current["rng"][0] += 1
    elif change == "counter":
        current["counter"] += 1
    elif change == "receipt":
        current["hook_state"]["expenditure_ledger"]["entries"][0]["reserved"] = .09
    elif change == "frame_order":
        entry = current["hook_state"]["expenditure_ledger"]["entries"][0]
        entry["frames"] = entry["frames"][::-1]
    elif change == "field":
        current["hook_state"]["records"][0]["id"] = "rewritten"
    else:
        current["request_boundary_pending"] = True
    with pytest.raises(ValueError, match="exact approved"):
        recovery.verify_patch(parent, current, patch, current["hook_state"]["expenditure_ledger"])


def test_checkpoint_cannot_overwrite_newer_durable_receipts():
    parent, patch, current = fixture()
    ledger = copy.deepcopy(current["hook_state"]["expenditure_ledger"])
    ledger["entries"].append({"dispatch_id": 1, "charged": None, "reserved": .2})
    with pytest.raises(ValueError, match="disagree"):
        recovery.verify_patch(parent, current, patch, ledger)


@pytest.mark.parametrize("change", [None, "parent", "checkpoint", "ledger", "sequence", "calls"])
def test_saved_failure_evidence_binds_exact_interrupted_bytes(tmp_path, change):
    parent = {"sha256": "a" * 64}
    state = {"checkpoint_sha256": "b" * 64, "source_checkpoint_sha256": "a" * 64,
             "verification_mode": "offline_transition_failure", "no_live_calls_during_transition": True,
             "actual_workflow_executions": 0}
    preserved = {"current_checkpoint_sha256": "b" * 64, "current_ledger_sha256": "c" * 64, "passed": True}
    outcome = {"status": "blocked_partial_transition_publication",
               "last_observed_journal_operation": "checkpoint-intent",
               "last_observed_journal_sequence": 78949, "new_provider_calls": 0,
               "new_metadata_lookups": 0, "transition_receipt_published": False}
    checkpoint, ledger = {"sha256": "b" * 64}, {"sha256": "c" * 64}
    if change == "parent": parent["sha256"] = "d" * 64
    elif change == "checkpoint": checkpoint["sha256"] = "d" * 64
    elif change == "ledger": ledger["sha256"] = "d" * 64
    elif change == "sequence": outcome["last_observed_journal_sequence"] = 78950
    elif change == "calls": outcome["new_provider_calls"] = 1
    for name, value in (("state_verification.json", state), ("preservation_verification.json", preserved),
                        ("transition_outcome.json", outcome)):
        (tmp_path / name).write_text(json.dumps(value))

    def verify():
        record = recovery.recorded_interruption(tmp_path / "plan.json", parent, 78949)
        recovery.verify_recorded_identity(record, checkpoint, ledger)
        assert len(record["reports"]) == 3

    if change is None:
        verify()
    else:
        with pytest.raises(ValueError, match="evidence"):
            verify()


def test_known_json_accounting_enum_is_canonical_without_core_coercion():
    from examples.adaptive_multi_agent_collab.schemas import AgentCallKind
    parent, patch, current = fixture()
    parent["hook_state"]["expenditure_ledger"]["entries"][0]["kind"] = AgentCallKind.INITIAL
    current = recovery.apply_patch_values(parent, patch)
    current = {**current, "hook_state": json.loads(json.dumps(current["hook_state"]))}
    recovery.verify_patch(parent, current, patch, current["hook_state"]["expenditure_ledger"])
    current["counter"] = 3.0
    with pytest.raises(ValueError, match="exact approved"):
        recovery.verify_patch(parent, current, patch, current["hook_state"]["expenditure_ledger"])
    current["counter"] = 3
    current["hook_state"]["expenditure_ledger"]["entries"][0]["kind"] = "role"
    with pytest.raises(ValueError, match="exact approved"):
        recovery.verify_patch(parent, current, patch, current["hook_state"]["expenditure_ledger"])


@pytest.mark.parametrize("change", ["sequence", "hash", "operation", "checkpoint", "safe", "boundary", "recovery"])
def test_tail_identity_cannot_authorize_intervening_or_ambiguous_work(tmp_path, change):
    checkpoint = tmp_path / "checkpoint.pt"
    tail = {"seq": 78949, "sha256": "hash", "operation": "checkpoint-intent",
            "data": {"checkpoint": str(checkpoint), "safe_requested": True, "recovery_fingerprint": "approved"}}
    journal = {"sequence": 78950, "sha256": "hash", "boundary": None}
    recovery.verify_tail(journal, tail, checkpoint, 78949, recovery_fingerprint="approved")
    if change == "sequence": journal["sequence"] += 1
    elif change == "hash": journal["sha256"] = "newer"
    elif change == "operation": tail["operation"] = "observation"
    elif change == "checkpoint": tail["data"]["checkpoint"] = str(tmp_path / "other.pt")
    elif change == "safe": tail["data"]["safe_requested"] = False
    elif change == "boundary": journal["boundary"] = {"checkpoint_sha256": "other"}
    else: tail["data"]["recovery_fingerprint"] = "different"
    with pytest.raises(ValueError, match="exact final"):
        recovery.verify_tail(journal, tail, checkpoint, 78949, recovery_fingerprint="approved")


def test_journal_tail_requires_complete_final_record(tmp_path):
    path = tmp_path / "journal.jsonl"
    record = {"seq": 78949, "operation": "checkpoint-intent"}
    path.write_bytes(b'{"padding":"' + b'x' * 9000 + b'"}\n' + json.dumps(record).encode() + b"\n")
    assert recovery.journal_tail(path) == record
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    with pytest.raises(ValueError, match="torn"):
        recovery.journal_tail(path)


def test_preservation_prefix_allows_append_but_not_rewrite(tmp_path):
    checkpoint, journal = tmp_path / "before.pt", tmp_path / "journal.jsonl"
    checkpoint.write_bytes(b"preserved checkpoint")
    journal.write_bytes(b"original immutable prefix\n")
    prefix = journal.read_bytes()
    manifest = {"files": [{"preserved_path": str(checkpoint),
                            "sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}],
                "journal_prefix": {"path": str(journal), "bytes": len(prefix),
                                   "sha256": hashlib.sha256(prefix).hexdigest()}}
    path = tmp_path / "PRESERVATION.json"
    path.write_text(json.dumps(manifest))
    journal.write_bytes(prefix + b"new approved publication\n")
    recovery.verify_preservation(path)
    journal.write_bytes(b"wrong" + journal.read_bytes()[5:])
    with pytest.raises(ValueError, match="prefix changed"):
        recovery.verify_preservation(path)


def test_bounded_preservation_copy_resumes_partial_and_rejects_collision(tmp_path):
    source, target = tmp_path / "source.pt", tmp_path / "before/source.pt"
    source.write_bytes(b"immutable" * 200)
    target.parent.mkdir()
    pending = target.with_name(target.name + ".pending")
    pending.write_bytes(source.read_bytes()[:91])
    record = recovery.bounded_copy(source, target)
    assert target.read_bytes() == source.read_bytes()
    assert record["metrics"]["copied_bytes"] == source.stat().st_size - 91
    assert recovery.bounded_copy(source, target)["metrics"]["copied_bytes"] == 0
    target.write_bytes(b"bad")
    with pytest.raises(ValueError, match="differs"):
        recovery.bounded_copy(source, target)


def test_preservation_deadline_retains_verified_partial_for_resume(tmp_path, monkeypatch):
    source, target = tmp_path / "source.pt", tmp_path / "before.pt"
    source.write_bytes(b"immutable")
    ticks = iter([0., 0., 0., 6.])
    monkeypatch.setattr(recovery.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ValueError, match="five-second"):
        recovery.bounded_copy(source, target)
    assert not target.exists()
    assert target.with_name(target.name + ".pending").read_bytes() == source.read_bytes()


@pytest.mark.parametrize("operation", ["ledger", "observation", "progress", "unsafe-boundary"])
def test_recovery_suffix_rejects_all_intervening_work(tmp_path, operation):
    proof = {"fingerprint": "approved", "expected_intent_checkpoint": str(tmp_path / "checkpoint.pt")}
    prefix = b"historical reviewed prefix\n"
    path = tmp_path / "accounting_progress.jsonl"
    data = {"operation": operation, "data": {}}
    path.write_bytes(prefix + json.dumps(data).encode() + b"\n")
    with pytest.raises(ValueError, match="intervened"):
        recovery.verify_recovery_suffix(tmp_path, proof, {"journal_prefix": {"bytes": len(prefix)}})


def test_recovery_suffix_allows_only_own_interrupted_publication_then_one_commit(tmp_path):
    proof = {"fingerprint": "approved", "expected_intent_checkpoint": str(tmp_path / "checkpoint.pt")}
    intent = {"operation": "checkpoint-intent", "data": {"recovery_fingerprint": "approved",
              "safe_requested": True, "checkpoint": proof["expected_intent_checkpoint"]}}
    commit = {"operation": "safe-boundary", "data": {}}
    path = tmp_path / "accounting_progress.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in [intent, intent, commit]) + "\n")
    recovery.verify_recovery_suffix(tmp_path, proof, {"journal_prefix": {"bytes": 0}})
    path.write_text(path.read_text() + json.dumps(intent) + "\n")
    with pytest.raises(ValueError, match="after the recovery commit"):
        recovery.verify_recovery_suffix(tmp_path, proof, {"journal_prefix": {"bytes": 0}})


@pytest.mark.parametrize("fault", [None, "ledger", "post_commit"])
def test_publication_recovery_commits_once_without_rewriting_parent_tensors(tmp_path, monkeypatch, fault):
    from examples.adaptive_multi_agent_collab.operations import experiment
    from examples.adaptive_multi_agent_collab.infrastructure import persistence
    from examples.adaptive_multi_agent_collab.infrastructure.persistence import DurableJournal, PersistenceFailure, read_journal, _json
    from examples.adaptive_multi_agent_collab.infrastructure.storage import load_checkpoint

    parent, patch, current = fixture()
    state = current["hook_state"]["expenditure_ledger"]
    parent_path = tmp_path / "parent.pt"
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save(parent, parent_path)
    torch.save(current, checkpoint)
    (tmp_path / "expenditure_ledger.json").write_text(json.dumps(state))
    (tmp_path / "manifest.json").write_text('{}')
    (tmp_path / "calibration").mkdir()
    (tmp_path / "calibration/transition_intent.json").write_text('{}')
    writer = DurableJournal([tmp_path], state)
    writer.submit("checkpoint-intent", {"checkpoint": str(checkpoint), "safe_requested": True})
    asyncio.run(writer.aclose())
    writer._thread.join(timeout=5)
    journal = read_journal(tmp_path)
    refs = lambda path: {"path": str(path), "sha256": recovery._sha(path)}
    proof = {"protocol": recovery.RECOVERY_PROTOCOL,
             "authority": {"path": "fixture", "sha256": "a" * 64},
             "authorization": {"path": "fixture", "sha256": "b" * 64},
             "parent_checkpoint": refs(parent_path), "interrupted_checkpoint": refs(checkpoint),
             "interrupted_ledger": refs(tmp_path / "expenditure_ledger.json"),
             "expected_sequence": journal["sequence"], "expected_previous": journal["sha256"],
             "expected_intent_sequence": journal["sequence"] - 1,
             "expected_intent_checkpoint": str(checkpoint),
             "expected_state_sha256": hashlib.sha256(_json(state)).hexdigest(),
             "unknown_dispatch_ids": [0]}
    proof["fingerprint"] = recovery._digest(proof)
    context = {"proof": proof, "ledger": state, "payload": current, "patch": patch,
               "journal": journal, "lineage": {"fixture": "lineage"}, "plan": {"fingerprint": "c" * 64}}
    monkeypatch.setattr(recovery, "inspect_recovery", lambda *args, **kwargs: context)

    @contextmanager
    def locked(_directory):
        yield lambda: True

    monkeypatch.setattr(experiment, "_campaign_lock", locked)
    before_parent = parent_path.read_bytes()
    if fault is not None:
        if fault == "ledger":
            original = persistence._atomic

            def fail_ledger(path, *args, **kwargs):
                if path.name == "expenditure_ledger.json":
                    raise OSError("fixture interrupted ledger publication")
                return original(path, *args, **kwargs)

            monkeypatch.setattr(persistence, "_atomic", fail_ledger)
        else:
            original = DurableJournal._write

            def fail_ack(self, operation, value):
                result = original(self, operation, value)
                if operation == "checkpoint-amendment":
                    raise OSError("fixture lost acknowledgement after durable commit")
                return result

            monkeypatch.setattr(DurableJournal, "_write", fail_ack)
        with pytest.raises(PersistenceFailure):
            recovery.recover_interrupted(tmp_path / "manifest.json", "unused", "unused", "unused", "unused")
        assert not (tmp_path / "calibration/recovery/receipt.json").exists()
        context["journal"] = read_journal(tmp_path)
        if fault == "ledger":
            assert context["journal"]["boundary"] is None
            with pytest.raises(ValueError, match="unfinished"):
                experiment.validate_journal_resume(checkpoint, tmp_path)
            monkeypatch.setattr(persistence, "_atomic", original)
        else:
            assert context["journal"]["boundary"] is not None
            monkeypatch.setattr(DurableJournal, "_write", original)
    result = recovery.recover_interrupted(tmp_path / "manifest.json", "unused", "unused", "unused", "unused")
    assert result["receipt_count"] == 1 and result["generation_calls"] == 0
    assert recovery.exact_equal(load_checkpoint(checkpoint), current)
    assert parent_path.read_bytes() == before_parent
    stored = torch.load(checkpoint, weights_only=False, map_location="cpu")
    assert stored["storage_protocol"] == "checkpoint_amendment_v1"
    assert "alpha" not in stored and "rng" not in stored
    assert all(item["path"][0] == "hook_state" for item in stored["patches"])
    committed = (tmp_path / "accounting_progress.jsonl").read_bytes()
    assert recovery.recover_interrupted(tmp_path / "manifest.json", "unused", "unused", "unused", "unused") == result
    assert (tmp_path / "accounting_progress.jsonl").read_bytes() == committed
