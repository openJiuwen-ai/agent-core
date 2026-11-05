"""Public graph location resolves a single immutable published version."""

import json

import pytest

from openjiuwen.symphony import GraphArtifactStore


def _payload():
    return {"schema_version": "1.0", "generated_at": "2026-10-26T00:00:00Z", "capabilities": []}


def test_current_artifact_uses_published_version_not_staging(tmp_path):
    store = GraphArtifactStore(tmp_path)
    assert store.current_artifact() is None
    first = store.publish(_payload(), version="v1")
    store.stage(_payload(), version="v2")
    assert store.current_artifact() == first
    assert store.current_artifact().graph_path.is_file()


def test_current_artifact_reads_pointer_once(tmp_path, monkeypatch):
    store = GraphArtifactStore(tmp_path)
    first = store.publish(_payload(), version="v1")
    original = store._read_current
    reads = []

    def read_current():
        reads.append(True)
        if len(reads) != 1:
            pytest.fail("A concurrent publish must not mix path and version")
        return original()

    monkeypatch.setattr(store, "_read_current", read_current)
    assert store.current_artifact() == first


def test_legacy_pointer_path_uses_canonical_version(tmp_path):
    store = GraphArtifactStore(tmp_path)
    expected = store.publish(_payload(), version="v1")
    pointer = {"schema_version": "1.0", "version": "v1", "path": "versions/v1"}
    (tmp_path / "current.json").write_text(json.dumps(pointer))
    actual = store.current_artifact()
    assert actual.version == expected.version
    assert actual.graph_path == expected.graph_path
    assert actual.generated_at == ""


@pytest.mark.parametrize("version", ["..", ".", "../escape", "/tmp/escape", ""])
def test_current_artifact_rejects_unsafe_version(tmp_path, version):
    (tmp_path / "current.json").write_text(json.dumps({"schema_version": "1.0", "version": version}))
    with pytest.raises(ValueError):
        GraphArtifactStore(tmp_path).current_artifact()


def test_current_artifact_rejects_symlink_escape(tmp_path):
    root = tmp_path / "graphs"
    (root / "versions").mkdir(parents=True)
    external = tmp_path / "outside"
    external.mkdir()
    (external / "graph.json").write_text(json.dumps(_payload()))
    (root / "versions" / "v1").symlink_to(external, target_is_directory=True)
    (root / "current.json").write_text(json.dumps({"schema_version": "1.0", "version": "v1"}))
    with pytest.raises(ValueError, match="inside"):
        GraphArtifactStore(root).current_artifact()


def test_current_artifact_surfaces_broken_publication(tmp_path):
    store = GraphArtifactStore(tmp_path)
    artifact = store.publish(_payload(), version="v1")
    artifact.graph_path.unlink()
    with pytest.raises(FileNotFoundError):
        store.current_artifact()


def test_current_artifact_defers_payload_validation_to_read(tmp_path, monkeypatch):
    store = GraphArtifactStore(tmp_path)
    store.publish({**_payload(), "schema_version": "2.0"}, version="v1")
    def do_not_read_graph(*_args, **_kwargs):
        pytest.fail("Locating the current version must not parse the full graph")

    with monkeypatch.context() as patch:
        patch.setattr(store, "read", do_not_read_graph)
        assert store.current_artifact().version == "v1"
    with pytest.raises(ValueError, match="Unsupported"):
        store.read("v1")
