# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A stalled auxiliary process must die and later rounds must really reclaim."""

import asyncio
import gc
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest
from filelock import FileLock

from openjiuwen.harness.personal_context import context_capacity as capacity
from openjiuwen.harness.personal_context import context_usage as usage
from tests.unit_tests.harness.personal_context.capacity_worker_helpers import inject_worker, ready_worker, until


def _tree(home):
    root = home / "workspace/context"
    root.mkdir(parents=True)
    (root / "description.md").write_text("# Context\n[Old](old.md)\n[Hot](hot.md)\n", encoding="utf-8")
    (root / "old.md").write_text("# Old\n" + "a" * 4000, encoding="utf-8")
    (root / "hot.md").write_text("# Hot\n" + "b" * 3000, encoding="utf-8")
    return root


async def _recover_and_reclaim(home):
    worker = await ready_worker(home)
    try:
        result = usage.read_with_usage(home, "hot.md", allowed=lambda: True)
        assert "Hot" in result["markdown"]
        recorded = usage._load_usage(home)["pages"]["hot.md"]["last_read_at"]
        assert recorded is not None
        result = await capacity.run_capacity_maintenance(
            home, limit_bytes=6000, timeout=2, cancelled=lambda: False,
        )
        assert result["status"] == "succeeded" and result["removed_count"] == 1, result
        root = home / "workspace/context"
        assert not (root / "old.md").exists() and (root / "hot.md").exists()
        ledger = usage._load_usage(home)
        assert ledger["pages"]["hot.md"]["last_read_at"] == recorded
        assert ledger["pages"]["old.md"]["present"] is False
        capacity._validate_description_coverage(root)
        return worker["process"].pid
    finally:
        await capacity._close_capacity_worker(home)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_kind", ["deadline", "cancel", "stop"])
async def test_stuck_preparation_is_terminated_and_next_round_actually_reclaims(tmp_path, monkeypatch, stop_kind):
    root = _tree(tmp_path)
    started = tmp_path / "copying"
    with monkeypatch.context() as patcher:
        inject_worker(patcher, tmp_path,
                      "original = capacity._copy_tree\n"
                      "def slow(source, target):\n"
                      "    original(source, target)\n"
                      f"    Path({str(started)!r}).touch()\n"
                      "    time.sleep(60)\n"
                      "capacity._copy_tree = slow")
        worker = await ready_worker(tmp_path)
        stopped = False
        task = asyncio.create_task(capacity.run_capacity_maintenance(
            tmp_path, limit_bytes=6000, timeout=0.3 if stop_kind == "deadline" else 3,
            cancelled=lambda: stopped,
        ))
        try:
            await until(started.exists)
            process = worker["process"]
            # Background copying owns neither publication nor usage locks.
            for name in ("context_update.lock", "context_usage.lock"):
                with FileLock(tmp_path / "state" / name, timeout=0):
                    pass
            began = time.monotonic()
            if stop_kind == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                stopped = stop_kind == "stop"
                assert (await task)["reason"] == "time_budget"
            assert time.monotonic() - began < 0.7
            assert process.returncode is not None
            assert tmp_path not in capacity._CAPACITY_WORKERS
            # The dead task cannot overwrite this subsequent normal publication.
            candidate = tmp_path / "new-publication"
            shutil.copytree(root, candidate)
            (candidate / "hot.md").write_text("# Hot\nNew publication survives\n" + "b" * 3000, encoding="utf-8")
            usage.publish_with_usage(tmp_path, candidate, {})
            assert "New publication" in (root / "hot.md").read_text(encoding="utf-8")
        finally:
            await capacity._close_capacity_worker(tmp_path)
    new_pid = await _recover_and_reclaim(tmp_path)
    assert new_pid != process.pid
    assert "New publication" in (root / "hot.md").read_text(encoding="utf-8")
    assert not list((tmp_path / "state").glob("context-capacity-*"))


@pytest.mark.asyncio
async def test_consecutive_timeouts_and_worker_crashes_do_not_leave_permanent_busy(tmp_path, monkeypatch):
    _tree(tmp_path)
    for body in ("def fail(*args):\n    time.sleep(60)",
                 "def fail(*args):\n    time.sleep(60)",
                 "def fail(*args):\n    os._exit(9)"):
        with monkeypatch.context() as patcher:
            inject_worker(patcher, tmp_path, body + "\ncapacity._copy_tree = fail")
            worker = await ready_worker(tmp_path)
            result = await capacity.run_capacity_maintenance(
                tmp_path, limit_bytes=6000, timeout=0.15, cancelled=lambda: False,
            )
            assert result["status"] == "failed" and result["reason"] in {"time_budget", "worker_exited"}, result
            assert worker["process"].returncode is not None
            assert tmp_path not in capacity._CAPACITY_WORKERS
    await _recover_and_reclaim(tmp_path)


@pytest.mark.asyncio
async def test_start_failure_isolated_and_next_launch_succeeds(tmp_path, monkeypatch):
    _tree(tmp_path)
    def fail(*_args, **_kwargs):
        raise OSError("temporary launch failure")
    with monkeypatch.context() as patcher:
        patcher.setattr(capacity.subprocess, "Popen", fail)
        worker = capacity._warm_capacity_worker(tmp_path)
        result = await capacity.run_capacity_maintenance(tmp_path, limit_bytes=6000, timeout=1, cancelled=lambda: False)
        assert result == {"status": "failed", "reason": "time_budget"}
        assert await asyncio.wait_for(worker["ready"], 1) == {"ready": False, "reason": "OSError"}
        await until(lambda: tmp_path not in capacity._CAPACITY_WORKERS)
    await _recover_and_reclaim(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_kind", ["cancel", "close", "startup_timeout"])
async def test_startup_can_be_cancelled_or_closed_and_retried(tmp_path, monkeypatch, stop_kind):
    _tree(tmp_path)
    entered = tmp_path / "startup-entered"
    real_launch = subprocess.Popen
    script = tmp_path / "slow_start.py"
    script.write_text(f"from pathlib import Path\nimport time, os\nPath({str(entered)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n",
                      encoding="utf-8")
    def launch(args, **kwargs):
        return real_launch([args[0], "-X", "utf8", str(script)], **kwargs)
    with monkeypatch.context() as patcher:
        patcher.setattr(capacity.subprocess, "Popen", launch)
        if stop_kind == "startup_timeout":
            patcher.setattr(capacity, "_WORKER_STARTUP_SECONDS", 0.3)
        worker = capacity._warm_capacity_worker(tmp_path)
        await until(entered.exists)
        process = worker["process"]
        assert int(entered.read_text()) == process.pid
        if stop_kind == "cancel":
            worker["startup"].cancel()
            await worker["startup"]
            assert await worker["ready"] == {"ready": False, "reason": "CancelledError"}
        elif stop_kind == "close":
            await capacity._close_capacity_worker(tmp_path)
        else:
            assert not (await asyncio.wait_for(worker["ready"], 1))["ready"]
        await until(lambda: process.returncode is not None and tmp_path not in capacity._CAPACITY_WORKERS)
    await _recover_and_reclaim(tmp_path)


@pytest.mark.asyncio
async def test_late_process_creation_after_close_is_killed_before_next_writer(tmp_path, monkeypatch):
    _tree(tmp_path)
    launched = threading.Event()
    release = threading.Event()
    actual = subprocess.Popen
    def slow_launch(*args, **kwargs):
        launched.set()
        assert release.wait(3)
        return actual(*args, **kwargs)
    with monkeypatch.context() as patcher:
        patcher.setattr(capacity.subprocess, "Popen", slow_launch)
        worker = capacity._warm_capacity_worker(tmp_path)
        await until(launched.is_set)
        await capacity._close_capacity_worker(tmp_path)
        assert capacity._CAPACITY_WORKERS[tmp_path] is worker
        assert capacity._warm_capacity_worker(tmp_path) is worker
        release.set()
        await until(lambda: tmp_path not in capacity._CAPACITY_WORKERS)
        assert worker["process"].returncode is not None
    await _recover_and_reclaim(tmp_path)


@pytest.mark.asyncio
async def test_warming_maintenance_does_not_spend_the_foreground_budget(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    actual = subprocess.Popen

    def slow_creation(*args, **kwargs):
        entered.set()
        if not release.wait(3):
            raise RuntimeError("test did not release capacity creation")
        return actual(*args, **kwargs)

    monkeypatch.setattr(capacity.subprocess, "Popen", slow_creation)
    worker = capacity._warm_capacity_worker(tmp_path)
    try:
        await until(entered.is_set)
        began = time.monotonic()
        result = await capacity.run_capacity_maintenance(
            tmp_path, limit_bytes=6000, timeout=0.5, cancelled=lambda: False,
        )
        assert result == {"status": "failed", "reason": "time_budget"}
        assert time.monotonic() - began < 0.15
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "normal tool result"), 0.2) == "normal tool result"
    finally:
        release.set()
        await capacity._close_capacity_worker(tmp_path)
        await until(lambda: tmp_path not in capacity._CAPACITY_WORKERS)
    assert worker["process"].poll() is not None


@pytest.mark.asyncio
async def test_slow_process_creation_keeps_the_parent_loop_responsive(tmp_path, monkeypatch):
    _tree(tmp_path)
    actual = subprocess.Popen
    thread_ids = []
    def slow_launch(*args, **kwargs):
        thread_ids.append(threading.get_ident())
        time.sleep(0.35)
        return actual(*args, **kwargs)
    monkeypatch.setattr(capacity.subprocess, "Popen", slow_launch)
    began = time.monotonic()
    worker = capacity._warm_capacity_worker(tmp_path)
    task = asyncio.create_task(capacity.run_capacity_maintenance(
        tmp_path, limit_bytes=6000, timeout=0.03, cancelled=lambda: False,
    ))
    ticks = 0
    while not task.done():
        ticks += 1
        await asyncio.sleep(0.002)
    assert (await task)["reason"] == "time_budget"
    assert time.monotonic() - began < 0.15
    assert ticks >= 1 and thread_ids and thread_ids[0] != threading.get_ident()
    await capacity._close_capacity_worker(tmp_path)
    await until(lambda: tmp_path not in capacity._CAPACITY_WORKERS)
    assert worker["process"].poll() is not None


@pytest.mark.asyncio
async def test_warming_worker_does_not_busy_spin_or_exhaust_foreground_polling(tmp_path, monkeypatch):
    script = tmp_path / "waiting_start.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    actual = subprocess.Popen

    def slow_start(args, **kwargs):
        return actual([args[0], "-X", "utf8", str(script)], **kwargs)

    monkeypatch.setattr(capacity.subprocess, "Popen", slow_start)
    worker = capacity._warm_capacity_worker(tmp_path)
    task = None
    try:
        await until(lambda: worker["process"] is not None)
        # Windows monotonic timers can run early by one 15.625 ms tick.
        # Short maintenance polling must not turn this into an active spin.
        monkeypatch.setattr(asyncio.get_running_loop(), "_clock_resolution", 0.015625)
        task = asyncio.create_task(capacity.run_capacity_maintenance(
            tmp_path, limit_bytes=6000, timeout=0.25, cancelled=lambda: False,
        ))
        for _ in range(500):
            if task.done():
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("optional maintenance exhausted normal foreground polling")
        assert (await task)["reason"] == "time_budget"
    finally:
        if task is not None and not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await capacity._close_capacity_worker(tmp_path)


@pytest.mark.asyncio
async def test_ready_worker_with_coarse_clock_short_reply_budget_can_recover(tmp_path, monkeypatch):
    _tree(tmp_path)
    entered = tmp_path / "preparation-entered"
    with monkeypatch.context() as patcher:
        inject_worker(patcher, tmp_path,
                      "def blocked(*args):\n"
                      f"    Path({str(entered)!r}).touch()\n"
                      "    time.sleep(60)\n"
                      "capacity._copy_tree = blocked")
        worker = await ready_worker(tmp_path)
        try:
            patcher.setattr(asyncio.get_running_loop(), "_clock_resolution", 0.015625)
            result = await capacity.run_capacity_maintenance(
                tmp_path, limit_bytes=6000, timeout=0.04, cancelled=lambda: False,
            )
            assert entered.exists()
            assert result["status"] == "failed" and result["reason"] in {"time_budget", "worker_exited"}, result
            process = worker["process"]
            assert process.poll() is not None
            assert tmp_path not in capacity._CAPACITY_WORKERS
        finally:
            await capacity._close_capacity_worker(tmp_path)
    new_pid = await _recover_and_reclaim(tmp_path)
    assert new_pid != process.pid


@pytest.mark.asyncio
async def test_late_handle_is_reaped_even_after_its_original_loop_closes(tmp_path, monkeypatch):
    _tree(tmp_path)
    entered, release = threading.Event(), threading.Event()
    actual = subprocess.Popen
    def delayed_launch(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return actual(*args, **kwargs)
    monkeypatch.setattr(capacity.subprocess, "Popen", delayed_launch)

    def temporary_loop():
        loop = asyncio.new_event_loop()
        async def start_then_close():
            worker = capacity._warm_capacity_worker(tmp_path)
            await until(entered.is_set)
            await capacity._close_capacity_worker(tmp_path)
            await worker["startup"]
            return worker
        try:
            return loop.run_until_complete(start_then_close())
        finally:
            loop.close()

    worker = await asyncio.to_thread(temporary_loop)
    assert worker["loop"].is_closed() and not worker["launch"].done()
    release.set()
    await until(lambda: tmp_path not in capacity._CAPACITY_WORKERS)
    assert worker["process"].poll() is not None


@pytest.mark.asyncio
async def test_reused_idle_worker_does_not_hold_locks_and_restarts_after_exit(tmp_path):
    _tree(tmp_path)
    worker = await ready_worker(tmp_path)
    try:
        for _ in range(2):
            result = await capacity.run_capacity_maintenance(tmp_path, limit_bytes=100_000, timeout=1,
                                                             cancelled=lambda: False)
            assert result["status"] == "succeeded"
            assert capacity._CAPACITY_WORKERS[tmp_path] is worker
        for name in ("context_update.lock", "context_usage.lock"):
            with FileLock(tmp_path / "state" / name, timeout=0):
                pass
        # EOF means parent lifetime ended, with no idle timeout/repeated imports.
        worker["process"].stdin.close()
        await asyncio.to_thread(worker["process"].wait, timeout=3)
        pid = worker["process"].pid
        new_pid = await _recover_and_reclaim(tmp_path)
        assert new_pid != pid
    finally:
        await capacity._close_capacity_worker(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["publication", "read"])
async def test_real_preparer_keeps_new_publications_and_recent_reads(tmp_path, monkeypatch, change):
    root = _tree(tmp_path)
    entered, release = tmp_path / "prepared", tmp_path / "release"
    inject_worker(monkeypatch, tmp_path,
                  "original = capacity._copy_tree\n"
                  "def pause(source, target):\n"
                  "    original(source, target)\n"
                  f"    Path({str(entered)!r}).touch()\n"
                  f"    while not Path({str(release)!r}).exists():\n"
                  "        time.sleep(0.005)\n"
                  "capacity._copy_tree = pause")
    await ready_worker(tmp_path)
    task = asyncio.create_task(capacity.run_capacity_maintenance(
        tmp_path, limit_bytes=6000, timeout=2, cancelled=lambda: False,
    ))
    try:
        await until(entered.exists)
        if change == "publication":
            candidate = tmp_path / "new-publication"
            shutil.copytree(root, candidate)
            (candidate / "old.md").write_text("# New version\n" + "a" * 4000, encoding="utf-8")
            async with usage.context_update_lock(tmp_path):
                usage.publish_with_usage(tmp_path, candidate, {})
        else:
            assert "Old" in usage.read_with_usage(tmp_path, "old.md", allowed=lambda: True)["markdown"]
        release.touch()
        result = await task
        assert result["reason"] == ("context_changed" if change == "publication" else "usage_changed"), result
        assert result["removed_count"] == 0 and (root / "old.md").exists()
    finally:
        release.touch()
        await capacity._close_capacity_worker(tmp_path)


@pytest.mark.asyncio
async def test_child_watchdog_releases_write_lock_even_while_parent_loop_is_delayed(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    entered = tmp_path / "commit-lock-held"
    with monkeypatch.context() as patcher:
        inject_worker(patcher, tmp_path,
                      "from filelock import FileLock\n"
                      "from openjiuwen.harness.personal_context import _capacity_commit as commits\n"
                      "def stuck(plan, *, seconds):\n"
                      "    with FileLock(plan['lock'], timeout=0):\n"
                      f"        Path({str(entered)!r}).touch()\n"
                      "        time.sleep(60)\n"
                      "commits.commit = stuck")
        worker = await ready_worker(tmp_path)
        task = asyncio.create_task(capacity.run_capacity_maintenance(
            tmp_path, limit_bytes=6000, timeout=2, cancelled=lambda: False,
        ))
        try:
            await until(entered.exists)
            # Deliberately delay the parent. The helper must enforce its own
            # short write window without relying on event-loop responsiveness.
            time.sleep(0.25)
            with FileLock(tmp_path / "state/context_update.lock", timeout=0):
                pass
            result = await asyncio.wait_for(task, 0.5)
            assert result["status"] == "failed"
            assert worker["process"].returncode is not None
            assert (root / "old.md").exists()
        finally:
            await capacity._close_capacity_worker(tmp_path)
    await _recover_and_reclaim(tmp_path)


def test_cleanup_preserves_live_and_unmarked_directories(tmp_path):
    root = _tree(tmp_path)
    state = usage._state_path(tmp_path, "context_usage.json").parent
    abandoned = state / "context-capacity-abandoned"
    active = state / "context-capacity-active"
    unmarked = state / "context-capacity-unmarked"
    unrelated = state / "unrelated"
    for directory in (abandoned, active, unmarked, unrelated):
        directory.mkdir()
        (directory / "keep.md").write_text("candidate data", encoding="utf-8")
    (abandoned / ".owner.lock").touch()
    (unrelated / ".owner.lock").touch()
    with FileLock(active / ".owner.lock", timeout=0):
        capacity._cleanup_capacity_temporary(tmp_path)
        assert active.exists()
    assert not abandoned.exists() and unmarked.exists() and unrelated.exists()
    assert (root / "old.md").exists()


@pytest.mark.asyncio
async def test_tiny_budget_keeps_ready_worker_for_next_useful_round(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    entered = tmp_path / "maintenance-entered"
    inject_worker(monkeypatch, tmp_path,
                  "original = capacity.maintain_capacity\n"
                  "def observed(*args, **kwargs):\n"
                  f"    Path({str(entered)!r}).touch()\n"
                  "    time.sleep(0.05)\n"
                  "    return original(*args, **kwargs)\n"
                  "capacity.maintain_capacity = observed")
    worker = await ready_worker(tmp_path)
    try:
        result = await capacity.run_capacity_maintenance(
            tmp_path, limit_bytes=6000, timeout=0.005, cancelled=lambda: False,
        )
        assert result == {"status": "failed", "reason": "time_budget"}
        await asyncio.sleep(0.03)
        assert not entered.exists()
        assert worker["process"].poll() is None
        assert capacity._CAPACITY_WORKERS[tmp_path] is worker
        usage.read_with_usage(tmp_path, "hot.md", allowed=lambda: True)
        result = await capacity.run_capacity_maintenance(
            tmp_path, limit_bytes=6000, timeout=2, cancelled=lambda: False,
        )
        assert entered.exists()
        assert result["status"] == "succeeded" and result["removed_count"] == 1, result
        assert (root / "hot.md").exists() and not (root / "old.md").exists()
        assert capacity._CAPACITY_WORKERS[tmp_path] is worker
    finally:
        await capacity._close_capacity_worker(tmp_path)


@pytest.mark.asyncio
async def test_dead_worker_identity_is_cleared_even_when_handle_cleanup_is_delayed(tmp_path, monkeypatch):
    _tree(tmp_path)
    worker = await ready_worker(tmp_path)
    process = worker["process"]
    original_wait = process.wait

    def slow_cleanup(*args, **kwargs):
        result = original_wait(*args, **kwargs)
        time.sleep(0.25)
        return result

    monkeypatch.setattr(process, "wait", slow_cleanup)
    try:
        await capacity._close_capacity_worker(tmp_path)
        assert process.poll() is not None
        assert tmp_path not in capacity._CAPACITY_WORKERS
        new_pid = await _recover_and_reclaim(tmp_path)
        assert new_pid != process.pid
    finally:
        await capacity._close_capacity_worker(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("closed_loop", [False, True])
async def test_sync_shutdown_reaps_ready_worker_and_closes_pipes(tmp_path, closed_loop):
    from openjiuwen.harness.personal_context import PersonalContext

    core = PersonalContext(home=tmp_path)
    home = core._home
    if closed_loop:
        def prepare():
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(ready_worker(home))
            finally:
                loop.close()
        worker = await asyncio.to_thread(prepare)
    else:
        worker = await ready_worker(home)
    process = worker["process"]
    try:
        began = time.monotonic()
        core.shutdown()
        core.shutdown()  # Terminal cleanup is idempotent.
        assert time.monotonic() - began < 0.15
        await until(lambda: home not in capacity._CAPACITY_WORKERS, timeout=1)
        assert process.returncode is not None
        assert process.stdin.closed and process.stdout.closed
    finally:
        core.shutdown()
        await capacity._close_capacity_worker(home)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["wait_timeout", "registry_busy", "kill_retry"])
async def test_sync_shutdown_finishes_after_transient_cleanup_failure(tmp_path, monkeypatch, failure):
    from openjiuwen.harness.personal_context import PersonalContext

    core = PersonalContext(home=tmp_path)
    home = core._home
    worker = await ready_worker(home)
    process = worker["process"]
    waited = threading.Event()
    actual_wait = process.wait
    actual_kill = process.kill

    def first_wait_times_out(*args, **kwargs):
        if not waited.is_set():
            waited.set()
            raise subprocess.TimeoutExpired(process.args, kwargs["timeout"])
        return actual_wait(*args, **kwargs)

    def first_kill_fails():
        if not waited.is_set():
            waited.set()
            raise OSError("transient process termination failure")
        return actual_kill()

    held = False
    if failure == "wait_timeout":
        monkeypatch.setattr(process, "wait", first_wait_times_out)
    elif failure == "kill_retry":
        monkeypatch.setattr(process, "kill", first_kill_fails)
    else:
        assert capacity._CAPACITY_WORKERS_LOCK.acquire(blocking=False)
        held = True
    try:
        core.shutdown()
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "normal tool result"), 0.2) == "normal tool result"
        if held:
            await asyncio.sleep(0.1)
            capacity._CAPACITY_WORKERS_LOCK.release()
            held = False
        await until(lambda: home not in capacity._CAPACITY_WORKERS, timeout=1)
        assert process.returncode is not None
        assert process.stdin.closed and process.stdout.closed
    finally:
        if held:
            capacity._CAPACITY_WORKERS_LOCK.release()
        core.shutdown()
        await capacity._close_capacity_worker(home)


def test_capacity_worker_can_import_core_from_outside_repository(tmp_path):
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    program = """
import asyncio
from pathlib import Path
from openjiuwen.harness.personal_context import context_capacity as capacity
async def main():
    home = Path.cwd() / 'isolated-home'
    worker = capacity._warm_capacity_worker(home)
    try:
        ready = await asyncio.wait_for(asyncio.shield(worker['ready']), 20)
        assert ready == {'ready': True}, ready
        result = await capacity.run_capacity_maintenance(home, limit_bytes=100_000, timeout=1, cancelled=lambda: False)
        assert result['status'] == 'succeeded', result
    finally:
        await capacity._close_capacity_worker(home)
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", program], cwd=tmp_path, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).decode("utf-8", errors="replace")


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_thread", [False, True])
async def test_immediate_sync_shutdown_does_not_miss_a_late_process(tmp_path, monkeypatch, foreign_thread):
    from openjiuwen.harness.personal_context import PersonalContext

    loop = asyncio.get_running_loop()
    errors = []
    original_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: errors.append(context.get("message")))
    entered, release = threading.Event(), threading.Event()
    actual_thread = threading.Thread

    def delayed_start(*args, **kwargs):
        if kwargs.get("name") == "pcs-capacity-start":
            target = kwargs["target"]
            def paused():
                entered.set()
                assert release.wait(3)
                target()
            kwargs["target"] = paused
        return actual_thread(*args, **kwargs)

    monkeypatch.setattr(capacity.threading, "Thread", delayed_start)

    async def close_before_spawn():
        core = PersonalContext(home=tmp_path)
        home = core._home
        worker = capacity._warm_capacity_worker(home)
        try:
            await until(entered.is_set)
            if foreign_thread:
                caller = threading.Thread(target=core.shutdown)
                caller.start()
                caller.join(timeout=0.2)
                assert not caller.is_alive()
            else:
                core.shutdown()
            await until(lambda: worker["startup"].done())
            release.set()
            await until(lambda: worker["launch"].done())
            process = worker.get("process")
            if process is not None:
                await until(lambda: process.returncode is not None and process.stdin.closed and process.stdout.closed)
            await until(lambda: home not in capacity._CAPACITY_WORKERS)
            await asyncio.sleep(0.03)
        finally:
            release.set()
            core.shutdown()
            await capacity._close_capacity_worker(home)
            process = worker.get("process")
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=0.5)
                process.stdin.close()
                process.stdout.close()

    try:
        await close_before_spawn()
        gc.collect()
        await asyncio.sleep(0.02)
        assert not errors, errors
    finally:
        release.set()
        loop.set_exception_handler(original_handler)
