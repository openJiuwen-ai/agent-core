# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Persistence, interprocess serialization, failures, and cancellation."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from portalocker import Lock
from portalocker.exceptions import LockException

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.extension.data import JsonStore, files
from openjiuwen.core.extension.data.store import STORE_MAX_BYTES


@pytest.mark.asyncio
async def test_crud_persistence_order_defaults_and_copies(tmp_path: Path) -> None:
    directory = tmp_path / "data"
    store = JsonStore(directory)
    assert not directory.exists()
    sentinel = object()
    assert await store.get("demo", "missing", sentinel) is sentinel
    assert await store.keys("demo") == []
    assert await store.delete("demo", "missing") is True
    assert not directory.exists()
    assert not (directory / "demo.json").exists()
    value = {"nested": [True, None, 1.5, "value"]}
    assert await store.set("demo", "first", value) is True
    value["nested"].append("mutated input")
    assert await store.set("demo", "null", None) is True
    assert await store.get("demo", "null", sentinel) is None
    assert await store.set("demo", "last", 3) is True
    result = await store.get("demo", "first")
    result["nested"].append("mutated output")
    reopened = JsonStore(directory)
    assert await reopened.get("demo", "first") == {"nested": [True, None, 1.5, "value"]}
    assert await reopened.keys("demo") == ["first", "null", "last"]
    assert await reopened.set("demo", "first", {}) is True
    assert await reopened.keys("demo") == ["first", "null", "last"]
    assert await reopened.delete("demo", "null") is True
    assert await reopened.set("demo", "null", 2) is True
    assert await reopened.keys("demo") == ["first", "last", "null"]
    assert await reopened.keys("other") == []
    assert (directory / "demo.json").read_bytes() == b'{"first":{},"last":3,"null":2}'


@pytest.mark.asyncio
async def test_concurrent_instances_do_not_lose_different_keys(tmp_path: Path) -> None:
    stores = [JsonStore(tmp_path), JsonStore(tmp_path)]
    results = await asyncio.gather(*(stores[i % 2].set("demo", str(i), i) for i in range(40)))
    assert all(results)
    assert set(await stores[0].keys("demo")) == {str(i) for i in range(40)}
    assert await asyncio.gather(*(stores[1].get("demo", str(i)) for i in range(40))) == list(range(40))


@pytest.mark.asyncio
async def test_processes_share_a_stable_lock(tmp_path: Path) -> None:
    script = (
        "import asyncio, sys\n"
        "from pathlib import Path\n"
        "from openjiuwen.core.extension.data import JsonStore\n"
        "async def main():\n"
        "    store = JsonStore(Path(sys.argv[1]))\n"
        "    for i in range(12):\n"
        "        assert await store.set('demo', sys.argv[2] + str(i), i)\n"
        "asyncio.run(main())\n"
    )

    def run(prefix: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), prefix],
            capture_output=True,
            text=True,
            timeout=60,
        )

    processes = await asyncio.gather(asyncio.to_thread(run, "a"), asyncio.to_thread(run, "b"))
    for process in processes:
        assert process.returncode == 0, process.stderr
    assert set(await JsonStore(tmp_path).keys("demo")) == {f"{prefix}{i}" for prefix in ("a", "b") for i in range(12)}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "wrong"}, (1, 2), {1, 2}, b"bytes", lambda: None])
async def test_non_json_values_are_rejected(tmp_path: Path, value: object) -> None:
    with pytest.raises(BaseError) as caught:
        await JsonStore(tmp_path).set("demo", "key", value)
    assert caught.value.status is StatusCode.EXTENSION_DATA_INVALID
    assert not (tmp_path / "demo.json").exists()


@pytest.mark.asyncio
async def test_cycles_and_invalid_unicode_are_rejected(tmp_path: Path) -> None:
    cycle: list[object] = []
    cycle.append(cycle)
    for value in (cycle, "\ud800"):
        with pytest.raises(BaseError) as caught:
            await JsonStore(tmp_path).set("demo", "key", value)
        assert caught.value.status is StatusCode.EXTENSION_DATA_INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "package_id",
    ["../escape", "..", "", "a/b", "a\\b", "C:bad", "CON", "nul.data", "demo.", "a..b"],
)
async def test_invalid_package_paths_are_rejected(tmp_path: Path, package_id: str) -> None:
    store = JsonStore(tmp_path)
    for operation in (store.keys(package_id), store.set(package_id, "key", 1), store.cleanup_package(package_id)):
        with pytest.raises(BaseError) as caught:
            await operation
        assert caught.value.status is StatusCode.EXTENSION_DATA_INVALID
    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


@pytest.mark.asyncio
async def test_capacity_is_checked_for_the_whole_utf8_file(tmp_path: Path) -> None:
    store = JsonStore(tmp_path)
    assert await store.set("demo", "x", "a" * (STORE_MAX_BYTES - len(b'{"x":""}')))
    original = (tmp_path / "demo.json").read_bytes()
    assert len(original) == STORE_MAX_BYTES
    with pytest.raises(BaseError) as caught:
        await store.set("demo", "y", "extra")
    assert caught.value.status is StatusCode.EXTENSION_DATA_INVALID
    assert (tmp_path / "demo.json").read_bytes() == original
    assert await store.delete("demo", "x")
    with pytest.raises(BaseError):
        await store.set("demo", "x", "界" * (STORE_MAX_BYTES // 3))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [b"{broken", b"[]", b'{"x":NaN}', b'{"x":1,"x":2}', b"\xff", b"x" * (STORE_MAX_BYTES + 1)],
    ids=["syntax", "array", "nan", "duplicates", "encoding", "capacity"],
)
async def test_corrupt_file_is_never_treated_as_empty(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "demo.json"
    path.write_bytes(data)
    store = JsonStore(tmp_path)
    for operation in (
        store.get("demo", "missing", 7),
        store.keys("demo"),
        store.set("demo", "new", 1),
        store.delete("demo", "x"),
    ):
        with pytest.raises(BaseError) as caught:
            await operation
        assert caught.value.status is StatusCode.EXTENSION_DATA_READ_FAILED
        assert path.read_bytes() == data


@pytest.mark.asyncio
async def test_symlink_and_non_file_targets_are_rejected(tmp_path: Path) -> None:
    store = JsonStore(tmp_path)
    (tmp_path / "demo.json").mkdir()
    with pytest.raises(BaseError) as caught:
        await store.set("demo", "key", 1)
    assert caught.value.status is StatusCode.EXTENSION_DATA_PATH_INVALID
    outside = tmp_path / "outside"
    outside.write_text('{"key":1}', encoding="utf-8")
    try:
        (tmp_path / "linked.json").symlink_to(outside)
    except OSError:
        pytest.skip("symbolic link creation is unavailable for this Windows account")
    with pytest.raises(BaseError):
        await store.set("linked", "key", 2)
    assert json.loads(outside.read_text(encoding="utf-8")) == {"key": 1}


@pytest.mark.asyncio
async def test_write_failure_and_unknown_commit_are_distinct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = JsonStore(tmp_path)
    assert await store.set("demo", "key", "old")
    path = tmp_path / "demo.json"
    original = path.read_bytes()

    def fail_replace(source, target):
        raise PermissionError("replace refused")

    monkeypatch.setattr(files.os, "replace", fail_replace)
    assert await store.set("demo", "key", "new") is False
    assert await store.delete("demo", "key") is False
    assert path.read_bytes() == original

    def unknown_replace(source, target):
        Path(target).write_bytes(b"unknown")
        raise OSError("replacement outcome unavailable")

    monkeypatch.setattr(files.os, "replace", unknown_replace)
    with pytest.raises(BaseError) as caught:
        await store.set("demo", "key", "new")
    assert caught.value.status is StatusCode.EXTENSION_DATA_COMMIT_UNKNOWN
    assert not await asyncio.to_thread(lambda: list(tmp_path.glob("*.tmp")))


@pytest.mark.asyncio
async def test_failed_replace_can_confirm_a_completed_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replace = os.replace

    def completed_then_failed(source, target):
        replace(source, target)
        raise OSError("acknowledgement lost")

    monkeypatch.setattr(files.os, "replace", completed_then_failed)
    store = JsonStore(tmp_path)
    assert await store.set("demo", "key", 1) is True
    assert await store.get("demo", "key") == 1


@pytest.mark.asyncio
async def test_unreadable_file_does_not_return_defaults(tmp_path: Path, monkeypatch) -> None:
    service = JsonStore(tmp_path)
    assert await service.set("demo", "key", 1)
    original = Path.open

    def refuse(path, *args, **kwargs):
        if path.name == "demo.json":
            raise PermissionError("read refused")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse)
    for operation in (service.get("demo", "key", "default"), service.keys("demo"), service.set("demo", "key", 2)):
        with pytest.raises(BaseError) as caught:
            await operation
        assert caught.value.status is StatusCode.EXTENSION_DATA_READ_FAILED


@pytest.mark.asyncio
async def test_temporary_file_flush_failure_never_replaces_old_data(tmp_path: Path, monkeypatch) -> None:
    service = JsonStore(tmp_path)
    assert await service.set("demo", "key", "old")
    original = (tmp_path / "demo.json").read_bytes()

    def refuse(descriptor):
        raise OSError("disk flush failed")

    monkeypatch.setattr(files.os, "fsync", refuse)
    assert await service.set("demo", "key", "new") is False
    assert (tmp_path / "demo.json").read_bytes() == original


@pytest.mark.asyncio
async def test_lock_release_failure_after_commit_is_never_false(tmp_path: Path, monkeypatch) -> None:
    original = Lock.release
    should_fail = [True]

    def release(lock):
        original(lock)
        if should_fail.pop() if should_fail else False:
            raise OSError("release failed after commit")

    monkeypatch.setattr(Lock, "release", release)
    with pytest.raises(BaseError) as caught:
        await JsonStore(tmp_path).set("demo", "key", 1)
    assert caught.value.status is StatusCode.EXTENSION_DATA_COMMIT_UNKNOWN
    assert await JsonStore(tmp_path).get("demo", "key") == 1


@pytest.mark.asyncio
async def test_failed_replace_with_unreadable_result_is_unknown(tmp_path: Path, monkeypatch) -> None:
    def fail_replace(source, target):
        raise OSError("replace failed")

    def fail_read(path):
        raise PermissionError("verification refused")

    service = JsonStore(tmp_path)
    assert await service.set("demo", "key", 1)
    monkeypatch.setattr(files.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(BaseError) as caught:
        await service.set("demo", "key", 2)
    assert caught.value.status is StatusCode.EXTENSION_DATA_COMMIT_UNKNOWN


@pytest.mark.asyncio
async def test_case_aliases_cannot_cross_package_files_on_windows(tmp_path: Path) -> None:
    service = JsonStore(tmp_path)
    assert await service.set("Demo", "key", 1)
    if os.name == "nt":
        with pytest.raises(BaseError) as caught:
            await service.get("demo", "key")
        assert caught.value.status is StatusCode.EXTENSION_DATA_PATH_INVALID
    else:
        assert await service.get("demo", "key") is None


@pytest.mark.asyncio
async def test_cancelled_writer_waits_for_worker_and_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    replace = os.replace

    def delayed_replace(source, target):
        entered.set()
        assert release.wait(5)
        replace(source, target)

    monkeypatch.setattr(files.os, "replace", delayed_replace)
    store = JsonStore(tmp_path)
    writer = asyncio.create_task(store.set("demo", "first", 1))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        writer.cancel()
        writer.cancel()
        other = asyncio.create_task(store.set("demo", "second", 2))
        await asyncio.sleep(0.05)
        assert not writer.done()
        assert not other.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await writer
    assert await other
    assert set(await store.keys("demo")) == {"first", "second"}


@pytest.mark.asyncio
async def test_cleanup_preserves_reload_shutdown_and_removes_only_package_data(tmp_path: Path) -> None:
    store = JsonStore(tmp_path)
    assert await store.set("demo", "key", 1)
    assert await store.set("other", "key", 2)
    for reason in ("reload", "shutdown"):
        await store.cleanup_package("demo", reason=reason)
        assert await store.get("demo", "key") == 1
    await store.cleanup_package("demo")
    await store.cleanup_package("demo")
    assert not (tmp_path / "demo.json").exists()
    assert (tmp_path / "demo.json.lock").exists()
    assert await store.get("other", "key") == 2


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows extended-length paths only")
async def test_extended_path_prefix_does_not_change_the_package_directory(tmp_path: Path, monkeypatch) -> None:
    original = Path.resolve

    def extended(path: Path, strict: bool = False) -> Path:
        resolved = original(path, strict=strict)
        return Path("\\\\?\\" + str(resolved))

    monkeypatch.setattr(Path, "resolve", extended)
    store = JsonStore(tmp_path)
    assert store.directory == tmp_path
    assert await store.set("demo", "key", 1)
    assert await store.get("demo", "key") == 1
    assert (tmp_path / "demo.json.lock").is_file()


@pytest.mark.asyncio
async def test_lock_acquisition_failure_never_reports_a_successful_write(tmp_path: Path, monkeypatch) -> None:
    import openjiuwen.core.extension.data.store as module

    class UnavailableLock:
        def __enter__(self):
            raise LockException("lock unavailable")

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    monkeypatch.setattr(module, "package_file_lock", lambda path: UnavailableLock())
    assert not await JsonStore(tmp_path).set("demo", "key", 1)
    assert not (tmp_path / "demo.json").exists()
