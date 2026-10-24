"""Production workflow, numerical update and journal restore with compact evidence."""

import asyncio
import copy
import json

import torch

from examples.adaptive_multi_agent_collab.operations.experiment import wire_durable_journal, validate_journal_resume
from examples.adaptive_multi_agent_collab.infrastructure.storage import COMPONENT_PROTOCOL, load_checkpoint, load_ledger
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment
from examples.adaptive_multi_agent_collab.tests.test_loss_recording import weighting_sessions
from examples.adaptive_multi_agent_collab.learning.training import update_weighting


def test_recovery_cli_forwards_exact_offline_authority(monkeypatch, capsys):
    from examples.adaptive_multi_agent_collab import cli
    from examples.adaptive_multi_agent_collab.operations import publication_recovery
    received = []
    monkeypatch.setattr(publication_recovery, "recover_interrupted",
                        lambda *args: received.append(args) or {"generation_calls": 0})
    assert cli.main(["publication-recover", "manifest", "--plan", "plan", "--forecast", "forecast",
                     "--authorization", "authorization", "--recovery-authority", "offline-authority"]) == 0
    assert received == [("manifest", "plan", "forecast", "authorization", "offline-authority")]
    assert json.loads(capsys.readouterr().out) == {"generation_calls": 0}


def test_workflow_update_compact_reload_and_completed_evidence_reuse(tmp_path):
    async def run():
        trainer, hooks, models, _, proposer = _build_offline_experiment()
        trainer.hooks = hooks
        trainer.checkpoint_path = tmp_path / "checkpoint.pt"
        wire_durable_journal(trainer, hooks, tmp_path, allow_empty_initialization=True)
        try:
            # Actual runtime/evaluation composition with a deterministic injected model.
            record = await hooks.validate("routing")
            assert len(record.examples) == 2 and len(models[0].calls) == 4
            before = copy.deepcopy(trainer.alpha.state_dict())
            parameter = next(trainer.weighting.parameters()).reshape(-1)[0]
            diagnostic = {}
            result = update_weighting(trainer.weighting, trainer.weighting_optimizer,
                                      weighting_sessions(parameter), "A", loss_record=diagnostic)
            assert result == diagnostic["loss"] and diagnostic["status"] == "updated"
            trainer.state.updates["weighting"] = 1
            # W changed: the earlier validation cache must not be reused.
            record = await hooks.validate("routing")
            assert len(models[0].calls) == 8
            await trainer.checkpoint_async()
            raw = torch.load(trainer.checkpoint_path, weights_only=False)
            assert raw["storage_protocol"] == COMPONENT_PROTOCOL
            assert raw["alpha"].get("$tensor")
            assert raw["hook_state"]["evaluation_records"].get("$collection")
            resolved = load_checkpoint(trainer.checkpoint_path)
            assert json.dumps(resolved["hook_state"], sort_keys=True) == json.dumps(hooks.state_dict(), sort_keys=True)
            assert json.dumps(load_ledger(tmp_path / "expenditure_ledger.json"), sort_keys=True) == json.dumps(hooks.expenditure_ledger.state_dict(), sort_keys=True)
            assert all(torch.equal(value, trainer.alpha.state_dict()[name]) for name, value in before.items())
            validate_journal_resume(trainer.checkpoint_path, tmp_path)
            call_count = len(models[0].calls)
            state = resolved["hook_state"]
            hooks.load_state_dict(state)
            assert (await hooks.validate("routing")).to_dict() == record.to_dict()
            assert len(models[0].calls) == call_count and proposer.calls == 0
            written = trainer.durable_journal.evidence_store.metrics["written_bytes"]
            await trainer.checkpoint_async()
            assert trainer.durable_journal.evidence_store.metrics["written_bytes"] == written
            assert load_checkpoint(trainer.checkpoint_path)["weighting_optimizer"]["state"]
            assert not load_checkpoint(trainer.checkpoint_path)["alpha_optimizer"]["state"]
        finally:
            await trainer.durable_journal.aclose()
        # A distinct writer can bind the completed checkpoint/WAL without dispatch.
        wire_durable_journal(trainer, hooks, tmp_path)
        try:
            validate_journal_resume(trainer.checkpoint_path, tmp_path)
            await trainer.checkpoint_async()
            assert len(models[0].calls) == call_count
            assert json.loads((tmp_path / "expenditure_ledger.json").read_text())["storage_protocol"]
        finally:
            await trainer.durable_journal.aclose()
    asyncio.run(run())
