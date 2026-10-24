"""Successor-only fixtures preserve historical evidence and never dispatch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from examples.adaptive_multi_agent_collab.operations import (
    campaigns,
    experiment,
    policy_activation,
)
from examples.adaptive_multi_agent_collab.infrastructure.persistence import read_journal
from examples.adaptive_multi_agent_collab.tests.test_pilot2_initialization import (
    initialization as initialization_fixture,
)
from examples.adaptive_multi_agent_collab.learning.training import CheckpointManager


@pytest.fixture
def initialization(tmp_path, monkeypatch):
    """Reuse the existing compact production-build fixture without a new model path."""
    return initialization_fixture.__wrapped__(tmp_path, monkeypatch)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def registered_archive(tmp_path):
    root = tmp_path / "scope"
    campaign = root / "runs/Pilot_2"
    campaign.mkdir(parents=True)
    source = campaign / "manifest.json"
    specification = {
        "campaign_directory": str(campaign),
        "independent_campaign": {
            "protocol": campaigns.CAMPAIGN_PROTOCOL,
            "campaign_id": "Pilot_2",
            "registry_path": str(root / "runs/campaign_registry.json"),
        },
    }
    _write(source, specification)
    ledger = {
        "campaign_binding": campaigns.campaign_binding(source, specification),
        "entries": [
            {
                "dispatch_id": index,
                "charged": None,
                "reference_cost": None,
                "reserved": hold,
                "status": "dispatched",
            }
            for index, hold in ((80, 0.0018432), (81, 0.0028032), (82, 0.024192))
        ],
        "halted": "historical interruption: immutable",
    }
    _write(campaign / "expenditure_ledger.json", ledger)
    (campaign / "checkpoint.pt").write_bytes(
        b"Historical unsafe checkpoint: do not deserialize"
    )
    (campaign / "accounting_progress.jsonl").write_bytes(
        b"original historical journal\n"
    )
    pilot1 = {
        "campaign_id": "Pilot_1",
        "directory": str(root / "runs/strategyqa"),
        "lifecycle": "closed-superseded",
    }
    registration = {
        "campaign_id": "Pilot_2",
        "directory": str(campaign),
        "manifest_path": str(source),
        "manifest_sha256": campaigns._sha(source),
        "specification_digest": campaigns._digest(specification),
        "lifecycle": "registered",
        "condition_fingerprint": "original Pilot2 condition",
    }
    _write(
        root / "runs/campaign_registry.json",
        {
            "protocol": campaigns.REGISTRY_PROTOCOL,
            "campaigns": {"Pilot_1": pilot1, "Pilot_2": registration},
        },
    )
    sentinel = root / "archive/Pilot_1/evidence.json"
    _write(sentinel, {"unchanged": True})
    artifacts = {str(path): path.name for path in campaign.iterdir()}
    return (
        {
            "campaign": campaign,
            "destination": root / "archive/Pilot_2",
            "artifacts": artifacts,
            "shared": [str(sentinel)],
            "authorization": {
                "source": "synthetic offline test",
                "scope": "close Pilot2 only",
            },
            "root": root,
            "campaign_id": "Pilot_2",
        },
        pilot1,
        sentinel.read_bytes(),
    )


def test_registered_successor_archive_preserves_identity_unknowns_and_pilot1(
    registered_archive,
):
    kwargs, pilot1, sentinel = registered_archive
    originals = {path: Path(path).read_bytes() for path in kwargs["artifacts"]}
    result = campaigns.archive_campaign(**kwargs)
    assert result["campaign_id"] == "Pilot_2"
    assert result["accounting_status"] == "accounting-unresolved"
    assert (
        result["execution_complete"] is False and result["accounting_complete"] is False
    )
    registry = campaigns._read(kwargs["root"] / "runs/campaign_registry.json")
    assert registry["campaigns"]["Pilot_1"] == pilot1
    assert Path(kwargs["shared"][0]).read_bytes() == sentinel
    manifest = campaigns._read(result["manifest"])
    for row in manifest["artifacts"]:
        assert Path(row["archive_path"]).read_bytes() == originals[row["original_path"]]
    ledger_row = next(
        row for row in manifest["artifacts"] if row["role"] == "expenditure_ledger.json"
    )
    ledger = campaigns._read(ledger_row["archive_path"])
    assert sum(item["reserved"] for item in ledger["entries"]) == pytest.approx(
        0.0288384
    )
    assert all(
        item["charged"] is None and item["reference_cost"] is None
        for item in ledger["entries"]
    )
    assert campaigns.archive_campaign(**kwargs) == result
    for directory in (kwargs["campaign"], kwargs["destination"]):
        with pytest.raises(ValueError, match="closed|archived"):
            campaigns.assert_campaign_open(directory, root=kwargs["root"])


@pytest.mark.parametrize("wrong", ["id", "manifest", "ledger", "selection"])
def test_archive_rejects_wrong_registered_identity_before_state_change(
    registered_archive, wrong
):
    kwargs, _, _ = registered_archive
    if wrong == "id":
        kwargs["campaign_id"] = "Pilot_1"
    elif wrong == "selection":
        kwargs["artifacts"].pop(str(kwargs["campaign"] / "checkpoint.pt"))
    else:
        path = kwargs["campaign"] / (
            "manifest.json" if wrong == "manifest" else "expenditure_ledger.json"
        )
        value = campaigns._read(path)
        value["independent_campaign" if wrong == "manifest" else "campaign_binding"][
            "campaign_id"
        ] = "Pilot_1"
        _write(path, value)
    registry = (kwargs["root"] / "runs/campaign_registry.json").read_bytes()
    with pytest.raises(ValueError, match="identity|manifest|ledger|selection"):
        campaigns.archive_campaign(**kwargs)
    assert (kwargs["root"] / "runs/campaign_registry.json").read_bytes() == registry
    assert not kwargs["destination"].exists()


def test_interrupted_registered_archive_resumes_without_touching_pilot1(
    registered_archive, monkeypatch
):
    kwargs, pilot1, sentinel = registered_archive
    original = campaigns._move_verified
    moved = []

    def interrupt(source, target, sha256):
        if moved:
            raise OSError("injected archival interruption")
        original(source, target, sha256)
        moved.append(target)

    monkeypatch.setattr(campaigns, "_move_verified", interrupt)
    with pytest.raises(OSError, match="interruption"):
        campaigns.archive_campaign(**kwargs)
    with pytest.raises(ValueError, match="closed"):
        campaigns.assert_campaign_open(kwargs["campaign"], root=kwargs["root"])
    monkeypatch.setattr(campaigns, "_move_verified", original)
    result = campaigns.archive_campaign(**kwargs)
    assert result["lifecycle"] == "closed-superseded"
    assert (
        campaigns._read(kwargs["root"] / "runs/campaign_registry.json")["campaigns"][
            "Pilot_1"
        ]
        == pilot1
    )
    assert Path(kwargs["shared"][0]).read_bytes() == sentinel


def _successor(item):
    destination = item.destination.with_name("Pilot_3")
    return destination, lambda: campaigns.initialize_pilot(
        item.source, destination, **item.kwargs
    )


def test_pilot3_uses_fresh_schema11_journal_and_no_predecessor_runtime_dependency(
    initialization,
):
    item = initialization
    source = campaigns._read(item.source)
    source["condition_change"] = {"old": str(item.destination)}
    source["role_recheck"] = {"old_checkpoint": str(item.destination / "checkpoint.pt")}
    _write(item.source, source)
    destination, initialize = _successor(item)
    receipt = initialize()
    manifest = campaigns._read(destination / "manifest.json")
    payload = torch.load(destination / "checkpoint.pt", weights_only=False)
    journal = read_journal(destination)
    assert payload["schema_version"] == CheckpointManager.schema_version
    assert journal["boundary"] is not None
    assert journal["state"]["entries"] == []
    assert journal["state"]["campaign_binding"]["campaign_id"] == "Pilot_3"
    assert manifest["independent_campaign"]["campaign_id"] == "Pilot_3"
    assert "condition_change" not in manifest and "role_recheck" not in manifest
    assert (
        receipt["policy_activated"] is False
        and receipt["generation_authorized"] is False
    )
    assert (
        not item.builds[-1][0].durable_journal._thread.is_alive()
        or item.builds[-1][0].durable_journal._closed
    )
    assert (
        experiment.validate_journal_resume(destination / "checkpoint.pt", destination)
        == journal["state"]
    )
    before = {
        path: path.read_bytes() for path in destination.iterdir() if path.is_file()
    }
    assert initialize() == receipt
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert item.invocations == []


@pytest.mark.parametrize("phase", ["manifest", "checkpoint", "publication"])
def test_pilot3_initialization_crash_is_restartable_without_reset(
    initialization, monkeypatch, phase
):
    item = initialization
    destination, initialize = _successor(item)
    write = campaigns._write
    save = CheckpointManager.write_payload
    from examples.adaptive_multi_agent_collab.infrastructure.persistence import DurableJournal

    publish = DurableJournal.publish
    once = []

    def fail_write(path, value):
        if path == destination / "manifest.json" and not once:
            once.append(True)
            raise OSError("injected manifest interruption")
        return write(path, value)

    def fail_save(*args, **kwargs):
        if not once:
            once.append(True)
            raise OSError("injected checkpoint interruption")
        return save(*args, **kwargs)

    def fail_publish(self, *args, **kwargs):
        if not once:
            once.append(True)
            raise OSError("injected publication interruption")
        return publish(self, *args, **kwargs)

    if phase == "manifest":
        monkeypatch.setattr(campaigns, "_write", fail_write)
    elif phase == "checkpoint":
        monkeypatch.setattr(CheckpointManager, "write_payload", fail_save)
    else:
        monkeypatch.setattr(DurableJournal, "publish", fail_publish)
    with pytest.raises(OSError, match="injected"):
        initialize()
    entry = campaigns._read(destination.parent / "campaign_registry.json")["campaigns"][
        "Pilot_3"
    ]
    assert entry["lifecycle"] == "initializing"
    assert not (destination / "initialization.json").exists()
    monkeypatch.setattr(campaigns, "_write", write)
    monkeypatch.setattr(CheckpointManager, "write_payload", save)
    monkeypatch.setattr(DurableJournal, "publish", publish)
    receipt = initialize()
    assert receipt["generation_authorized"] is False
    assert read_journal(destination)["state"]["entries"] == []
    assert experiment.validate_journal_resume(
        destination / "checkpoint.pt", destination
    )
    assert item.invocations == []


def test_pilot3_rejects_existing_unregistered_or_closed_state(initialization):
    item = initialization
    destination, initialize = _successor(item)
    destination.mkdir()
    marker = destination / "unexpected.json"
    marker.write_text("Unregistered evidence must remain untouched")
    with pytest.raises(ValueError, match="unregistered"):
        initialize()
    assert marker.read_text() == "Unregistered evidence must remain untouched"


def test_pilot3_mismatched_restart_does_not_replace_initial_state(initialization):
    item = initialization
    destination, initialize = _successor(item)
    initialize()
    before = (destination / "checkpoint.pt").read_bytes()
    with pytest.raises(ValueError, match="mathematical condition"):
        campaigns.initialize_pilot(
            item.source,
            destination,
            **{**item.kwargs, "specification_sha256": "c" * 64},
        )
    assert (destination / "checkpoint.pt").read_bytes() == before


def test_pilot3_closed_registration_never_reinitializes_or_replenishes(initialization):
    destination, initialize = _successor(initialization)
    initialize()
    path = destination.parent / "campaign_registry.json"
    registry = campaigns._read(path)
    registry["campaigns"]["Pilot_3"]["lifecycle"] = "closed-superseded"
    _write(path, registry)
    checkpoint = (destination / "checkpoint.pt").read_bytes()
    with pytest.raises(ValueError, match="closed campaign"):
        initialize()
    assert (destination / "checkpoint.pt").read_bytes() == checkpoint


def test_explicit_formal_attachment_is_verified_without_copying_source(initialization):
    item = initialization
    destination, _ = _successor(item)
    formal = destination.parent / "synthetic_formal.txt"
    formal.write_text("Synthetic formal source: no actual mathematical source copied.")
    with pytest.raises(ValueError, match="attachment.*SHA256"):
        campaigns.initialize_pilot(
            item.source, destination, **item.kwargs, specification_attachment=formal
        )
    assert not destination.exists()
    kwargs = {
        **item.kwargs,
        "specification_sha256": campaigns._sha(formal),
        "specification_attachment": formal,
    }
    campaigns.initialize_pilot(item.source, destination, **kwargs)
    assert campaigns._read(destination / "manifest.json")[
        "specification_attachment"
    ] == str(formal)
    assert not (destination / formal.name).exists()


def test_pilot3_mock_activation_publishes_schema11_journal_and_preflight(
    initialization, monkeypatch
):
    from examples.adaptive_multi_agent_collab.operations import preflight
    from examples.adaptive_multi_agent_collab.tests.test_pilot2_policy import _approve
    from types import SimpleNamespace

    item = initialization
    destination, initialize = _successor(item)
    initialize()
    old_checkpoint = torch.load(destination / "checkpoint.pt", weights_only=False)
    initial_journal = (destination / "accounting_progress.jsonl").read_bytes()
    approval = _approve(SimpleNamespace(anchor=destination))
    receipt = policy_activation.activate_policy_plan(
        destination / "policy_plan.json", approval
    )
    state = experiment.validate_journal_resume(
        destination / "checkpoint.pt", destination
    )
    assert (
        state["entries"] == [] and state["failure_policy"]["historical_adoptions"] == []
    )
    assert (
        (destination / "accounting_progress.jsonl")
        .read_bytes()
        .startswith(initial_journal)
    )
    assert receipt["pilot_authorized"] is False
    activated = torch.load(destination / "checkpoint.pt", weights_only=False)
    assert activated["schema_version"] == CheckpointManager.schema_version
    assert activated["trainer_state"] == old_checkpoint["trainer_state"]
    for name in ("alpha", "weighting"):
        assert all(
            torch.equal(value, activated[name][key])
            for key, value in old_checkpoint[name].items()
        )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    report = preflight.inspect_preflight(
        destination / "manifest.json",
        operation="pilot",
        checkpoint_path=destination / "checkpoint.pt",
    )
    codes = {issue.code for issue in report.issues}
    assert "failure_policy_activation" not in codes
    assert "revised_condition_lineage" not in codes
    assert report.checks["bounded_failure_policy"]["activated"] is True
    assert report.ready is False and "stage_authorization" in codes
    assert item.invocations == []
