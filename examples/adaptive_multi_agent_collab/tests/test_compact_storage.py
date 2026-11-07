"""Compact evidence publication, integrity, locking and crash boundaries."""

import asyncio
import copy
import hashlib
import json
import threading
from dataclasses import asdict, dataclass

import pytest
import torch

from examples.adaptive_multi_agent_collab.infrastructure.persistence import (
    DurableJournal, PersistenceFailure, _apply, _json, read_journal,
)
from examples.adaptive_multi_agent_collab.infrastructure.storage import (
    COMPACT_PROTOCOL, EvidenceDict, EvidenceList, EvidenceStore, StorageFailure, amendment_payload, canonical_bytes, compact_references,
    file_sha256, ledger_envelope, load_checkpoint, load_ledger,
)


def ledger(count=1):
    return {"protocol": "fixture", "stage": "development", "halted": None,
            "entries": [{"dispatch_id": i, "charged": None, "reservation": 0.01,
                         "usage": {"raw_gateway_frames": [{"choices": [], "usage": {"cost": None}}]},
                         "recovery_events": []} for i in range(count)]}


def test_reference_roundtrip_keeps_fields_order_and_unknowns(tmp_path):
    state = ledger(3)
    store = EvidenceStore(tmp_path)
    compact = store.compact_ledger(state)
    path = tmp_path / "expenditure_ledger.json"
    path.write_bytes(canonical_bytes(ledger_envelope(compact)))
    restored = load_ledger(path)
    assert restored == state
    assert len(restored["entries"]) == 3
    assert all(item["charged"] is None for item in restored["entries"])
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save({"storage_protocol": COMPACT_PROTOCOL,
                "hook_state": {"expenditure_ledger": compact}, "weight": torch.ones(3)}, checkpoint)
    assert load_checkpoint(checkpoint)["hook_state"]["expenditure_ledger"] == state


@pytest.mark.parametrize("kind", ["missing", "modified", "sequence"])
def test_evidence_tampering_rejected(tmp_path, kind):
    store = EvidenceStore(tmp_path)
    ref = store.put(ledger()["entries"][0])
    path = tmp_path / ref["$evidence"]["path"]
    if kind == "missing":
        path.unlink()
    elif kind == "modified":
        path.write_text("{}")
    else:
        value = json.loads(path.read_text())
        value["usage"]["raw_gateway_frames"].append({"different": True})
        path.write_bytes(canonical_bytes(value))
    with pytest.raises(StorageFailure):
        store.resolve(ref)


def test_restart_reuses_verified_evidence_without_serializing_history(tmp_path):
    store = EvidenceStore(tmp_path)
    value = ledger()["entries"][0]
    ref = store.put(value)
    loaded = copy.deepcopy(store.resolve(ref))
    restarted = EvidenceStore(tmp_path)
    assert restarted.put(loaded, identity=(0, 0)) == ref
    assert restarted.metrics["serialized_bytes"] == 0
    assert restarted.metrics["written_bytes"] == 0
    loaded["usage"]["raw_gateway_frames"].append({"usage": {"cost": 0.003}})
    changed = restarted.put(loaded, identity=(0, 1))
    assert changed != ref
    assert restarted.metrics["serialized_bytes"] > 0
    assert restarted.resolve(changed)["usage"]["raw_gateway_frames"][-1]["usage"]["cost"] == 0.003


def test_nested_value_does_not_reuse_parent_reference(tmp_path):
    store = EvidenceStore(tmp_path)
    reference = store.put({"nested": {"x": 1}})
    value = store.resolve(reference)
    child = store.put(value["nested"])
    assert child != reference
    assert store.resolve(child) == {"x": 1}


def test_raw_evidence_reference_lookalikes_remain_literal(tmp_path):
    store = EvidenceStore(tmp_path)
    value = {"frames": [{"$evidence": {"path": "user-supplied", "bytes": 123}},
                         {"$literal_evidence": ["user", "content"]},
                         {"$tensor": {"path": "user-supplied", "bytes": 123}},
                         {"$collection": {"node": "user content"}}]}
    assert store.resolve(store.put(value)) == value


def test_restored_selected_checkpoint_reuses_immutable_evidence_without_touching_tensors(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = store.put(ledger()["entries"][0])
    tensor = torch.ones(3)
    selected = {"alpha": {"weight": tensor}, "hook_state": {"entries": [store.resolve(ref)]}}
    compact = compact_references(copy.deepcopy(selected), tmp_path)
    assert compact["hook_state"] == {"entries": [ref]}
    assert torch.equal(compact["alpha"]["weight"], tensor)
    assert store.resolve(compact["hook_state"])["entries"] == ledger()["entries"]
    selected["hook_state"]["entries"][0]["charged"] = 0.002
    changed = compact_references(selected, tmp_path)
    assert changed["hook_state"]["entries"][0]["charged"] == 0.002


def test_dataclass_rendering_of_verified_records_remains_supported(tmp_path):
    @dataclass
    class Record:
        receipt: dict

    store = EvidenceStore(tmp_path)
    receipt = store.resolve(store.put(ledger()["entries"][0]))
    rendered = asdict(Record(receipt))
    assert rendered == {"receipt": ledger()["entries"][0]}
    assert rendered["receipt"]._evidence_stamp.dirty is True
    assert store.resolve(store.put(rendered)) == rendered


def test_hook_restore_retains_verified_dataclass_sources_without_rendering(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.learning import evaluation
    from examples.adaptive_multi_agent_collab.learning.calibration import PilotObservation
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment

    _, hooks, *_ = _build_offline_experiment()
    hooks.pilot_observations = [PilotObservation(hooks.pilot_plan.ordered_example_ids[0],
                                                (1,), (0.1,), (0.2,), True)]
    store = EvidenceStore(tmp_path)
    compact = hooks.checkpoint_state_dict(store)
    restored = store.resolve(compact)
    hooks.load_state_dict(restored)
    original = evaluation.asdict
    def guarded(value):
        if isinstance(value, PilotObservation):
            pytest.fail("verified completed observation should not be rendered again")
        return original(value)
    monkeypatch.setattr(evaluation, "asdict", guarded)
    fresh = EvidenceStore(tmp_path)
    assert hooks.checkpoint_state_dict(fresh)["pilot_observations"] == compact["pilot_observations"]
    # A restored ledger starts an independent receipt-reference index. Its
    # empty node is storage metadata; no completed observation is re-rendered.
    from examples.adaptive_multi_agent_collab.infrastructure.storage import canonical_bytes
    empty_index = {"protocol": "incremental_collection_v1", "kind": "list", "length": 0,
                   "parent": None, "members": [], "removed": []}
    assert fresh.metrics["serialized_bytes"] == len(canonical_bytes(empty_index))


def test_checkpoint_pickle_contains_values_without_verified_wrapper_provenance(tmp_path):
    store = EvidenceStore(tmp_path)
    verified = store.resolve(store.put({"nested": [{"receipt": None}], "order": [3, 2, 1]}))
    model_state = torch.nn.Linear(2, 1).state_dict()
    payload = {"metadata": verified, "model": model_state, "tuple": (1, 2)}
    path = tmp_path / "plain_checkpoint.pt"
    torch.save(payload, path)
    restored = torch.load(path, weights_only=False)
    assert type(restored["metadata"]) is dict
    assert type(restored["metadata"]["nested"]) is list
    assert type(restored["metadata"]["nested"][0]) is dict
    assert restored["metadata"] == verified
    assert not hasattr(restored["metadata"], "_evidence_stamp")
    assert type(restored["model"]) is type(model_state)
    assert restored["model"]._metadata == model_state._metadata
    assert restored["tuple"] == (1, 2)
    for key, value in model_state.items():
        torch.testing.assert_close(restored["model"][key], value, rtol=0, atol=0)
    verified["nested"][0]["receipt"] = 0.1
    assert verified._evidence_stamp.dirty is True


def test_running_compatibility_record_is_never_cached_as_immutable(tmp_path):
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment

    _, hooks, *_ = _build_offline_experiment()
    record = {"status": "running", "requests": []}
    hooks.compatibility_records = [record]
    store = EvidenceStore(tmp_path)
    first = hooks.checkpoint_state_dict(store)
    record["requests"].append({"status": "completed"})
    second = hooks.checkpoint_state_dict(store)
    assert first["compatibility_records"][0]["requests"] == []
    assert second["compatibility_records"][0]["requests"] == [{"status": "completed"}]


def test_nested_reference_identity_survives_outer_reference_and_invalidates_parent(tmp_path):
    store = EvidenceStore(tmp_path)
    event = store.put({"event": "terminal", "nested": {"status": "completed"}})
    policy = store.put({"events": [event], "limit": 32})
    loaded = store.resolve(policy)
    fresh = EvidenceStore(tmp_path)
    assert fresh.put(loaded["events"][0]) == event
    assert fresh.put(loaded) == policy
    assert fresh.metrics["serialized_bytes"] == 0
    cloned = copy.deepcopy(loaded)
    cloned["events"][0]["nested"]["status"] = "failed"
    assert cloned._evidence_stamp.dirty is True
    assert cloned["events"][0]._evidence_stamp.dirty is True
    assert loaded._evidence_stamp.dirty is False
    assert fresh.resolve(fresh.put(cloned))["events"][0]["nested"]["status"] == "failed"


def test_production_ledger_persist_appends_only_new_policy_events(tmp_path):
    from examples.adaptive_multi_agent_collab.tests.test_persistence import wired

    async def run():
        ctl, trainer = wired(tmp_path)
        journal = trainer.durable_journal
        ctl.ledger._policy_event("logical-start", logical_id="fixture-0", backend="fixture", kind="role")
        await ctl.ledger.flush_durable()
        first = read_journal(tmp_path)
        retained = copy.deepcopy(first["state"]["failure_policy"]["events"])
        count = len(retained)
        start_bytes = journal.metrics["wal_bytes"]
        ctl.ledger._policy_event("logical-start", logical_id="fixture-1", backend="fixture", kind="role")
        await ctl.ledger.flush_durable()
        record = json.loads((tmp_path / "accounting_progress.jsonl").read_text().splitlines()[-1])
        assert record["operation"] == "ledger-delta-v1"
        assert "failure_policy" not in record["data"]["header"]
        assert record["data"]["entries"] == []
        assert record["data"]["appends"][0]["expected_length"] == count
        assert len(record["data"]["appends"][0]["values"]) == 1
        assert read_journal(tmp_path)["state"]["failure_policy"]["events"][:count] == retained
        assert journal.metrics["wal_bytes"] - start_bytes < 2000
        await journal.aclose()
    asyncio.run(run())


def test_identity_reuse_requires_same_frozen_object(tmp_path):
    store = EvidenceStore(tmp_path)
    store.put({"x": 1}, identity="immutable")
    with pytest.raises(StorageFailure, match="identity reused"):
        store.put({"x": 1}, identity="immutable")


def test_missing_modified_parent_and_overlapping_patches_rejected(tmp_path):
    parent = tmp_path / "parent.pt"
    torch.save({"weight": torch.ones(3), "config": {"limit": 8}}, parent)
    target = tmp_path / "checkpoint.pt"
    value = amendment_payload(parent, file_sha256(parent),
                              [{"path": ["config", "limit"], "value": 32}], target=target)
    torch.save(value, target)
    assert load_checkpoint(target)["config"] == {"limit": 32}
    assert torch.equal(load_checkpoint(target)["weight"], torch.ones(3))
    value["patches"].append({"path": ["config"], "value": {}})
    torch.save(value, target)
    with pytest.raises(StorageFailure, match="overlapping"):
        load_checkpoint(target)
    value["patches"].pop()
    torch.save(value, target)
    parent.write_bytes(b"modified")
    with pytest.raises(StorageFailure, match="parent changed"):
        load_checkpoint(target)


def test_incremental_suffixes_verify_exact_history_length():
    state = ledger()
    state["policy"] = {"events": [{"id": 0}]}
    _apply(state, "ledger", {"appends": [{"path": ["policy", "events"],
                                          "expected_length": 1, "values": [{"id": 1}]}]})
    assert state["policy"]["events"] == [{"id": 0}, {"id": 1}]
    with pytest.raises(PersistenceFailure, match="noncontiguous"):
        _apply(state, "ledger", {"appends": [{"path": ["policy", "events"],
                                              "expected_length": 1, "values": []}]})


def test_compact_publication_commits_consistent_snapshot_and_rejects_second_writer(tmp_path):
    async def run():
        state = ledger(10)
        store = EvidenceStore(tmp_path)
        compact = store.compact_ledger(state)
        journal = DurableJournal([tmp_path], state)
        with pytest.raises(PersistenceFailure, match="another accounting writer"):
            DurableJournal([tmp_path], state)
        payload = {"weight": torch.ones(3), "hook_state": {"expenditure_ledger": compact}}
        path = tmp_path / "checkpoint.pt"
        await journal.checkpoint(payload, path, safe=True)
        assert read_journal(tmp_path)["state"] == state
        assert read_journal(tmp_path)["boundary"]["ledger_storage_sha256"]
        assert load_ledger(tmp_path / "expenditure_ledger.json") == state
        assert load_checkpoint(path)["hook_state"]["expenditure_ledger"] == state
        await journal.aclose()
        journal._thread.join(timeout=1)
        next_writer = DurableJournal([tmp_path], state)
        await next_writer.aclose()
        next_writer._thread.join(timeout=1)
    asyncio.run(run())


def test_unpublished_intent_requires_exact_audited_recovery_token(tmp_path):
    async def run():
        state = ledger()
        path = tmp_path / "checkpoint.pt"
        journal = DurableJournal([tmp_path], state)
        journal.submit("checkpoint-intent", {"checkpoint": str(path), "safe_requested": True})
        await journal.aclose()
        journal._thread.join(timeout=1)
        saved = read_journal(tmp_path)
        token = {"protocol": "audited_publication_recovery_v1",
                 "expected_sequence": saved["sequence"], "expected_previous": saved["sha256"],
                 "expected_intent_sequence": saved["tail_record"]["sequence"],
                 "expected_intent_checkpoint": str(path),
                 "expected_state_sha256": hashlib.sha256(_json(state)).hexdigest(),
                 **{key: "a" * 64 for key in ["authorization_sha256", "plan_fingerprint",
                                             "parent_checkpoint_sha256", "interrupted_checkpoint_sha256",
                                             "validation_sha256"]}}
        with pytest.raises(PersistenceFailure, match="unfinished"):
            DurableJournal([tmp_path], state)
        bad = {**token, "expected_previous": "0" * 64}
        with pytest.raises(PersistenceFailure, match="recovery token"):
            DurableJournal([tmp_path], state, recovery_token=bad)
        recovered = DurableJournal([tmp_path], state, recovery_token=token)
        await recovered.aclose()
        recovered._thread.join(timeout=1)
        assert read_journal(tmp_path)["boundary"] is None
    asyncio.run(run())


def test_failed_checkpoint_write_cannot_publish_safe_boundary(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.learning.training import CheckpointManager

    async def run():
        state = ledger()
        journal = DurableJournal([tmp_path], state)
        def fail(*args, **kwargs):
            raise OSError("injected fsync failure")
        monkeypatch.setattr(CheckpointManager, "write_payload", fail)
        with pytest.raises(PersistenceFailure):
            await journal.checkpoint({"hook_state": {"expenditure_ledger": state}},
                                     tmp_path / "checkpoint.pt", safe=True)
        assert read_journal(tmp_path)["boundary"] is None
        assert read_journal(tmp_path)["tail_record"]["operation"] == "checkpoint-intent"
        with pytest.raises(PersistenceFailure):
            await journal.aclose()
        journal._thread.join(timeout=1)
    asyncio.run(run())


def test_ordered_preparation_returns_result_and_preserves_failure_guard(tmp_path):
    async def run():
        state = ledger()
        journal = DurableJournal([tmp_path], state)
        compact = await journal.prepare_checkpoint_state(lambda: EvidenceStore(tmp_path).compact_ledger(state))
        assert compact["entries"][0]["$evidence"]["bytes"] > 0
        await journal.aclose()
        journal._thread.join(timeout=1)
    asyncio.run(run())


@pytest.mark.parametrize("asynchronous", [False, True])
def test_trainer_rejects_intervening_accounting_after_snapshot(tmp_path, monkeypatch, asynchronous):
    from examples.adaptive_multi_agent_collab.tests.test_persistence import wired

    controller, trainer = wired(tmp_path)
    checkpoint = tmp_path / "checkpoint.pt"
    before = checkpoint.read_bytes()
    capture = trainer.checkpoint_payload

    def changed_after_capture(**kwargs):
        result = capture(**kwargs)
        controller.ledger.stage = "development"
        controller.ledger.persist()
        return result

    monkeypatch.setattr(trainer, "checkpoint_payload", changed_after_capture)

    async def run():
        with pytest.raises(PersistenceFailure):
            if asynchronous:
                await trainer.checkpoint_async()
            else:
                trainer._checkpoint()
        assert checkpoint.read_bytes() == before
        assert trainer._request_boundary_pending is True
        rebuilt = read_journal(tmp_path)
        assert rebuilt["state"]["stage"] == "development"
        assert rebuilt["boundary"] is None
        assert rebuilt["tail_record"]["operation"] == "ledger-delta-v1"
        assert rebuilt["state"]["entries"] == []
        with pytest.raises(PersistenceFailure):
            await trainer.durable_journal.aclose()

    asyncio.run(run())


def test_snapshot_artifact_rejects_intervening_progress_without_overwrite(tmp_path):
    async def run():
        journal = DurableJournal([tmp_path], ledger())
        value, snapshot = await journal.prepare_checkpoint_state(lambda: {"snapshot": True}, bind_publication=True)
        output = tmp_path / "report.json"
        output.write_text("original")
        await journal.progress("workflow-start", {"attempt_id": 1})
        with pytest.raises(PersistenceFailure):
            await journal.artifact(output, value, expected_snapshot=snapshot)
        assert output.read_text() == "original"
        assert read_journal(tmp_path)["progress"][-1]["payload"]["attempt_id"] == 1
        with pytest.raises(PersistenceFailure):
            await journal.aclose()

    asyncio.run(run())


def test_failed_acknowledgement_keeps_writer_ownership_until_physical_exit(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.infrastructure import persistence
    from examples.adaptive_multi_agent_collab.learning.training import CheckpointManager

    async def run():
        state = ledger()
        journal = DurableJournal([tmp_path], state)
        entered, release = threading.Event(), threading.Event()
        original = CheckpointManager.write_payload
        def stalled(*args, **kwargs):
            entered.set()
            release.wait(1)
            return original(*args, **kwargs)
        monkeypatch.setattr(CheckpointManager, "write_payload", stalled)
        monkeypatch.setattr(persistence, "PERSISTENCE_DEADLINE", 0.03)
        with pytest.raises(PersistenceFailure, match="acknowledgement deadline"):
            await journal.checkpoint({"hook_state": {"expenditure_ledger": state}},
                                     tmp_path / "checkpoint.pt", safe=True)
        assert entered.is_set()
        with pytest.raises(PersistenceFailure, match="another accounting writer"):
            DurableJournal([tmp_path], state)
        with pytest.raises(PersistenceFailure):
            await journal.aclose()
        release.set()
        journal._thread.join(timeout=1)
        assert not journal._thread.is_alive()
        assert not (tmp_path / "checkpoint.pt").exists()
        assert read_journal(tmp_path)["boundary"] is None
        with pytest.raises(PersistenceFailure, match="unfinished"):
            DurableJournal([tmp_path], state)
    asyncio.run(run())


def test_object_migration_is_resumable_without_rewriting_committed_objects(tmp_path):
    state = ledger(3)
    store = EvidenceStore(tmp_path)
    first = store.put(state["entries"][0])
    path = tmp_path / first["$evidence"]["path"]
    original_stat = path.stat()
    path.with_name(path.name + ".pending-interrupted").write_bytes(b"partial")
    resumed = EvidenceStore(tmp_path)
    assert resumed.compact_ledger(state)["entries"][0] == first
    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert resumed.metrics["objects_written"] == 2
    assert path.with_name(path.name + ".pending-interrupted").read_bytes() == b"partial"


def test_legacy_checkpoint_reader_does_not_walk_or_rewrite_accounting(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.infrastructure import storage

    path = tmp_path / "legacy.pt"
    torch.save({"schema_version": 12, "hook_state": {"expenditure_ledger": ledger()}}, path)
    before = file_sha256(path)
    monkeypatch.setattr(storage, "resolve_references", lambda *args: pytest.fail("legacy must not be traversed"))
    assert load_checkpoint(path)["hook_state"]["expenditure_ledger"] == ledger()
    assert file_sha256(path) == before


@pytest.mark.parametrize("corruption", [None, "value", "order", "mutable"])
def test_amended_scientific_json_projection_normal_prebinding_restore(tmp_path, corruption):
    from examples.adaptive_multi_agent_collab.operations.experiment import wire_durable_journal
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment

    async def run():
        trainer, hooks, models, _, proposer = _build_offline_experiment()
        trainer.hooks = hooks
        for module, optimizer in ((trainer.alpha, trainer.alpha_optimizer),
                                  (trainer.weighting, trainer.weighting_optimizer)):
            next(module.parameters()).square().sum().backward()
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
        parent = tmp_path / "parent.pt"
        trainer.save_checkpoint(parent)
        original = load_checkpoint(parent)
        scientific = copy.deepcopy(original["scientific_configuration"])
        if corruption == "value":
            scientific["immutable"]["config"]["reproducibility"]["seed"] += 1
        elif corruption == "order":
            scientific["immutable"]["numerical_runtime"]["construction_order"].reverse()
        elif corruption == "mutable":
            scientific["mutable_continuation"]["training.max_outer_cycles"] -= 1
        store = EvidenceStore(tmp_path)
        reference = store.put(scientific)
        path = tmp_path / "checkpoint.pt"
        envelope = amendment_payload(parent, file_sha256(parent),
            [{"path": ["scientific_configuration"], "value": reference},
             *[{"path": [key], "value": store.put(original[key])}
               for key in ("config", "declared_config")],
             {"path": ["hook_state", "stage_authorizations"],
              "value": store.put(original["hook_state"]["stage_authorizations"])}], target=path)
        ledger_state = hooks.expenditure_ledger.state_dict()
        journal = DurableJournal([tmp_path], ledger_state)
        await journal.amendment(envelope, path, ledger_state, safe=True, recovery_fingerprint="a" * 64)
        await journal.aclose()

        fresh, fresh_hooks, fresh_models, _, fresh_proposer = _build_offline_experiment()
        # Match production order: authoritative ledger prebinding, journal
        # binding, then normal CheckpointManager.load and hook restoration.
        fresh_hooks.expenditure_ledger.load_state_dict(load_ledger(tmp_path / "expenditure_ledger.json"))
        wire_durable_journal(fresh, fresh_hooks, tmp_path)
        before = {key: value.detach().clone() for key, value in fresh.alpha.state_dict().items()}
        try:
            if corruption is None:
                restored = fresh.resume_from_checkpoint(path, hooks=fresh_hooks)
                assert restored["scientific_configuration"]["fingerprint"] == scientific["fingerprint"]
                assert fresh_hooks.expenditure_ledger.state_dict() == ledger_state
                assert fresh.alpha_optimizer.state_dict()["state"]
                assert fresh.weighting_optimizer.state_dict()["state"]
                assert fresh_hooks.stage_authorizations == original["hook_state"]["stage_authorizations"]
                # Runtime authorization controls are independently mutable;
                # audit provenance may be shared only by read-only evidence.
                fresh_hooks.stage_authorizations["independent-test-control"] = {}
                assert "independent-test-control" not in restored["hook_state"]["stage_authorizations"]
                del fresh_hooks.stage_authorizations["independent-test-control"]
                for key, value in fresh.alpha.state_dict().items():
                    torch.testing.assert_close(value, original["alpha"][key], rtol=0, atol=0)
                fresh.checkpoint_path = path
                await fresh.checkpoint_async()
                ordinary = torch.load(path, weights_only=False)

                def no_wrapper(value):
                    assert not isinstance(value, (EvidenceDict, EvidenceList))
                    if isinstance(value, dict):
                        for nested in value.values():
                            no_wrapper(nested)
                    elif isinstance(value, (list, tuple)):
                        for nested in value:
                            no_wrapper(nested)

                no_wrapper(ordinary)
                resolved = load_checkpoint(path)
                assert resolved["alpha"]._metadata == original["alpha"]._metadata
                assert resolved["weighting"]._metadata == original["weighting"]._metadata
                reloaded = fresh.resume_from_checkpoint(path, hooks=fresh_hooks)
                assert reloaded["scientific_configuration"]["fingerprint"] == scientific["fingerprint"]
                assert fresh_hooks.expenditure_ledger.state_dict() == ledger_state
                for key, value in fresh.alpha.state_dict().items():
                    torch.testing.assert_close(value, original["alpha"][key], rtol=0, atol=0)
            else:
                with pytest.raises(ValueError, match="fingerprint|scientific configuration|declared configuration"):
                    fresh.resume_from_checkpoint(path, hooks=fresh_hooks)
                for key, value in fresh.alpha.state_dict().items():
                    torch.testing.assert_close(value, before[key], rtol=0, atol=0)
                assert not fresh.alpha_optimizer.state_dict()["state"]
            assert all(not model.calls for model in [*models, *fresh_models])
            assert proposer.calls == fresh_proposer.calls == 0
        finally:
            await fresh.durable_journal.aclose()

    asyncio.run(run())


def test_replicated_ledgers_share_verified_evidence_without_copying_objects(tmp_path):
    async def run():
        first, second = tmp_path / "test", tmp_path / "campaign"
        first.mkdir()
        second.mkdir()
        state = ledger(4)
        journal = DurableJournal([first, second], state)
        compact = journal.evidence_store.compact_ledger(state)
        await journal.checkpoint({"hook_state": {"expenditure_ledger": compact}},
                                 first / "checkpoint.pt", safe=True)
        assert load_ledger(first / "expenditure_ledger.json") == state
        assert load_ledger(second / "expenditure_ledger.json") == state
        assert read_journal(second)["state"] == state
        assert not (second / ".evidence").exists()
        await journal.aclose()
        journal._thread.join(timeout=1)
    asyncio.run(run())
