# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Durable state, file failure boundaries and per-address CAS."""

import asyncio
import json
import threading
from dataclasses import replace

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.extension.data import JsonState, StateAddress, StateSnapshot

ADDRESS = StateAddress("sample.counter", "count", "session", "s1")


@pytest.mark.asyncio
async def test_scope_isolation_restore_copies_and_clear_tombstone(tmp_path):
    states = JsonState(tmp_path / "state")
    assert await states.get(ADDRESS) == StateSnapshot(False)
    assert not states.directory.exists()
    value = {"nested": [1]}
    assert (await states.set(ADDRESS, value, expected_version=0)).version == 1
    value["nested"].append(2)
    snapshot = await states.get(ADDRESS)
    snapshot.value["nested"].append(3)
    assert (await JsonState(states.directory).get(ADDRESS)).value == {"nested": [1]}
    application = replace(ADDRESS, scope="application", session_id=None)
    assert (await states.set(application, "shared")).is_set
    assert not (await states.get(replace(ADDRESS, session_id="s2"))).exists
    await states.clear_session("s1")
    assert await states.get(ADDRESS) == StateSnapshot(False, None, 2)
    assert (await states.get(application)).value == "shared"
    stale = await states.set(ADDRESS, 10, expected_version=1)
    assert stale.reason == "version_conflict" and stale.version == 2
    assert (await states.set(ADDRESS, 10, expected_version=2)).version == 3
    disk = json.loads(
        (states.directory / "sample.counter" / "sessions" / "s1.json").read_text(encoding="utf-8")
    )
    assert disk["entries"]["count"] == {"value": 10, "version": 3}
    application_disk = json.loads(
        (states.directory / "sample.counter" / "application.json").read_text(encoding="utf-8")
    )
    assert application_disk["entries"]["count"]["value"] == "shared"


@pytest.mark.asyncio
async def test_same_value_and_concurrent_compare_and_set(tmp_path):
    states = JsonState(tmp_path)
    notices = []
    states.changes.subscribe(lambda address, version: notices.append((address, version)))
    assert (await states.set(ADDRESS, {"a": 1, "b": 2})).changed
    same = await states.set(ADDRESS, {"b": 2, "a": 1}, expected_version=1)
    assert same.is_set and not same.changed and same.version == 1
    first, second = await asyncio.gather(
        states.set(ADDRESS, 10, expected_version=1),
        JsonState(tmp_path).set(ADDRESS, 20, expected_version=1),
    )
    assert sum(result.is_set for result in (first, second)) == 1
    assert {result.reason for result in (first, second)} == {None, "version_conflict"}
    assert (await states.get(ADDRESS)).version == 2
    assert (await states.set(ADDRESS, True)).changed
    assert (await states.set(ADDRESS, 1)).changed
    assert notices[0] == (ADDRESS, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "invalid"}, object()])
async def test_invalid_json_is_rejected_before_creating_files(tmp_path, value):
    states = JsonState(tmp_path / "absent")
    with pytest.raises(BaseError):
        await states.set(ADDRESS, value)
    assert not states.directory.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [True, -1, "1", 1.5])
async def test_invalid_version_is_rejected(tmp_path, version):
    with pytest.raises(BaseError):
        await JsonState(tmp_path).set(ADDRESS, 1, expected_version=version)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "not json", '{"format_version":1,"format_version":1}',
    '{"format_version":true,"entries":{}}',
    '{"format_version":1.0,"entries":{}}',
    '{"format_version":1,"entries":{"x":{"value":0,"version":true}}}',
    '{"format_version":1,"entries":[]}',
])
async def test_corrupt_files_are_never_overwritten(tmp_path, content):
    path = tmp_path / "sample.counter" / "sessions" / "s1.json"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    states = JsonState(tmp_path)
    with pytest.raises(BaseError):
        await states.get(ADDRESS)
    with pytest.raises(BaseError):
        await states.set(ADDRESS, 99)
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_write_failure_and_listener_failure_have_distinct_results(tmp_path, monkeypatch):
    import openjiuwen.core.extension.data.state as module

    states = JsonState(tmp_path)
    assert (await states.set(ADDRESS, 1)).version == 1
    with monkeypatch.context() as patch:
        patch.setattr(module, "replace_json", lambda path, data, previous: False)
        result = await states.set(ADDRESS, 2)
    assert not result.is_set and result.reason == "storage_error" and result.version == 1
    assert (await states.get(ADDRESS)).value == 1
    delivered = []

    def listener(address, version):
        if not delivered:
            delivered.append("failed")
            raise RuntimeError("temporary enqueue failure")
        delivered.append(version)

    states.changes.subscribe(listener)
    result = await states.set(ADDRESS, 2)
    assert result.is_set and result.version == 2
    states.changes.retry_pending()
    assert delivered == ["failed", 2]


@pytest.mark.asyncio
async def test_unavailable_lock_returns_unknown_version_without_claiming_a_commit(tmp_path, monkeypatch):
    import openjiuwen.core.extension.data.state as module

    def unavailable(path):
        raise OSError("lock unavailable")

    monkeypatch.setattr(module, "package_file_lock", unavailable)
    result = await JsonState(tmp_path).set(ADDRESS, 1)
    assert not result.is_set and not result.changed and result.version is None
    assert result.reason == "storage_error"
    assert not (tmp_path / "sample.counter").exists()


@pytest.mark.asyncio
async def test_lock_release_failure_after_state_commit_is_an_unknown_commit(tmp_path, monkeypatch):
    from portalocker import Lock

    original = Lock.release
    fail = [True]

    def release(lock):
        original(lock)
        if fail.pop() if fail else False:
            raise OSError("release failed")

    monkeypatch.setattr(Lock, "release", release)
    states = JsonState(tmp_path)
    noticed = []
    states.changes.subscribe(lambda address, version: noticed.append((address, version)))
    with pytest.raises(BaseError) as caught:
        await states.set(ADDRESS, 1)
    assert caught.value.status is StatusCode.EXTENSION_DATA_COMMIT_UNKNOWN
    assert noticed == [(ADDRESS, 1)]
    assert (await states.get(ADDRESS)).value == 1


@pytest.mark.asyncio
async def test_cancel_waits_for_worker_and_lock_before_returning(tmp_path, monkeypatch):
    import openjiuwen.core.extension.data.state as module

    entered, release = threading.Event(), threading.Event()
    original = module.replace_json

    def blocked(path, data, previous):
        entered.set()
        assert release.wait(5)
        return original(path, data, previous)

    monkeypatch.setattr(module, "replace_json", blocked)
    states = JsonState(tmp_path)
    task = asyncio.create_task(states.set(ADDRESS, 1))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await states.get(ADDRESS)).value == 1


@pytest.mark.asyncio
async def test_cleanup_preserves_reload_and_shutdown_and_removes_uninstall(tmp_path):
    states = JsonState(tmp_path)
    other = replace(ADDRESS, session_id="s2")
    application = replace(ADDRESS, scope="application", session_id=None)
    await states.set(ADDRESS, 1)
    await states.set(other, 2)
    await states.set(application, "shared")
    for reason in ("reload", "shutdown"):
        await states.cleanup_package(ADDRESS.package, reason=reason)
        assert (await states.get(ADDRESS)).version == 1
        assert (await states.get(other)).value == 2
        assert (await states.get(application)).value == "shared"
    await states.cleanup_package(ADDRESS.package)
    await states.cleanup_package(ADDRESS.package)
    assert not (tmp_path / "sample.counter").exists()
    assert not (tmp_path / ".sample.counter.lock").exists()


@pytest.mark.asyncio
async def test_capacity_path_and_explicit_clear_errors(tmp_path):
    states = JsonState(tmp_path, max_bytes=128)
    with pytest.raises(BaseError):
        await states.set(replace(ADDRESS, package="../other"), 1)
    with pytest.raises(BaseError):
        await states.set(ADDRESS, "x" * 129)
    with pytest.raises(BaseError):
        await states.clear_session("")
    with pytest.raises(BaseError):
        await states.get(replace(ADDRESS, scope="application"))
