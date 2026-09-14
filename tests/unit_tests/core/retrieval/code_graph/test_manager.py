# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Concurrent single-flight invariants for CodeGraphManager / CodeGraphService."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.identity import RepoIdentity
from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager, reset_code_graph_manager
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig
from openjiuwen.core.retrieval.code_graph.service import CodeGraphService
from tests.unit_tests.core.retrieval.code_graph.parser_guard import skip_unless_code_graph_parser

pytestmark = pytest.mark.level0
skip_unless_code_graph_parser()

SAMPLE = '''\
class UserService:
    def create_user(self, name: str) -> str:
        return name
'''


def _write_repo(root: Path) -> Path:
    src = root / "src"
    src.mkdir(parents=True)
    (src / "user.py").write_text(SAMPLE, encoding="utf-8")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _write_repo(tmp_path / "repo")


@pytest.fixture(autouse=True)
def _reset_manager() -> None:
    reset_code_graph_manager()
    yield
    reset_code_graph_manager()


def _count_builds(monkeypatch: pytest.MonkeyPatch, delay: float = 0.05):
    builds = {"n": 0}
    real = build_index

    def counting(repo_root, config, **kwargs):
        builds["n"] += 1
        if delay:
            time.sleep(delay)
        return real(repo_root, config, **kwargs)

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.service.build_index",
        counting,
    )
    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.manager.build_index",
        counting,
    )
    return builds


@pytest.mark.asyncio
async def test_twenty_concurrent_search_code_builds_once(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = _count_builds(monkeypatch)
    service = CodeGraphService(repo, CodeGraphConfig(cache_dir=None, max_files=100))
    results = await asyncio.gather(*[service.search_code("UserService") for _ in range(20)])
    assert builds["n"] == 1
    assert all(item["status"] == "COMPLETE" for item in results)


@pytest.mark.asyncio
async def test_manager_ensure_ready_single_flight(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = _count_builds(monkeypatch)
    manager = CodeGraphManager(max_cached_repos=4)
    config = CodeGraphConfig(cache_dir=None, max_files=100)
    services = await asyncio.gather(
        *[manager.get_service(repo, config, ensure=True) for _ in range(20)]
    )
    assert builds["n"] == 1
    assert len({id(item) for item in services}) == 1


@pytest.mark.asyncio
async def test_first_build_is_full_and_records_seconds(repo: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=None, max_files=100)
    generation = await manager.ensure_fresh(repo, cfg)
    assert generation.reason == "full"
    entry = manager._peek_entry(repo, cfg)
    assert entry is not None
    assert entry.last_full_build_seconds is not None
    assert entry.last_full_build_seconds > 0


@pytest.mark.asyncio
async def test_product_default_sees_shell_edit_without_mark_dirty(
    repo: Path,
) -> None:
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=None, max_files=100)
    first = await manager.ensure_fresh(repo, cfg)
    (repo / "src" / "user.py").write_text(SAMPLE + "\n# dirty\n", encoding="utf-8")
    second = await manager.ensure_fresh(repo, cfg)
    assert cfg.freshness_check_interval_ms == 0
    assert second.generation_id != first.generation_id


@pytest.mark.asyncio
async def test_stats_marks_shell_edit_stale_without_mark_dirty(repo: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=None, max_files=100)
    await manager.ensure_fresh(repo, cfg)
    assert manager.stats(repo)["state"] == "ready"
    (repo / "src" / "user.py").write_text(SAMPLE + "\n# dirty\n", encoding="utf-8")
    stats = manager.stats(repo)
    assert stats["state"] == "stale"
    assert stats.get("dirty_unknown") is not True


@pytest.mark.asyncio
async def test_small_repo_dirty_rebuilds_full(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = _count_builds(monkeypatch, delay=0)
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=None, max_files=100, freshness_check_interval_ms=0)
    first = await manager.ensure_fresh(repo, cfg)
    assert first.reason == "full"
    assert builds["n"] == 1
    (repo / "src" / "user.py").write_text(SAMPLE + "\n# dirty\n", encoding="utf-8")
    manager.mark_dirty(repo, ["src/user.py"], config=cfg)
    second = await manager.ensure_fresh(repo, cfg)
    assert second.reason == "full"
    assert builds["n"] == 2


@pytest.mark.asyncio
async def test_medium_repo_one_file_stays_incremental(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = _count_builds(monkeypatch, delay=0)
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=None, max_files=100, freshness_check_interval_ms=0)
    first = await manager.ensure_fresh(repo, cfg)
    entry = manager._peek_entry(repo, cfg)
    assert entry is not None
    entry.last_full_build_seconds = 5.0
    (repo / "src" / "user.py").write_text(SAMPLE + "\n# dirty\n", encoding="utf-8")
    manager.mark_dirty(repo, ["src/user.py"], config=cfg)
    second = await manager.ensure_fresh(repo, cfg)
    assert first.reason == "full"
    assert second.reason == "incremental"
    assert builds["n"] == 1


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_build(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = build_index
    entered = threading.Event()
    gate = threading.Event()
    builds = {"n": 0}

    def blocked(repo_root, config, **kwargs):
        builds["n"] += 1
        entered.set()
        assert gate.wait(timeout=5)
        return real(repo_root, config, **kwargs)

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.service.build_index",
        blocked,
    )
    service = CodeGraphService(repo, CodeGraphConfig(cache_dir=None, max_files=100))
    waiter = asyncio.create_task(service.search_code("UserService"))
    await asyncio.to_thread(entered.wait, 5)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    gate.set()
    result = await service.search_code("UserService")
    assert result["status"] == "COMPLETE"
    assert builds["n"] == 1


@pytest.mark.asyncio
async def test_failed_flight_cleans_up_and_retries(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = build_index
    attempts = {"n": 0}

    def flaky(repo_root, config, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("index boom")
        return real(repo_root, config, **kwargs)

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.service.build_index",
        flaky,
    )
    service = CodeGraphService(repo, CodeGraphConfig(cache_dir=None, max_files=100))
    with pytest.raises(Exception, match="index boom"):
        await service.ensure_ready()
    index = await service.ensure_ready()
    assert index is not None
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_different_repos_build_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = build_index
    overlap = {"current": 0, "max": 0}
    lock = threading.Lock()

    def overlapping(repo_root, config, **kwargs):
        with lock:
            overlap["current"] += 1
            overlap["max"] = max(overlap["max"], overlap["current"])
        time.sleep(0.08)
        try:
            return real(repo_root, config, **kwargs)
        finally:
            with lock:
                overlap["current"] -= 1

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.service.build_index",
        overlapping,
    )
    repo_a = _write_repo(tmp_path / "a")
    repo_b = _write_repo(tmp_path / "b")
    cfg = CodeGraphConfig(cache_dir=None, max_files=100)
    await asyncio.gather(
        CodeGraphService(repo_a, cfg).ensure_ready(),
        CodeGraphService(repo_b, cfg).ensure_ready(),
    )
    assert overlap["max"] >= 2


@pytest.mark.asyncio
async def test_changed_snapshot_does_not_reuse_old_index(repo: Path) -> None:
    service = CodeGraphService(repo, CodeGraphConfig(cache_dir=None, max_files=100))
    first = await service.ensure_ready()
    (repo / "src" / "user.py").write_text(SAMPLE + "\n# changed\n", encoding="utf-8")
    second = await service.ensure_ready()
    assert first.snapshot != second.snapshot


@pytest.mark.asyncio
async def test_lru_skips_in_flight_and_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = build_index
    entered = threading.Event()
    gate = threading.Event()

    def blocked(repo_root, config, **kwargs):
        entered.set()
        assert gate.wait(timeout=5)
        return real(repo_root, config, **kwargs)

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.service.build_index",
        blocked,
    )
    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.manager.build_index",
        blocked,
    )
    manager = CodeGraphManager(max_cached_repos=1)
    cfg = CodeGraphConfig(cache_dir=None, max_files=100, query_wait_seconds=30)
    repo_a = _write_repo(tmp_path / "a")
    repo_b = _write_repo(tmp_path / "b")
    key_a = RepoIdentity.from_path(repo_a).entry_key(cfg.config_hash())
    task_a = asyncio.create_task(manager.get_service(repo_a, cfg, ensure=True))
    await asyncio.to_thread(entered.wait, 5)
    pinned = key_a in manager._pins
    cached = key_a in manager._entries
    assert pinned and cached
    task_b = asyncio.create_task(manager.get_service(repo_b, cfg, ensure=True))
    await asyncio.sleep(0.01)
    still_cached = key_a in manager._entries
    assert still_cached
    gate.set()
    service_a, service_b = await asyncio.gather(task_a, task_b)
    assert service_a is not service_b


@pytest.mark.asyncio
async def test_idle_ttl_evicts_under_quota(tmp_path: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=3, memory_idle_ttl_seconds=0.05)
    cfg = CodeGraphConfig(cache_dir=None, max_files=100, memory_idle_ttl_seconds=0.05)
    repo_a = _write_repo(tmp_path / "a")
    repo_b = _write_repo(tmp_path / "b")
    await manager.get_service(repo_a, cfg, ensure=True)
    await manager.get_service(repo_b, cfg, ensure=True)
    key_a = RepoIdentity.from_path(repo_a).entry_key(cfg.config_hash())
    manager._entries[key_a].last_access_at = time.time() - 5
    manager.reclaim()
    assert key_a not in manager._entries
    key_b = RepoIdentity.from_path(repo_b).entry_key(cfg.config_hash())
    assert key_b in manager._entries


@pytest.mark.asyncio
async def test_high_rss_skips_a_new_build(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = _count_builds(monkeypatch, delay=0)
    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.budgets.process_rss_bytes",
        lambda: 8 * 1024 * 1024 * 1024,
    )
    manager = CodeGraphManager(max_cached_repos=4)
    cfg = CodeGraphConfig(cache_dir=None, max_files=100, max_build_rss_mb=1)
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded

    with pytest.raises(CodeGraphLimitExceeded) as caught:
        await manager.ensure_fresh(repo, cfg)
    assert caught.value.limit == "max_build_rss_mb"
    assert builds["n"] == 0


@pytest.mark.asyncio
async def test_rss_evicts_oldest_unused_other_then_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = CodeGraphManager(max_cached_repos=4, memory_idle_ttl_seconds=3600)
    roomy = CodeGraphConfig(cache_dir=None, max_files=100, max_build_rss_mb=4096)
    oldest = _write_repo(tmp_path / "oldest")
    middle = _write_repo(tmp_path / "middle")
    newer = _write_repo(tmp_path / "newer")
    await manager.ensure_fresh(oldest, roomy)
    await manager.ensure_fresh(middle, roomy)
    await manager.ensure_fresh(newer, roomy)
    oldest_key = RepoIdentity.from_path(oldest).repo_id
    middle_key = RepoIdentity.from_path(middle).repo_id
    manager._entries[oldest_key].active.created_at = 1.0
    manager._entries[middle_key].active.created_at = 2.0

    def rss() -> int:
        oldest_entry = manager._entries.get(oldest_key)
        if oldest_entry is not None and oldest_entry.active is not None:
            return 8 * 1024 * 1024 * 1024
        return 256 * 1024

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.budgets.process_rss_bytes",
        rss,
    )
    tight = CodeGraphConfig(cache_dir=None, max_files=100, max_build_rss_mb=1)
    (newer / "src" / "user.py").write_text(SAMPLE + "\n# dirty\n", encoding="utf-8")
    manager.mark_dirty(newer, ["src/user.py"], config=tight)
    generation = await manager.ensure_fresh(newer, tight)
    assert generation.index is not None
    assert oldest_key not in manager._entries
    assert middle_key in manager._entries
    assert manager._entries[middle_key].active is not None


@pytest.mark.asyncio
async def test_rss_keeps_other_repo_while_a_window_is_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = CodeGraphManager(max_cached_repos=4, memory_idle_ttl_seconds=30)
    roomy = CodeGraphConfig(cache_dir=None, max_files=100, max_build_rss_mb=4096)
    older = _write_repo(tmp_path / "older")
    newer = _write_repo(tmp_path / "newer")
    await manager.ensure_fresh(older, roomy)
    await manager.ensure_fresh(newer, roomy)
    older_key = RepoIdentity.from_path(older).repo_id
    newer_key = RepoIdentity.from_path(newer).repo_id
    manager._entries[older_key].active.created_at = 1.0
    manager._entries[older_key].last_access_at = time.time() - 120
    manager._entries[older_key].active.reader_count = 1
    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.budgets.process_rss_bytes",
        lambda: 8 * 1024 * 1024 * 1024,
    )
    tight = CodeGraphConfig(cache_dir=None, max_files=100, max_build_rss_mb=1)
    (newer / "src" / "user.py").write_text(SAMPLE + "\n# dirty\n", encoding="utf-8")
    manager.mark_dirty(newer, ["src/user.py"], config=tight)
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded

    with pytest.raises(CodeGraphLimitExceeded):
        await manager.ensure_fresh(newer, tight)
    assert older_key in manager._entries
    assert manager._entries[older_key].active is not None
    assert manager._entries[newer_key].active is None


@pytest.mark.asyncio
async def test_rss_still_high_after_cleanup_clears_current_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = CodeGraphManager(max_cached_repos=2)
    repo = _write_repo(tmp_path / "repo")
    roomy = CodeGraphConfig(cache_dir=None, max_files=100, max_build_rss_mb=4096)
    first = await manager.ensure_fresh(repo, roomy)
    assert first.index is not None
    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.budgets.process_rss_bytes",
        lambda: 8 * 1024 * 1024 * 1024,
    )
    tight = CodeGraphConfig(cache_dir=None, max_files=100, max_build_rss_mb=1)
    (repo / "src" / "user.py").write_text(SAMPLE + "\n# dirty\n", encoding="utf-8")
    manager.mark_dirty(repo, ["src/user.py"], config=tight)
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded

    with pytest.raises(CodeGraphLimitExceeded) as caught:
        await manager.ensure_fresh(repo, tight)
    assert caught.value.limit == "max_build_rss_mb"
    entry = manager._peek_entry(repo, tight)
    assert entry is not None
    assert entry.active is None
    assert entry.limit_error is caught.value


@pytest.mark.asyncio
async def test_unraised_rss_cap_stays_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = _count_builds(monkeypatch, delay=0)
    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.budgets.process_rss_bytes",
        lambda: 8 * 1024 * 1024 * 1024,
    )
    manager = CodeGraphManager(max_cached_repos=2)
    repo = _write_repo(tmp_path / "repo")
    cfg = CodeGraphConfig(cache_dir=None, max_files=100, max_build_rss_mb=1)
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded

    with pytest.raises(CodeGraphLimitExceeded):
        await manager.ensure_fresh(repo, cfg)
    assert builds["n"] == 0
    with pytest.raises(CodeGraphLimitExceeded) as caught:
        await manager.ensure_fresh(repo, cfg)
    assert caught.value.limit == "max_build_rss_mb"
    assert builds["n"] == 0


@pytest.mark.asyncio
async def test_raised_rss_cap_retries_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = _count_builds(monkeypatch, delay=0)
    rss_mb = {"value": 8 * 1024}
    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.budgets.process_rss_bytes",
        lambda: rss_mb["value"] * 1024 * 1024,
    )
    manager = CodeGraphManager(max_cached_repos=2)
    repo = _write_repo(tmp_path / "repo")
    low = CodeGraphConfig(cache_dir=None, max_files=100, max_build_rss_mb=1)
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded

    with pytest.raises(CodeGraphLimitExceeded):
        await manager.ensure_fresh(repo, low)
    assert builds["n"] == 0
    rss_mb["value"] = 10
    high = CodeGraphConfig(cache_dir=None, max_files=100, max_build_rss_mb=64)
    generation = await manager.ensure_fresh(repo, high)
    assert generation.index is not None
    assert builds["n"] == 1


@pytest.mark.asyncio
async def test_disk_quota_keeps_other_repo_while_a_window_is_reading(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    manager = CodeGraphManager(max_cached_repos=4, memory_idle_ttl_seconds=3600)
    older = _write_repo(tmp_path / "older")
    newer = _write_repo(tmp_path / "newer")
    roomy = CodeGraphConfig(cache_dir=str(cache), max_files=100, max_cache_size_mb=64)
    await manager.ensure_fresh(older, roomy)
    older_id = RepoIdentity.from_path(older).repo_id
    from openjiuwen.core.retrieval.code_graph.store.index_store import DiskIndexStore

    older_dir = cache / DiskIndexStore.safe_part(older_id)
    older_dir.mkdir(parents=True, exist_ok=True)
    (older_dir / "filler.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    manager._entries[older_id].active.reader_count = 1
    tight = CodeGraphConfig(cache_dir=str(cache), max_files=100, max_cache_size_mb=1)
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded

    with pytest.raises(CodeGraphLimitExceeded) as caught:
        await manager.ensure_fresh(newer, tight)
    assert caught.value.limit == "max_cache_size_mb"
    assert older_dir.exists()
    assert manager._peek_entry(older, roomy).active is not None


@pytest.mark.asyncio
async def test_disk_quota_deletes_unused_older_repo_then_builds(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    manager = CodeGraphManager(max_cached_repos=4, memory_idle_ttl_seconds=3600)
    older = _write_repo(tmp_path / "older")
    newer = _write_repo(tmp_path / "newer")
    roomy = CodeGraphConfig(cache_dir=str(cache), max_files=100, max_cache_size_mb=64)
    await manager.ensure_fresh(older, roomy)
    older_key = RepoIdentity.from_path(older).repo_id
    from openjiuwen.core.retrieval.code_graph.store.index_store import DiskIndexStore

    older_dir = cache / DiskIndexStore.safe_part(older_key)
    older_dir.mkdir(parents=True, exist_ok=True)
    (older_dir / "filler.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    manager._entries[older_key].active.created_at = 1.0
    tight = CodeGraphConfig(cache_dir=str(cache), max_files=100, max_cache_size_mb=1)
    generation = await manager.ensure_fresh(newer, tight)
    assert generation.index is not None
    assert older_key not in manager._entries
    assert not older_dir.exists()


@pytest.mark.asyncio
async def test_refresh_timeout_returns_building_not_old_graph(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = CodeGraphManager(max_cached_repos=4)
    cfg = CodeGraphConfig(
        cache_dir=None,
        max_files=100,
        query_wait_seconds=0.2,
        first_build_wait_seconds=5,
    )
    first = await manager.ensure_fresh(repo, cfg)
    entry = manager._peek_entry(repo, cfg)
    assert entry is not None
    entry.last_full_build_seconds = 5.0
    entered = threading.Event()

    def hanging(index, paths, config=None, **kwargs):
        del index, paths, config, kwargs
        entered.set()
        time.sleep(2)
        from openjiuwen.core.retrieval.code_graph.indexing.refresh import RefreshResult

        return RefreshResult()

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.manager.refresh_index_files",
        hanging,
    )
    (repo / "src" / "user.py").write_text(SAMPLE + "\n# x\n", encoding="utf-8")
    manager.mark_dirty(repo, ["src/user.py"], config=cfg)
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphBusy, CodeGraphStatus

    with pytest.raises(CodeGraphBusy) as caught:
        await manager.ensure_fresh(repo, cfg)
    assert caught.value.status == CodeGraphStatus.BUILDING
    assert caught.value.index is None
    assert await asyncio.to_thread(entered.wait, 2)
    entry = manager._peek_entry(repo, cfg)
    assert entry is not None
    assert entry.active is None


@pytest.mark.asyncio
async def test_refresh_over_cap_clears_the_old_graph(
    tmp_path: Path,
) -> None:
    manager = CodeGraphManager(max_cached_repos=2)
    repo = _write_repo(tmp_path / "repo")
    cfg = CodeGraphConfig(cache_dir=None, max_files=2, max_source_bytes=10_000_000)
    await manager.ensure_fresh(repo, cfg)
    for index in range(8):
        (repo / "src" / f"extra{index}.py").write_text(
            f"def extra_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    manager.mark_dirty_unknown(repo, "growth", config=cfg)
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded

    with pytest.raises(CodeGraphLimitExceeded) as caught:
        await manager.ensure_fresh(repo, cfg)
    assert caught.value.limit == "max_files"
    entry = manager._peek_entry(repo, cfg)
    assert entry is not None
    assert entry.active is None


def test_refresh_stops_before_relations_when_cancelled(repo: Path) -> None:
    from openjiuwen.core.retrieval.code_graph.indexing.refresh import refresh_index_files

    cfg = CodeGraphConfig(max_files=200)
    built = build_index(repo, cfg)
    cancel = threading.Event()
    cancel.set()
    (repo / "src" / "user.py").write_text(SAMPLE + "\n# y\n", encoding="utf-8")
    result = refresh_index_files(built, ["src/user.py"], cfg, cancel=cancel)
    assert result.cancelled is True


def test_stats_unindexed_repo_is_absent(tmp_path: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=2)
    stats = manager.stats(tmp_path / "never")
    assert stats["present"] is False
    assert stats["state"] == "absent"


@pytest.mark.asyncio
async def test_stats_finds_entry_when_config_hash_differs(tmp_path: Path) -> None:
    manager = CodeGraphManager(max_cached_repos=2)
    repo = _write_repo(tmp_path / "repo")
    cfg = CodeGraphConfig(cache_dir=None, max_files=7)
    await manager.ensure_fresh(repo, cfg)
    stats = manager.stats(repo)
    assert stats["present"] is True
    assert stats["state"] == "ready"


@pytest.mark.asyncio
async def test_mark_dirty_unknown_during_first_build_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    gate = threading.Event()
    real = build_index

    def blocked(repo_root, config, **kwargs):
        entered.set()
        assert gate.wait(timeout=5)
        return real(repo_root, config, **kwargs)

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.manager.build_index",
        blocked,
    )
    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.service.build_index",
        blocked,
    )
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=None, max_files=50, first_build_wait_seconds=30)
    repo = _write_repo(tmp_path / "repo")
    task = asyncio.create_task(manager.ensure_fresh(repo, cfg))
    await asyncio.to_thread(entered.wait, 5)
    entry = manager._peek_entry(repo, cfg)
    assert entry is not None
    assert entry.active is None
    manager.mark_dirty_unknown(repo, "bash", config=cfg)
    assert entry.dirty_unknown is False
    stats = manager.stats(repo)
    assert stats.get("state") == "building"
    assert stats.get("dirty_unknown") is not True
    gate.set()
    await task
    stats = manager.stats(repo)
    assert stats.get("dirty_unknown") is not True
