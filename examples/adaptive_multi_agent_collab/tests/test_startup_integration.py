"""Actual hook/trainer/journal startup, synthetic large history and atomic failure."""
from __future__ import annotations

import asyncio
import copy
import json
import random
import resource
import sys
import time
from dataclasses import asdict

import numpy as np
import pytest
import torch

from examples.adaptive_multi_agent_collab.operations.experiment import validate_journal_resume, wire_durable_journal
from examples.adaptive_multi_agent_collab.infrastructure.storage import StartupVerification, load_checkpoint, load_ledger
from examples.adaptive_multi_agent_collab.tests.test_bootstrap_boundary import assert_optimizer_equal
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment
from examples.adaptive_multi_agent_collab.tests.test_loss_recording import weighting_sessions
from examples.adaptive_multi_agent_collab.tests.test_receipt_restore import receipt_state
from examples.adaptive_multi_agent_collab.learning.training import update_weighting


def _history(hooks, count, raw_text_bytes=256):
    base = receipt_state()["entries"][0]
    entries = []
    for index in range(count):
        entry = copy.deepcopy(base)
        entry["dispatch_id"] = index
        entry["request_hash"] = f"synthetic-history-{index}"
        entry["usage"]["generation_id"] = f"synthetic-{index}"
        entry["usage"]["raw_gateway_frames"] = [{"id": f"synthetic-{index}", "content": "x" * raw_text_bytes}]
        entries.append(entry)
    state = hooks.expenditure_ledger.state_dict()
    state["entries"] = entries
    hooks.expenditure_ledger.load_state_dict(state)


def _step(trainer):
    parameter = next(trainer.weighting.parameters()).reshape(-1)[0]
    diagnostic = {}
    value = update_weighting(trainer.weighting, trainer.weighting_optimizer,
                             weighting_sessions(parameter), "A", loss_record=diagnostic)
    assert value == diagnostic["loss"] and diagnostic["status"] == "updated"
    trainer.state.updates["weighting"] += 1
    return diagnostic


def _equal_models(left, right):
    assert left.keys() == right.keys()
    assert all(torch.equal(value, right[name]) for name, value in left.items())


@pytest.mark.parametrize("raw_text_bytes", [pytest.param(256, id="compact"), pytest.param(65536, id="large")])
def test_large_history_actual_startup_validation_once_update_publication_and_restart(
    tmp_path, monkeypatch, raw_text_bytes
):
    async def run():
        initial, hooks, models, _, proposer = _build_offline_experiment()
        initial.hooks = hooks
        initial.checkpoint_path = tmp_path / "checkpoint.pt"
        with torch.no_grad():
            next(initial.weighting.parameters()).reshape(-1)[0].fill_(.2)
        _step(initial)  # Inherited nonzero optimizer moments, not an untrained checkpoint.
        _history(hooks, 4096, raw_text_bytes)  # Larger record count than the authoritative 2008-receipt campaign.
        diagnostics = {"synthetic_receipts": 4096, "raw_text_bytes_per_receipt": raw_text_bytes,
                       "raw_text_bytes_total": 4096 * raw_text_bytes}
        started = time.perf_counter()
        wire_durable_journal(initial, hooks, tmp_path, allow_empty_initialization=True)
        try:
            await initial.checkpoint_async()
            diagnostics["fixture_publication"] = {
                "seconds": time.perf_counter() - started,
                "writer": dict(initial.durable_journal.metrics),
                "evidence": dict(initial.durable_journal.evidence_store.metrics),
            }
        finally:
            await initial.durable_journal.aclose()
        expected_alpha = copy.deepcopy(initial.alpha.state_dict())
        expected_weighting = copy.deepcopy(initial.weighting.state_dict())
        expected_optimizer = copy.deepcopy(initial.weighting_optimizer.state_dict())
        restored, fresh_hooks, fresh_models, _, fresh_proposer = _build_offline_experiment()
        validations = []
        original_validate = fresh_hooks.validate_state
        def observed_validate(state, **kwargs):
            validations.append(1)
            return original_validate(state, **kwargs)
        monkeypatch.setattr(fresh_hooks, "validate_state", observed_validate)
        started = time.perf_counter()
        with StartupVerification() as startup:
            durable = load_ledger(tmp_path / "expenditure_ledger.json")
            fresh_hooks.expenditure_ledger.load_state_dict(durable)
            validate_journal_resume(initial.checkpoint_path, tmp_path)
            restored.resume_from_checkpoint(initial.checkpoint_path, hooks=fresh_hooks)
            assert validations == [1]
            _equal_models(expected_alpha, restored.alpha.state_dict())
            _equal_models(expected_weighting, restored.weighting.state_dict())
            assert_optimizer_equal(expected_optimizer, restored.weighting_optimizer.state_dict())
            assert restored.gamma.to_dict() == initial.gamma.to_dict()
            assert asdict(restored.state) == asdict(initial.state)
            wire_durable_journal(restored, fresh_hooks, tmp_path)
            assert startup.metrics["checkpoint_decodes"] == 1
            assert startup.metrics["journal_replays"] == 1
            assert len(fresh_hooks.expenditure_ledger.entries) == 4096
            assert all(not model.calls for model in fresh_models)
            diagnostics["startup"] = {"seconds": time.perf_counter() - started, **startup.metrics}
        restored.checkpoint_path = initial.checkpoint_path
        started = time.perf_counter()
        try:
            # Actual mocked workflow plus numerical update, after verified startup.
            result = await fresh_hooks.validate("routing")
            assert len(result.examples) == 2 and len(fresh_models[0].calls) == 4
            _step(restored)
            _equal_models(expected_alpha, restored.alpha.state_dict())
            assert any(not torch.equal(value, restored.weighting.state_dict()[name])
                       for name, value in expected_weighting.items())
            await restored.checkpoint_async()
            # No hidden first-save expansion/write of the historical receipts.
            assert restored.durable_journal.evidence_store.metrics["objects_written"] <= 6
            diagnostics["mock_workflow_update_first_publication"] = {
                "seconds": time.perf_counter() - started,
                "writer": dict(restored.durable_journal.metrics),
                "evidence": dict(restored.durable_journal.evidence_store.metrics),
            }
        finally:
            await restored.durable_journal.aclose()
        final, final_hooks, final_models, _, final_proposer = _build_offline_experiment()
        started = time.perf_counter()
        with StartupVerification() as startup:
            final_hooks.expenditure_ledger.load_state_dict(load_ledger(tmp_path / "expenditure_ledger.json"))
            validate_journal_resume(initial.checkpoint_path, tmp_path)
            final.resume_from_checkpoint(initial.checkpoint_path, hooks=final_hooks)
            wire_durable_journal(final, final_hooks, tmp_path)
            assert startup.metrics["journal_replays"] == 1
            assert startup.metrics["checkpoint_decodes"] == 1
            diagnostics["restart"] = {"seconds": time.perf_counter() - started, **startup.metrics}
        try:
            _equal_models(restored.weighting.state_dict(), final.weighting.state_dict())
            assert_optimizer_equal(restored.weighting_optimizer.state_dict(), final.weighting_optimizer.state_dict())
            assert final.state.updates["weighting"] == 2
            assert len(final_hooks.expenditure_ledger.entries) == 4096
            assert all(not model.calls for model in models + final_models)
            assert proposer.calls == fresh_proposer.calls == final_proposer.calls == 0
        finally:
            await final.durable_journal.aclose()
        # Process high-water RSS includes fixture construction and imported SDKs;
        # it is not an incremental allocator measure or a cold-filesystem claim.
        diagnostics["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
            1 if sys.platform == "darwin" else 1024
        )
        (tmp_path / "startup_metrics.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    asyncio.run(run())


def test_failed_hook_restore_leaves_numerical_state_rng_and_counters_unchanged(tmp_path):
    trainer, hooks, _, _, _ = _build_offline_experiment()
    trainer.hooks = hooks
    _history(hooks, 1)
    path = tmp_path / "checkpoint.pt"
    trainer.save_checkpoint(path)
    bad = load_checkpoint(path)
    bad["hook_state"]["expenditure_ledger"]["entries"][0]["charged"] += .001
    torch.save(bad, path)
    before = {
        "alpha": copy.deepcopy(trainer.alpha.state_dict()),
        "weighting": copy.deepcopy(trainer.weighting.state_dict()),
        "alpha_optimizer": copy.deepcopy(trainer.alpha_optimizer.state_dict()),
        "weighting_optimizer": copy.deepcopy(trainer.weighting_optimizer.state_dict()),
        "state": asdict(trainer.state), "gamma": trainer.gamma.to_dict(),
        "python_rng": random.getstate(), "numpy_rng": np.random.get_state(),
        "torch_rng": torch.get_rng_state().clone(), "ledger": hooks.expenditure_ledger.state_dict(),
        "hooks": hooks.state_dict(),
    }
    with pytest.raises(ValueError, match="finalized expenditure"):
        trainer.resume_from_checkpoint(path, hooks=hooks)
    _equal_models(before["alpha"], trainer.alpha.state_dict())
    _equal_models(before["weighting"], trainer.weighting.state_dict())
    assert_optimizer_equal(before["alpha_optimizer"], trainer.alpha_optimizer.state_dict())
    assert_optimizer_equal(before["weighting_optimizer"], trainer.weighting_optimizer.state_dict())
    assert asdict(trainer.state) == before["state"] and trainer.gamma.to_dict() == before["gamma"]
    assert random.getstate() == before["python_rng"]
    assert np.random.get_state()[0] == before["numpy_rng"][0]
    np.testing.assert_array_equal(np.random.get_state()[1], before["numpy_rng"][1])
    assert np.random.get_state()[2:] == before["numpy_rng"][2:]
    assert torch.equal(torch.get_rng_state(), before["torch_rng"])
    assert hooks.expenditure_ledger.state_dict() == before["ledger"]
    assert hooks.state_dict() == before["hooks"]


@pytest.mark.parametrize("changed", ["current-counter", "current-approval", "prepared-fields"])
def test_hook_plan_rejects_intervening_progress_or_prepared_mutation(changed):
    trainer, hooks, _, _, _ = _build_offline_experiment()
    trainer.hooks = hooks
    saved = hooks.state_dict()
    plan = hooks.prepare_restore(saved)
    if changed == "current-counter":
        hooks.sample_counters["routing"] += 1
    elif changed == "current-approval":
        hooks.stage_authorizations["development"] = {"source": "intervening different approval"}
    else:
        plan["fields"]["sample_counters"]["routing"] += 1
    before, ledger_before = hooks.state_dict(), hooks.expenditure_ledger.state_dict()
    with pytest.raises(ValueError, match="changed after validation"):
        hooks.apply_restore(plan)
    assert hooks.state_dict() == before
    assert hooks.expenditure_ledger.state_dict() == ledger_before
    assert not plan["used"]


def test_hook_plan_cannot_be_applied_twice():
    trainer, hooks, _, _, _ = _build_offline_experiment()
    trainer.hooks = hooks
    plan = hooks.prepare_restore(hooks.state_dict())
    hooks.apply_restore(plan)
    before = hooks.state_dict()
    with pytest.raises(ValueError, match="changed after validation"):
        hooks.apply_restore(plan)
    assert hooks.state_dict() == before
