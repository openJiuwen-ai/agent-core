# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optional capacity work must yield to normal publication and cancellation."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from filelock import FileLock

from openjiuwen.harness.personal_context import _capacity_commit as commit_worker
from openjiuwen.harness.personal_context import context_capacity as capacity
from openjiuwen.harness.personal_context import context_pipeline as pipeline
from openjiuwen.harness.personal_context import context_usage as usage
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem


def _tree(home):
    root = home / "workspace/context"
    root.mkdir(parents=True)
    (root / "description.md").write_text("# Context\n[Old](old.md)\n", encoding="utf-8")
    (root / "old.md").write_text("# Old\n" + "x" * 4000, encoding="utf-8")
    (home / "state").mkdir()
    return root


def _plan(home):
    root = _tree(home)
    candidate = home / "candidate"
    shutil.copytree(root, candidate)
    (candidate / "description.md").write_text("# Context\n", encoding="utf-8")
    (candidate / "old.md").unlink()
    manifest = {
        "root": str(root), "candidate": str(candidate),
        "lock": str(home / "state/context_update.lock"),
        "baseline": {path.name: capacity._file_signature(path) for path in root.iterdir()},
        "replace": ["description.md"], "remove": ["old.md"],
    }
    plan = home / "plan.json"
    plan.write_text(json.dumps(manifest), encoding="utf-8")
    return root, plan, manifest


async def _until(predicate, timeout=2):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), timeout)


def test_commit_applies_only_prepared_changes_with_unicode_and_spaces(tmp_path):
    root, _plan_path, plan = _plan(tmp_path / "带空格 home")
    (root / "untouched.txt").write_text("keep", encoding="utf-8")
    result = commit_worker.commit(plan, seconds=1)
    assert result == {"status": "succeeded", "removed": ["old.md"]}
    assert not (root / "old.md").exists()
    assert (root / "description.md").read_text(encoding="utf-8") == "# Context\n"
    assert (root / "untouched.txt").read_text(encoding="utf-8") == "keep"
    with FileLock(plan["lock"], timeout=0):
        pass


def test_busy_publication_lock_skips_commit_without_waiting(tmp_path):
    root, _plan_path, plan = _plan(tmp_path)
    with FileLock(plan["lock"], timeout=0):
        began = time.monotonic()
        result = commit_worker.commit(plan, seconds=1)
        assert time.monotonic() - began < 0.1
    assert result["reason"] == "publication_busy"
    assert (root / "old.md").exists()
    assert "[Old]" in (root / "description.md").read_text(encoding="utf-8")


def test_stale_candidate_cannot_replace_new_publication(tmp_path):
    root, _plan_path, plan = _plan(tmp_path)
    replacement = tmp_path / "new-candidate"
    shutil.copytree(root, replacement)
    (replacement / "old.md").write_text("# Newly published content", encoding="utf-8")
    usage.publish_with_usage(tmp_path, replacement, {})
    result = commit_worker.commit(plan, seconds=1)
    assert result["reason"] == "context_changed"
    assert result["removed"] == []
    assert (root / "old.md").read_text(encoding="utf-8") == "# Newly published content"


@pytest.mark.parametrize("relative", ["../outside.md", "C:/outside.md"])
def test_commit_rejects_escaping_relative_path(tmp_path, relative):
    root, _plan_path, plan = _plan(tmp_path)
    plan["remove"] = [relative]
    with pytest.raises(ValueError, match="path"):
        commit_worker.commit(plan, seconds=1)
    assert (root / "old.md").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows rooted paths have a distinct anchor form")
def test_commit_rejects_windows_rooted_path(tmp_path):
    with pytest.raises(ValueError, match="path"):
        commit_worker._safe_path(tmp_path, "\\outside.md")


def test_commit_rejects_symlink_candidate(tmp_path):
    root, _plan_path, plan = _plan(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "candidate-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create directory symlinks")
    plan["candidate"] = str(link)
    with pytest.raises(ValueError, match="link"):
        commit_worker.commit(plan, seconds=1)
    assert (root / "old.md").exists()


def test_successful_capacity_removal_preserves_description_coverage(tmp_path):
    root = _tree(tmp_path)
    topic = root / "topic"
    topic.mkdir()
    (root / "old.md").rename(topic / "old.md")
    (root / "description.md").write_text("# Context\n[Topic](topic/description.md)\n", encoding="utf-8")
    (topic / "description.md").write_text("# Topic\n[Old](old.md)\n", encoding="utf-8")
    result = capacity.maintain_capacity(tmp_path, limit_bytes=1000)
    assert result["removed_count"] == 1
    capacity._validate_description_coverage(root)


@pytest.mark.asyncio
async def test_worker_start_failure_does_not_permanently_disable_maintenance(tmp_path, monkeypatch):
    _tree(tmp_path)

    def cannot_start(_thread):
        raise OSError("temporary worker resource exhaustion")

    with monkeypatch.context() as patcher:
        patcher.setattr(capacity.threading.Thread, "start", cannot_start)
        result = await capacity.run_capacity_maintenance(
            tmp_path, limit_bytes=100_000, timeout=1, cancelled=lambda: False,
        )
        assert result["status"] == "failed"
        assert tmp_path not in capacity._CAPACITY_WORKERS or capacity._CAPACITY_WORKERS[tmp_path].done()
    result = await capacity.run_capacity_maintenance(
        tmp_path, limit_bytes=100_000, timeout=1, cancelled=lambda: False,
    )
    assert result["status"] == "succeeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_kind", ["deadline", "cancel"])
async def test_slow_preparation_never_blocks_or_later_overwrites_publication(tmp_path, monkeypatch, stop_kind):
    root = _tree(tmp_path)
    copying = threading.Event()
    proceed = threading.Event()
    original_copy = capacity._copy_tree

    def slow_copy(source, target):
        original_copy(source, target)
        copying.set()
        assert proceed.wait(3)

    monkeypatch.setattr(capacity, "_copy_tree", slow_copy)
    task = asyncio.create_task(capacity.run_capacity_maintenance(
        tmp_path, limit_bytes=1000, timeout=0.75 if stop_kind == "deadline" else 2,
        cancelled=lambda: False,
    ))
    try:
        await _until(copying.is_set)
        future = capacity._CAPACITY_WORKERS[tmp_path]
        if stop_kind == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.3)
        else:
            result = await asyncio.wait_for(task, 1)
            assert result["reason"] == "time_budget"
        assert not future.done()
        # A stuck reader/preparer must not create more workers on later rounds.
        result = await capacity.run_capacity_maintenance(
            tmp_path, limit_bytes=1000, timeout=1, cancelled=lambda: False,
        )
        assert result["reason"] == "maintenance_busy"
        assert capacity._CAPACITY_WORKERS[tmp_path] is future
        with FileLock(tmp_path / "state/context_update.lock", timeout=0):
            pass
        new = tmp_path / "new-candidate"
        shutil.copytree(root, new)
        (new / "old.md").write_text("# New publication survives", encoding="utf-8")
        await asyncio.wait_for(asyncio.to_thread(usage.publish_with_usage, tmp_path, new, {}), 1)
    finally:
        proceed.set()
        await _until(lambda: tmp_path not in capacity._CAPACITY_WORKERS)
        if not task.done():
            await task
    assert (root / "old.md").read_text(encoding="utf-8") == "# New publication survives"


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_kind", ["deadline", "cancel"])
async def test_stuck_commit_child_is_killed_and_releases_its_real_lock(tmp_path, monkeypatch, stop_kind):
    root, plan_path, plan = _plan(tmp_path)
    started = tmp_path / "child-started"
    script = tmp_path / "slow_commit.py"
    script.write_text(
        "import sys, time\nfrom pathlib import Path\nfrom filelock import FileLock\n"
        "Path(sys.argv[3]).touch()\n"
        "with FileLock(sys.argv[1], timeout=0):\n"
        "    Path(sys.argv[2]).write_text(str(time.monotonic()))\n"
        "    time.sleep(30)\n",
        encoding="utf-8",
    )
    processes = []
    real_popen = subprocess.Popen

    def slow_worker(_args, **kwargs):
        child = real_popen(
            [sys.executable, str(script), plan["lock"], str(started), str(plan_path.with_suffix(".ready"))],
            **kwargs,
        )
        processes.append(child)
        return child

    monkeypatch.setattr(capacity.subprocess, "Popen", slow_worker)
    stop = threading.Event()
    began = time.monotonic()
    task = asyncio.create_task(asyncio.to_thread(
        capacity._commit_candidate, plan_path, deadline=began + 2, cancelled=stop.is_set,
    ))
    try:
        if stop_kind == "cancel":
            await _until(started.exists, timeout=0.5)
            stop.set()
        result = await asyncio.wait_for(task, 2)
        assert result["reason"] == "commit_time_budget"
        assert started.exists(), "the child must actually acquire the lock before this test can prove release"
        assert time.monotonic() - float(started.read_text()) < 0.3
        assert len(processes) == 1 and processes[0].poll() is not None
        with FileLock(plan["lock"], timeout=0):
            pass
        assert (root / "old.md").exists()
    finally:
        stop.set()
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=1)


def test_commit_result_larger_than_pipe_buffer_does_not_deadlock(tmp_path, monkeypatch):
    _root, plan_path, _plan_value = _plan(tmp_path)
    script = tmp_path / "large_result.py"
    script.write_text(
        "import sys,json\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).touch()\n"
        "print(json.dumps({'status': 'succeeded', 'removed': ['x' * 512] * 256}))\n",
        encoding="utf-8",
    )
    real_popen = subprocess.Popen

    def large_result(_args, **kwargs):
        return real_popen([sys.executable, str(script), str(plan_path.with_suffix(".ready"))], **kwargs)

    monkeypatch.setattr(capacity.subprocess, "Popen", large_result)
    result = capacity._commit_candidate(plan_path, deadline=time.monotonic() + 2, cancelled=lambda: False)
    assert result["status"] == "succeeded"
    assert len(result["removed"]) == 256


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["finish", "retain"])
@pytest.mark.parametrize("read_phase", ["copy", "snapshot"])
async def test_queued_publication_protects_its_reads_from_capacity_deletion(
    tmp_path, monkeypatch, event, read_phase,
):
    queue = asyncio.Queue()
    config = PersonalContextConfig.from_dict({
        "collection_enabled": True, "agent_use_enabled": True,
        "strategy_profile": "rules", "fetch_services": [],
    })
    service = pipeline.ContextPipelineService(home=tmp_path, config=config, input_queue=queue)

    async def send(tag, run_id, title=None):
        payload = None if title is None else FetchBatch(batch_id="batch", items=[RawChangeItem(
            logical_id=run_id, revision_id="v1", operation="upsert", title=title,
            content=title, original_ref=f"file:///notes/{run_id}",
            metadata={"kind": "note"}, raw_snapshot=title,
        )])
        completion = asyncio.get_running_loop().create_future()
        await queue.put((tag, "local", run_id, payload, completion))
        await asyncio.wait_for(asyncio.shield(completion), timeout=10)

    await service.start()
    try:
        await send("batch", "seed", "Existing baseline content")
        await send("finish", "seed")
        root = tmp_path / "workspace/context"
        victim = next(path for path in root.rglob("*.md") if path.name != "description.md")
        relative = victim.relative_to(root).as_posix()
        candidate = tmp_path / "gc-candidate"
        shutil.copytree(root, candidate)
        (candidate / relative).unlink()
        capacity._repair_navigation(candidate, {relative})
        before = {path.relative_to(root).as_posix(): path for path in root.rglob("*.md")}
        after = {path.relative_to(candidate).as_posix(): path for path in candidate.rglob("*.md")}
        manifest = {
            "root": str(root), "candidate": str(candidate),
            "lock": str(tmp_path / "state/context_update.lock"),
            "baseline": {name: capacity._file_signature(path) for name, path in before.items()},
            "replace": [name for name, path in after.items() if path.read_bytes() != before[name].read_bytes()],
            "remove": sorted(before.keys() - after.keys()),
        }
        attempts = []
        original_copytree = pipeline.shutil.copytree
        original_snapshot = pipeline._snapshot_managed_files

        def attempt_during_copy(source, destination, *args, **kwargs):
            if Path(source).samefile(root):
                def copy_file(source_file, target_file):
                    if not attempts and Path(source_file).samefile(victim):
                        attempts.append(commit_worker.commit(manifest, seconds=1))
                    return shutil.copy2(source_file, target_file)
                kwargs["copy_function"] = copy_file
            return original_copytree(source, destination, *args, **kwargs)

        def attempt_after_enumeration(path):
            snapshot = original_snapshot(path)
            if not attempts and Path(path) == root:
                assert relative in snapshot
                attempts.append(commit_worker.commit(manifest, seconds=1))
            return snapshot

        if read_phase == "copy":
            monkeypatch.setattr(pipeline.shutil, "copytree", attempt_during_copy)
        else:
            monkeypatch.setattr(pipeline, "_snapshot_managed_files", attempt_after_enumeration)
        await send("batch", "new", "New publish content")
        await send(event, "new")
        assert len(attempts) == 1
        assert attempts[0]["reason"] == "publication_busy"
        assert attempts[0]["removed"] == []
        published = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.md"))
        assert "Existing baseline content" in published
        assert "New publish content" in published
        pipeline._validate_description_coverage(root)
    finally:
        await service.stop(timeout_seconds=1)
