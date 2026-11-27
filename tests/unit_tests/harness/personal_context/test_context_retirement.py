# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real file and lease boundaries for temporary capacity deletion records."""

import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from openjiuwen.harness.personal_context import context_retirement as retirement


def _page(home, relative="topic/page.md", text="old body"):
    path = home / "workspace" / "context" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _cleanup(home, **kwargs):
    return retirement.cleanup_retired(home, seconds=3, **kwargs)


def test_two_sandboxes_hold_record_until_both_finish(tmp_path):
    source = _page(tmp_path)
    first = retirement.register_sandbox(tmp_path)
    second = retirement.register_sandbox(tmp_path)
    try:
        receipt = retirement.retire_page(tmp_path, "topic/page.md")
        body = Path(receipt["body_path"])
        record = Path(receipt["record_path"])
        recorded = record.read_bytes()
        assert set(receipt["sandbox_ids"]) == {first[0], second[0]}
        assert not source.exists()
        assert body.read_text(encoding="utf-8") == "old body"
        assert _cleanup(tmp_path) == 0
        retirement.release_sandbox(first)
        assert _cleanup(tmp_path) == 0
        assert record.read_bytes() == recorded
        assert body.exists()
        retirement.release_sandbox(second)
        assert _cleanup(tmp_path) == 1
        assert not body.exists() and not record.exists()
    finally:
        retirement.release_sandbox(first)
        retirement.release_sandbox(second)


def test_new_sandbox_does_not_inherit_old_retirement(tmp_path):
    _page(tmp_path)
    old = retirement.register_sandbox(tmp_path)
    receipt = retirement.retire_page(tmp_path, "topic/page.md")
    later = retirement.register_sandbox(tmp_path)
    try:
        retirement.release_sandbox(old)
        assert retirement.is_sandbox_active(tmp_path, later[0])
        assert _cleanup(tmp_path) == 1
        assert not Path(receipt["body_path"]).exists()
    finally:
        retirement.release_sandbox(old)
        retirement.release_sandbox(later)


def test_busy_owner_file_does_not_extend_completed_sandbox_lifetime(tmp_path, monkeypatch):
    _page(tmp_path)
    lease = retirement.register_sandbox(tmp_path)
    receipt = retirement.retire_page(tmp_path, "topic/page.md")
    unlink = Path.unlink

    def busy(path, *args, **kwargs):
        if path == lease[1]:
            raise PermissionError("reader denies deletion")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(Path, "unlink", busy)
        retirement.release_sandbox(lease)
        assert _cleanup(tmp_path) == 1
        assert not Path(receipt["body_path"]).exists()
        assert lease[1].exists()
    _cleanup(tmp_path)
    assert list(lease[1].parent.iterdir()) == []


def test_duplicate_live_lease_cannot_be_registered(tmp_path):
    lease = retirement.register_sandbox(tmp_path, "service-run-unique")
    try:
        with pytest.raises(Timeout):
            retirement.register_sandbox(tmp_path, lease[0])
        assert retirement.active_sandbox_ids(tmp_path) == (lease[0],)
    finally:
        retirement.release_sandbox(lease)
    assert not retirement.is_sandbox_active(tmp_path, lease[0])


def test_registration_proceeds_while_optional_cleanup_is_blocked(tmp_path, monkeypatch):
    _page(tmp_path)
    retirement.retire_page(tmp_path, "topic/page.md")
    checked, resume = threading.Event(), threading.Event()
    original = retirement._remove_body

    def pause(path, kind):
        checked.set()
        assert resume.wait(3)
        original(path, kind)

    monkeypatch.setattr(retirement, "_remove_body", pause)
    cleaner = threading.Thread(target=lambda: _cleanup(tmp_path))
    cleaner.start()
    try:
        assert checked.wait(2)
        lease = retirement.register_sandbox(tmp_path)
        try:
            assert retirement.is_sandbox_active(tmp_path, lease[0])
        finally:
            retirement.release_sandbox(lease)
    finally:
        resume.set()
        cleaner.join(3)
    assert not cleaner.is_alive()


def test_registration_does_not_wait_for_publication_or_the_old_registry_lock(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    with FileLock(state / "context_sandbox_registry.lock", timeout=0):
        with FileLock(state / "context_priority.lock", timeout=0):
            with FileLock(state / "context_update.lock", timeout=0):
                lease = retirement.register_sandbox(tmp_path)
                try:
                    assert retirement.active_sandbox_ids(tmp_path) == (lease[0],)
                finally:
                    retirement.release_sandbox(lease)


def test_child_observes_fallback_sandbox_and_retains_its_body(tmp_path, monkeypatch):
    _page(tmp_path)
    sandbox = tmp_path / "workspace/sandboxes/local/input"
    sandbox_id = hashlib.sha256(b"local/input").hexdigest() + "-" + "1" * 32

    def unavailable(*args):
        raise PermissionError("registration unavailable")

    monkeypatch.setattr(retirement, "register_sandbox", unavailable)
    actual, lease = retirement.create_run_sandbox(tmp_path, sandbox_id, sandbox)
    assert actual.parent.samefile(sandbox)
    assert list(actual.iterdir()) == []
    script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import context_retirement as retirement
home = Path(sys.argv[2])
print(json.dumps(retirement.retire_page(home, 'topic/page.md')))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(Path(retirement.__file__).parent), str(tmp_path)],
            capture_output=True, text=True, timeout=10, check=True,
        )
        receipt = json.loads(result.stdout)
        assert receipt["sandbox_ids"] == [sandbox_id]
        assert _cleanup(tmp_path) == 0
        with pytest.raises(FileExistsError):
            retirement.create_run_sandbox(tmp_path, sandbox_id[:-32] + "2" * 32, sandbox)
    finally:
        actual.rmdir()
        sandbox.rmdir()
        retirement.release_sandbox(lease)
    assert _cleanup(tmp_path) == 1
    assert not Path(receipt["body_path"]).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH")
def test_long_windows_home_can_create_and_claim_a_fallback_sandbox(tmp_path):
    home = tmp_path / ("home-" + "x" * 120)
    state = retirement._safe_path(home, "state")
    state.mkdir(parents=True)
    (state / "context-sandboxes").write_text("unavailable", encoding="ascii")
    sandbox = home / "workspace/sandboxes/local" / ("r" * 32)
    sandbox_id = hashlib.sha256(("local/" + sandbox.name).encode()).hexdigest() + "-" + "3" * 32
    assert len(str(sandbox.parent / f".pcs-live-{os.getpid()}-{sandbox_id}")) > 260

    actual, lease = retirement.create_run_sandbox(home, sandbox_id, sandbox)
    try:
        (actual / "inputs").mkdir()
        assert actual.parent.samefile(retirement._safe_path(sandbox))
        assert (actual / "inputs").is_dir()
    finally:
        (actual / "inputs").rmdir()
        actual.rmdir()
        retirement._safe_path(sandbox).rmdir()
        retirement.release_sandbox(lease)


def test_crashed_process_releases_lease_without_loading_core(tmp_path):
    _page(tmp_path)
    script = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import context_retirement as retirement
home = Path(sys.argv[2])
lease = retirement.register_sandbox(home)
receipt = retirement.retire_page(home, 'topic/page.md')
assert not any(name == 'openjiuwen' or name.startswith('openjiuwen.') for name in sys.modules)
print(json.dumps(receipt), flush=True)
os._exit(0)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(Path(retirement.__file__).parent), str(tmp_path)],
        capture_output=True, text=True, timeout=10, check=True,
    )
    receipt = json.loads(result.stdout)
    assert Path(receipt["body_path"]).exists()
    assert retirement.active_sandbox_ids(tmp_path) == ()
    assert _cleanup(tmp_path) == 1
    assert not Path(receipt["record_path"]).exists()


@pytest.mark.parametrize("after_move", [False, True])
def test_interrupted_move_preserves_evidence_and_never_overwrites_new_body(tmp_path, monkeypatch, after_move):
    source = _page(tmp_path)
    lease = retirement.register_sandbox(tmp_path)
    original_replace = os.replace

    def interrupted_replace(origin, destination):
        if Path(destination).suffix == ".body":
            record = Path(destination).with_suffix(".json")
            assert json.loads(record.read_text(encoding="utf-8"))["sandbox_ids"] == [lease[0]]
            if after_move:
                original_replace(origin, destination)
            raise OSError("interrupted body move")
        original_replace(origin, destination)

    monkeypatch.setattr(retirement.os, "replace", interrupted_replace)
    try:
        with pytest.raises(OSError, match="interrupted"):
            retirement.retire_page(tmp_path, "topic/page.md")
        directory = tmp_path / "state" / "context-retired"
        record = next(directory.glob("*.json"))
        body = record.with_suffix(".body")
        assert body.exists() == after_move
        assert source.exists() != after_move
        assert _cleanup(tmp_path) == 0
        source.write_text("new sandbox content", encoding="utf-8")
        retirement.release_sandbox(lease)
        assert _cleanup(tmp_path) == 1
        assert source.read_text(encoding="utf-8") == "new sandbox content"
        assert not body.exists() and not record.exists()
    finally:
        retirement.release_sandbox(lease)


def test_record_commit_failure_does_not_move_body(tmp_path, monkeypatch):
    source = _page(tmp_path)
    lease = retirement.register_sandbox(tmp_path)
    original_replace = os.replace

    def fail_record(origin, destination):
        if Path(destination).suffix == ".json":
            raise PermissionError("record cannot be committed")
        original_replace(origin, destination)

    monkeypatch.setattr(retirement.os, "replace", fail_record)
    try:
        with pytest.raises(PermissionError):
            retirement.retire_page(tmp_path, "topic/page.md")
        assert source.read_text(encoding="utf-8") == "old body"
        assert list((tmp_path / "state" / "context-retired").iterdir()) == []
    finally:
        retirement.release_sandbox(lease)


def test_record_is_fsynced_before_body_is_moved(tmp_path, monkeypatch):
    _page(tmp_path)
    lease = retirement.register_sandbox(tmp_path)
    synced = []
    original_fsync = os.fsync
    original_replace = os.replace

    def fsync(descriptor):
        original_fsync(descriptor)
        synced.append(descriptor)

    def replace(origin, destination):
        if Path(destination).suffix == ".body":
            assert synced
            assert Path(destination).with_suffix(".json").exists()
        original_replace(origin, destination)

    monkeypatch.setattr(retirement.os, "fsync", fsync)
    monkeypatch.setattr(retirement.os, "replace", replace)
    try:
        retirement.retire_page(tmp_path, "topic/page.md")
    finally:
        retirement.release_sandbox(lease)


@pytest.mark.parametrize("relative", ["../page.md", "topic/../../page.md", "/page.md", "C:/page.md", "topic\\..\\page.md"])
def test_retirement_rejects_escaped_paths(tmp_path, relative):
    source = _page(tmp_path)
    with pytest.raises(ValueError):
        retirement.retire_page(tmp_path, relative, ())
    assert source.exists()


def test_state_symlink_is_rejected_without_touching_external_files(tmp_path):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    try:
        (home / "state").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt":
            pytest.skip(f"symlink creation unavailable: {exc}")
        # Directory junctions have the same reparse boundary and do not require
        # the Windows privilege needed to create symbolic links.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(home / "state"), str(outside)],
            capture_output=True, timeout=10,
        )
        if result.returncode:
            pytest.skip("neither symlink nor junction creation is available")
    with pytest.raises(ValueError, match="link"):
        retirement.register_sandbox(home)
    assert list(outside.iterdir()) == []


def test_cleanup_yields_to_publication_and_honors_batch_limit(tmp_path):
    lease = retirement.register_sandbox(tmp_path)
    for number in range(3):
        _page(tmp_path, f"topic/{number}.md")
        retirement.retire_page(tmp_path, f"topic/{number}.md")
    retirement.release_sandbox(lease)
    with FileLock(tmp_path / "state" / "context_update.lock", timeout=0):
        assert _cleanup(tmp_path) == 0
    with FileLock(tmp_path / "state" / "context_priority.lock", timeout=0):
        assert _cleanup(tmp_path) == 0
    assert retirement.cleanup_retired(tmp_path, seconds=0) == 0
    assert _cleanup(tmp_path, max_records=1) <= 1
    _cleanup(tmp_path)
    assert list((tmp_path / "state" / "context-retired").iterdir()) == []


def test_no_active_sandbox_requires_no_retained_history(tmp_path):
    source = _page(tmp_path)
    receipt = retirement.retire_page(tmp_path, "topic/page.md")
    assert not source.exists()
    assert receipt["sandbox_ids"] == []
    assert Path(receipt["body_path"]).read_text(encoding="utf-8") == "old body"
    assert _cleanup(tmp_path) == 1
    assert list((tmp_path / "state" / "context-retired").iterdir()) == []


def test_cleanup_keeps_publication_intent_lock_available(tmp_path, monkeypatch):
    _page(tmp_path)
    retirement.retire_page(tmp_path, "topic/page.md")
    remove_body = retirement._remove_body
    inspected = []

    def inspect(path, kind):
        with FileLock(tmp_path / "state/context_priority.lock", timeout=0):
            inspected.append(True)
        remove_body(path, kind)

    monkeypatch.setattr(retirement, "_remove_body", inspect)
    assert _cleanup(tmp_path) == 1
    assert inspected == [True]
    assert list((tmp_path / "state" / "context-retired").iterdir()) == []


def test_long_home_paths_remain_in_controlled_state(tmp_path):
    home = tmp_path / ("long" * 20) / ("nested" * 15)
    physical_home = Path("\\\\?\\" + str(home)) if os.name == "nt" else home
    _page(physical_home)
    lease = retirement.register_sandbox(home)
    try:
        receipt = retirement.retire_page(home, "topic/page.md")
        body = Path(receipt["body_path"])
        assert body.read_text(encoding="utf-8") == "old body"
        assert body.parent == retirement._safe_path(home, "state/context-retired")
    finally:
        retirement.release_sandbox(lease)
    assert _cleanup(home) == 1


def test_empty_topic_moves_as_one_directory_and_is_cleaned(tmp_path):
    description = _page(tmp_path, "topic/description.md", "# Topic")
    lease = retirement.register_sandbox(tmp_path)
    try:
        receipt = retirement.retire_empty_topic(tmp_path, "topic")
        body = Path(receipt["body_path"])
        assert not description.parent.exists()
        assert (body / "description.md").read_text(encoding="utf-8") == "# Topic"
        assert _cleanup(tmp_path) == 0
    finally:
        retirement.release_sandbox(lease)
    assert _cleanup(tmp_path) == 1
    assert not body.exists()


def test_topic_retirement_and_cleanup_refuse_unexpected_children(tmp_path):
    _page(tmp_path, "topic/description.md")
    extra = _page(tmp_path)
    with pytest.raises(ValueError, match="empty topic"):
        retirement.retire_empty_topic(tmp_path, "topic")
    extra.unlink()
    receipt = retirement.retire_empty_topic(tmp_path, "topic")
    body = Path(receipt["body_path"])
    (body / "unexpected.md").write_text("keep", encoding="utf-8")
    assert _cleanup(tmp_path) == 0
    assert (body / "description.md").exists()
    assert (body / "unexpected.md").read_text(encoding="utf-8") == "keep"
