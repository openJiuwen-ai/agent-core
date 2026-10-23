"""Changed-member publication, bounded acknowledgements and scalar admission."""

import asyncio
import copy
import time

import pytest

from examples.adaptive_multi_agent_collab.infrastructure import persistence
from examples.adaptive_multi_agent_collab.infrastructure.incremental import (
    MAX_CHAIN_DEPTH, TrackedDict, TrackedList, snapshot_collection,
)
from examples.adaptive_multi_agent_collab.infrastructure.storage import EvidenceStore, StorageFailure
from examples.adaptive_multi_agent_collab.tests.test_failure_policy_resources import controller


def test_nested_changes_and_append_do_not_serialize_old_payloads(tmp_path):
    store = EvidenceStore(tmp_path)
    values = TrackedList([{"raw": "x" * 200_000, "nested": {"count": 1}}])
    first = snapshot_collection(store, values)
    before = store.metrics["serialized_bytes"]
    values.append({"new": 2})
    second = snapshot_collection(store, values)
    assert store.metrics["serialized_bytes"] - before < 4000
    assert store.resolve(first) == [{"raw": "x" * 200_000, "nested": {"count": 1}}]
    assert store.resolve(second) == values
    unchanged = store.metrics["serialized_bytes"]
    assert snapshot_collection(store, values) == second
    assert store.metrics["serialized_bytes"] == unchanged
    values[0]["nested"]["count"] = 3
    third = snapshot_collection(store, values)
    assert store.resolve(third) == values
    assert store.resolve(first)[0]["nested"]["count"] == 1


def test_collection_consolidation_and_independent_restore(tmp_path):
    store = EvidenceStore(tmp_path)
    values = TrackedDict({"old": {"list": [1]}})
    for i in range(MAX_CHAIN_DEPTH * 2 + 2):
        values[str(i)] = i
        ref = snapshot_collection(store, values)
    del values["old"]
    ref = snapshot_collection(store, values)
    result = store.resolve(ref)
    assert result == values
    result["0"] = "changed"
    assert store.resolve(ref)["0"] == 0
    assert copy.deepcopy(values) == values
    assert type(copy.deepcopy(values)) is dict
    assert all(state["depth"] <= MAX_CHAIN_DEPTH for state in values._snapshots.values())


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_changed_or_missing_collection_evidence_rejected(tmp_path, damage):
    store = EvidenceStore(tmp_path)
    ref = snapshot_collection(store, TrackedList([{"data": 1}]))
    path = tmp_path / ref["$collection"]["node"]["$evidence"]["path"]
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("{}")
    with pytest.raises(StorageFailure):
        store.resolve(ref)


def test_admission_does_not_copy_audit_frames_and_settlement_semantics_match():
    class NoCopy:
        def __deepcopy__(self, memo):
            raise AssertionError("admission must not copy audit-only payload")

    ledger = controller().ledger
    entry = {"dispatch_id": 0, "status": "completed", "charged": .01,
             "reserved": .02, "usage": {"accounting_complete": True,
             "raw_gateway_frames": NoCopy()}}
    ledger.entries.append(entry)
    assert ledger.uncertainty_summary()["reserved_slots"] == 0
    entry["usage"]["accounting_complete"] = False
    assert ledger.uncertainty_summary()["terminal_unresolved_ids"] == [0]
    entry["usage"].pop("raw_gateway_frames")
    ledger.accounting_reconciliations.append({"dispatch_id": 0, "usage": {
        "input_tokens": 1, "output_tokens": 2, "reference_cost": .01, "total_cost": .01}})
    assert ledger.accounting_complete(entry) == ledger.effective_usage(entry)["accounting_complete"] is True
    entry["billing_tariff"] = {"require_native_accounting": True}
    assert ledger.accounting_complete(entry) == ledger.effective_usage(entry)["accounting_complete"] is False


def test_changed_receipt_only_and_unknown_hold_survive_snapshot(tmp_path):
    ledger = controller().ledger
    ledger.entries = [
        {"dispatch_id": i, "status": "completed", "charged": .001,
         "reserved": .01, "usage": {"accounting_complete": True, "raw_gateway_frames": ["x" * 100_000]}}
        for i in range(3)]
    store = EvidenceStore(tmp_path)
    before = ledger.checkpoint_state_dict(store)
    size = store.metrics["serialized_bytes"]
    ledger.entries.append({"dispatch_id": 3, "status": "reserved", "charged": None, "reserved": .02})
    ledger.persist(ledger.entries[-1])
    after = ledger.checkpoint_state_dict(store)
    assert store.metrics["serialized_bytes"] - size < 10_000
    assert len(store.resolve(before)["entries"]) == 3
    restored = store.resolve(after)
    assert restored["entries"] == ledger.entries
    assert restored["entries"][-1]["charged"] is None
    ledger.entries[0]["usage"]["accounting_complete"] = False
    ledger.persist(ledger.entries[0])
    assert store.resolve(ledger.checkpoint_state_dict(store))["entries"][0]["usage"]["accounting_complete"] is False


def test_delayed_ack_delivery_is_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "PERSISTENCE_DEADLINE", .03)
    journal = persistence.DurableJournal([tmp_path], {"entries": []})

    async def exercise():
        future = journal.submit("progress", {"kind": "fixture", "payload": {}})
        await asyncio.sleep(.01)
        time.sleep(.05)  # Explicitly model delayed loop delivery, not slow disk.
        with pytest.raises(persistence.PersistenceFailure):
            await journal._acknowledge(future)
        with pytest.raises(persistence.PersistenceFailure):
            journal.submit("progress", {"kind": "must-not-dispatch"})
        with pytest.raises(persistence.PersistenceFailure):
            await journal.aclose()
    asyncio.run(exercise())
    assert not journal._thread.is_alive()
    assert persistence.read_journal(tmp_path)["boundary"] is None


def test_acknowledged_future_does_not_renew_or_expire_on_later_flush(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "PERSISTENCE_DEADLINE", .03)
    journal = persistence.DurableJournal([tmp_path], {"entries": []})
    async def exercise():
        await journal.progress("fixture", {"example_id": "query"})
        await asyncio.sleep(.05)
        await journal.flush()
        await journal.aclose()
    asyncio.run(exercise())
    assert journal.metrics["serialized_bytes"] > 0
    assert any(item["query_id"] == "query" for item in journal.operation_timings)


def test_tracked_recovery_slot_commits_owned_progress_and_never_replays(tmp_path):
    from examples.adaptive_multi_agent_collab.workflows.recovery import run_workflow_slot
    from examples.adaptive_multi_agent_collab.tests.test_workflow_recovery import trajectory

    store, slots, commits, executions = EvidenceStore(tmp_path), TrackedDict(), [], []

    async def execute(index, identity):
        executions.append(identity)
        assert store.resolve(commits[-1])["slot"]["executions"][index]["status"] == "started"
        return trajectory(cost=None, kind="provider-unavailable") if index == 0 else trajectory()

    async def run():
        kwargs = dict(slot_id="slot", condition="fixed", operation="selection", execute=execute,
                      allocate=lambda index: f"execution:{index}",
                      persist=lambda: commits.append(snapshot_collection(store, slots)),
                      assert_allowed=lambda: None)
        result, record = await run_workflow_slot(slots, **kwargs)
        assert result.completed and record["selected_index"] == 1
        restored = store.resolve(commits[-1])
        assert restored == slots and restored["slot"]["status"] == "complete"
        assert all(item["status"] == "terminal" for item in restored["slot"]["executions"])
        with pytest.raises(ValueError, match="already recorded"):
            await run_workflow_slot(TrackedDict(restored), **kwargs)

    asyncio.run(run())
    assert executions == ["execution:0", "execution:1"]


def test_failure_diagnostics_preserve_first_cause_without_certifying_completion(tmp_path, capsys):
    journal = persistence.DurableJournal([tmp_path], {"entries": []})
    original = OSError("injected durable-write failure")
    journal._record_failure(original)
    journal._record_failure(RuntimeError("later waiter failure"))
    assert journal._failure is original
    report = journal.failure_diagnostics()
    assert report["failure_type"] == "OSError"
    assert report["resume_safe"] is report["stage_complete"] is report["authoritative_accounting"] is False
    assert capsys.readouterr().err.count('"protocol":"writer_failure_diagnostics_v1"') == 1
    with pytest.raises(persistence.PersistenceFailure):
        asyncio.run(journal.aclose())
    assert not journal._thread.is_alive()
