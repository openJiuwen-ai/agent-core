"""Prospective approvals must not bypass the published regular-stage boundary."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from examples.adaptive_multi_agent_collab.operations.preflight import REGULAR_SCOPE


@pytest.mark.parametrize("published,matching,blocked", [
    (False, False, True), (True, False, True), (True, True, False),
])
def test_preflight_requires_exact_published_regular_plan(tmp_path, monkeypatch, published, matching, blocked):
    from examples.adaptive_multi_agent_collab.operations import experiment, preflight, regular_development, supplement
    from examples.adaptive_multi_agent_collab.tests.test_preflight_data import _local_specification

    source, _, _, _ = _local_specification(tmp_path)
    prepared = experiment.prepare_experiment(source)
    plan = tmp_path / "regular-plan.json"
    plan.write_text('{"synthetic":true}')
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    if published:
        prepared = replace(prepared, specification_amendments=({"protocol": "regular_textgrad_repair_plan_v1"},))
    payload = {"hook_state": {"regular_lineage": {"plan_sha256": digest if matching else "0" * 64}}}
    monkeypatch.setattr(preflight, "load_checkpoint", lambda _: payload)
    monkeypatch.setattr(supplement, "prepare_checkpoint_condition", lambda *_args, **_kwargs: prepared)
    # This test isolates the preflight publication gate. Exact immutable plan
    # construction/tampering checks are covered by test_regular_development.
    monkeypatch.setattr(regular_development, "load_plan", lambda *_: {"fingerprint": "f" * 64})
    monkeypatch.setattr(regular_development, "auth_bindings", lambda *_: (_ for _ in ()).throw(ValueError("synthetic no authority")))
    report = preflight.inspect_preflight(source, operation="train", checkpoint_path=tmp_path / "fixture.pt",
                                         regular_plan_path=plan, regular_forecast_path=tmp_path / "forecast.json")
    assert ("regular_checkpoint" in {item.code for item in report.issues}) is blocked
    assert report.checks["regular_plan"] == "f" * 64
    assert report.checks["execution_scope"] == REGULAR_SCOPE
    assert not report.ready and not report.checks["models_constructed"]


@pytest.mark.parametrize("phase", ["weighting", "routing", "prompt", "system", "complete"])
def test_registered_regular_authority_allows_actual_trainer_phases(tmp_path, phase):
    trainer, hooks = _regular_authority(tmp_path)
    trainer.state.phase = phase
    hooks.require_development(bootstrap_only=False)


@pytest.mark.parametrize("phase", ["pilot_complete", "weighting_bootstrap", "calibration", "system_baseline", "system_validation"])
def test_regular_authority_rejects_other_or_invented_phases(tmp_path, phase):
    trainer, hooks = _regular_authority(tmp_path)
    trainer.state.phase = phase
    with pytest.raises(ValueError, match="after checkpoint0"):
        hooks.require_development()


def _regular_authority(tmp_path):
    from examples.adaptive_multi_agent_collab.tests.test_bootstrap_authorization import accepted_fixture
    from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _stage_artifact

    trainer, hooks, _, _ = accepted_fixture(bootstrap=False)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"references": {"forecast": {"sha256": "f" * 64}}}))
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    hooks.regular_lineage = {"plan_path": str(plan), "plan_sha256": digest}
    trainer.state.calibration_complete = True
    approval = _stage_artifact(hooks, "development", execution_scope=REGULAR_SCOPE,
                              regular_plan_sha256=digest, regular_forecast_sha256="f" * 64)
    hooks.authorize_stage(approval)
    return trainer, hooks


def test_regular_cli_and_execution_wrapper_preserve_exact_scope(monkeypatch, capsys):
    from examples.adaptive_multi_agent_collab import cli
    from examples.adaptive_multi_agent_collab.operations import experiment, preflight
    from examples.adaptive_multi_agent_collab.infrastructure import shutdown

    received = []
    async def execute(specification, output, **kwargs):
        received.append((specification, output, kwargs))
        return {"phase": "complete", "stage_complete": True}
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(experiment, "_execute_stage", execute)
    monkeypatch.setattr(shutdown, "run_stage", asyncio.run)
    common = ["--authorization", "approval.json", "--regular-plan", "plan.json", "--regular-forecast", "forecast.json"]
    assert cli.main(["train", "manifest.json", "--output", "campaign", "--resume", "checkpoint.pt", *common]) == 0
    assert received[0][2]["regular_plan_path"] == "plan.json"
    assert received[0][2]["regular_forecast_path"] == "forecast.json"
    assert received[0][2]["stage"] == "development" and len(received) == 1
    calls = []
    def inspect(source, **kwargs):
        calls.append((source, kwargs))
        return SimpleNamespace(ready=False, to_dict=lambda: {"ready": False})
    monkeypatch.setattr(preflight, "inspect_preflight", inspect)
    assert cli.main(["preflight", "manifest.json", "--operation", "train", "--checkpoint", "checkpoint.pt", *common]) == 2
    assert calls[0][1]["regular_plan_path"] == "plan.json"
    assert calls[0][1]["regular_forecast_path"] == "forecast.json"
    assert calls[0][1]["checkpoint_path"] == "checkpoint.pt" and len(calls) == 1


def test_regular_cannot_mix_standalone_extension_authorization_before_io():
    from examples.adaptive_multi_agent_collab.operations.preflight import inspect_preflight

    with pytest.raises(ValueError, match="regular-development scope"):
        inspect_preflight("must-not-read.json", operation="train", regular_plan_path="plan.json",
                          regular_forecast_path="forecast.json", extension_scope_authorization="other.json")
