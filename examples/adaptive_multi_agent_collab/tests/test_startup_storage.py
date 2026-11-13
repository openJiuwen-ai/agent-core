"""Pinned startup reuse preserves exact evidence and fail-closed writer admission."""

import asyncio
import copy
import hashlib
import os

import pytest
import torch

from examples.adaptive_multi_agent_collab.infrastructure.persistence import DurableJournal, PersistenceFailure, read_journal
from examples.adaptive_multi_agent_collab.infrastructure.storage import (
    COMPACT_PROTOCOL, EvidenceStore, StartupVerification, StorageFailure,
    canonical_bytes, canonical_sha256, current_startup, file_sha256, freeze_json, ledger_envelope,
    load_checkpoint, load_ledger, mutable_json, read_json, startup_scope,
    strict_json_equal, strict_json_fingerprint, validate_json, verified_reference,
)
from examples.adaptive_multi_agent_collab.tests.test_compact_storage import ledger
from examples.adaptive_multi_agent_collab.tests.test_persistence import wired


def compact_fixture(tmp_path):
    store = EvidenceStore(tmp_path)
    state = ledger(2)
    compact = store.compact_ledger(state)
    path = tmp_path / "expenditure_ledger.json"
    path.write_bytes(canonical_bytes(ledger_envelope(compact)))
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save({"storage_protocol": COMPACT_PROTOCOL, "weight": torch.ones(2),
                "hook_state": {"expenditure_ledger": compact}}, checkpoint)
    return state, checkpoint, path, compact


def test_checkpoint_and_ledger_share_verified_objects_not_mutable_trainable_state(tmp_path):
    state, checkpoint, path, _ = compact_fixture(tmp_path)
    with StartupVerification() as context:
        first = load_checkpoint(checkpoint)
        second = load_checkpoint(checkpoint)
        saved = load_ledger(path)
        assert first["hook_state"]["expenditure_ledger"] == state == saved
        assert context.metrics["checkpoint_decodes"] == 1
        assert context.metrics["objects_verified"] == 2
        first["weight"].add_(10)
        first["hook_state"]["new"] = "local"
        assert torch.equal(second["weight"], torch.ones(2))
        assert "new" not in second["hook_state"]
        receipt = saved["entries"][0]
        assert receipt is second["hook_state"]["expenditure_ledger"]["entries"][0]
        with pytest.raises(TypeError, match="read-only"):
            receipt["usage"]["raw_gateway_frames"].append({"changed": True})
        detached = mutable_json(receipt)
        detached["usage"]["raw_gateway_frames"].append({"changed": True})
        assert detached != receipt
        assert copy.deepcopy(receipt) is receipt
        context.assert_fresh()


@pytest.mark.parametrize("changed", ["missing", "bytes", "same_size_and_mtime"])
def test_freshness_checks_content_not_only_filename_metadata(tmp_path, changed):
    _, _, path, compact = compact_fixture(tmp_path)
    with StartupVerification() as context:
        saved = load_ledger(path)
        object_path = tmp_path / compact["entries"][0]["$evidence"]["path"]
        before = object_path.stat()
        raw = object_path.read_bytes()
        if changed == "missing":
            object_path.unlink()
        elif changed == "bytes":
            object_path.write_bytes(raw + b" ")
        else:
            object_path.write_bytes(raw.replace(b'"charged":null', b'"charged":true'))
            os.utime(object_path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert saved["entries"][0]["charged"] is None
        with pytest.raises((StorageFailure, FileNotFoundError)):
            context.assert_fresh()
        assert current_startup() is None
        with pytest.raises(StorageFailure):
            load_ledger(path)


def test_changed_approval_invalidates_complete_context(tmp_path):
    path = tmp_path / "authorization.json"
    path.write_text('{"approved":true}')
    with StartupVerification() as context:
        assert read_json(path)["approved"] is True
        first_hash = file_sha256(path)
        path.write_text('{"approved":false}')
        assert file_sha256(path) == first_hash  # A pinned view, not perpetual permission.
        with pytest.raises(StorageFailure, match="changed"):
            context.assert_fresh()
        assert read_json(path)["approved"] is False


def test_missing_journal_appearance_requires_new_snapshot(tmp_path):
    with StartupVerification() as context:
        assert read_journal(tmp_path) is None
        (tmp_path / "accounting_progress.jsonl").write_text("{}\n")
        with pytest.raises(StorageFailure, match="absent"):
            context.assert_fresh()


def test_journal_reused_and_first_new_commit_ends_scope(tmp_path):
    ctl, trainer = wired(tmp_path)
    asyncio.run(ctl.ledger.durable_journal.aclose())
    with StartupVerification() as context:
        first = read_journal(tmp_path)
        second = read_journal(tmp_path)
        assert first is second
        assert context.metrics["journal_replays"] == 1
        journal = DurableJournal([tmp_path], first["state"])
        assert context.metrics["journal_replays"] == 1
        assert context.metrics["freshness_passes"] == 1
        journal.submit("progress", {"event": "startup-test"}).result(timeout=5)
        assert current_startup() is None
        asyncio.run(journal.aclose())
    assert read_journal(tmp_path)["boundary"] is None
    assert trainer.state.workflow_attempt_counter == 0


@pytest.mark.parametrize("tail", [b'{"partial":', b"\n"])
def test_changed_journal_cannot_bind_cached_boundary(tmp_path, tail):
    ctl, _ = wired(tmp_path)
    asyncio.run(ctl.ledger.durable_journal.aclose())
    with StartupVerification():
        saved = read_journal(tmp_path)
        with (tmp_path / "accounting_progress.jsonl").open("ab") as stream:
            stream.write(tail)
        with pytest.raises(StorageFailure, match="changed"):
            DurableJournal([tmp_path], saved["state"])
    with pytest.raises(PersistenceFailure):
        read_journal(tmp_path)


def test_readonly_exact_json_and_mutation_tokens(tmp_path):
    store = EvidenceStore(tmp_path)
    reference = store.put({"frames": [{"cost": 1.0, "ids": ["a", "b"]}]})
    with StartupVerification():
        first, second = store.resolve(reference), store.resolve(reference)
        assert verified_reference(first) == verified_reference(second)
        assert strict_json_equal(first, second)
        assert strict_json_equal(first, {"frames": ({"cost": 1.0, "ids": ("a", "b")},)})
        assert not strict_json_equal(first, {"frames": [{"cost": 1, "ids": ["a", "b"]}]})
        validate_json(first)
        mutable = mutable_json(first)
        token = strict_json_fingerprint(mutable)
        mutable["frames"][0]["ids"].reverse()
        assert strict_json_fingerprint(mutable) != token
        assert not strict_json_equal(first, mutable)
    for invalid in (float("nan"), float("inf"), object()):
        with pytest.raises((ValueError, TypeError)):
            validate_json({"cost": invalid})
    assert not strict_json_equal(0.0, -0.0)
    assert not strict_json_equal(1, True)
    assert not strict_json_equal(1.0, 1)


def test_cache_budget_declines_reuse_without_weakening_integrity(tmp_path):
    default = StartupVerification()
    assert default.max_cached_bytes == 8 * 1024 * 1024 * 1024
    assert default.max_objects == 32768
    _, checkpoint, path, _ = compact_fixture(tmp_path)
    with StartupVerification(max_cached_bytes=1, max_objects=1) as context:
        assert load_checkpoint(checkpoint)["hook_state"]["expenditure_ledger"] == load_ledger(path)
        assert load_checkpoint(checkpoint)["weight"].tolist() == [1.0, 1.0]
        assert context.metrics["checkpoint_decodes"] == 2
        context.assert_fresh()


def test_nested_sync_async_scope_finishes_without_leaking(tmp_path):
    path = tmp_path / "value.json"
    path.write_text('{"value":1}')
    contexts = []

    @startup_scope
    def inner():
        contexts.append(current_startup())
        return read_json(path)

    @startup_scope
    async def outer():
        contexts.append(current_startup())
        first = inner()
        second = inner()
        assert first is second
        current_startup().finish()
        assert current_startup() is None

    asyncio.run(outer())
    assert contexts[0] is contexts[1] is contexts[2]
    assert current_startup() is None


def test_canonical_hash_cache_preserves_historical_bytes_and_detects_mutation(tmp_path):
    store = EvidenceStore(tmp_path)
    reference = store.put({"raw": [{"text": "λ", "cost": 1.0}]})
    with StartupVerification() as context:
        value = {"entries": [store.resolve(reference)], "tuple": ("a", "b")}
        import json
        expected = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                            allow_nan=False).encode()).hexdigest()
        assert canonical_sha256(value) == expected
        bytes_once = context.metrics["canonical_hash_bytes"]
        assert canonical_sha256(copy.deepcopy(value)) == expected
        assert context.metrics["canonical_hash_bytes"] == bytes_once
        value["tuple"] = ["a", "b"]
        assert canonical_sha256(value) == expected
        assert context.metrics["canonical_hash_bytes"] == bytes_once
        value["tuple"].reverse()
        assert canonical_sha256(value) != expected
        assert context.metrics["canonical_hash_bytes"] > bytes_once


def test_legacy_checkpoint_audit_is_shared_but_checkpoint_controls_are_independent(tmp_path):
    path = tmp_path / "legacy.pt"
    payload = {"hook_state": {"expenditure_ledger": ledger(2)},
               "trainer_state": {"attempts": 3}, "weight": torch.ones(2)}
    torch.save(payload, path)
    with StartupVerification() as context:
        first, second = load_checkpoint(path), load_checkpoint(path)
        assert context.metrics["checkpoint_decodes"] == 1
        receipt = first["hook_state"]["expenditure_ledger"]["entries"][0]
        assert receipt is second["hook_state"]["expenditure_ledger"]["entries"][0]
        assert verified_reference(receipt) is not None
        assert canonical_sha256(receipt) == canonical_sha256(payload["hook_state"]["expenditure_ledger"]["entries"][0])
        first["trainer_state"]["attempts"] = 9
        first["weight"].add_(4)
        assert second["trainer_state"]["attempts"] == 3
        assert torch.equal(second["weight"], torch.ones(2))


def test_full_journal_cache_is_charged_even_without_serialized_size():
    value = freeze_json({"state": ledger(1200), "progress": []})
    with StartupVerification(max_cached_bytes=100_000) as context:
        context.remember(("journal", "fixture"), value)
        assert context.cached(("journal", "fixture")) is None
        assert context.metrics["cache_budget_declines"] == 1
        assert not context._retained_ids
        assert context._cache_bytes == 0


def test_verified_cross_representation_equality_reuses_only_exact_proof(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.infrastructure import storage

    state, checkpoint, path, _ = compact_fixture(tmp_path)
    legacy = tmp_path / "legacy.pt"
    torch.save({"hook_state": {"expenditure_ledger": state}}, legacy)
    with StartupVerification() as context:
        compact = load_ledger(path)["entries"][0]
        prior = load_checkpoint(legacy)["hook_state"]["expenditure_ledger"]["entries"][0]
        assert verified_reference(compact) != verified_reference(prior)
        assert strict_json_equal(compact, prior)
        hits = context.metrics["cache_hits"]
        original = storage._strict_json_equal_contents
        def reject_repeat(left, right):
            if left is compact and right is prior:
                pytest.fail("unchanged verified pair was expanded again")
            return original(left, right)
        monkeypatch.setattr(storage, "_strict_json_equal_contents", reject_repeat)
        assert strict_json_equal(compact, prior)
        assert context.metrics["cache_hits"] == hits + 1
        changed = mutable_json(prior)
        changed["usage"]["raw_gateway_frames"].reverse()
        changed["charged"] = 0.01
        assert not strict_json_equal(compact, changed)


@pytest.mark.parametrize("number", ["NaN", "1e1000"])
def test_hash_verified_object_cannot_smuggle_nonfinite_json_into_validation(tmp_path, number):
    raw = ('{"cost":' + number + "}").encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "object.json"
    path.write_bytes(raw)
    reference = {"$evidence": {"protocol": "immutable_json_v1", "path": path.name,
                               "sha256": digest, "bytes": len(raw)}}
    with StartupVerification():
        with pytest.raises(StorageFailure, match="invalid immutable"):
            EvidenceStore(tmp_path).resolve(reference)


def test_freeze_allocates_one_unverified_stamp_per_independent_tree(monkeypatch):
    from examples.adaptive_multi_agent_collab.infrastructure import storage

    allocations = []
    original = storage._unverified_stamp

    def counted_stamp():
        stamp = original()
        allocations.append(stamp)
        return stamp

    monkeypatch.setattr(storage, "_unverified_stamp", counted_stamp)
    source = {"rows": [{"values": [index, {"v": index}]} for index in range(256)],
              "pair": ("a", "b")}
    frozen = freeze_json(source, root=True)
    assert len(allocations) == 1
    pending, nodes = [frozen], []
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            nodes.append(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            nodes.append(item)
            pending.extend(item)
    assert len(nodes) == 771
    assert all(item._evidence_stamp is allocations[0] for item in nodes)
    assert allocations[0].dirty is True
    assert all(verified_reference(item) is None for item in nodes)
    assert canonical_bytes(frozen) == canonical_bytes(source)
    assert copy.deepcopy(frozen) is frozen
    with pytest.raises(TypeError, match="read-only"):
        frozen["rows"][0]["values"].append("changed")
    separate = freeze_json(source)
    assert len(allocations) == 2
    assert separate._evidence_stamp is not frozen._evidence_stamp
    mutable = mutable_json(frozen)
    mutable["rows"][0]["values"][1]["v"] = -1
    assert frozen["rows"][0]["values"][1]["v"] == 0


def test_shared_unverified_stamp_preserves_verified_nested_identity(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.infrastructure import storage

    store = EvidenceStore(tmp_path)
    reference = store.put({"usage": [1, 2], "charge": None})
    with StartupVerification():
        verified = store.resolve(reference)
        expected_reference = verified_reference(verified)
        assert expected_reference is not None
        allocations = []
        original = storage._unverified_stamp

        def counted_stamp():
            stamp = original()
            allocations.append(stamp)
            return stamp

        monkeypatch.setattr(storage, "_unverified_stamp", counted_stamp)
        frozen = freeze_json({"same": [verified, verified], "unverified": [{"nested": []}]})
        assert len(allocations) == 1
        assert frozen["same"][0] is frozen["same"][1] is verified
        assert verified_reference(frozen["same"][0]) == expected_reference
        assert verified_reference(frozen) is None
        assert verified._evidence_stamp is not allocations[0]
        assert frozen["unverified"][0]._evidence_stamp is allocations[0]
        assert strict_json_equal(frozen["same"][0], {"usage": (1, 2), "charge": None})


def test_wrapper_slots_preserve_copy_mutation_and_inline_provenance(tmp_path):
    store = EvidenceStore(tmp_path)
    reference = store.put({"nested": [{"cost": None}]})
    value = store.resolve(reference)
    cloned = copy.deepcopy(value)
    for item in (value, value["nested"], value["nested"][0], value._evidence_stamp):
        assert not hasattr(item, "__dict__")
    cloned["nested"][0]["cost"] = 0.01
    assert cloned._evidence_stamp.dirty and value._evidence_stamp.dirty is False
    assert value["nested"][0]["cost"] is None
    frozen = freeze_json(value)
    frozen._verified_json_identity = ("checkpoint:fixture", "a" * 64, "hook_state.receipt")
    assert not hasattr(frozen, "__dict__")
    assert verified_reference(frozen) == frozen._verified_json_identity
    assert copy.deepcopy(frozen) is frozen
    with StartupVerification(max_cached_bytes=100_000) as context:
        context.remember("slotted", frozen)
        assert context.cached("slotted") is frozen
        assert context.metrics["cache_retained_peak_bytes"] > 0


def test_minimal_journal_reuses_bounded_verified_state_without_audit_retention(tmp_path):
    import json

    ctl, trainer = wired(tmp_path)
    binding = {"body": "x" * 65536, "index": 15}

    async def write():
        for index in range(16):
            await ctl.ledger.record_progress("fixture", {"binding": {**binding, "index": index}})
        trainer._checkpoint()
        await trainer.durable_journal.aclose()

    asyncio.run(write())
    full = read_journal(tmp_path)
    assert len(full["progress"]) == full["progress_count"] == 16
    assert set(full["objects"]) == set(full["object_ids"])
    with StartupVerification(max_cached_bytes=512_000) as context:
        minimal = read_journal(tmp_path, include_audit=False)
        assert "objects" not in minimal and "progress" not in minimal
        assert strict_json_equal(minimal, {key: value for key, value in full.items()
                                          if key not in {"objects", "progress"}})
        assert read_journal(tmp_path, include_audit=False) is minimal
        assert context.metrics["journal_replays"] == 1
        assert context.metrics.get("cache_budget_declines", 0) == 0
        journal = DurableJournal([tmp_path], minimal["state"])
        assert context.metrics["journal_replays"] == 1
        assert journal._objects == set(full["object_ids"])
        # Writing the same immutable object uses its already verified identity;
        # retaining every historical object payload is unnecessary.
        journal.submit("progress", {"kind": "fixture", "payload": {"binding": binding}}).result(timeout=5)
        asyncio.run(journal.aclose())
    last = json.loads((tmp_path / "accounting_progress.jsonl").read_bytes().splitlines()[-1])
    assert last["objects"] == {}
    audit = read_journal(tmp_path)
    assert audit["progress"][:16] == full["progress"]
    assert audit["progress"][-1]["payload"]["binding"] == binding


@pytest.mark.parametrize("include_audit", [False, True])
@pytest.mark.parametrize("failure", ["unsafe", "torn"])
def test_minimal_and_audit_journal_views_reject_unsafe_or_torn_resume(tmp_path, include_audit, failure):
    ctl, trainer = wired(tmp_path)

    async def write():
        await ctl.ledger.record_progress("unfinished", {"attempt": 1})
        await trainer.durable_journal.aclose()

    asyncio.run(write())
    if failure == "torn":
        with (tmp_path / "accounting_progress.jsonl").open("ab") as stream:
            stream.write(b'{"partial":')
        with StartupVerification(), pytest.raises(PersistenceFailure, match="unfinished"):
            read_journal(tmp_path, include_audit=include_audit)
    else:
        with StartupVerification():
            saved = read_journal(tmp_path, include_audit=include_audit)
            assert saved["boundary"] is None
            with pytest.raises(PersistenceFailure, match="unfinished workflow"):
                DurableJournal([tmp_path], saved["state"])
