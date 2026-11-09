# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Normal reads and publication must not depend on successful usage accounting."""

import json
import os
import shutil

import pytest

from openjiuwen.harness.personal_context import context_pipeline as pipeline
from openjiuwen.harness.personal_context import context_usage as usage


def _tree(home):
    root = home / "workspace/context"
    root.mkdir(parents=True)
    (root / "description.md").write_text("# Context\n", encoding="utf-8")
    (root / "page.md").write_text("# Body\nOriginal", encoding="utf-8")
    os.utime(root / "page.md", (100, 100))
    return root


@pytest.mark.parametrize("path", ["page.md", "description.md"])
def test_busy_usage_lock_does_not_block_read(tmp_path, path):
    root = _tree(tmp_path)
    with usage.usage_lock(tmp_path).acquire(timeout=0):
        assert usage.read_with_usage(tmp_path, path, allowed=lambda: True)["markdown"] == (
            root / path
        ).read_bytes().decode("utf-8")
    assert not (tmp_path / "state/context_usage.json").exists()
    usage.read_with_usage(tmp_path, "page.md", allowed=lambda: True)
    assert usage._load_usage(tmp_path)["pages"]["page.md"]["last_read_at"] is not None


@pytest.mark.parametrize("operation", ["read", "publish"])
@pytest.mark.parametrize("damaged", [
    "{", '{"version": 1, "pages": {"page.md": {}}}', b"\xff",
    '{"version": 1, "pages": {"page.md": {"fingerprint": "x", "present": true, '
    '"created_at": 100, "updated_at": 100, "last_read_at": NaN}}}',
])
def test_damaged_current_ledger_recovers_without_manual_repair(tmp_path, operation, damaged):
    root = _tree(tmp_path)
    ledger = tmp_path / "state/context_usage.json"
    ledger.parent.mkdir()
    ledger.write_bytes(damaged if isinstance(damaged, bytes) else damaged.encode("utf-8"))
    if operation == "read":
        assert usage.read_with_usage(tmp_path, "page.md", allowed=lambda: True)["markdown"].startswith("# Body")
    else:
        candidate = tmp_path / "candidate"
        shutil.copytree(root, candidate)
        (candidate / "page.md").write_text("# Body\nUpdated", encoding="utf-8")
        usage.publish_with_usage(tmp_path, candidate, {})
    value = json.loads(ledger.read_text(encoding="utf-8"))
    assert value["version"] == 1
    record = value["pages"]["page.md"]
    assert record["present"] and record["fingerprint"] == usage._fingerprint(root, "page.md", {})
    if operation == "read":
        assert record["created_at"] == record["updated_at"] == 100
        assert record["last_read_at"] > 100
    assert not (tmp_path / "context_usage.suspended").exists()


def test_unknown_future_ledger_is_not_overwritten_by_reads_or_publication(tmp_path):
    from openjiuwen.harness.personal_context import context_capacity

    root = _tree(tmp_path)
    ledger = tmp_path / "state/context_usage.json"
    ledger.parent.mkdir()
    original = '{"version": 2, "pages": {"future": "schema"}}'
    ledger.write_text(original, encoding="utf-8")
    assert usage.read_with_usage(tmp_path, "page.md", allowed=lambda: True)["markdown"].startswith("# Body")
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    (candidate / "page.md").write_text("# Updated\n" + "x" * 2000, encoding="utf-8")
    usage.publish_with_usage(tmp_path, candidate, {})
    assert (root / "page.md").read_text(encoding="utf-8").startswith("# Updated")
    with pytest.raises(ValueError, match="version"):
        context_capacity.maintain_capacity(tmp_path, limit_bytes=1000)
    assert ledger.read_text(encoding="utf-8") == original
    assert (root / "page.md").exists()


@pytest.mark.parametrize("failure", ["busy", "unavailable"])
def test_usage_lock_failure_does_not_block_publication(tmp_path, monkeypatch, failure):
    root = _tree(tmp_path)
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    (candidate / "page.md").write_text("# Body\nUpdated", encoding="utf-8")
    lock = usage.usage_lock(tmp_path)
    with lock.acquire(timeout=0):
        if failure == "unavailable":
            def unavailable(_home):
                raise OSError("accounting directory unavailable")

            monkeypatch.setattr(usage, "usage_lock", unavailable)
        usage.publish_with_usage(tmp_path, candidate, {})
    assert (root / "page.md").read_text(encoding="utf-8") == "# Body\nUpdated"


def test_unavailable_accounting_directory_does_not_block_read(tmp_path, monkeypatch):
    _tree(tmp_path)

    def unavailable(_home):
        raise OSError("accounting directory unavailable")

    monkeypatch.setattr(usage, "usage_lock", unavailable)
    assert usage.read_with_usage(tmp_path, "page.md", allowed=lambda: True)["markdown"].splitlines() == ["# Body", "Original"]


def test_read_fingerprint_reuses_returned_body(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    original = usage._fingerprint
    seen = []

    def fingerprint(root, relative, relocations, *, markdown=None):
        seen.append(markdown)
        assert markdown is not None
        return original(root, relative, relocations, markdown=markdown)

    monkeypatch.setattr(usage, "_fingerprint", fingerprint)
    usage.read_with_usage(tmp_path, "page.md", allowed=lambda: True)
    assert seen == [(root / "page.md").read_bytes().decode("utf-8")]
    assert usage._load_usage(tmp_path)["pages"]["page.md"]["fingerprint"] == original(root, "page.md", {})


@pytest.mark.parametrize("move", [False, True])
def test_publication_reuses_snapshots_and_preserves_usage(tmp_path, monkeypatch, move):
    root = _tree(tmp_path)
    (root / "linked.md").write_text("# Link\n[Body](page.md)", encoding="utf-8")
    os.utime(root / "linked.md", (100, 100))
    usage.read_with_usage(tmp_path, "page.md", allowed=lambda: True)
    usage.read_with_usage(tmp_path, "linked.md", allowed=lambda: True)
    previous = usage._load_usage(tmp_path)["pages"]
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    relocations = {}
    target = "page.md"
    if move:
        target = "moved.md"
        (candidate / "page.md").rename(candidate / target)
        (candidate / "linked.md").write_text("# Link\n[Body](moved.md)", encoding="utf-8")
        relocations["page.md"] = target
    before_usage, after_usage = {}, {}
    pipeline._snapshot_context_nodes(root, relocations=relocations, usage=before_usage)
    pipeline._snapshot_context_nodes(candidate, usage=after_usage)

    def unexpected_scan(*args, **kwargs):
        pytest.fail("publication must reuse the existing sandbox snapshots")

    monkeypatch.setattr(usage, "_pages", unexpected_scan)
    usage.publish_with_usage(tmp_path, candidate, relocations, snapshots=(before_usage, after_usage))
    records = usage._load_usage(tmp_path)["pages"]
    assert records[target]["created_at"] == records[target]["updated_at"] == 100
    assert records[target]["last_read_at"] == previous["page.md"]["last_read_at"]
    assert records["linked.md"]["updated_at"] == 100
    assert records["linked.md"]["last_read_at"] == previous["linked.md"]["last_read_at"]


def test_optional_snapshot_fingerprint_failure_does_not_block_publication(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    (candidate / "page.md").write_text("# Updated", encoding="utf-8")

    def fail(*args, **kwargs):
        raise OSError("optional fingerprint failure")

    monkeypatch.setattr(usage, "_fingerprint", fail)
    before_usage, after_usage = {}, {}
    before = pipeline._snapshot_context_nodes(root, usage=before_usage)
    after = pipeline._snapshot_context_nodes(candidate, usage=after_usage)
    assert pipeline._count_published_nodes(before, after)["updated_node_count"] == 1
    usage.publish_with_usage(tmp_path, candidate, {}, snapshots=(before_usage, after_usage))
    assert (root / "page.md").read_text(encoding="utf-8") == "# Updated"


def test_read_during_moved_publication_succeeds_without_mixing_page_records(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    usage.read_with_usage(tmp_path, "page.md", allowed=lambda: True)
    original_read = usage._load_usage(tmp_path)["pages"]["page.md"]["last_read_at"]
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    (candidate / "page.md").rename(candidate / "moved.md")
    original_commit = pipeline._commit_context_tree
    observed = []

    def commit_and_read(*args):
        original_commit(*args)
        observed.append(usage.read_with_usage(tmp_path, "moved.md", allowed=lambda: True)["markdown"])

    monkeypatch.setattr(pipeline, "_commit_context_tree", commit_and_read)
    usage.publish_with_usage(tmp_path, candidate, {"page.md": "moved.md"})
    assert observed == [(root / "moved.md").read_bytes().decode("utf-8")]
    assert usage._load_usage(tmp_path)["pages"]["moved.md"]["last_read_at"] == original_read


def test_candidate_only_rehashes_changed_bodies_even_with_same_size_and_mtime(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    (root / "unchanged.md").write_text("# Unchanged", encoding="utf-8")
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    (candidate / "page.md").write_text("# Body\nModified", encoding="utf-8")
    os.utime(candidate / "page.md", (100, 100))
    assert (candidate / "page.md").stat().st_size == (root / "page.md").stat().st_size
    before_usage, after_usage = {}, {}
    before = pipeline._snapshot_context_nodes(root, usage=before_usage)
    original = usage._fingerprint
    computed = []

    def fingerprint(root, relative, relocations, *, markdown=None):
        assert markdown is not None
        computed.append(relative)
        return original(root, relative, relocations, markdown=markdown)

    monkeypatch.setattr(usage, "_fingerprint", fingerprint)
    pipeline._snapshot_context_nodes(candidate, usage=after_usage, previous=(before, before_usage))
    assert computed == ["page.md"]
    usage.publish_with_usage(tmp_path, candidate, {}, snapshots=(before_usage, after_usage))
    records = usage._load_usage(tmp_path)["pages"]
    assert records["page.md"]["updated_at"] > 100
    assert records["unchanged.md"]["updated_at"] == before_usage["unchanged.md"][2]


def test_path_swap_moves_read_times_with_body_identity(tmp_path):
    root = _tree(tmp_path)
    (root / "cold.md").write_text("# Cold", encoding="utf-8")
    os.utime(root / "cold.md", (100, 100))
    usage.read_with_usage(tmp_path, "page.md", allowed=lambda: True)
    hot_read = usage._load_usage(tmp_path)["pages"]["page.md"]["last_read_at"]
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    hot, cold = (candidate / "page.md").read_bytes(), (candidate / "cold.md").read_bytes()
    (candidate / "page.md").write_bytes(cold)
    (candidate / "cold.md").write_bytes(hot)
    usage.publish_with_usage(tmp_path, candidate, {"page.md": "cold.md", "cold.md": "page.md"})
    records = usage._load_usage(tmp_path)["pages"]
    assert records["cold.md"]["last_read_at"] == hot_read
    assert records["page.md"]["last_read_at"] is None


def test_usage_lock_release_error_does_not_turn_publication_into_failure(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    (candidate / "page.md").write_text("# Updated", encoding="utf-8")
    lock = usage.usage_lock(tmp_path)
    original_release = lock.release

    def release_with_error(*args, **kwargs):
        original_release(*args, **kwargs)
        raise OSError("accounting lock release error")

    with monkeypatch.context() as patcher:
        patcher.setattr(lock, "release", release_with_error)
        patcher.setattr(usage, "usage_lock", lambda _home: lock)
        usage.publish_with_usage(tmp_path, candidate, {})
    assert (root / "page.md").read_text(encoding="utf-8") == "# Updated"
