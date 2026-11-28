# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Snapshot checks must not consume the short publication mutation window."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock

from openjiuwen.harness.personal_context import _capacity_commit as worker
from openjiuwen.harness.personal_context import context_capacity as capacity
from openjiuwen.harness.personal_context import context_retirement as retirement


def _plan(home, count=1):
    root = home / "workspace/context"
    root.mkdir(parents=True)
    candidate = home / "candidate"
    candidate.mkdir()
    (home / "state").mkdir()
    (root / "description.md").write_text("# Root\n[Old](old.md)\n", encoding="utf-8")
    (root / "old.md").write_text("# Old\n", encoding="utf-8")
    for index in range(count - 1):
        (root / f"keep-{index}.md").write_text("# Keep\n", encoding="utf-8")
    (candidate / "description.md").write_text("# Root\n", encoding="utf-8")
    return root, {
        "root": str(root), "candidate": str(candidate), "lock": str(home / "state/context_update.lock"),
        "baseline": {page.name: worker._signature(page) for page in root.iterdir()},
        "replace": ["description.md"], "remove": ["old.md"],
    }


def test_large_snapshot_checks_only_mutation_targets(tmp_path, monkeypatch):
    root, plan = _plan(tmp_path, 2000)
    clock = [0.0]
    stages = []
    started = [False]
    signature = worker._signature

    def measured(path):
        clock[0] += 0.001
        stages.append((Path(path).name, started[0]))
        return signature(path)

    def begin():
        started[0] = True
        with FileLock(plan["lock"], timeout=0):
            pass

    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(worker, "_signature", measured)
    result = worker.commit(plan, seconds=0.1, deadline=5, on_commit=begin)
    assert result == {"status": "succeeded", "removed": ["old.md"]}
    assert not (root / "old.md").exists()
    assert len(list(root.glob("keep-*.md"))) == 1999
    assert not any(name.startswith("keep-") for name, _in_window in stages)
    assert clock[0] < 0.1


@pytest.mark.parametrize("changed", ["description.md", "old.md"])
def test_change_after_preflight_is_not_overwritten(tmp_path, changed):
    root, plan = _plan(tmp_path)

    def edit():
        (root / changed).write_text("# Concurrent new content\n", encoding="utf-8")

    result = worker.commit(plan, seconds=1, on_commit=edit)
    assert result["reason"] == "context_changed"
    assert (root / changed).read_text(encoding="utf-8") == "# Concurrent new content\n"
    assert (root / "old.md").exists()


def test_new_reference_outside_commit_targets_does_not_keep_deleted_body(tmp_path):
    root, plan = _plan(tmp_path, 2)
    assert "keep-0.md" not in plan["replace"] + plan["remove"]
    # References in another page do not protect an unchanged deletion target.
    updated = "# Keep\n[Old](old.md)\n"
    (root / "keep-0.md").write_text(updated, encoding="utf-8")
    starts = []
    result = worker.commit(plan, seconds=1, on_commit=lambda: starts.append(True))
    assert result == {"status": "succeeded", "removed": ["old.md"]}
    assert starts
    assert (root / "keep-0.md").read_text(encoding="utf-8") == updated
    assert not (root / "old.md").exists()
    assert "[Old]" not in (root / "description.md").read_text(encoding="utf-8")


def test_expired_preflight_never_enters_the_write_window(tmp_path, monkeypatch):
    root, plan = _plan(tmp_path)
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: 2))
    starts = []
    result = worker.commit(plan, seconds=0.1, deadline=1, on_commit=lambda: starts.append(True))
    assert result["reason"] == "time_budget" and not starts
    assert (root / "old.md").exists()


def test_generation_is_captured_before_preparation_scans(tmp_path, monkeypatch):
    root, _plan_data = _plan(tmp_path)
    (root / "old.md").write_text("# Old\n" + "x" * 4000, encoding="utf-8")
    original_sizes = capacity._markdown_sizes
    scanned = []

    def concurrent_publish(path):
        if path == root and not scanned:
            scanned.append(True)
            replacement = root / "new-root"
            replacement.write_text("# Concurrent publication\n[Old](old.md)\n", encoding="utf-8")
            replacement.replace(root / "description.md")
        return original_sizes(path)

    def apply(manifest, *, deadline, cancelled):
        assert not cancelled()
        return worker.commit(json.loads(manifest.read_text(encoding="utf-8")), seconds=1, deadline=deadline)

    monkeypatch.setattr(capacity, "_markdown_sizes", concurrent_publish)
    result = capacity.maintain_capacity(tmp_path, limit_bytes=1000, commit_candidate=apply)
    assert result["reason"] == "context_changed"
    assert (root / "old.md").exists()
    assert "Concurrent publication" in (root / "description.md").read_text(encoding="utf-8")


def test_retired_cleanup_failure_does_not_fail_optional_maintenance(tmp_path, monkeypatch):
    root, _plan_data = _plan(tmp_path)

    def fail(*_args, **_kwargs):
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(capacity, "cleanup_retired", fail)
    result = capacity.maintain_capacity(tmp_path, limit_bytes=1000)
    assert result["status"] == "succeeded"
    assert (root / "old.md").exists()


def test_sandbox_registered_between_deletions_is_associated_with_later_body(tmp_path, monkeypatch):
    _root, plan = _plan(tmp_path, 2)
    plan["remove"].append("keep-0.md")
    leases = [retirement.register_sandbox(tmp_path)]
    receipts = []
    retire_page = worker.retire_page

    def retire(home, relative):
        receipts.append(retire_page(home, relative))
        if len(receipts) == 1:
            leases.append(retirement.register_sandbox(home))

    monkeypatch.setattr(worker, "retire_page", retire)
    try:
        assert worker.commit(plan, seconds=1)["status"] == "succeeded"
        assert receipts[0]["sandbox_ids"] == [leases[0][0]]
        assert set(receipts[1]["sandbox_ids"]) == {lease[0] for lease in leases}
    finally:
        for lease in leases:
            retirement.release_sandbox(lease)
