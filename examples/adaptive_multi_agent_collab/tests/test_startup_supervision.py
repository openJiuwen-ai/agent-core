"""Pinned startup dependencies cannot become stale permission to execute."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from examples.adaptive_multi_agent_collab.datasets import assets
from examples.adaptive_multi_agent_collab.operations import campaigns, experiment, preflight
from examples.adaptive_multi_agent_collab.providers import identity
from examples.adaptive_multi_agent_collab.infrastructure.storage import read_json, StartupVerification, StorageFailure
from examples.adaptive_multi_agent_collab.tests.test_data_evaluation import _build_offline_experiment


@pytest.mark.parametrize("changed", ["approval", "checkpoint", "ledger"])
def test_execution_rechecks_pinned_inputs_before_writer_binding(tmp_path, monkeypatch, changed):
    """Real resume and writer boundary, with a mutation after the preflight read."""
    monkeypatch.setattr(campaigns, "ROOT", tmp_path.parent)
    trainer, hooks, models, _, proposer = _build_offline_experiment()
    trainer.hooks = hooks
    checkpoint = tmp_path / "checkpoint.pt"
    trainer.save_checkpoint(checkpoint)
    ledger = tmp_path / "expenditure_ledger.json"
    ledger.write_text(json.dumps(hooks.expenditure_ledger.state_dict()))
    approval = tmp_path / "approval.json"
    approval.write_text('{"approved":false}')
    prepared = SimpleNamespace(config=trainer.config)
    monkeypatch.setattr(experiment, "_campaign_directory", lambda *_: tmp_path)
    monkeypatch.setattr(experiment, "prepare_experiment", lambda *_: prepared)
    monkeypatch.setattr(identity, "with_registered_response_identity", lambda value, *_: value)
    monkeypatch.setattr(hooks, "authorize_stage", lambda _: None)

    def inspect(*_args, **_kwargs):
        read_json(approval)
        return SimpleNamespace(require_ready=lambda: None)

    def build(*_args, **_kwargs):
        # After unlocked preflight; replacement need not change mtime or length.
        if changed == "approval":
            approval.write_text('{"approved": true}')
        elif changed == "checkpoint":
            checkpoint.write_bytes(checkpoint.read_bytes() + b" ")
        else:
            ledger.write_text(ledger.read_text() + " ")
        return trainer, hooks

    monkeypatch.setattr(preflight, "inspect_preflight", inspect)
    monkeypatch.setattr(experiment, "build_experiment", build)
    monkeypatch.setattr(experiment, "_write_run_inputs", lambda *_: pytest.fail("stale startup published run inputs"))
    monkeypatch.setattr(trainer, "_checkpoint", lambda: pytest.fail("stale startup published checkpoint"))
    with pytest.raises((StorageFailure, ValueError), match="changed|differs|publication"):
        asyncio.run(experiment._execute_stage(
            tmp_path / "manifest.json", tmp_path, stage="development",
            authorization_path=approval, resume=checkpoint, bootstrap_only=True,
            bootstrap_forecast_path=tmp_path / "forecast.json",
        ))
    assert hooks.expenditure_ledger.durable_journal is None
    assert not any(model.calls for model in models) and proposer.calls == 0
    assert hooks.expenditure_ledger.entries == []


def test_counting_vocabulary_is_pinned_until_final_freshness(tmp_path, monkeypatch):
    raw = b"synthetic approved counting vocabulary"
    vocabulary = tmp_path / hashlib.sha1(assets.TIKTOKEN_URL.encode()).hexdigest()
    vocabulary.write_bytes(raw)
    monkeypatch.setattr(assets, "TIKTOKEN_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(preflight.importlib.metadata, "version", lambda _: "synthetic")
    profile = SimpleNamespace(
        tokenizer_kind="tiktoken", tokenizer_name="o200k_base",
        tokenizer_revision="synthetic", tokenizer_cache_directory=str(tmp_path),
        chat_tokens_per_message=3, chat_priming_tokens=3, counting_method="estimated",
    )
    with StartupVerification() as verification:
        assert preflight._tokenizer_present(profile)
        vocabulary.write_bytes(raw + b" changed")
        with pytest.raises(StorageFailure, match="changed"):
            verification.assert_fresh()
