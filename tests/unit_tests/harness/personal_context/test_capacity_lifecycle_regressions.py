# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Capacity settings and maintenance must not disrupt the existing runtime."""

import asyncio
import threading
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context import PersonalContext, context_capacity
from openjiuwen.harness.personal_context import personal_context as runtime


def _config(*, source: Path | None = None, with_model: bool = False):
    value = {
        "collection_enabled": True, "strategy_profile": "rules", "fetch_services": [],
    }
    if source is not None:
        source.mkdir()
        value["fetch_services"] = [{
            "service_id": "notes", "provider": "local_files", "enabled": False,
            "interval_seconds": 3600, "time_range": {"mode": "all"},
            "source": {"root_dir": str(source)},
        }]
    if with_model:
        value.update(
            model_client={
                "client_provider": "OpenAI", "api_key": "test-only", "api_base": "https://example.invalid/v1",
            },
            model_request={"model": "test-model"},
        )
    return PersonalContext.Config.from_dict(value)


@pytest.mark.asyncio
async def test_capacity_update_accepts_regenerated_model_id_without_replacing_live_model(tmp_path):
    core = PersonalContext(home=tmp_path)
    original = _config(with_model=True)
    try:
        await core.set_configuration(original)
        await core.activate_runtime()
        pipeline = core._pipeline_service
        replacement = PersonalContext.Config.from_dict({
            **original.model_dump(exclude={"model_client": {"client_id"}}), "max_context_size_mb": 75,
        })
        assert replacement.model_client.client_id != original.model_client.client_id
        await core.set_configuration(replacement)
        assert core._config.max_context_size_mb == 75
        assert core._config.model_client is original.model_client
        assert core._config.model_request is original.model_request
        assert core._pipeline_service is pipeline
        assert pipeline._config is core._config
        # Host retries may resolve the same settings to another new client ID.
        await core.set_configuration(replacement)
        assert core._state == "RUNNING"
        assert core._config.model_client is original.model_client
    finally:
        await core.deactivate_runtime()
        core.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [
    {"collection_enabled": False},
    {"agent_use_enabled": True},
    {"model_request": {"model": "different-model"}},
    {"model_client": {
        "client_provider": "OpenAI", "api_key": "changed-key", "api_base": "https://example.invalid/v1",
    }},
])
async def test_capacity_update_does_not_hide_actual_running_config_changes(tmp_path, changed):
    core = PersonalContext(home=tmp_path)
    original = _config(with_model=True)
    try:
        await core.set_configuration(original)
        await core.activate_runtime()
        candidate = PersonalContext.Config.from_dict({
            **original.model_dump(exclude={"model_client": {"client_id"}}),
            "max_context_size_mb": 75, **changed,
        })
        with pytest.raises(BaseError, match="must be stopped"):
            await core.set_configuration(candidate)
        assert core._config is original
        assert core._state == "RUNNING"
    finally:
        await core.deactivate_runtime()
        core.shutdown()


async def _start_empty_run(tmp_path):
    core = PersonalContext(home=tmp_path / "home")
    await core.set_configuration(_config(source=tmp_path / "source"))
    await core.activate_runtime()
    return core


async def _wait_for_cursor(core):
    async def wait():
        while core._read_cursor("notes") is None:
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), timeout=2)


@pytest.mark.asyncio
async def test_under_limit_collection_does_not_wait_for_another_writer(tmp_path):
    core = await _start_empty_run(tmp_path)
    lock = FileLock(core._home / "state/context_update.lock", thread_local=False)
    lock.acquire(timeout=0)
    try:
        await core.run_fetch(service_id="notes")
        await asyncio.wait_for(asyncio.shield(core._manual_fetch_tasks["notes"]), timeout=1)
        record = (await core.get_fetch_run_status("notes"))["runs"][0]
        assert record["run_state"] == "succeeded"
        assert core._read_cursor("notes") is not None
        assert lock.is_locked
        with pytest.raises(Timeout):
            FileLock(lock.lock_file).acquire(timeout=0)
    finally:
        lock.release()
        await core.deactivate_runtime()
        core.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_requested", [False, True])
async def test_stalled_preparation_does_not_hold_writers_or_delay_stop(tmp_path, monkeypatch, stop_requested):
    from openjiuwen.harness.personal_context.fetch.local_files import LocalFilesFetchService

    core = await _start_empty_run(tmp_path)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original_commit = LocalFilesFetchService.commit_run

    async def commit_with_useful_work(provider, **kwargs):
        # Give this run a nonzero maintenance allowance even on a coarse clock.
        await asyncio.sleep(0.15)
        await original_commit(provider, **kwargs)

    monkeypatch.setattr(LocalFilesFetchService, "commit_run", commit_with_useful_work)

    def maintain(*_args, **_kwargs):
        started.set()
        try:
            assert release.wait(timeout=5)
            return {"status": "failed", "reason": "test_preparation_released"}
        finally:
            finished.set()

    monkeypatch.setattr(context_capacity, "maintain_capacity", maintain)
    try:
        await core.run_fetch(service_id="notes")
        manual = core._manual_fetch_tasks["notes"]
        assert await asyncio.to_thread(started.wait, 2)
        if stop_requested and not manual.done():
            await asyncio.wait_for(core.stop_fetch_run("notes"), timeout=0.5)
        await asyncio.wait_for(asyncio.shield(manual), timeout=0.5)
        assert not release.is_set()
        with FileLock(core._home / "state/context_update.lock", timeout=0):
            pass
        record = (await core.get_fetch_run_status("notes"))["runs"][0]
        assert record["run_state"] in {"succeeded", "cancelled"}
        assert core._read_cursor("notes") is not None
        # A second run remains usable while the old optional thread is stuck.
        await core.run_fetch(service_id="notes")
        await asyncio.wait_for(asyncio.shield(core._manual_fetch_tasks["notes"]), timeout=0.5)
        assert (await core.get_fetch_run_status("notes"))["runs"][0]["run_state"] == "succeeded"
    finally:
        release.set()
        if started.is_set():
            assert await asyncio.to_thread(finished.wait, 2)
        await core.deactivate_runtime()
        core.shutdown()
