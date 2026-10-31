"""Small offline fixtures for immutable, restart-safe campaign retirement."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from examples.adaptive_multi_agent_collab.operations import campaigns


@pytest.fixture
def campaign_files(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str], dict[str, str]]:
    root = tmp_path / "scope"
    campaign = root / "runs" / "old"
    destination = root / "archive" / "Pilot_1"
    campaign.mkdir(parents=True)
    (campaign / ".campaign.lock").touch()
    checkpoint = campaign / "checkpoint.pt"
    checkpoint.write_bytes(b"original checkpoint\x00\xff")
    ledger = campaign / "expenditure_ledger.json"
    ledger.write_text('{"charged":null,"held":0.0018816,"halted":true}\n')
    observation = root / "reports" / "old" / "observation.json"
    observation.parent.mkdir(parents=True)
    observation.write_text('{"historical_path":"runs/old/checkpoint.pt"}\n')
    artifacts = {str(checkpoint): "checkpoint", str(ledger): "ledger", str(observation): "observation"}
    authorization = {"source": "current explicit user instruction", "scope": "offline archival only"}
    return root, campaign, destination, artifacts, authorization


def _archive(fixture: tuple[Path, Path, Path, dict[str, str], dict[str, str]]) -> dict:
    root, campaign, destination, artifacts, authorization = fixture
    return campaigns.archive_campaign(
        campaign, destination, artifacts=artifacts, shared=[], authorization=authorization, root=root
    )


def test_archive_preserves_bytes_paths_and_unknown_accounting(campaign_files) -> None:
    root, campaign, destination, artifacts, _ = campaign_files
    originals = {path: Path(path).read_bytes() for path in artifacts}
    shared = root / "reports" / "shared.json"
    shared.write_text('{"shared":true}\n')
    result = campaigns.archive_campaign(
        campaign,
        destination,
        artifacts=artifacts,
        shared=[str(shared)],
        authorization=campaign_files[-1],
        root=root,
    )
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert len(manifest["artifacts"]) == len(artifacts)
    for entry in manifest["artifacts"]:
        before = originals[entry["original_path"]]
        assert Path(entry["archive_path"]).read_bytes() == before
        assert entry["sha256"] == hashlib.sha256(before).hexdigest()
        assert entry["role"] == artifacts[entry["original_path"]]
        assert not Path(entry["original_path"]).exists()
    assert shared.read_text() == '{"shared":true}\n'
    campaigns.verify_archive(destination, root=root)
    ledger_entry = next(entry for entry in manifest["artifacts"] if entry["role"] == "ledger")
    assert json.loads(Path(ledger_entry["archive_path"]).read_text()) == {
        "charged": None,
        "held": 0.0018816,
        "halted": True,
    }


def test_duplicate_archive_is_idempotent(campaign_files) -> None:
    root, _, destination, _, _ = campaign_files
    first = _archive(campaign_files)
    original_manifest = (destination / "ARCHIVE_MANIFEST.json").read_bytes()
    second = _archive(campaign_files)
    assert second == first
    assert (destination / "ARCHIVE_MANIFEST.json").read_bytes() == original_manifest
    campaigns.verify_archive(destination, root=root)


def test_interrupted_move_is_closed_and_can_resume(campaign_files, monkeypatch) -> None:
    root, campaign, destination, _, _ = campaign_files
    move = campaigns._move_verified
    completed = []

    def interrupted(source, target, sha256):
        if completed:
            raise RuntimeError("injected interruption")
        move(source, target, sha256)
        completed.append((source, target))

    monkeypatch.setattr(campaigns, "_move_verified", interrupted)
    with pytest.raises(RuntimeError, match="injected interruption"):
        _archive(campaign_files)
    assert len(completed) == 1
    with pytest.raises((ValueError, RuntimeError)):
        campaigns.assert_campaign_open(campaign, root=root)
    with pytest.raises((ValueError, RuntimeError)):
        campaigns.assert_campaign_open(destination, root=root)
    monkeypatch.setattr(campaigns, "_move_verified", move)
    _archive(campaign_files)
    campaigns.verify_archive(destination, root=root)


def test_archived_bytes_collision_is_not_overwritten(campaign_files) -> None:
    root, _, destination, _, _ = campaign_files
    result = _archive(campaign_files)
    manifest = json.loads(Path(result["manifest"]).read_text())
    target = Path(manifest["artifacts"][0]["archive_path"])
    target.write_bytes(b"collision: preserve rather than overwrite")
    with pytest.raises((ValueError, RuntimeError)):
        _archive(campaign_files)
    assert target.read_bytes() == b"collision: preserve rather than overwrite"
    with pytest.raises((ValueError, RuntimeError)):
        campaigns.verify_archive(destination, root=root)


def test_symlink_artifact_outside_registered_root_is_rejected(campaign_files, tmp_path) -> None:
    root, _, _, artifacts, _ = campaign_files
    outside = tmp_path / "outside.txt"
    outside.write_text("unchanged outside fixture root")
    link = root / "linked.txt"
    link.symlink_to(outside)
    artifacts[str(link)] = "unsafe-link"
    with pytest.raises((ValueError, RuntimeError)):
        _archive(campaign_files)
    assert outside.read_text() == "unchanged outside fixture root"


def test_old_campaign_and_archive_cannot_dispatch(campaign_files) -> None:
    root, campaign, destination, _, _ = campaign_files
    campaigns.assert_campaign_open(campaign, root=root)
    _archive(campaign_files)
    for path in (campaign, campaign / "conditions" / "old", destination, destination / "runs" / "old"):
        with pytest.raises((ValueError, RuntimeError)):
            campaigns.assert_campaign_open(path, root=root)


def test_concurrent_campaign_lock_prevents_archive(campaign_files) -> None:
    _, campaign, destination, artifacts, _ = campaign_files
    with (campaign / ".campaign.lock").open("a+b") as active:
        fcntl.flock(active.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises((ValueError, RuntimeError, BlockingIOError)):
            _archive(campaign_files)
        assert all(Path(path).exists() for path in artifacts)
        assert not (destination / "ARCHIVE_MANIFEST.json").exists()


def test_destination_durability_failure_preserves_source_for_resume(campaign_files, monkeypatch) -> None:
    _, _, destination, artifacts, _ = campaign_files
    source = Path(next(iter(artifacts)))
    original = source.read_bytes()
    target = destination / "checkpoint.pt"
    digest = hashlib.sha256(original).hexdigest()
    fsync = os.fsync

    def reject_directory_sync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("injected directory durability failure")
        fsync(descriptor)

    monkeypatch.setattr(campaigns.os, "fsync", reject_directory_sync)
    with pytest.raises(OSError, match="injected directory durability failure"):
        campaigns._move_verified(source, target, digest)
    assert source.read_bytes() == original
    assert target.read_bytes() == original
    monkeypatch.setattr(campaigns.os, "fsync", fsync)
    campaigns._move_verified(source, target, digest)
    assert not source.exists()
    assert target.read_bytes() == original
