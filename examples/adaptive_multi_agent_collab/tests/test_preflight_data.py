"""Local-only preparation, protected reference provenance, and execution readiness."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from examples.adaptive_multi_agent_collab.datasets.benchmark import RECONCILE_REFERENCE, verify_reconcile_main100
from examples.adaptive_multi_agent_collab.config import Config, SelectionConfig
from examples.adaptive_multi_agent_collab.datasets.data import (
    ActiveDataPlan,
    load_strategyqa,
    partition_queries,
    select_active_data,
)


def _local_specification(tmp_path):
    source = tmp_path / "questions.json"
    source.write_text(
        json.dumps(
            [
                {"qid": str(index), "question": f"Synthetic question {index}?", "answer": bool(index % 2)}
                for index in range(600)
            ]
        )
    )
    ids = tmp_path / "protected.json"
    ids.write_text(json.dumps([str(index) for index in range(100)]))
    config = Config(selection=SelectionConfig(lambda_select=0.1))
    raw = {
        "config": config.to_dict(),
        "dataset": {
            "source_files": [source.name],
            "revision": "offline-fixture",
            "evaluation_id_files": [ids.name],
            "split_seed": 7,
        },
        "active_data": {"seed": 17},
        "backends": {},
        "textgrad": {},
    }
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(raw))
    return path, raw, source, ids


def test_active_subsets_are_stratified_disjoint_frozen_and_pilot_is_not_extra_data(tmp_path):
    path, raw, source, ids = _local_specification(tmp_path)
    examples, provenance = load_strategyqa((source,), revision="fixture")
    dataset = partition_queries(
        examples, evaluation_id_sets=(json.loads(ids.read_text()),), split_seed=7, provenance=provenance
    )
    plan = select_active_data(dataset, seed=17)
    assert tuple(
        map(len, (plan.train_ids, plan.component_validation_ids, plan.system_validation_ids, plan.pilot_ids))
    ) == (60, 20, 20, 8)
    assert set(plan.pilot_ids).issubset(plan.train_ids)
    assert len(set(plan.train_ids + plan.component_validation_ids + plan.system_validation_ids)) == 100
    assert not set(plan.train_ids + plan.component_validation_ids + plan.system_validation_ids) & {
        str(i) for i in range(100)
    }
    for partition, identifiers in (
        ("train", plan.train_ids),
        ("comp-val", plan.component_validation_ids),
        ("sys-val", plan.system_validation_ids),
    ):
        assert (
            sum(example.gold_answer == "yes" for example in dataset.select(partition, identifiers))
            == len(identifiers) // 2
        )
    assert select_active_data(dataset, seed=17) == plan
    assert ActiveDataPlan.from_dict(json.loads(json.dumps(plan.to_dict()))) == plan
    with pytest.raises(ValueError, match="active training"):
        replace(plan, pilot_ids=(plan.component_validation_ids[0], *plan.pilot_ids[1:]))
    with pytest.raises(ValueError, match="different recorded dataset"):
        replace(plan, dataset_fingerprint="other").validate(dataset)
    with pytest.raises(ValueError, match="60/20/20"):
        select_active_data(dataset, seed=17, counts=(59, 20, 20, 8))


def test_primary_preparation_uses_shared_twenty_query_plans_and_registered_presets(tmp_path):
    from examples.adaptive_multi_agent_collab.operations.experiment import prepare_experiment

    path, raw, _, _ = _local_specification(tmp_path)
    prepared = prepare_experiment(path)
    active = prepared.active_data_plan
    assert active is not None
    assert all(
        prepared.plans[name].ordered_example_ids == active.component_validation_ids
        for name in ("weighting", "routing", "prompt")
    )
    assert prepared.plans["system"].ordered_example_ids == active.system_validation_ids
    assert prepared.plans["weighting"].domain == "wval"
    assert prepared.pilot_plan.ordered_example_ids == active.pilot_ids and prepared.pilot_plan.horizon == 3
    assert prepared.config.termination in prepared.termination_plan.candidates
    assert 5 <= len(prepared.termination_plan.candidates) <= 6
    assert all(plan.scoring_configuration["lambda_select"] == 0.1 for plan in prepared.plans.values())
    raw["validation"] = {"prompt": list(reversed(active.component_validation_ids))}
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="fixed shared"):
        prepare_experiment(path)


def test_main100_rejects_self_attested_fake_release_bytes(tmp_path):
    from examples.adaptive_multi_agent_collab.datasets.assets import RECONCILE_REVISION

    _, _, source, ids = _local_specification(tmp_path)
    evidence = {
        "repository": RECONCILE_REFERENCE["repository"],
        "repository_path": "dataset/SQA/dev.json",
        "repository_revision": RECONCILE_REVISION,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "local_source": source.name,
    }
    ordered = tuple(json.loads(ids.read_text()))
    with pytest.raises(ValueError, match="verified pinned repository blob"):
        verify_reconcile_main100(evidence, ordered, directory=tmp_path)
    with pytest.raises(ValueError, match="exact predeclared"):
        verify_reconcile_main100({**evidence, "repository_revision": "a" * 40}, ordered, directory=tmp_path)


def test_preflight_never_constructs_models_and_never_exports_credentials(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.operations.preflight import inspect_preflight
    from examples.adaptive_multi_agent_collab.operations import experiment

    path, raw, _, _ = _local_specification(tmp_path)
    raw["backends"] = {agent["backend_key"]: {"api_key": "must-not-appear"} for agent in raw["config"]["agent_pool"]}
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(experiment, "build_model", lambda *_: pytest.fail("constructed a model"))
    report = inspect_preflight(path, operation="pilot")
    assert {"primary_condition", "credentials", "credential_storage", "stage_authorization"} <= {
        item.code for item in report.issues
    }
    assert "must-not-appear" not in json.dumps(report.to_dict())
    assert not report.ready and not report.checks["network_accessed"] and not report.checks["models_constructed"]


def test_stage_approval_requires_exact_scope_profile_report_and_checkpoint(tmp_path):
    from examples.adaptive_multi_agent_collab.operations.preflight import stage_authorization_template, load_stage_authorization

    bindings: dict[str, Any] = dict(
        stage="test",
        manifest_fingerprint="a" * 64,
        condition_fingerprint="b" * 64,
        resource_profile_fingerprint="c" * 64,
        approved_budget_usd=20,
        pilot_fingerprint="d" * 64,
        checkpoint_sha256="e" * 64,
    )
    path = tmp_path / "authorization.json"
    template = stage_authorization_template(**bindings)
    path.write_text(json.dumps(template))
    with pytest.raises(ValueError, match="explicit approval"):
        load_stage_authorization(path, **bindings)
    artifact = {
        **template,
        "decision": "approve_stage",
        "reviewer": "offline fixture",
        "rationale": "Approve this exact synthetic test scope.",
    }
    path.write_text(json.dumps(artifact))
    assert load_stage_authorization(path, **bindings) == artifact
    for field, value in (
        ("checkpoint_sha256", "f" * 64),
        ("pilot_fingerprint", "f" * 64),
        ("approved_budget_usd", 21),
        ("resource_profile_fingerprint", "f" * 64),
    ):
        with pytest.raises(ValueError, match="differs from the exact"):
            load_stage_authorization(path, **{**bindings, field: value})
    path.write_text(json.dumps({**artifact, "extra": True}))
    with pytest.raises(ValueError, match="exactly"):
        load_stage_authorization(path, **bindings)


def test_authorization_planning_reads_real_checkpoint_trainer_state_without_restoring(tmp_path, monkeypatch):
    import asyncio
    from examples.adaptive_multi_agent_collab.operations import experiment
    from examples.adaptive_multi_agent_collab.operations.preflight import authorization_bindings
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import (
        _build_offline_experiment,
        _enable_stage_authorization,
        _stage_artifact,
    )

    trainer, hooks, _, _, _ = _build_offline_experiment()
    _enable_stage_authorization(trainer, hooks)
    hooks.authorize_stage(_stage_artifact(hooks, "pilot"))
    checkpoint = tmp_path / "checkpoint.pt"
    asyncio.run(trainer.run_pilot(hooks, checkpoint_path=checkpoint))
    prepared = SimpleNamespace(
        config=trainer.config, authorization_manifest_fingerprint="a" * 64, resource_profile_fingerprint="b" * 64
    )
    monkeypatch.setattr(experiment, "planning_hooks", lambda _: hooks)
    bindings, _ = authorization_bindings(prepared, "development", checkpoint)
    assert bindings["pilot_fingerprint"] == hooks.safety_record.fingerprint
    test_bindings, _ = authorization_bindings(prepared, "test", checkpoint)
    assert test_bindings["checkpoint_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert trainer.state.phase == "pilot_complete" and not any(trainer.state.updates.values())


def test_journal_persists_before_dispatch_without_full_checkpoint_and_survives_model_rollback(tmp_path):
    from examples.adaptive_multi_agent_collab.operations.experiment import wire_durable_journal
    from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal
    from examples.adaptive_multi_agent_collab.config import ResourceConfig
    from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger

    ledger = ExpenditureLedger(ResourceConfig(pilot_budget=5, currency="USD"))
    ledger.stage = "pilot"
    unsafe_markers = []
    wire_durable_journal(
        SimpleNamespace(_request_checkpoint=lambda: unsafe_markers.append(True)),
        SimpleNamespace(expenditure_ledger=ledger), tmp_path
    )
    old = ledger.state_dict()

    async def scenario():
        entry = ledger.reserve(0.2, kind="initial", model="fixture", metadata={})
        await ledger.flush_durable()
        assert read_journal(tmp_path)["state"]["entries"][-1]["status"] == "reserved"
        ledger.settle(entry, {"total_cost": 0.1}, status="complete", elapsed=0.01)
        await ledger.flush_durable()
        assert read_journal(tmp_path)["state"]["entries"][-1]["charged"] == 0.1
        assert read_journal(tmp_path)["boundary"] is None
        assert len(unsafe_markers) == 2 and not (tmp_path / "checkpoint.pt").exists()
        ledger.load_state_dict(old)
        assert len(ledger.entries) == 1 and ledger.entries[0]["charged"] == 0.1
        await ledger.durable_journal.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("settled", [False, True])
def test_journal_ahead_of_safe_checkpoint_blocks_before_mutation(tmp_path, settled):
    import torch
    from examples.adaptive_multi_agent_collab.operations.experiment import validate_journal_resume
    from examples.adaptive_multi_agent_collab.config import ResourceConfig
    from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger

    ledger = ExpenditureLedger(ResourceConfig(pilot_budget=5, currency="USD"))
    ledger.stage = "pilot"
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save(
        {"request_boundary_pending": False, "hook_state": {"expenditure_ledger": ledger.state_dict()}}, checkpoint
    )
    entry = ledger.reserve(0.2, kind="initial", model="fixture", metadata={})
    if settled:
        ledger.settle(entry, {"total_cost": 0.1}, status="complete", elapsed=0.01)
    journal = tmp_path / "expenditure_ledger.json"
    journal.write_text(json.dumps(ledger.state_dict()))
    original = {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    with pytest.raises(ValueError, match="operator reconciliation"):
        validate_journal_resume(checkpoint, tmp_path)
    assert original == {item.name: item.read_bytes() for item in tmp_path.iterdir()}


def test_missing_journal_cannot_reset_recorded_paid_checkpoint(tmp_path):
    import torch
    from examples.adaptive_multi_agent_collab.operations.experiment import validate_journal_resume

    checkpoint = tmp_path / "checkpoint.pt"
    torch.save(
        {"request_boundary_pending": False, "hook_state": {"expenditure_ledger": {"entries": [{"charged": 0.1}]}}},
        checkpoint,
    )
    with pytest.raises(ValueError, match="journal is missing"):
        validate_journal_resume(checkpoint, tmp_path)


def test_campaign_binding_prevents_changed_output_and_lock_blocks_second_writer(tmp_path, monkeypatch):
    from examples.adaptive_multi_agent_collab.operations import experiment

    monkeypatch.setattr(experiment, "PACKAGE_ROOT", tmp_path)
    source = tmp_path / "condition.json"
    source.write_text(json.dumps({"campaign_directory": "runs/fixed"}))
    campaign = tmp_path / "runs/fixed"
    assert experiment._campaign_directory(source, campaign) == campaign
    assert experiment._campaign_directory(source, campaign / "test", test=True) == campaign
    with pytest.raises(ValueError, match="cannot reset"):
        experiment._campaign_directory(source, tmp_path / "runs/new")
    with experiment._campaign_lock(campaign) as active:
        assert active()
        with pytest.raises(ValueError, match="another process"):
            with experiment._campaign_lock(campaign):
                pytest.fail("second campaign writer entered")
    assert not active()


def test_cli_json_has_no_sdk_stdout_prefix_or_implicit_external_log_files(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "examples.adaptive_multi_agent_collab", "smoke"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3]), "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout)["completed"] is True
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("settled", [False, True])
def test_recorded_final_test_dispatch_blocks_replay_before_preparation_or_file_changes(tmp_path, monkeypatch, settled):
    import asyncio
    from examples.adaptive_multi_agent_collab.operations import experiment, preflight
    from examples.adaptive_multi_agent_collab.config import ResourceConfig
    from examples.adaptive_multi_agent_collab.providers.resources import ExpenditureLedger

    output = tmp_path / "existing-output"
    output.mkdir()
    ledger = ExpenditureLedger(ResourceConfig(test_budget=1, currency="USD"))
    ledger.stage = "test"
    entry = ledger.reserve(0.2, kind="initial", model="fixture", metadata={})
    if settled:
        ledger.settle(entry, {"total_cost": 0.1}, status="complete", elapsed=0.01)
    (output / "expenditure_ledger.json").write_text(json.dumps(ledger.state_dict()))
    original = {item.name: item.read_bytes() for item in output.iterdir()}
    monkeypatch.setattr(preflight, "inspect_preflight", lambda *a, **kw: pytest.fail("reached preparation"))
    with pytest.raises(ValueError, match="automatic replay.*disabled"):
        asyncio.run(experiment.evaluate_experiment(tmp_path / "unused.json", tmp_path / "unused.pt", output))
    assert original == {item.name: item.read_bytes() for item in output.iterdir()}


def test_canonical_campaign_test_journal_blocks_fresh_child_output(tmp_path, monkeypatch):
    import asyncio
    from examples.adaptive_multi_agent_collab.operations import experiment, preflight

    monkeypatch.setattr(experiment, "PACKAGE_ROOT", tmp_path)
    source = tmp_path / "condition.json"
    source.write_text(json.dumps({"campaign_directory": "runs/fixed"}))
    campaign = tmp_path / "runs/fixed"
    campaign.mkdir(parents=True)
    (campaign / "expenditure_ledger.json").write_text(
        json.dumps({"entries": [{"stage": "test", "status": "reserved"}]})
    )
    monkeypatch.setattr(preflight, "inspect_preflight", lambda *a, **kw: pytest.fail("reached preparation"))
    with pytest.raises(ValueError, match="campaign already records"):
        asyncio.run(experiment.evaluate_experiment(source, campaign / "checkpoint.pt", campaign / "test"))
    assert not (campaign / "test").exists()


def test_wrapped_public_model_factory_does_not_disable_stage_authorization(monkeypatch):
    from examples.adaptive_multi_agent_collab.operations import experiment
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment
    from openjiuwen.core.foundation.llm import Model

    captured: dict[str, Any] = {}
    original = experiment.build_experiment

    def capture(prepared, **kwargs):
        captured.update(prepared=prepared, kwargs=kwargs)
        return original(prepared, **kwargs)

    monkeypatch.setattr(experiment, "build_experiment", capture)
    _build_offline_experiment()
    prepared = captured["prepared"]
    backends = {key: {**value, "resources": {}} for key, value in prepared.backends.items()}
    public_model = object.__new__(Model)
    _, hooks = original(
        replace(prepared, backends=backends),
        model_factory=lambda _: public_model,
        fixed_encoders=captured["kwargs"]["fixed_encoders"],
        proposer=captured["kwargs"]["proposer"],
        resource_factory=lambda *args: SimpleNamespace(),
    )
    assert hooks.authorization_required
    with pytest.raises(ValueError, match="active locked campaign session"):
        hooks.require_stage_authorization("pilot")
    hooks.execution_session_guard = lambda: True
    with pytest.raises(ValueError, match="explicit pilot"):
        hooks.require_stage_authorization("pilot")


@pytest.mark.parametrize("location", ["checkpoint", "journal"])
def test_sticky_halt_without_new_entries_blocks_resume_before_mutation(tmp_path, location):
    import torch
    from examples.adaptive_multi_agent_collab.operations.experiment import validate_journal_resume

    checkpoint = tmp_path / "checkpoint.pt"
    saved: dict[str, Any] = {"entries": [], "halted": None}
    journal: dict[str, Any] = {"entries": [], "halted": None}
    target = saved if location == "checkpoint" else journal
    target["halted"] = {"reason": "native accounting undercount", "stage": "compatibility", "after_entries": 0}
    torch.save({"request_boundary_pending": False, "hook_state": {"expenditure_ledger": saved}}, checkpoint)
    ledger_path = tmp_path / "expenditure_ledger.json"
    ledger_path.write_text(json.dumps(journal))
    before = checkpoint.read_bytes(), ledger_path.read_bytes()
    with pytest.raises(ValueError, match="sticky campaign halt"):
        validate_journal_resume(checkpoint, tmp_path)
    assert before == (checkpoint.read_bytes(), ledger_path.read_bytes())


@pytest.mark.parametrize("mutation", [None, "charge", "status", "frame", "pending"])
def test_gateway_tuple_frames_compare_as_json_but_changed_receipts_still_fail(tmp_path, mutation):
    import torch
    from examples.adaptive_multi_agent_collab.operations.experiment import validate_journal_resume

    entry = {
        "dispatch_id": 0,
        "status": "completed",
        "reserved": 0.02,
        "charged": 0.01,
        "usage": {"raw_gateway_frames": ({"id": "fixture-generation", "choices": ({"finish_reason": "stop"},)},)},
    }
    state = {"entries": [entry], "halted": None}
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save(
        {"request_boundary_pending": mutation == "pending", "hook_state": {"expenditure_ledger": state}}, checkpoint
    )
    journal = json.loads(json.dumps(state))
    if mutation == "charge":
        journal["entries"][0]["charged"] = 0.011
    elif mutation == "status":
        journal["entries"][0]["status"] = "reserved"
    elif mutation == "frame":
        journal["entries"][0]["usage"]["raw_gateway_frames"][0]["id"] = "different-generation"
    path = tmp_path / "expenditure_ledger.json"
    path.write_text(json.dumps(journal))
    before = checkpoint.read_bytes(), path.read_bytes()
    if mutation is None:
        assert validate_journal_resume(checkpoint, tmp_path) == state
    else:
        with pytest.raises(ValueError, match="operator reconciliation"):
            validate_journal_resume(checkpoint, tmp_path)
    assert before == (checkpoint.read_bytes(), path.read_bytes())


def test_mock_gateway_compatibility_finalization_and_pilot_resume_preserve_tuple_receipts(tmp_path, monkeypatch):
    import asyncio
    import time
    import torch
    from examples.adaptive_multi_agent_collab.operations import experiment as module
    from examples.adaptive_multi_agent_collab.operations.preflight import finalize_compatibility_evidence
    from examples.adaptive_multi_agent_collab.workflows.prompts import PromptProposal
    from examples.adaptive_multi_agent_collab.providers.resources import BackendResourceProfile, ResourceController
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import (
        _build_offline_experiment,
        _enable_stage_authorization,
        _stage_artifact,
    )

    trainer, hooks, models, _, proposer = _build_offline_experiment()
    _enable_stage_authorization(trainer, hooks)
    checkpoint = tmp_path / "checkpoint.pt"
    trainer.hooks, trainer.checkpoint_path = hooks, checkpoint

    class Counter:
        def count_input(self, messages, schema=None):
            return sum(len(item["content"].split()) + 2 for item in messages) + 1

        def count_output(self, text):
            return len(text.split())

    counter = Counter()
    profile = BackendResourceProfile(
        "fixture",
        "fixed",
        "transformers",
        20000,
        4096,
        1e-6,
        2e-6,
        "USD",
        "fixture",
        reference_input_price=1e-6,
        reference_output_price=2e-6,
        reference_pricing_date="fixture",
    )
    controller = ResourceController(profile, counter, hooks.expenditure_ledger, limits=trainer.config.resources)
    model = models[0]
    original = model.invoke

    async def native_invoke(**kwargs):
        output = await original(**kwargs)
        count = counter.count_input(kwargs["messages"])
        output.usage_metadata = {
            "input_tokens": count,
            "output_tokens": 20,
            "reasoning_tokens": 0,
            "total_cost": count * 1e-6 + 20 * 2e-6,
            "is_byok": False,
            "served_model": "fixture",
            "served_provider": "fixture",
            "response_identity_verified": True,
            "usage_sources": {"input_tokens": "generation_native", "output_tokens": "generation_native"},
            "raw_gateway_frames": ({"id": f"mock-{len(model.calls)}", "choices": ({"finish_reason": "stop"},)},),
        }
        output.finish_reason = "stop"
        return output

    model.invoke = native_invoke
    for adapter in hooks._runtime().agents.values():
        adapter.resources = controller

    async def propose(spec, evidence, *, iteration_id):
        for kind in ("textgrad_evaluation", "textgrad_backward", "textgrad_optimizer"):
            await controller.invoke(
                model,
                messages=[{"role": "user", "content": "synthetic engine fixture"}],
                kind=kind,
                deadline=time.monotonic() + 10,
                settings={},
            )
        return PromptProposal(spec.template + "\nDiscarded synthetic proposal.")

    proposer.propose_compatibility = propose
    plan = hooks.compatibility_plan()
    hooks.authorize_stage(_stage_artifact(hooks, "compatibility", compatibility_plan_fingerprint=plan["fingerprint"]))
    module.wire_durable_journal(trainer, hooks, tmp_path)
    report = asyncio.run(hooks.run_compatibility(plan))
    trainer._checkpoint()
    assert report["status"] == "complete" and report["assessment"]["passed"]
    assert isinstance(hooks.expenditure_ledger.entries[0]["usage"]["raw_gateway_frames"], tuple)
    report_path = tmp_path / "compatibility_report.json"
    module._write_json_atomic(report_path, report)
    source = tmp_path / "condition.json"
    source.write_text(json.dumps({"campaign_directory": str(tmp_path)}))
    prepared = SimpleNamespace(
        config=trainer.config,
        specification_sha256="a" * 64,
        authorization_manifest_fingerprint="a" * 64,
        resource_profile_fingerprint="b" * 64,
    )
    monkeypatch.setattr(module, "prepare_experiment", lambda _: prepared)
    monkeypatch.setattr(module, "planning_hooks", lambda _: hooks)
    journal = tmp_path / "expenditure_ledger.json"
    before = checkpoint.read_bytes(), journal.read_bytes(), report_path.read_bytes()
    assert finalize_compatibility_evidence(source, report_path)["pilot_authorization"] == "pending"
    assert before == (checkpoint.read_bytes(), journal.read_bytes(), report_path.read_bytes())
    module.validate_journal_resume(checkpoint, tmp_path)
    resumed, restored, fresh_models, _, _ = _build_offline_experiment()
    _enable_stage_authorization(resumed, restored)
    resumed.resume_from_checkpoint(checkpoint, hooks=restored)
    restored.authorize_stage(_stage_artifact(restored, "pilot"))
    restored.require_stage_authorization("pilot")
    assert resumed.state.phase == "unstarted" and not fresh_models[0].calls
    assert restored.expenditure_ledger.stage_summary("compatibility") == hooks.expenditure_ledger.stage_summary(
        "compatibility"
    )
    assert restored.expenditure_ledger.stage_summary("pilot")["attempts"] == 0
    assert torch.load(checkpoint, weights_only=False)["request_boundary_pending"] is False
