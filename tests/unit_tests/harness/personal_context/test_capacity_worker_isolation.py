# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optional capacity IPC and terminal cleanup cannot obstruct normal work."""

import asyncio
import concurrent.futures
import contextlib
import threading

import pytest

from openjiuwen.harness.personal_context import PersonalContext
from openjiuwen.harness.personal_context import context_capacity as capacity
from tests.unit_tests.harness.personal_context.capacity_worker_helpers import until


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["handshake", "send", "response"])
async def test_slow_capacity_ipc_leaves_default_executor_available(tmp_path, monkeypatch, stage):
    entered, release = threading.Event(), threading.Event()
    actual_launch = capacity.subprocess.Popen

    def launch(*args, **kwargs):
        process = actual_launch(*args, **kwargs)
        readline, flush = process.stdout.readline, process.stdin.flush
        reads = 0

        def read():
            nonlocal reads
            reads += 1
            if (stage == "handshake" and reads == 1) or (stage == "response" and reads == 2):
                entered.set()
                assert release.wait(5)
            return readline()

        def write():
            if stage == "send":
                entered.set()
                assert release.wait(5)
            return flush()

        monkeypatch.setattr(process.stdout, "readline", read)
        monkeypatch.setattr(process.stdin, "flush", write)
        return process

    monkeypatch.setattr(capacity.subprocess, "Popen", launch)
    loop = asyncio.get_running_loop()
    previous = loop._default_executor
    ordinary_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(ordinary_pool)
    worker = capacity._warm_capacity_worker(tmp_path)
    maintenance = None
    try:
        if stage != "handshake":
            assert await asyncio.wait_for(asyncio.shield(worker["ready"]), 20) == {"ready": True}
            maintenance = asyncio.create_task(capacity.run_capacity_maintenance(
                tmp_path, limit_bytes=100_000, timeout=3, cancelled=lambda: False,
            ))
        await until(entered.is_set, timeout=20)
        result = await asyncio.wait_for(asyncio.to_thread(lambda: "normal tool result"), timeout=0.3)
        assert result == "normal tool result"
        release.set()
        if maintenance is not None:
            assert (await maintenance)["status"] == "succeeded"
        else:
            assert await asyncio.wait_for(asyncio.shield(worker["ready"]), 20) == {"ready": True}
    finally:
        release.set()
        if maintenance is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await maintenance
        await capacity._close_capacity_worker(tmp_path)
        await until(lambda: tmp_path not in capacity._CAPACITY_WORKERS)
        loop._default_executor = previous
        ordinary_pool.shutdown(wait=False, cancel_futures=True)


@pytest.mark.asyncio
async def test_shutdown_during_startup_registration_cannot_leak_late_task(tmp_path, monkeypatch):
    core = PersonalContext(home=tmp_path)
    loop = asyncio.get_running_loop()
    create_task = loop.create_task
    errors = []

    def shutdown():
        try:
            core.shutdown()
        except Exception as exc:
            errors.append(exc)

    def interleaved_create(coro, **kwargs):
        if kwargs.get("name") == "pcs-capacity-startup":
            caller = threading.Thread(target=shutdown)
            caller.start()
            caller.join(timeout=0.3)
            assert not caller.is_alive()
        return create_task(coro, **kwargs)

    monkeypatch.setattr(loop, "create_task", interleaved_create)
    worker = capacity._warm_capacity_worker(core._home)
    try:
        await until(lambda: worker["startup"].done())
        await until(lambda: core._home not in capacity._CAPACITY_WORKERS)
        assert not errors
        with pytest.raises(RuntimeError, match="shutdown"):
            core._query_executor.submit(lambda: None)
        process = worker.get("process")
        assert process is None or process.poll() is not None
    finally:
        core.shutdown()
        await capacity._close_capacity_worker(core._home)


def test_auxiliary_shutdown_failure_still_closes_query_pool(tmp_path, monkeypatch):
    core = PersonalContext(home=tmp_path)

    def fail(_home):
        raise OSError("auxiliary cleanup failed")

    monkeypatch.setattr(capacity, "_cancel_capacity_worker", fail)
    try:
        core.shutdown()
        with pytest.raises(RuntimeError, match="shutdown"):
            core._query_executor.submit(lambda: None)
    finally:
        core._query_executor.shutdown(wait=False, cancel_futures=True)
