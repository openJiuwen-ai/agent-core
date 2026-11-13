"""Narrow successor registration and completed Pilot4 retirement regressions."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from examples.adaptive_multi_agent_collab.operations import campaigns, experiment, policy_activation, preflight
from examples.adaptive_multi_agent_collab.tests.test_pilot2_initialization import initialization as initialization_fixture
from examples.adaptive_multi_agent_collab.tests.test_pilot2_policy import _approve
from examples.adaptive_multi_agent_collab.tests.test_pilot4_lifecycle import _complete_fixture
from examples.adaptive_multi_agent_collab.tests.test_successor_lifecycle import registered_archive as archive_fixture


@pytest.fixture
def initialization(tmp_path, monkeypatch):
    return initialization_fixture.__wrapped__(tmp_path, monkeypatch)


def _pilot5(item):
    formal = item.source.parent / "synthetic_pilot5_source.tex"
    formal.write_bytes(b"\\documentclass{article}\nSynthetic Pilot5 source fixture.\n")
    destination = item.destination.with_name("Pilot_5")
    kwargs = {**item.kwargs, "specification_sha256": campaigns._sha(formal), "specification_attachment": formal}
    return destination, formal, kwargs


def test_pilot5_snapshot_fresh_state_and_duplicate_nonreset(initialization):
    item = initialization
    destination, formal, kwargs = _pilot5(item)
    receipt = campaigns.initialize_pilot(item.source, destination, **kwargs)
    assert (destination / "mathematical_specification.tex").read_bytes() == formal.read_bytes()
    manifest = campaigns._read(destination / "manifest.json")
    assert manifest["specification_attachment"] == str(destination / "mathematical_specification.tex")
    assert manifest["independent_campaign"]["campaign_id"] == "Pilot_5"
    assert not receipt["generation_authorized"] and not receipt["policy_activated"]
    checkpoint = torch.load(destination / "checkpoint.pt", weights_only=False)
    assert checkpoint["schema_version"] == 12
    assert checkpoint["hook_state"]["expenditure_ledger"]["entries"] == []
    assert not checkpoint["alpha_optimizer"]["state"] and not checkpoint["weighting_optimizer"]["state"]
    assert not checkpoint["trainer_state"]["pilot_complete"]
    assert all(value["paid"] == value["unresolved"] == value["attempts"] == 0
               for value in receipt["operational_balances"].values())
    before = {path: path.read_bytes() for path in destination.iterdir() if path.is_file()}
    assert campaigns.initialize_pilot(item.source, destination, **kwargs) == receipt
    assert all(path.read_bytes() == contents for path, contents in before.items())
    assert all(path.read_bytes() == contents for path, contents in item.old_bytes.items())
    assert item.invocations == []


def test_pilot5_missing_exact_source_rejected_before_directory_creation(initialization):
    item = initialization
    destination, _, kwargs = _pilot5(item)
    kwargs.pop("specification_attachment")
    with pytest.raises(ValueError, match="exact supplied mathematical source snapshot"):
        campaigns.initialize_pilot(item.source, destination, **kwargs)
    assert not destination.exists()


def test_pilot5_mock_activation_then_credential_free_preflight(initialization, monkeypatch):
    item = initialization
    destination, _, kwargs = _pilot5(item)
    campaigns.initialize_pilot(item.source, destination, **kwargs)
    before = torch.load(destination / "checkpoint.pt", weights_only=False)
    approval = _approve(SimpleNamespace(anchor=destination))
    result = policy_activation.activate_policy_plan(destination / "policy_plan.json", approval)
    assert result["pilot_authorized"] is False
    after = torch.load(destination / "checkpoint.pt", weights_only=False)
    assert after["trainer_state"] == before["trainer_state"]
    assert after["alpha_optimizer"] == before["alpha_optimizer"]
    assert after["weighting_optimizer"] == before["weighting_optimizer"]
    assert experiment.validate_journal_resume(destination / "checkpoint.pt", destination)["entries"] == []
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    report = preflight.inspect_preflight(destination / "manifest.json", operation="pilot",
                                         checkpoint_path=destination / "checkpoint.pt")
    codes = {issue.code for issue in report.issues}
    assert "failure_policy_activation" not in codes
    assert not report.ready and "stage_authorization" in codes
    assert item.invocations == []


def test_completed_pilot4_retirement_preserves_pending_acceptance_and_rejects_reopening(tmp_path):
    kwargs, pilot1, sentinel = archive_fixture.__wrapped__(tmp_path)
    old = kwargs["campaign"]
    directory = old.with_name("Pilot_4")
    old.rename(directory)
    kwargs.update(campaign=directory, destination=kwargs["destination"].with_name("Pilot_4"), campaign_id="Pilot_4")
    manifest_path = directory / "manifest.json"
    manifest = campaigns._read(manifest_path)
    manifest["campaign_directory"] = str(directory)
    manifest["independent_campaign"]["campaign_id"] = "Pilot_4"
    manifest_path.write_text(json.dumps(manifest))
    ledger_path = directory / "expenditure_ledger.json"
    ledger = campaigns._read(ledger_path)
    ledger["campaign_binding"] = campaigns.campaign_binding(manifest_path, manifest)
    ledger_path.write_text(json.dumps(ledger))
    registry_path = kwargs["root"] / "runs/campaign_registry.json"
    registry = campaigns._read(registry_path)
    registered = registry["campaigns"].pop("Pilot_2")
    registered.update(campaign_id="Pilot_4", directory=str(directory), manifest_path=str(manifest_path),
                      manifest_sha256=campaigns._sha(manifest_path), specification_digest=campaigns._digest(manifest))
    registry["campaigns"]["Pilot_4"] = registered
    registry_path.write_text(json.dumps(registry))
    _complete_fixture(kwargs)
    record_path = directory / "pilot_record.json"
    record = campaigns._read(record_path)
    record.update(approved=False, human_accepted=False, review=None)
    record_path.write_text(json.dumps(record))
    originals = {path: Path(path).read_bytes() for path in kwargs["artifacts"]}
    receipt = campaigns.archive_campaign(**kwargs)
    assert receipt["execution_complete"] and receipt["accounting_complete"]
    archived = campaigns._read(receipt["manifest"])
    for row in archived["artifacts"]:
        assert Path(row["archive_path"]).read_bytes() == originals[row["original_path"]]
    retired = campaigns.remove_closed_campaign_directory(directory, authorization=kwargs["authorization"], root=kwargs["root"])
    assert retired["former_directory_removed"] and not directory.exists()
    assert campaigns._read(registry_path)["campaigns"]["Pilot_1"] == pilot1
    assert Path(kwargs["shared"][0]).read_bytes() == sentinel
    for forbidden in (directory, kwargs["destination"]):
        with pytest.raises(ValueError, match="closed|archived"):
            with campaigns.campaign_lock(forbidden, root=kwargs["root"]):
                pytest.fail("retired Pilot4 reopened")
    assert not directory.exists()
