"""Incremental numerical/history publication using the maintained storage path."""

import asyncio
import copy
from dataclasses import asdict
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.infrastructure.storage import (
    COMPONENT_PROTOCOL, EvidenceStore, StartupVerification, StorageFailure,
    load_checkpoint,
)
from examples.adaptive_multi_agent_collab.learning.training import CheckpointManager, _numerical_step
from examples.adaptive_multi_agent_collab.operations.experiment import wire_durable_journal, validate_journal_resume
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment
from examples.adaptive_multi_agent_collab.tests.test_training_schedule import config_for, make_trainer


def make_components(tmp_path):
    trainer = make_trainer(config_for())
    trainer.alpha_optimizer = torch.optim.Adam(trainer.alpha.parameters(), lr=.01)
    trainer.weighting_optimizer = torch.optim.Adam(trainer.weighting.parameters(), lr=.01)
    store = EvidenceStore(tmp_path)
    trainer.durable_journal = SimpleNamespace(evidence_store=store)
    return trainer, store


def publish(trainer, path):
    payload = trainer.checkpoint_payload(compact_hook={})
    CheckpointManager.write_payload(path, payload)
    return payload


def test_validation_append_does_not_copy_or_serialize_numerical_state_or_old_records(tmp_path):
    trainer, store = make_components(tmp_path)
    trainer.alpha.register_buffer("representative_state", torch.zeros(100_000))
    trainer.weighting.register_buffer("representative_state", torch.ones(100_000))
    trainer.state.phase_records = [{"index": i, "evidence": "x" * 1000} for i in range(250)]
    first = publish(trainer, tmp_path / "checkpoint.pt")
    old = dict(store.metrics)
    trainer.state.phase_records.append({"index": 250, "evidence": "new"})
    second = publish(trainer, tmp_path / "checkpoint.pt")
    assert second["storage_protocol"] == COMPONENT_PROTOCOL
    for name in ("alpha", "weighting", "alpha_optimizer", "weighting_optimizer"):
        assert second[name] == first[name]
    assert store.metrics["tensor_copied_bytes"] == old["tensor_copied_bytes"]
    assert store.metrics["tensor_serialized_bytes"] == old["tensor_serialized_bytes"]
    assert store.metrics["serialized_bytes"] - old["serialized_bytes"] < 4000
    expected = asdict(trainer.state)
    expected["prompt_cache"] = trainer.prompt_optimizer.state_dict()
    assert load_checkpoint(tmp_path / "checkpoint.pt")["trainer_state"] == expected


def test_actual_adam_update_republishes_only_changed_components_and_owns_tensors(tmp_path):
    trainer, store = make_components(tmp_path)
    first = publish(trainer, tmp_path / "before.pt")
    original = copy.deepcopy(trainer.alpha.state_dict())
    loss = (trainer.weighting.value - 1).square()
    _numerical_step(trainer.weighting_optimizer, loss, None)
    second = publish(trainer, tmp_path / "after.pt")
    assert first["alpha"] == second["alpha"]
    assert first["alpha_optimizer"] == second["alpha_optimizer"]
    assert first["weighting"] != second["weighting"]
    assert first["weighting_optimizer"] != second["weighting_optimizer"]
    restored = load_checkpoint(tmp_path / "after.pt")
    assert torch.equal(restored["alpha"]["value"], original["value"])
    assert torch.equal(restored["weighting"]["value"], trainer.weighting.value)
    with StartupVerification():
        another = load_checkpoint(tmp_path / "after.pt")
        another["weighting"]["value"].detach().add_(10)
        again = load_checkpoint(tmp_path / "after.pt")
        assert torch.equal(again["weighting"]["value"], trainer.weighting.value)
    # Mutating live parameters cannot change already durable snapshots.
    with torch.no_grad():
        trainer.weighting.value.add_(5)
    assert not torch.equal(load_checkpoint(tmp_path / "after.pt")["weighting"]["value"], trainer.weighting.value)


@pytest.mark.parametrize("mode", ["missing", "modified"])
def test_invalid_component_rejected_before_model_optimizer_rng_mutation(tmp_path, mode):
    trainer, _ = make_components(tmp_path)
    payload = publish(trainer, tmp_path / "checkpoint.pt")
    target = tmp_path / payload["weighting"]["$tensor"]["path"]
    if mode == "missing":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b"corrupt")
    other = make_trainer(config_for())
    params = copy.deepcopy(other.alpha.state_dict())
    rng = torch.get_rng_state().clone()
    with pytest.raises(StorageFailure, match="numerical component"):
        other.resume_from_checkpoint(tmp_path / "checkpoint.pt")
    assert torch.equal(other.alpha.value, params["value"])
    assert torch.equal(torch.get_rng_state(), rng)
    assert not other.alpha_optimizer.state


def test_mutated_nested_progress_and_best_snapshot_are_not_stale(tmp_path):
    trainer, _ = make_components(tmp_path)
    trainer.state.phase_progress = {"records": [{"status": "before"}]}
    initial = publish(trainer, tmp_path / "checkpoint.pt")
    trainer.state.phase_progress["records"][0]["status"] = "after"
    updated = publish(trainer, tmp_path / "checkpoint.pt")
    assert initial["trainer_state"]["phase_progress"] != updated["trainer_state"]["phase_progress"]
    assert load_checkpoint(tmp_path / "checkpoint.pt")["trainer_state"]["phase_progress"]["records"][0]["status"] == "after"


def test_best_selection_restoration_tracks_actual_model_adam_and_hook_mutations(tmp_path):
    trainer, store = make_components(tmp_path)
    owner = trainer.durable_journal
    restored_hooks = []
    trainer.hooks = SimpleNamespace(state_dict=lambda: {
        "record": {"status": "selected"},
        "safety_record": {"status": "approved"},
        "termination_record": {"status": "selected"},
    },
                                    restore_selected_state=restored_hooks.append)
    trainer.durable_journal = None
    trainer.best_system = trainer._joint_state(SimpleNamespace(score=1., to_dict=lambda: {"score": 1.}))
    trainer.durable_journal = owner
    initial = publish(trainer, tmp_path / "checkpoint.pt")
    _numerical_step(trainer.weighting_optimizer, (trainer.weighting.value - 1).square(), None)
    changed = publish(trainer, tmp_path / "checkpoint.pt")
    assert changed["best_system"]["weighting"] == initial["best_system"]["weighting"]
    trainer.best_system["hook_state"]["record"]["status"] = "selected-amended"
    trainer._restore_best()
    final = publish(trainer, tmp_path / "checkpoint.pt")
    resolved = load_checkpoint(tmp_path / "checkpoint.pt")
    assert resolved["weighting"]["value"].item() == 0.
    assert not resolved["weighting_optimizer"]["state"]
    assert final["weighting"] != changed["weighting"]
    assert final["best_system"]["hook_state"] != initial["best_system"]["hook_state"]
    assert restored_hooks[0]["record"]["status"] == "selected-amended"
    assert store.metrics["tensor_objects_written"] > 4
    # Production startup freezes referenced best-joint hook state. Its verified
    # calibration/safety fields must not be reassigned by legacy audit freezing.
    with StartupVerification():
        startup = load_checkpoint(tmp_path / "checkpoint.pt")
        assert startup["best_system"]["hook_state"]["safety_record"]["status"] == "approved"
        assert startup["best_system"]["hook_state"]["termination_record"]["status"] == "selected"


def test_tensor_guard_prevents_reference_commit_on_sticky_writer_failure(tmp_path):
    trainer, store = make_components(tmp_path)
    stopped = False
    original = store.put_tensor

    def publish_then_stop(value, **kwargs):
        nonlocal stopped
        reference = original(value, **kwargs)
        stopped = True
        return reference

    def gate():
        if stopped:
            raise StorageFailure("writer stopped")

    store.put_tensor = publish_then_stop
    store.publication_guard = gate
    with pytest.raises(StorageFailure, match="writer stopped"):
        publish(trainer, tmp_path / "checkpoint.pt")
    assert not (tmp_path / "checkpoint.pt").exists()
    assert len(list((tmp_path / ".evidence").glob("*/*.pt"))) == 1


@pytest.mark.parametrize("consolidate", [False, True])
def test_failed_collection_node_does_not_commit_removal_index(tmp_path, consolidate):
    from examples.adaptive_multi_agent_collab.infrastructure.incremental import (
        COLLECTION_PROTOCOL, MAX_CHAIN_DEPTH, TrackedDict, snapshot_collection,
    )
    store = EvidenceStore(tmp_path)
    source = TrackedDict({"removed": {"value": 1}, "kept": {"value": 2}})
    first = snapshot_collection(store, source)
    previous = next(iter(source._snapshots.values()))
    if consolidate:
        previous["depth"] = MAX_CHAIN_DEPTH
    del source["removed"]
    source["kept"]["value"] = 3
    original = store.put

    def fail_node(value, **kwargs):
        if isinstance(value, dict) and value.get("protocol") == COLLECTION_PROTOCOL:
            raise OSError("injected collection-node fsync failure")
        return original(value, **kwargs)

    store.put = fail_node
    with pytest.raises(OSError, match="fsync"):
        snapshot_collection(store, source)
    assert "removed" in previous["members"]
    assert previous["reference"] == first
    store.put = original
    retried = snapshot_collection(store, source)
    assert store.resolve(retried) == {"kept": {"value": 3}}
    assert store.resolve(first) == {"removed": {"value": 1}, "kept": {"value": 2}}


def test_tracked_json_state_normalizes_tuple_values_without_changing_numerical_rng(tmp_path):
    from examples.adaptive_multi_agent_collab.infrastructure.incremental import TrackedDict

    trainer, _ = make_components(tmp_path)
    trainer.state.phase_records.append({"records": ({"examples": ({"prediction": "yes"},)},)})
    assert isinstance(trainer.state.phase_records[0]["records"], list)
    assert isinstance(trainer.state.phase_records[0]["records"][0]["examples"], list)
    owner = TrackedDict({("key", "identity"): (1, 2)})
    assert list(owner) == [("key", "identity")]
    assert owner[("key", "identity")] == [1, 2]
    payload = publish(trainer, tmp_path / "checkpoint.pt")
    assert isinstance(payload["python_random_state"], tuple)
    with StartupVerification():
        restored = load_checkpoint(tmp_path / "checkpoint.pt")
    assert restored["trainer_state"]["phase_records"] == trainer.state.phase_records
    assert restored["python_random_state"] == payload["python_random_state"]


def test_prompt_record_projection_is_current_without_reconverting_cached_history(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.tests.test_training_schedule import Hooks

    trainer, _ = make_components(tmp_path)
    hooks = Hooks()
    hooks.bind(trainer)
    record = hooks._record("promptval", .5)
    original = type(record).to_dict
    projected = []

    def project(value):
        projected.append(value)
        return original(value)

    monkeypatch.setattr(type(record), "to_dict", project)

    async def evaluate(_collection, _plan):
        return record

    asyncio.run(trainer.prompt_optimizer.begin_phase(trainer.gamma, record.plan, "condition", evaluate))
    assert len(projected) == 1
    publish(trainer, tmp_path / "checkpoint.pt")
    publish(trainer, tmp_path / "checkpoint.pt")
    assert len(projected) == 1
    assert trainer.state.prompt_cache["condition"] == "condition"
    with StartupVerification():
        restored = load_checkpoint(tmp_path / "checkpoint.pt")
    assert restored["trainer_state"]["prompt_cache"] == trainer.state.prompt_cache
    trainer.prompt_optimizer.load_state_dict({})
    publish(trainer, tmp_path / "checkpoint.pt")
    assert trainer.state.prompt_cache == {"condition": None, "plan": None, "cache": {}}


def test_real_hooks_writer_validation_and_update_restart(tmp_path):
    async def run():
        trainer, hooks, models, _, _ = _build_offline_experiment()
        trainer.hooks = hooks
        trainer.checkpoint_path = tmp_path / "checkpoint.pt"
        wire_durable_journal(trainer, hooks, tmp_path, allow_empty_initialization=True)
        try:
            await hooks.validate("routing")
            await trainer.checkpoint_async()
            before = dict(trainer.durable_journal.evidence_store.metrics)
            # A matched completed validation is reused; publication must still
            # advance the durable descriptor with no numerical copying.
            calls = sum(len(model.calls) for model in models)
            await hooks.validate("routing")
            await trainer.checkpoint_async()
            assert sum(len(model.calls) for model in models) == calls
            assert trainer.durable_journal.evidence_store.metrics["tensor_copied_bytes"] == before["tensor_copied_bytes"]
            parameter = next(trainer.weighting.parameters()).reshape(-1)[0]
            _numerical_step(trainer.weighting_optimizer, (parameter - 1).square(), 1.)
            await trainer.checkpoint_async()
            saved = load_checkpoint(trainer.checkpoint_path)
            assert saved["weighting_optimizer"]["state"]
            assert not saved["alpha_optimizer"]["state"]
            validate_journal_resume(trainer.checkpoint_path, tmp_path)
        finally:
            await trainer.durable_journal.aclose()
        wire_durable_journal(trainer, hooks, tmp_path)
        try:
            await trainer.checkpoint_async()
            validate_journal_resume(trainer.checkpoint_path, tmp_path)
            assert sum(len(model.calls) for model in models) == calls
        finally:
            await trainer.durable_journal.aclose()
    asyncio.run(run())
