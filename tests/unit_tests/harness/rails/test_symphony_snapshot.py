"""Published identity snapshots do not depend on application adapters."""

import json

import pytest

from openjiuwen.harness.rails.evolution import PublishedCapabilitySnapshotProvider
from openjiuwen.harness.rails.evolution.symphony_graph_evolution_rail import (
    PublishedCapabilitySnapshotProvider as RailPublishedCapabilitySnapshotProvider,
)
from openjiuwen.symphony import GraphArtifactStore


def _capability(identity="custom-id", **updates):
    return {
        "capability_id": identity, "capability_type": "skill", "name": "Same name",
        "version": "1", "description": "Summary", "inputs": [{"name": "text", "type": "text", "required": True}],
        "outputs": [], **updates,
    }


def _publish(root, version, capabilities):
    return GraphArtifactStore(root).publish({
        "schema_version": "1.0", "generated_at": "2026-10-26T00:00:00Z", "capabilities": capabilities,
    }, version=version)


def test_snapshot_public_export_uses_rail_implementation():
    assert PublishedCapabilitySnapshotProvider is RailPublishedCapabilitySnapshotProvider


def test_snapshot_reads_current_graph_and_preserves_ids(tmp_path):
    _publish(tmp_path, "v1", [_capability("z"), _capability("a")])
    provider = PublishedCapabilitySnapshotProvider(tmp_path)
    before = provider.snapshot_capabilities()
    assert [item.capability_id for item in before] == ["a", "z"]
    assert before[0].inputs[0]["required"] is True
    _publish(tmp_path, "v2", [_capability("next")])
    assert [item.capability_id for item in provider.snapshot_capabilities()] == ["next"]
    assert [item.capability_id for item in before] == ["a", "z"]


@pytest.mark.parametrize("bad", [
    None, {}, _capability(version=""), _capability(capability_type="invalid"),
    _capability(inputs=[{"name": "x", "type": "text", "required": "true"}]),
    _capability(description=42), _capability(outputs=[{"name": "x"}]),
])
def test_snapshot_filters_malformed_records(tmp_path, bad):
    _publish(tmp_path, "v1", [bad, _capability("valid")])
    result = PublishedCapabilitySnapshotProvider(tmp_path).snapshot_capabilities()
    assert [item.capability_id for item in result] == ["valid"]


def test_snapshot_missing_or_corrupt_graph_is_empty(tmp_path):
    provider = PublishedCapabilitySnapshotProvider(tmp_path)
    assert provider.snapshot_capabilities() == ()
    (tmp_path / "current.json").write_text("not json")
    assert provider.snapshot_capabilities() == ()
    artifact = _publish(tmp_path, "v1", [])
    artifact.graph_path.write_text("[]")
    assert provider.snapshot_capabilities() == ()


def test_snapshot_cyclic_symlink_is_empty(tmp_path):
    (tmp_path / "versions").mkdir()
    (tmp_path / "versions" / "v1").symlink_to("v1", target_is_directory=True)
    (tmp_path / "current.json").write_text(json.dumps({"schema_version": "1.0", "version": "v1"}))
    assert PublishedCapabilitySnapshotProvider(tmp_path).snapshot_capabilities() == ()
