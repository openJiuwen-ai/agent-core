"""Scoped development approval reaches production hooks/CLI without authorizing later phases."""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from examples.adaptive_multi_agent_collab.learning.calibration import PilotObservation, calibrate_safety
from examples.adaptive_multi_agent_collab.operations.preflight import (
    BOOTSTRAP_SCOPE,
    stage_authorization_template,
    validate_stage_authorization,
)
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import (
    _build_offline_experiment,
    _enable_stage_authorization,
    _stage_artifact,
)


def accepted_fixture(*, bootstrap=True):
    trainer, hooks, models, _, _ = _build_offline_experiment()
    _enable_stage_authorization(trainer, hooks)
    observation = PilotObservation("train-a", (4,), (1.0,), (2.0,), True, 1, ())
    hooks.pilot_observations = [observation]
    hooks.safety_record = calibrate_safety(hooks.pilot_plan, (observation,), trainer.config.safety)
    trainer.state.phase = "pilot_complete"
    trainer.state.pilot_complete = True
    trainer.hooks = hooks
    bindings = ({"execution_scope": BOOTSTRAP_SCOPE, "bootstrap_forecast_sha256": "c" * 64}
                if bootstrap else {})
    artifact = _stage_artifact(hooks, "development", **bindings)
    hooks.authorize_stage(artifact)
    return trainer, hooks, models, artifact


def test_bootstrap_authorization_binds_forecast_and_cannot_be_interpreted_as_full_development():
    _, hooks, _, artifact = accepted_fixture()
    bindings = hooks._authorization_bindings(
        "development", execution_scope=BOOTSTRAP_SCOPE, bootstrap_forecast_sha256="c" * 64
    )
    assert validate_stage_authorization(artifact, **bindings) == artifact
    assert artifact["scope"] == BOOTSTRAP_SCOPE and artifact["approved_budget_usd"] == 80
    with pytest.raises(ValueError, match="exactly|differs"):
        validate_stage_authorization(artifact, **hooks._authorization_bindings("development"))
    with pytest.raises(ValueError, match="differs"):
        validate_stage_authorization(artifact, **{**bindings, "bootstrap_forecast_sha256": "d" * 64})
    assert artifact["decision"] == "approve_stage"  # Synthetic fixture only.


@pytest.mark.parametrize("scope,forecast", [
    (BOOTSTRAP_SCOPE, None), (BOOTSTRAP_SCOPE, "not-a-hash"),
    ("development_campaign", "c" * 64), ("unknown_scope", "c" * 64),
])
def test_bootstrap_scope_requires_exact_separate_forecast_binding(scope, forecast):
    bindings = {
        "stage": "development", "manifest_fingerprint": "a" * 64,
        "condition_fingerprint": "b" * 64, "resource_profile_fingerprint": "c" * 64,
        "pilot_fingerprint": "d" * 64, "approved_budget_usd": 80,
    }
    with pytest.raises(ValueError, match="scope|forecast|SHA-256"):
        stage_authorization_template(**bindings, execution_scope=scope, bootstrap_forecast_sha256=forecast)


def test_scoped_approval_survives_real_hook_validation_and_checkpoint_roundtrip(tmp_path):
    trainer, hooks, models, artifact = accepted_fixture()
    hooks.validate_state(hooks.state_dict())
    checkpoint = tmp_path / "scoped.pt"
    trainer.save_checkpoint(checkpoint)
    resumed, restored, fresh_models, _, _ = _build_offline_experiment()
    _enable_stage_authorization(resumed, restored)
    resumed.resume_from_checkpoint(checkpoint, hooks=restored)
    assert restored.stage_authorizations["development"] == artifact
    assert restored.safety_record.approved
    restored.require_development(bootstrap_only=True)
    with pytest.raises(ValueError, match="cannot launch full development"):
        asyncio.run(resumed.fit(restored, checkpoint_path=checkpoint))
    assert resumed.state.phase == "pilot_complete"
    assert not models[0].calls and not fresh_models[0].calls
    damaged = copy.deepcopy(restored.state_dict())
    damaged["stage_authorizations"]["development"]["bootstrap_forecast_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="matching explicit development authorization"):
        restored.validate_state(damaged)


@pytest.mark.parametrize("phase", ["pilot_complete", "weighting_bootstrap", "routing_bootstrap"])
def test_bootstrap_hook_allows_only_registered_numerical_phase_workflows(phase):
    trainer, hooks, models, _ = accepted_fixture()
    trainer.state.phase = phase
    hooks.require_development()
    hooks.require_development(bootstrap_only=True)
    assert hooks.expenditure_ledger.stage == "development"
    with pytest.raises(ValueError, match="cannot launch full development"):
        hooks.require_development(bootstrap_only=False)
    assert not models[0].calls


def test_bootstrap_boundary_noop_does_not_authorize_calibration_or_prompt_work():
    trainer, hooks, models, _ = accepted_fixture()
    trainer.state.phase = "calibration"
    hooks.require_development(bootstrap_only=True)
    with pytest.raises(ValueError, match="before termination calibration"):
        hooks.require_development()
    with pytest.raises(ValueError, match="cannot launch full development"):
        asyncio.run(hooks.calibrate_termination())
    trainer.state.phase = "prompt"
    with pytest.raises(ValueError, match="cannot launch full development"):
        asyncio.run(hooks.prompt_evidence("tie"))
    assert not models[0].calls and hooks.sample_counters["prompt"] == 0
    assert not hooks.termination_record


@pytest.mark.parametrize("operation", ["calibration", "prompt_evidence", "system", "prompt_validation"])
def test_bootstrap_scope_rejects_later_work_even_from_a_permitted_numerical_phase(operation):
    trainer, hooks, models, _ = accepted_fixture()
    trainer.state.phase = "weighting_bootstrap"
    calls = {
        "calibration": hooks.calibrate_termination,
        "prompt_evidence": lambda: hooks.prompt_evidence("tie"),
        "system": hooks.system_validation,
        "prompt_validation": lambda: hooks._evaluate(hooks.plans["prompt"]),
    }
    with pytest.raises(ValueError, match="cannot launch full development|only numerical component validation"):
        asyncio.run(calls[operation]())
    assert not models[0].calls and hooks.sample_counters["prompt"] == 0
    assert not hooks.termination_record and not hooks.evaluation_records


def test_production_development_wrapper_preserves_bootstrap_execution_scope(monkeypatch):
    from examples.adaptive_multi_agent_collab.operations import experiment

    received = []

    async def execute(specification, output, **kwargs):
        received.append((specification, output, kwargs))
        return {"phase": "calibration", "stage_complete": True}

    monkeypatch.setattr(experiment, "_execute_stage", execute)
    result = asyncio.run(experiment.run_experiment(
        "fixture.json", "campaign", resume="checkpoint.pt", authorization_path="scope.json",
        bootstrap_only=True, bootstrap_forecast_path="forecast.json",
    ))
    assert result["stage_complete"] is True
    assert received == [("fixture.json", "campaign", {
        "stage": "development", "resume": "checkpoint.pt", "authorization_path": "scope.json",
        "bootstrap_only": True, "bootstrap_forecast_path": "forecast.json",
    })]


def test_existing_full_development_authorization_shape_and_scope_remain_unchanged():
    trainer, hooks, models, artifact = accepted_fixture(bootstrap=False)
    assert artifact["scope"] == "development_campaign"
    assert "bootstrap_forecast_sha256" not in artifact
    assert validate_stage_authorization(artifact, **hooks._authorization_bindings("development")) == artifact
    hooks.require_development(bootstrap_only=False)
    trainer.state.phase = "calibration"
    hooks.require_development()
    hooks.validate_state(hooks.state_dict())
    assert not models[0].calls


def test_train_cli_forwards_bootstrap_flag_and_exact_forecast_to_production_entrypoint(monkeypatch, capsys):
    from examples.adaptive_multi_agent_collab import cli
    from examples.adaptive_multi_agent_collab.operations import experiment
    from examples.adaptive_multi_agent_collab.infrastructure import shutdown

    received = []

    async def runner(specification, output, **kwargs):
        received.append((specification, output, kwargs))
        return {"phase": "calibration", "stage_complete": True, "execution_scope": BOOTSTRAP_SCOPE}

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(experiment, "run_experiment", runner)
    monkeypatch.setattr(shutdown, "run_stage", asyncio.run)
    assert cli.main([
        "train", "fixture.json", "--output", "campaign", "--resume", "checkpoint.pt",
        "--authorization", "scope.json", "--bootstrap-only", "--bootstrap-forecast", "forecast.json",
    ]) == 0
    assert received == [("fixture.json", "campaign", {
        "resume": "checkpoint.pt", "authorization_path": "scope.json",
        "bootstrap_only": True, "bootstrap_forecast_path": "forecast.json",
    })]
    assert json.loads(capsys.readouterr().out)["phase"] == "calibration"


def test_preflight_cli_forwards_scope_without_model_execution(monkeypatch, capsys):
    from examples.adaptive_multi_agent_collab import cli
    from examples.adaptive_multi_agent_collab.operations import preflight

    received = []

    def inspect(specification, **kwargs):
        received.append((specification, kwargs))
        return SimpleNamespace(ready=False, to_dict=lambda: {"ready": False, "issues": ["pending"]})

    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    monkeypatch.setattr(preflight, "inspect_preflight", inspect)
    assert cli.main([
        "preflight", "fixture.json", "--operation", "train", "--checkpoint", "checkpoint.pt",
        "--authorization", "scope.json", "--bootstrap-only", "--bootstrap-forecast", "forecast.json",
    ]) == 2
    assert received == [("fixture.json", {
        "operation": "train", "authorization_path": "scope.json", "checkpoint_path": "checkpoint.pt",
        "bootstrap_only": True, "bootstrap_forecast_path": "forecast.json",
    })]
    assert json.loads(capsys.readouterr().out)["ready"] is False
