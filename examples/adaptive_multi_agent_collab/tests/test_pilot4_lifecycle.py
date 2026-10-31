"""Completed retirement, removed run paths and exact-source fresh initialization."""

from __future__ import annotations

import asyncio
import fcntl
import json
from pathlib import Path

import pytest
import torch

from examples.adaptive_multi_agent_collab.operations import campaigns, experiment
from examples.adaptive_multi_agent_collab.infrastructure.persistence import DurableJournal
from examples.adaptive_multi_agent_collab.tests.test_pilot2_initialization import (
    initialization as initialization_fixture,
)
from examples.adaptive_multi_agent_collab.tests.test_successor_lifecycle import (
    registered_archive as archive_fixture,
)


@pytest.fixture
def initialization(tmp_path, monkeypatch):
    return initialization_fixture.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def retired(tmp_path):
    return archive_fixture.__wrapped__(tmp_path)


def _write(path, value):
    path.write_text(json.dumps(value))


def _complete_fixture(kwargs):
    """A real journal publication around a minimal historical checkpoint."""
    directory = kwargs["campaign"]
    ledger = campaigns._read(directory / "expenditure_ledger.json")
    ledger["entries"] = [
        {"dispatch_id": 0, "status": "completed", "reserved": 0.1,
         "charged": 0.02,
         "usage": {"input_tokens": 8, "output_tokens": 4, "reference_cost": 0.02}}
    ]
    ledger["halted"] = None
    payload = {
        "schema_version": 11,  # Read-only archival is independent of today's schema.
        "request_boundary_pending": False,
        "trainer_state": {"pilot_complete": True, "phase": "pilot_complete"},
        "hook_state": {"expenditure_ledger": ledger},
    }
    checkpoint = directory / "checkpoint.pt"
    torch.save(payload, checkpoint)
    (directory / "accounting_progress.jsonl").unlink()  # Disposable fixture only.
    writer = DurableJournal([directory], ledger)
    try:
        writer.publish(ledger, checkpoint, safe=True)
    finally:
        asyncio.run(writer.aclose())
    _write(directory / "result.json", {"stage_complete": True, "pilot_complete": True})
    _write(directory / "pilot_record.json", {
        "stage_complete": True, "planned_workflow_attempts": 1,
        "attempted_workflows": 1, "completed_workflows": 1,
        "accounting_complete_workflows": 1, "observations": [{"successful": True}],
    })
    kwargs["artifacts"] = {
        str(path): path.name for path in directory.rglob("*") if path.is_file()
    }


def test_completed_archive_preserves_authoritative_outcome_and_bytes(retired):
    kwargs, pilot1, sentinel = retired
    _complete_fixture(kwargs)
    originals = {name: Path(name).read_bytes() for name in kwargs["artifacts"]}
    result = campaigns.archive_campaign(**kwargs)
    assert result["execution_complete"] is True
    assert result["accounting_complete"] is True
    assert result["accounting_status"] == "accounting-complete"
    manifest = campaigns._read(result["manifest"])
    for row in manifest["artifacts"]:
        assert Path(row["archive_path"]).read_bytes() == originals[row["original_path"]]
    registry = campaigns._read(kwargs["root"] / "runs/campaign_registry.json")
    assert registry["campaigns"]["Pilot_1"] == pilot1
    assert Path(kwargs["shared"][0]).read_bytes() == sentinel


@pytest.mark.parametrize("bad", ["checkpoint", "publication", "ledger"])
def test_completed_archive_rejects_conflicting_authoritative_state(retired, bad):
    kwargs, _, _ = retired
    _complete_fixture(kwargs)
    campaign = kwargs["campaign"]
    if bad == "checkpoint":
        path = campaign / "checkpoint.pt"
        payload = torch.load(path, weights_only=False)
        payload["request_boundary_pending"] = True
        torch.save(payload, path)
    elif bad == "publication":
        (campaign / "accounting_progress.jsonl").unlink()
    else:
        path = campaign / "expenditure_ledger.json"
        ledger = campaigns._read(path)
        ledger["entries"][0]["charged"] = 0.03
        _write(path, ledger)
    if bad == "publication":
        kwargs["artifacts"].pop(str(campaign / "accounting_progress.jsonl"))
    with pytest.raises(ValueError, match="completed archival"):
        campaigns.archive_campaign(**kwargs)
    assert not kwargs["destination"].exists()


def test_archive_with_missing_usage_is_not_declared_accounting_complete(retired):
    kwargs, _, _ = retired
    ledger_path = kwargs["campaign"] / "expenditure_ledger.json"
    value = campaigns._read(ledger_path)
    for row in value["entries"]:
        row["charged"] = 0.01
    _write(ledger_path, value)
    result = campaigns.archive_campaign(**kwargs)
    assert not result["execution_complete"] and not result["accounting_complete"]


def test_remove_stub_preserves_unique_metadata_and_original_archive(retired):
    kwargs, _, _ = retired
    closure = campaigns.archive_campaign(**kwargs)
    destination, directory = kwargs["destination"], kwargs["campaign"]
    original = (destination / "ARCHIVE_MANIFEST.json").read_bytes()
    _write(directory / "CLOSED.json", closure)
    (directory / "empty").mkdir()
    (directory / "audit.txt").write_text("Unique old closure audit, preserve unchanged")
    result = campaigns.remove_closed_campaign_directory(
        directory, authorization=kwargs["authorization"], root=kwargs["root"]
    )
    assert result["former_directory_removed"] and result["supplemental_files"] == 2
    assert not directory.exists()
    assert (destination / "ARCHIVE_MANIFEST.json").read_bytes() == original
    supplement = campaigns._read(result["supplement"])
    assert {row["role"] for row in supplement["artifacts"]} == {"retired-directory-closure-or-audit-metadata"}
    for row in supplement["artifacts"]:
        assert campaigns._sha(row["archive_path"]) == row["sha256"]
    assert campaigns.remove_closed_campaign_directory(
        directory, authorization=kwargs["authorization"], root=kwargs["root"]
    ) == result
    assert not directory.exists()
    with pytest.raises(ValueError, match="closed"):
        with campaigns.campaign_lock(directory, root=kwargs["root"]):
            raise AssertionError("closed campaign reopened")
    assert not directory.exists()


def test_cleanup_interruption_resumes_without_loss(retired, monkeypatch):
    kwargs, _, _ = retired
    campaigns.archive_campaign(**kwargs)
    directory = kwargs["campaign"]
    _write(directory / "CLOSED.json", {"original": True})
    move = campaigns._move_verified
    once = []

    def interrupt(source, target, digest):
        move(source, target, digest)
        if not once:
            once.append(True)
            raise OSError("interrupted after verified copy")

    monkeypatch.setattr(campaigns, "_move_verified", interrupt)
    with pytest.raises(OSError, match="interrupted"):
        campaigns.remove_closed_campaign_directory(directory, authorization=kwargs["authorization"], root=kwargs["root"])
    monkeypatch.setattr(campaigns, "_move_verified", move)
    result = campaigns.remove_closed_campaign_directory(directory, authorization=kwargs["authorization"], root=kwargs["root"])
    assert result["former_directory_removed"]
    campaigns.verify_archive(kwargs["destination"], root=kwargs["root"])


def test_retirement_cannot_unlink_active_legacy_lock(retired):
    kwargs, _, _ = retired
    campaigns.archive_campaign(**kwargs)
    directory = kwargs["campaign"]
    with (directory / ".campaign.lock").open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="another process"):
            campaigns.remove_closed_campaign_directory(directory, authorization=kwargs["authorization"], root=kwargs["root"])
        assert directory.exists()


def test_execution_and_retirement_share_stable_and_legacy_gates(retired, monkeypatch):
    kwargs, _, _ = retired
    monkeypatch.setattr(campaigns, "ROOT", kwargs["root"])
    directory = kwargs["campaign"]
    with experiment._campaign_lock(directory) as guard:
        assert guard()
        with pytest.raises(ValueError, match="another process"):
            campaigns.archive_campaign(**kwargs)
        with pytest.raises(ValueError, match="another process"):
            with experiment._campaign_lock(directory):
                pytest.fail("a second executor acquired the same campaign")
    with (directory / ".campaign.lock").open("a+b") as legacy:
        fcntl.flock(legacy.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="another process"):
            with experiment._campaign_lock(directory):
                pytest.fail("legacy writer was not excluded")


def test_retirement_rejects_symlink_without_following_or_removing(retired):
    kwargs, _, _ = retired
    campaigns.archive_campaign(**kwargs)
    directory = kwargs["campaign"]
    (directory / "unsafe").symlink_to(Path(kwargs["shared"][0]))
    with pytest.raises(ValueError, match="symlink"):
        campaigns.remove_closed_campaign_directory(directory, authorization=kwargs["authorization"], root=kwargs["root"])
    assert (directory / "unsafe").is_symlink()


def _pilot4(item):
    formal = item.source.parent / "synthetic_source.tex"
    formal.write_bytes(b"\\documentclass{article}\nSynthetic source fixture.\n")
    destination = item.destination.with_name("Pilot_4")
    kwargs = {**item.kwargs, "specification_sha256": campaigns._sha(formal), "specification_attachment": formal}
    return destination, formal, lambda: campaigns.initialize_pilot(item.source, destination, **kwargs)


def test_pilot4_exact_snapshot_fresh_state_and_duplicate_nonreset(initialization):
    item = initialization
    destination, formal, initialize = _pilot4(item)
    receipt = initialize()
    snapshot = destination / "mathematical_specification.tex"
    assert snapshot.read_bytes() == formal.read_bytes()
    manifest = campaigns._read(destination / "manifest.json")
    assert manifest["specification_attachment"] == str(snapshot)
    assert receipt["generation_authorized"] is False and receipt["policy_activated"] is False
    payload = torch.load(destination / "checkpoint.pt", weights_only=False)
    from examples.adaptive_multi_agent_collab.learning.training import CheckpointManager
    assert payload["schema_version"] == CheckpointManager.schema_version
    assert payload["hook_state"]["expenditure_ledger"]["entries"] == []
    assert not payload["alpha_optimizer"]["state"] and not payload["weighting_optimizer"]["state"]
    before = {p: p.read_bytes() for p in destination.iterdir() if p.is_file()}
    assert initialize() == receipt
    assert all(p.read_bytes() == value for p, value in before.items())
    assert item.invocations == []


def test_removed_closed_id_is_rejected_before_any_recreated_path(initialization):
    item = initialization
    destination, _, initialize = _pilot4(item)
    registry_path = destination.parent / "campaign_registry.json"
    _write(registry_path, {"protocol": campaigns.REGISTRY_PROTOCOL, "campaigns": {
        "Pilot_4": {"directory": str(destination), "lifecycle": "closed-superseded"}
    }})
    with pytest.raises(ValueError, match="closed"):
        initialize()
    assert not destination.exists()


def test_closed_ordinary_execution_path_cannot_recreate_lock(retired, monkeypatch):
    kwargs, _, _ = retired
    campaigns.archive_campaign(**kwargs)
    directory = kwargs["campaign"]
    campaigns.remove_closed_campaign_directory(directory, authorization=kwargs["authorization"], root=kwargs["root"])
    monkeypatch.setattr(campaigns, "ROOT", kwargs["root"])
    with pytest.raises(ValueError, match="closed"):
        with experiment._campaign_lock(directory):
            raise AssertionError("closed execution allowed")
    assert not directory.exists()


def test_closed_ledger_rejects_before_reading_removed_manifest(retired, monkeypatch):
    from types import SimpleNamespace
    kwargs, _, _ = retired
    ledger = campaigns._read(kwargs["campaign"] / "expenditure_ledger.json")
    campaigns.archive_campaign(**kwargs)
    campaigns.remove_closed_campaign_directory(kwargs["campaign"], authorization=kwargs["authorization"], root=kwargs["root"])
    monkeypatch.setattr(campaigns, "ROOT", kwargs["root"])
    with pytest.raises(ValueError, match="closed"):
        campaigns.assert_registered_dispatch(SimpleNamespace(campaign_binding=ledger["campaign_binding"]))
    assert not kwargs["campaign"].exists()


def test_pilot4_snapshot_interruption_resumes_same_empty_state(initialization, monkeypatch):
    item = initialization
    destination, formal, initialize = _pilot4(item)
    write = campaigns._write
    once = []

    def interrupted(path, value):
        if path == destination / "manifest.json" and not once:
            once.append(True)
            raise OSError("interrupted after exact source snapshot")
        return write(path, value)

    monkeypatch.setattr(campaigns, "_write", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        initialize()
    assert (destination / "mathematical_specification.tex").read_bytes() == formal.read_bytes()
    monkeypatch.setattr(campaigns, "_write", write)
    receipt = initialize()
    assert receipt["operational_balances"]["pilot"]["attempts"] == 0
    assert receipt["generation_authorized"] is False and item.invocations == []


def test_pilot4_mock_activation_and_credential_free_preflight(initialization, monkeypatch):
    from types import SimpleNamespace
    from examples.adaptive_multi_agent_collab.operations import policy_activation, preflight
    from examples.adaptive_multi_agent_collab.tests.test_pilot2_policy import _approve

    item = initialization
    destination, _, initialize = _pilot4(item)
    initialize()
    before = torch.load(destination / "checkpoint.pt", weights_only=False)
    approval = _approve(SimpleNamespace(anchor=destination))
    receipt = policy_activation.activate_policy_plan(destination / "policy_plan.json", approval)
    assert receipt["pilot_authorized"] is False
    activated = torch.load(destination / "checkpoint.pt", weights_only=False)
    assert activated["trainer_state"] == before["trainer_state"]
    assert activated["alpha_optimizer"] == before["alpha_optimizer"]
    assert activated["weighting_optimizer"] == before["weighting_optimizer"]
    assert experiment.validate_journal_resume(destination / "checkpoint.pt", destination)["entries"] == []
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    report = preflight.inspect_preflight(
        destination / "manifest.json", operation="pilot", checkpoint_path=destination / "checkpoint.pt"
    )
    codes = {issue.code for issue in report.issues}
    assert "failure_policy_activation" not in codes
    assert not report.ready and "stage_authorization" in codes
    assert item.invocations == []
