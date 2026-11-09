# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Capacity and read accounting contracts against temporary published trees."""

import asyncio
import json
import os
import shutil
import threading

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context import context_graph
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.context_capacity import maintain_capacity
from openjiuwen.harness.personal_context.context_usage import (
    _load_usage, context_update_lock, publish_with_usage, read_with_usage, usage_lock,
)


def _page(home, relative, text, stamp=100):
    root = home / "workspace" / "context"
    root.mkdir(parents=True, exist_ok=True)
    (root / "description.md").write_text("# Root\n", encoding="utf-8")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in path.parents:
        if parent == root:
            break
        (parent / "description.md").write_text(f"# {parent.name}\n", encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    os.utime(path, (stamp, stamp))
    return path


@pytest.mark.parametrize("value", [9, 10001, 50.5, "50", True, None])
def test_capacity_rejects_invalid_config(value):
    with pytest.raises(BaseError):
        PersonalContextConfig.from_dict({"strategy_profile": "rules", "fetch_services": [], "max_context_size_mb": value})


@pytest.mark.parametrize("value", [10, 50, 10000])
def test_capacity_accepts_closed_integer_range(value):
    config = PersonalContextConfig.from_dict({
        "strategy_profile": "rules", "fetch_services": [], "max_context_size_mb": value,
    })
    assert config.max_context_size_mb == value
    assert PersonalContextConfig.from_dict({"strategy_profile": "rules", "fetch_services": []}).max_context_size_mb == 50


def test_old_mtime_read_and_navigation_semantics(tmp_path):
    _page(tmp_path, "topic/old.md", "# Old\nvalue", 100)
    _page(tmp_path, "topic/new.md", "# New\nvalue", 200)
    maintain_capacity(tmp_path, limit_bytes=100_000)
    records = _load_usage(tmp_path)["pages"]
    assert records == {}  # Legacy pages are initialized only when needed.
    context_graph.search_context_graph(tmp_path, "Old")
    context_graph.read_context_graph_page(tmp_path, "page:topic/old.md")
    read_with_usage(tmp_path, "topic/description.md", allowed=lambda: True)
    assert _load_usage(tmp_path)["pages"] == records
    assert read_with_usage(tmp_path, "topic/old.md", allowed=lambda: True)["markdown"].splitlines() == ["# Old", "value"]
    record = _load_usage(tmp_path)["pages"]["topic/old.md"]
    assert record["created_at"] == record["updated_at"] == 100
    assert record["last_read_at"] > 200


@pytest.mark.parametrize("failure", ["corrupt", "write", "fingerprint"])
def test_accounting_failure_preserves_read_and_next_read_records_usage(tmp_path, monkeypatch, failure):
    from openjiuwen.harness.personal_context import context_usage

    page = _page(tmp_path, "topic/page.md", "# Page\n" + "x" * 20000)
    expected = read_with_usage(tmp_path, "topic/page.md", allowed=lambda: True)
    ledger = tmp_path / "state/context_usage.json"
    valid_ledger = ledger.read_bytes()
    last_read = _load_usage(tmp_path)["pages"]["topic/page.md"]["last_read_at"]

    def fail(*_args, **_kwargs):
        raise OSError("simulated accounting failure")

    with monkeypatch.context() as patcher:
        if failure == "corrupt":
            ledger.write_text("{", encoding="utf-8")
        else:
            patcher.setattr(context_usage, "_save_usage" if failure == "write" else "_fingerprint", fail)
        assert read_with_usage(tmp_path, "topic/page.md", allowed=lambda: True) == expected
    assert not (tmp_path / "context_usage.suspended").exists()
    if failure == "corrupt":
        ledger.write_bytes(valid_ledger)
    monkeypatch.setattr(context_usage.time, "time", lambda: last_read + 10)
    assert read_with_usage(tmp_path, "topic/page.md", allowed=lambda: True) == expected
    assert _load_usage(tmp_path)["pages"]["topic/page.md"]["last_read_at"] == last_read + 10
    assert page.exists()
    assert read_with_usage(tmp_path, "description.md", allowed=lambda: True)["markdown"] == (
        tmp_path / "workspace/context/description.md"
    ).read_bytes().decode("utf-8")
    assert maintain_capacity(tmp_path, limit_bytes=1000)["removed_count"] == 1
    assert not page.exists()


def test_old_suspension_marker_does_not_affect_read_publication_or_eviction(tmp_path):
    page = _page(tmp_path, "topic/page.md", "# Page\n" + "x" * 20000)
    marker = tmp_path / "context_usage.suspended"
    marker.write_text("legacy suspension", encoding="utf-8")
    assert read_with_usage(tmp_path, "topic/page.md", allowed=lambda: True)["markdown"].startswith("# Page")
    assert _load_usage(tmp_path)["pages"]["topic/page.md"]["last_read_at"] is not None
    candidate = tmp_path / "candidate"
    shutil.copytree(tmp_path / "workspace/context", candidate)
    (candidate / "topic/page.md").write_text("# Updated\n" + "x" * 20000, encoding="utf-8")
    publish_with_usage(tmp_path, candidate, {})
    assert page.read_text(encoding="utf-8").startswith("# Updated")
    assert maintain_capacity(tmp_path, limit_bytes=1000)["removed_count"] == 1
    assert not page.exists()
    assert marker.read_text(encoding="utf-8") == "legacy suspension"


@pytest.mark.parametrize("failure", ["corrupt", "write", "fingerprint"])
def test_accounting_failure_does_not_turn_publication_into_failure(tmp_path, monkeypatch, failure):
    from openjiuwen.harness.personal_context import context_usage

    root = tmp_path / "workspace/context"
    page = _page(tmp_path, "topic/page.md", "# Old")
    read_with_usage(tmp_path, "topic/page.md", allowed=lambda: True)
    ledger = tmp_path / "state/context_usage.json"
    valid_ledger = ledger.read_bytes()
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    (candidate / "topic/page.md").write_text("# Updated", encoding="utf-8")

    def fail(*_args, **_kwargs):
        raise OSError("simulated accounting failure")

    with monkeypatch.context() as patcher:
        if failure == "corrupt":
            ledger.write_text("{", encoding="utf-8")
        else:
            patcher.setattr(context_usage, "_save_usage" if failure == "write" else "_pages", fail)
        publish_with_usage(tmp_path, candidate, {})
    assert page.read_text(encoding="utf-8") == "# Updated"
    assert not (tmp_path / "context_usage.suspended").exists()
    if failure == "corrupt":
        ledger.write_bytes(valid_ledger)
    (candidate / "topic/page.md").write_text("# Updated again", encoding="utf-8")
    publish_with_usage(tmp_path, candidate, {})
    assert page.read_text(encoding="utf-8") == "# Updated again"
    assert _load_usage(tmp_path)["pages"]["topic/page.md"]["fingerprint"] == context_usage._fingerprint(
        root, "topic/page.md", {},
    )


def test_actual_publication_failure_is_not_swallowed(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import context_pipeline

    root = tmp_path / "workspace/context"
    _page(tmp_path, "topic/page.md", "# Old")
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)

    def fail(*_args, **_kwargs):
        raise OSError("actual publication failed")

    monkeypatch.setattr(context_pipeline, "_commit_context_tree", fail)
    with pytest.raises(OSError, match="actual publication failed"):
        publish_with_usage(tmp_path, candidate, {})
    assert not (tmp_path / "context_usage.suspended").exists()


def test_valid_ledger_allows_eviction_despite_persistent_usage_save_failure(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import context_capacity, context_pipeline, context_usage

    cold = _page(tmp_path, "cold.md", "# Cold\n" + "x" * 7000, 200)
    recent = _page(tmp_path, "recent.md", "# Recent\n" + "y" * 2000, 100)
    expected = read_with_usage(tmp_path, "recent.md", allowed=lambda: True)
    ledger = tmp_path / "state/context_usage.json"
    valid_ledger = ledger.read_bytes()
    saved_after_deletion = []

    def fail(*_args, **_kwargs):
        raise OSError("usage state write failed")

    def fail_after_deletion(*args, **kwargs):
        saved_after_deletion.append(not cold.exists())
        fail(*args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(context_usage, "_save_usage", fail)
        patcher.setattr(context_capacity, "_save_usage", fail_after_deletion)
        assert read_with_usage(tmp_path, "recent.md", allowed=lambda: True) == expected
        result = maintain_capacity(tmp_path, limit_bytes=9000)
    assert result["status"] == "succeeded" and result["removed_count"] == 1
    assert result["after_bytes"] <= 5400 and recent.exists() and not cold.exists()
    assert saved_after_deletion == [True]
    assert ledger.read_bytes() == valid_ledger
    context_pipeline._validate_description_coverage(tmp_path / "workspace/context")
    assert not (tmp_path / "context_usage.suspended").exists()
    assert read_with_usage(tmp_path, "recent.md", allowed=lambda: True) == expected
    assert maintain_capacity(tmp_path, limit_bytes=1000)["removed_count"] == 1
    assert not recent.exists()


def test_under_limit_checks_neither_hash_bodies_nor_acquire_usage_lock(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import context_capacity

    page = _page(tmp_path, "topic/page.md", "# Page")

    def unexpected(*_args, **_kwargs):
        pytest.fail("under-limit maintenance must not lock or scan body contents")

    for name in ("_pages", "_load_usage", "_save_usage", "usage_lock"):
        monkeypatch.setattr(context_capacity, name, unexpected)
    result = maintain_capacity(tmp_path, limit_bytes=100_000)
    assert result["removed_count"] == 0 and page.exists()
    assert not (tmp_path / "state/context_usage.json").exists()


@pytest.mark.asyncio
async def test_publication_scans_do_not_block_or_overwrite_concurrent_read(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import context_usage

    root = tmp_path / "workspace/context"
    _page(tmp_path, "topic/page.md", "# Page")
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    original_pages = context_usage._pages
    scanning = threading.Event()
    proceed = threading.Event()
    calls = []

    def scan(path, relocations=None):
        value = original_pages(path, relocations)
        calls.append(path)
        if path == candidate:
            scanning.set()
            assert proceed.wait(5)
        return value

    monkeypatch.setattr(context_usage, "_pages", scan)
    worker = asyncio.create_task(asyncio.to_thread(publish_with_usage, tmp_path, candidate, {}))
    try:
        assert await asyncio.to_thread(scanning.wait, 5)
        result = await asyncio.wait_for(
            asyncio.to_thread(read_with_usage, tmp_path, "topic/page.md", allowed=lambda: True), timeout=2,
        )
        assert result["markdown"] == "# Page"
        read_record = _load_usage(tmp_path)["pages"]["topic/page.md"]
    finally:
        proceed.set()
        await worker
    assert calls == [root, candidate]
    assert _load_usage(tmp_path)["pages"]["topic/page.md"] == read_record


def test_post_eviction_fingerprint_failure_preserves_success_and_next_reclamation(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import context_capacity

    _page(tmp_path, "old.md", "# Old\n" + "x" * 7000, 100)
    _page(tmp_path, "new.md", "# New\n" + "y" * 2000, 200)
    original_pages = context_capacity._pages
    calls = []

    def scan(path):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("post-eviction fingerprint failed")
        return original_pages(path)

    monkeypatch.setattr(context_capacity, "_pages", scan)
    result = maintain_capacity(tmp_path, limit_bytes=9000)
    assert result["status"] == "succeeded" and result["removed_count"] == 1
    assert result["after_bytes"] <= 5400
    assert not (tmp_path / "workspace/context/old.md").exists()
    assert (tmp_path / "workspace/context/new.md").exists()
    assert not (tmp_path / "context_usage.suspended").exists()
    assert maintain_capacity(tmp_path, limit_bytes=1000)["removed_count"] == 1
    assert not (tmp_path / "workspace/context/new.md").exists()


def test_capacity_publication_failure_is_not_swallowed(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import context_capacity

    page = _page(tmp_path, "page.md", "# Page\n" + "x" * 20000)

    def fail(*_args, **_kwargs):
        raise OSError("actual capacity publication failed")

    with monkeypatch.context() as patcher:
        patcher.setattr(context_capacity, "_commit_candidate", fail)
        with pytest.raises(OSError, match="actual capacity publication failed"):
            maintain_capacity(tmp_path, limit_bytes=1000)
    assert page.exists()
    assert not (tmp_path / "context_usage.suspended").exists()
    assert maintain_capacity(tmp_path, limit_bytes=1000)["removed_count"] == 1


def test_capacity_candidate_path_safety_rejection_is_not_swallowed(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import context_capacity, context_pipeline

    page = _page(tmp_path, "page.md", "# Page\n" + "x" * 20000)
    original_pages = context_capacity._pages
    calls = []

    def scan(path):
        calls.append(path)
        if len(calls) == 2:
            raise context_pipeline._publish_error("managed path must not traverse symlinks")
        return original_pages(path)

    monkeypatch.setattr(context_capacity, "_pages", scan)
    with pytest.raises(BaseError, match="managed path must not traverse symlinks"):
        maintain_capacity(tmp_path, limit_bytes=1000)
    assert page.exists()
    assert not (tmp_path / "context_usage.suspended").exists()


@pytest.mark.parametrize("path", ["../secret.md", "/secret.md", "C:/secret.md", "topic/x.md:secret", "topic\\old.md", "topic/missing.md"])
def test_failed_or_unsafe_read_does_not_create_ledger(tmp_path, path):
    _page(tmp_path, "topic/old.md", "# Old")
    with pytest.raises(BaseError):
        read_with_usage(tmp_path, path, allowed=lambda: True)
    assert not (tmp_path / "state/context_usage.json").exists()


def test_disable_during_read_does_not_renew(tmp_path, monkeypatch):
    _page(tmp_path, "topic/old.md", "# Old")
    enabled = [True]
    original = context_graph.read_context_graph_page

    def read(*args):
        result = original(*args)
        enabled[0] = False
        return result

    monkeypatch.setattr(context_graph, "read_context_graph_page", read)
    with pytest.raises(BaseError):
        read_with_usage(tmp_path, "topic/old.md", allowed=lambda: enabled[0])
    assert not (tmp_path / "state/context_usage.json").exists()


def test_publication_preserves_unchanged_and_relocated_usage(tmp_path):
    root = tmp_path / "workspace/context"
    _page(tmp_path, "old/page.md", "# Body\n[Root](../description.md)", 100)
    maintain_capacity(tmp_path, limit_bytes=100_000)
    read_with_usage(tmp_path, "old/page.md", allowed=lambda: True)
    original = _load_usage(tmp_path)["pages"]["old/page.md"]
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    publish_with_usage(tmp_path, candidate, {})
    assert _load_usage(tmp_path)["pages"]["old/page.md"] == original
    (candidate / "new/deep").mkdir(parents=True)
    (candidate / "old/page.md").unlink()
    (candidate / "new/deep/page.md").write_text("# Body\n[Root](../../description.md)", encoding="utf-8")
    publish_with_usage(tmp_path, candidate, {"old/page.md": "new/deep/page.md"})
    moved = _load_usage(tmp_path)["pages"]["new/deep/page.md"]
    assert moved["created_at"] == moved["updated_at"] == 100
    assert moved["last_read_at"] == original["last_read_at"]
    (candidate / "new/deep/page.md").write_text("# Body\nMaterially new content", encoding="utf-8")
    publish_with_usage(tmp_path, candidate, {})
    assert _load_usage(tmp_path)["pages"]["new/deep/page.md"]["updated_at"] > 100


def test_lru_deletes_oldest_unread_and_repairs_links_but_preserves_sources(tmp_path):
    root = tmp_path / "workspace/context"
    old = _page(tmp_path, "old/old.md", "# Old\n" + "a" * 3000, 100)
    cold = _page(tmp_path, "cold/cold.md", "# Cold\n" + "b" * 9000, 200)
    recent = _page(tmp_path, "recent/new.md", "# New\n[Cold](../cold/cold.md)\n" + "c" * 3000, 300)
    source = tmp_path / "workspace/source-meta/src.md"
    source.parent.mkdir()
    source.write_text("source record", encoding="utf-8")
    cursor = tmp_path / "state/cursor.json"
    cursor.parent.mkdir(exist_ok=True)
    cursor.write_text('{"version": 1}', encoding="utf-8")
    read_with_usage(tmp_path, "old/old.md", allowed=lambda: True)
    result = maintain_capacity(tmp_path, limit_bytes=13_000)
    assert result["removed_count"] == 1
    assert result["after_bytes"] <= 7_800
    assert old.exists() and recent.exists() and not cold.exists()
    assert not (root / "cold").exists()
    assert "../cold/cold.md" not in recent.read_text(encoding="utf-8")
    assert "cold/description.md" not in (root / "description.md").read_text(encoding="utf-8")
    records = _load_usage(tmp_path)["pages"]
    assert records["recent/new.md"]["updated_at"] == 300
    assert records["cold/cold.md"]["removal_reason"] == "least_recently_used"
    assert source.read_text(encoding="utf-8") == "source record"
    assert json.loads(cursor.read_text(encoding="utf-8")) == {"version": 1}


@pytest.mark.parametrize("total_bytes", [6001, 9999, 10000])
def test_capacity_waits_for_limit_even_when_above_reclaim_target(tmp_path, total_bytes):
    body = _page(tmp_path, "page.md", "")
    navigation_bytes = (tmp_path / "workspace/context/description.md").stat().st_size
    body.write_bytes(b"x" * (total_bytes - navigation_bytes))
    result = maintain_capacity(tmp_path, limit_bytes=10_000)
    assert result["before_bytes"] == result["after_bytes"] == total_bytes
    assert result["removed_count"] == 0 and body.exists()


def test_over_limit_reclaims_to_sixty_percent_then_waits_for_limit_again(tmp_path):
    for number in range(7):
        _page(tmp_path, f"page-{number}.md", f"# Page {number}\n" + "x" * 1700, 100 + number)
    result = maintain_capacity(tmp_path, limit_bytes=10_000)
    assert result["before_bytes"] > 10_000
    assert 4_000 < result["after_bytes"] <= 6_000
    assert result["removed_count"] == 4 and result["oversized_count"] == 0
    root = tmp_path / "workspace/context"
    assert all(not (root / f"page-{number}.md").exists() for number in range(4))
    assert all((root / f"page-{number}.md").exists() for number in range(4, 7))
    page = root / "page-6.md"
    page.write_text(page.read_text(encoding="utf-8") + "y" * 2000, encoding="utf-8")
    second = maintain_capacity(tmp_path, limit_bytes=10_000)
    assert 6_000 < second["after_bytes"] <= 10_000
    assert second["removed_count"] == 0


def test_page_below_limit_but_above_target_uses_lru_reason(tmp_path):
    _page(tmp_path, "old.md", "# Old\n" + "a" * 3000, 100)
    _page(tmp_path, "new.md", "# New\n" + "b" * 7000, 200)
    result = maintain_capacity(tmp_path, limit_bytes=10_000)
    assert result["after_bytes"] <= 6000
    assert result["removed_count"] == 2 and result["oversized_count"] == 0
    assert _load_usage(tmp_path)["pages"]["new.md"]["removal_reason"] == "least_recently_used"


def test_oversized_removal_still_reclaims_to_sixty_percent(tmp_path):
    old = _page(tmp_path, "old.md", "# Old\n" + "a" * 4000, 100)
    recent = _page(tmp_path, "recent.md", "# Recent\n" + "b" * 4500, 200)
    huge = _page(tmp_path, "huge.md", "# Huge\n" + "c" * 12000, 300)
    result = maintain_capacity(tmp_path, limit_bytes=10_000)
    assert result["removed_count"] == 2 and result["oversized_count"] == 1
    assert result["after_bytes"] <= 6000
    assert recent.exists() and not old.exists() and not huge.exists()


def test_navigation_above_reclaim_target_does_not_clear_bodies(tmp_path):
    page = _page(tmp_path, "page.md", "# Body\n" + "x" * 5000)
    (tmp_path / "workspace/context/description.md").write_text("# Root\n" + "n" * 7000, encoding="utf-8")
    result = maintain_capacity(tmp_path, limit_bytes=10_000)
    assert result["status"] == "over_limit"
    assert result["reason"] == "navigation_exceeds_reclaim_target"
    assert result["removed_count"] == 0 and page.exists()


def test_oversized_singleton_does_not_evict_fitting_older_knowledge(tmp_path):
    old = _page(tmp_path, "old/old.md", "# Old\n" + "a" * 4000, 100)
    huge = _page(tmp_path, "huge/page.md", "# Huge\n" + "x" * 12000, 200)
    result = maintain_capacity(tmp_path, limit_bytes=10_000)
    assert old.exists() and not huge.exists()
    assert result["removed_count"] == result["oversized_count"] == 1
    assert result["after_bytes"] <= 10_000


def test_singleton_uses_body_size_without_exact_navigation_measurement(tmp_path):
    old = _page(tmp_path, "old/page.md", "# Old\n" + "a" * 1000, 100)
    huge = _page(tmp_path, "new/page.md", "# Huge\n" + "b" * 9500, 200)
    result = maintain_capacity(tmp_path, limit_bytes=10_000)
    assert result["oversized_count"] == 0
    assert not old.exists() and not huge.exists()
    assert _load_usage(tmp_path)["pages"]["new/page.md"]["removal_reason"] == "least_recently_used"


def test_deep_navigation_does_not_require_single_page_candidate_trees(tmp_path):
    old = _page(tmp_path, "old.md", "# Old\n" + "a" * 4000, 100)
    huge = _page(tmp_path, "/".join([f"d{i}" for i in range(12)]) + "/page.md", "# New\n" + "b" * 6000, 200)
    result = maintain_capacity(tmp_path, limit_bytes=10_000)
    assert result["oversized_count"] == 0
    assert not old.exists() and not huge.exists()


@pytest.mark.parametrize("failure", ["json", "record", "read"])
def test_ledger_damage_recovers_but_disk_read_error_skips_only_current_deletion(tmp_path, monkeypatch, failure):
    from openjiuwen.harness.personal_context import context_capacity

    page = _page(tmp_path, "topic/page.md", "# Page\n" + "x" * 20000)
    state = tmp_path / "state"
    state.mkdir()
    ledger = state / "context_usage.json"
    invalid = "{" if failure == "json" else '{"version": 1, "pages": {"bad": {}}}'
    ledger.write_text(invalid, encoding="utf-8")

    def fail(*_args, **_kwargs):
        raise OSError("usage state read failed")

    with monkeypatch.context() as patcher:
        if failure == "read":
            patcher.setattr(context_capacity, "_load_usage", fail)
        assert maintain_capacity(tmp_path, limit_bytes=100_000)["removed_count"] == 0
        assert page.exists() and ledger.read_text(encoding="utf-8") == invalid
        if failure == "read":
            with pytest.raises(OSError):
                maintain_capacity(tmp_path, limit_bytes=1000)
        else:
            assert maintain_capacity(tmp_path, limit_bytes=1000)["removed_count"] == 1
    if failure == "read":
        assert page.exists() and ledger.read_text(encoding="utf-8") == invalid
        assert maintain_capacity(tmp_path, limit_bytes=1000)["removed_count"] == 1
    records = _load_usage(tmp_path)["pages"]
    assert records["topic/page.md"]["present"] is False
    assert not (tmp_path / "context_usage.suspended").exists()
    assert not page.exists()


def test_navigation_alone_over_limit_does_not_clear_bodies(tmp_path):
    page = _page(tmp_path, "topic/page.md", "# Page")
    (tmp_path / "workspace/context/description.md").write_text("# Root\n" + "x" * 5000, encoding="utf-8")
    result = maintain_capacity(tmp_path, limit_bytes=1000)
    assert result["status"] == "over_limit"
    assert result["removed_count"] == 0 and page.exists()


@pytest.mark.asyncio
async def test_busy_usage_ledger_skips_only_current_maintenance(tmp_path):
    _page(tmp_path, "a/page.md", "# A\n" + "a" * 5000, 100)
    _page(tmp_path, "b/page.md", "# B\n" + "b" * 5000, 200)
    entered = threading.Event()

    def collect():
        entered.set()
        return maintain_capacity(tmp_path, limit_bytes=8000)

    with usage_lock(tmp_path):
        worker = asyncio.create_task(asyncio.to_thread(collect))
        assert await asyncio.to_thread(entered.wait, 5)
        result = await asyncio.wait_for(worker, 2)
        assert result["reason"] == "usage_busy" and result["removed_count"] == 0
        assert (tmp_path / "workspace/context/a/page.md").exists()
    assert maintain_capacity(tmp_path, limit_bytes=8000)["removed_count"] > 0
    assert not (tmp_path / "workspace/context/a/page.md").exists()


@pytest.mark.asyncio
async def test_capacity_preparation_allows_reads_and_preserves_newly_used_candidate(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import context_capacity

    oldest = _page(tmp_path, "a.md", "# A\n" + "a" * 7000, 100)
    _page(tmp_path, "b.md", "# B\n" + "b" * 2000, 200)
    preparing = threading.Event()
    proceed = threading.Event()
    original_copy = context_capacity._copy_tree

    def copy(source, destination):
        original_copy(source, destination)
        preparing.set()
        assert proceed.wait(5)

    monkeypatch.setattr(context_capacity, "_copy_tree", copy)
    worker = asyncio.create_task(asyncio.to_thread(maintain_capacity, tmp_path, limit_bytes=8000))
    try:
        assert await asyncio.to_thread(preparing.wait, 5)
        read = await asyncio.wait_for(
            asyncio.to_thread(read_with_usage, tmp_path, "a.md", allowed=lambda: True), 2,
        )
        assert read["markdown"].startswith("# A")
        assert read_with_usage(tmp_path, "description.md", allowed=lambda: True)["markdown"].startswith("# Root")
    finally:
        proceed.set()
        result = await worker
    assert result["reason"] == "usage_changed" and result["removed_count"] == 0
    assert oldest.exists() and _load_usage(tmp_path)["pages"]["a.md"]["last_read_at"] > 200


def test_capacity_batch_repairs_navigation_once_and_reports_actual_size(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import context_capacity

    for number in range(32):
        _page(tmp_path, f"page-{number}.md", f"# Page {number}\n" + "x" * 4000, 100 + number)
    render_calls = []
    size_calls = []
    original_render = context_capacity._render_context_navigation
    original_sizes = context_capacity._markdown_sizes

    def render(root):
        render_calls.append(root)
        return original_render(root)

    def sizes(root):
        size_calls.append(root)
        return original_sizes(root)

    monkeypatch.setattr(context_capacity, "_render_context_navigation", render)
    monkeypatch.setattr(context_capacity, "_markdown_sizes", sizes)
    result = maintain_capacity(tmp_path, limit_bytes=125_000)
    assert result["removed_count"] > 1
    assert len(render_calls) == 1 and len(size_calls) == 3
    assert result["after_bytes"] == sum(original_sizes(tmp_path / "workspace/context").values())
    assert not hasattr(context_capacity, "_single_page_size")


@pytest.mark.parametrize("missing", ["description.md", "hot/description.md"])
def test_capacity_recreates_missing_navigation_while_preserving_retained_body(tmp_path, missing):
    from openjiuwen.harness.personal_context.context_pipeline import _validate_description_coverage

    old = _page(tmp_path, "cold/old.md", "# Old\n" + "a" * 7000, 100)
    hot = _page(tmp_path, "hot/keep.md", "# Keep\n" + "b" * 5000, 200)
    root = tmp_path / "workspace/context"
    (root / missing).unlink()
    result = maintain_capacity(tmp_path, limit_bytes=10_000)
    assert result["status"] == "succeeded" and result["removed_count"] == 1
    assert not old.exists() and hot.read_text(encoding="utf-8") == "# Keep\n" + "b" * 5000
    assert (root / missing).is_file()
    assert result["after_bytes"] <= 6000
    _validate_description_coverage(root)


def test_capacity_final_usage_lock_contention_keeps_published_tree(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import context_capacity

    page = _page(tmp_path, "page.md", "# Page\n" + "x" * 20000)
    held = usage_lock(tmp_path)
    repair = context_capacity._repair_navigation

    def repair_and_hold(root, removed):
        repair(root, removed)
        held.acquire(timeout=0)

    monkeypatch.setattr(context_capacity, "_repair_navigation", repair_and_hold)
    try:
        result = maintain_capacity(tmp_path, limit_bytes=1000)
    finally:
        held.release()
    assert result["reason"] == "usage_busy" and result["removed_count"] == 0 and page.exists()


@pytest.mark.asyncio
async def test_cancelled_candidate_wait_releases_no_other_owners_lock(tmp_path):
    async with context_update_lock(tmp_path):
        async def wait():
            async with context_update_lock(tmp_path):
                pytest.fail("second writer entered before first released")
        task = asyncio.create_task(wait())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with context_update_lock(tmp_path):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("budget_stage,expected_status", [("before_worker", "failed"), ("after_scan", "over_limit")])
async def test_fast_empty_collection_defers_expensive_reclaim_and_keeps_progress_after_restart(
    tmp_path, monkeypatch, budget_stage, expected_status,
):
    from openjiuwen.harness.personal_context import PersonalContext, context_capacity

    original_maintenance = context_capacity.run_capacity_maintenance

    async def defer_maintenance(home, *, limit_bytes, timeout, cancelled):
        # Exercise both real budget exits without depending on filesystem speed.
        if budget_stage == "before_worker":
            return await original_maintenance(home, limit_bytes=limit_bytes, timeout=0, cancelled=cancelled)
        return maintain_capacity(home, limit_bytes=limit_bytes, deadline=0, cancelled=cancelled)

    monkeypatch.setattr(context_capacity, "run_capacity_maintenance", defer_maintenance)

    home = tmp_path / "home"
    source = tmp_path / "source"
    source.mkdir()
    oversized = _page(home, "old/large.md", "# Large\n" + "x" * 10_100_000)
    config = PersonalContextConfig.from_dict({
        "collection_enabled": True, "strategy_profile": "rules", "max_context_size_mb": 10,
        "fetch_services": [{"service_id": "notes", "provider": "local_files", "enabled": False,
                            "interval_seconds": 3600, "time_range": {"mode": "all"},
                            "source": {"root_dir": str(source)}}],
    })
    core = PersonalContext(home=home)
    await core.set_configuration(config)
    assert oversized.exists()  # Initialization and saving never delete.
    await core.activate_runtime()
    try:
        await core.run_fetch(service_id="notes")
        await core._manual_fetch_tasks["notes"]
        assert oversized.exists()  # Tiny useful runs do not wait for expensive maintenance.
        record = (await core.get_fetch_run_status("notes"))["runs"][0]
        assert record["run_state"] == "succeeded"
        assert record["capacity_maintenance"]["status"] == expected_status
        assert record["capacity_maintenance"]["reason"] == "time_budget"
        assert core._read_cursor("notes") is not None
        await core.run_fetch(service_id="notes")
        await core._manual_fetch_tasks["notes"]
        assert oversized.exists()
        second_record = (await core.get_fetch_run_status("notes"))["runs"][0]
        assert second_record["run_state"] == "succeeded"
        assert second_record["capacity_maintenance"]["status"] == expected_status
        assert second_record["capacity_maintenance"]["reason"] == "time_budget"
        assert core._read_cursor("notes") is not None
    finally:
        await core.deactivate_runtime()
    restored = PersonalContext(home=home)
    try:
        await restored.set_configuration(config)
        restored_record = (await restored.get_fetch_run_status("notes"))["runs"][0]
        assert restored_record["run_state"] == "succeeded"
        assert restored_record["capacity_maintenance"] == second_record["capacity_maintenance"]
    finally:
        restored.shutdown()


@pytest.mark.asyncio
async def test_usage_save_failure_keeps_completed_collection_and_cursor(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import PersonalContext, context_usage

    home = tmp_path / "home"
    source = tmp_path / "source"
    source.mkdir()
    (source / "notes.md").write_text("# Useful knowledge\nAccounting failure must not discard this page.", encoding="utf-8")
    config = PersonalContextConfig.from_dict({
        "collection_enabled": True, "strategy_profile": "rules",
        "fetch_services": [{"service_id": "notes", "provider": "local_files", "enabled": False,
                            "interval_seconds": 3600, "time_range": {"mode": "all"},
                            "source": {"root_dir": str(source)}}],
    })

    def fail(*_args, **_kwargs):
        raise OSError("simulated usage save failure")

    monkeypatch.setattr(context_usage, "_save_usage", fail)
    core = PersonalContext(home=home)
    await core.set_configuration(config)
    await core.activate_runtime()
    try:
        await core.run_fetch(service_id="notes")
        await core._manual_fetch_tasks["notes"]
        record = (await core.get_fetch_run_status("notes"))["runs"][0]
        assert record["run_state"] == "succeeded" and record["created_node_count"] > 0
        assert record["capacity_maintenance"]["status"] == "succeeded"
        assert core._read_cursor("notes") is not None
        assert any("Accounting failure" in page.read_text(encoding="utf-8")
                   for page in (home / "workspace/context").rglob("*.md"))
        assert not (home / "context_usage.suspended").exists()
    finally:
        await core.deactivate_runtime()
        core.shutdown()


@pytest.mark.asyncio
async def test_maintenance_failure_does_not_rollback_committed_cursor(tmp_path, monkeypatch):
    from openjiuwen.harness.personal_context import PersonalContext, context_capacity
    from openjiuwen.harness.personal_context.fetch.local_files import LocalFilesFetchService

    source = tmp_path / "source"
    source.mkdir()
    home = tmp_path / "home"
    config = PersonalContextConfig.from_dict({
        "collection_enabled": True, "strategy_profile": "rules",
        "fetch_services": [{"service_id": "notes", "provider": "local_files", "enabled": False,
                            "interval_seconds": 3600, "time_range": {"mode": "all"},
                            "source": {"root_dir": str(source)}}],
    })
    async def fail(*_args, **_kwargs):
        raise OSError("simulated capacity failure")
    monkeypatch.setattr(context_capacity, "run_capacity_maintenance", fail)
    core = PersonalContext(home=home)
    try:
        await core.set_configuration(config)
        await core._run_fetch_once("notes", LocalFilesFetchService(config.fetch_services[0], home=home))
        record = (await core.get_fetch_run_status("notes"))["runs"][0]
        assert record["run_state"] == "succeeded"
        assert record["capacity_maintenance"] == {"status": "failed", "reason": "OSError"}
        assert core._read_cursor("notes") is not None
    finally:
        core.shutdown()


@pytest.mark.asyncio
async def test_eviction_keeps_cursor_but_source_update_can_publish_again(tmp_path):
    from openjiuwen.harness.personal_context import PersonalContext

    source = tmp_path / "source"
    source.mkdir()
    original = source / "notes.md"
    original.write_text("# Knowledge\n" + "useful facts " * 1500, encoding="utf-8")
    home = tmp_path / "home"
    config = PersonalContextConfig.from_dict({
        "collection_enabled": True, "strategy_profile": "rules",
        "fetch_services": [{"service_id": "notes", "provider": "local_files", "enabled": False,
                            "interval_seconds": 3600, "time_range": {"mode": "all"},
                            "source": {"root_dir": str(source)}}],
    })
    core = PersonalContext(home=home)
    await core.set_configuration(config)

    async def collect():
        await core.run_fetch(service_id="notes")
        await core._manual_fetch_tasks["notes"]
        return (await core.get_fetch_run_status("notes"))["runs"][0]

    try:
        first = await collect()
        assert first["run_state"] == "succeeded" and first["created_node_count"] > 0
        cursor = core._read_cursor("notes")
        removed = maintain_capacity(home, limit_bytes=1000)
        assert removed["removed_count"] > 0
        assert core._read_cursor("notes") == cursor
        second = await collect()
        assert second["no_new_content"] is True
        assert second["created_node_count"] == 0
        original.write_text("# Knowledge\nNew version contains new facts", encoding="utf-8")
        third = await collect()
        assert third["run_state"] == "succeeded" and third["created_node_count"] > 0
        assert any("New version" in page.read_text(encoding="utf-8") for page in (home / "workspace/context").rglob("*.md"))
    finally:
        await core.deactivate_runtime()
        core.shutdown()
