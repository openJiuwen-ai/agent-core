"""Development finalization publishes scalar diagnostics through the real journal."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.operations import campaigns, experiment, preflight
from examples.adaptive_multi_agent_collab.providers import identity
from examples.adaptive_multi_agent_collab.infrastructure.failure_policy import cancel_with_cause
from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal
from examples.adaptive_multi_agent_collab.infrastructure.storage import load_checkpoint
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import (
    _build_offline_experiment,
)
from examples.adaptive_multi_agent_collab.learning.training import _numerical_step


def _loss_record(attempt, *, updated, loss):
    return {
        "protocol": "numerical_preupdate_loss_v1",
        "phase": "weighting_bootstrap",
        "component": "weighting",
        "outer_cycle": 0,
        "workflow_attempt_id": attempt,
        "query_id": "synthetic-publication-query",
        "phase_attempt": attempt + 1,
        "component_attempt": attempt + 1,
        "update_index_before": 0 if updated else 1,
        "update_index_after": 1,
        "phase_update_index_before": 0 if updated else 1,
        "phase_update_index_after": 1,
        "loss": loss,
        "example_count": 1 if updated else None,
        "normalization_denominator": 1 if updated else None,
        "status": "updated" if updated else "failed",
        "reason": None if updated else "CancelledError",
    }


@pytest.mark.parametrize("cancelled", [False, True])
def test_development_stage_publishes_matching_complete_or_interrupted_loss_state(
    tmp_path, monkeypatch, cancelled
):
    """Only execution/authorization fixtures are replaced; checkpoint and WAL are real."""
    monkeypatch.setattr(campaigns, "ROOT", tmp_path.parent)

    async def run():
        trainer, hooks, models, _, proposer = _build_offline_experiment()
        prepared = SimpleNamespace(
            config=trainer.config,
            dataset=hooks.dataset,
            specification_sha256="synthetic-publication-fixture",
        )
        report = SimpleNamespace(require_ready=lambda: None)
        monkeypatch.setattr(experiment, "_campaign_directory", lambda *_: tmp_path)
        monkeypatch.setattr(experiment, "prepare_experiment", lambda *_: prepared)
        monkeypatch.setattr(experiment, "build_experiment", lambda *_: (trainer, hooks))
        monkeypatch.setattr(experiment, "_write_run_inputs", lambda *_: None)
        monkeypatch.setattr(preflight, "inspect_preflight", lambda *_, **__: report)
        monkeypatch.setattr(identity, "with_registered_response_identity", lambda value, *_: value)
        monkeypatch.setattr(hooks, "authorize_stage", lambda _: None)
        trainer.hooks = hooks
        path = tmp_path / "checkpoint.pt"
        trainer.save_checkpoint(path)
        (tmp_path / "expenditure_ledger.json").write_text(
            json.dumps(hooks.expenditure_ledger.state_dict())
        )
        authorization = tmp_path / "authorization.json"
        authorization.write_text("{}")
        started = asyncio.Event()

        async def fit(actual_hooks, *, checkpoint_path, bootstrap_only, weighting_supplement=None,
                      calibration_only=False):
            assert actual_hooks is hooks and checkpoint_path == path and bootstrap_only
            assert not weighting_supplement and not calibration_only
            # A synthetic numerical operation, not a model generation or workflow.
            parameter = next(trainer.weighting.parameters())
            loss = (parameter.reshape(-1)[0] - 1).square()
            scalar = float(loss.detach())
            assert _numerical_step(trainer.weighting_optimizer, loss, 1.0) == scalar
            trainer.state.phase = "weighting_bootstrap"
            trainer.state.workflow_attempt_counter = 1
            trainer.state.attempts["weighting"] = 1
            trainer.state.updates["weighting"] = 1
            trainer.state.training_loss_records.append(_loss_record(0, updated=True, loss=scalar))
            trainer._checkpoint()
            if cancelled:
                trainer.state.workflow_attempt_counter = 2
                trainer.state.attempts["weighting"] = 2
                await hooks.expenditure_ledger.record_progress(
                    "workflow-started", {"example_id": "synthetic-interrupted-query", "workflow_complete": False}
                )
                started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    trainer.state.training_loss_records.append(_loss_record(1, updated=False, loss=None))
                    raise
            trainer.state.phase = "calibration"
            trainer.state.bootstrap_complete = {"weighting": True, "routing": True}
            trainer._checkpoint()
            return trainer.state

        monkeypatch.setattr(trainer, "fit", fit)
        task = asyncio.create_task(experiment._execute_stage(
            tmp_path / "manifest.json",
            tmp_path,
            stage="development",
            authorization_path=authorization,
            resume=path,
            bootstrap_only=True,
            bootstrap_forecast_path=tmp_path / "forecast.json",
        ))
        if cancelled:
            await asyncio.wait_for(started.wait(), 5)
            cancel_with_cause(task, "operator-cancellation")
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result["stage_complete"] and result["phase"] == "calibration"
        diagnostics = json.loads((tmp_path / "training_diagnostics.json").read_text())
        saved = load_checkpoint(path)
        assert diagnostics["training_losses"] == saved["trainer_state"]["training_loss_records"]
        assert diagnostics["attempts"] == saved["trainer_state"]["attempts"]
        assert diagnostics["successful_updates"] == saved["trainer_state"]["updates"]
        assert diagnostics["validation_history"] == saved["trainer_state"]["validation_history"]
        assert diagnostics["request_boundary_pending"] is saved["request_boundary_pending"] is cancelled
        assert len(diagnostics["training_losses"]) == 1 + int(cancelled)
        assert saved["weighting_optimizer"]["state"]
        assert not saved["alpha_optimizer"]["state"]
        assert diagnostics["training_losses"][0]["loss"] is not None
        if cancelled:
            assert diagnostics["training_losses"][-1]["loss"] is None
            result = json.loads((tmp_path / "result.json").read_text())
            assert result["phase"] == "interrupted" and not result["stage_complete"]
            assert result["unfinished_workflow"] and not result["resume_safe"]
            assert read_journal(tmp_path)["boundary"] is None
        else:
            assert read_journal(tmp_path)["boundary"] is not None
            experiment.validate_journal_resume(path, tmp_path)
        assert all(not model.calls for model in models) and proposer.calls == 0
        assert hooks.expenditure_ledger.state_dict()["entries"] == []
        await hooks.expenditure_ledger.durable_journal.aclose()

    asyncio.run(run())
