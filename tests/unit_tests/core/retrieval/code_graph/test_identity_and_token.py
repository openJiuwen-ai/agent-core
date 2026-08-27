# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Repo identity, content-aware tokens, and disk-key isolation."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.identity import RepoIdentity, workspace_relative_path
from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig, DEFAULT_EXCLUDE_DIRS
from openjiuwen.core.retrieval.code_graph.store.index_store import DiskIndexStore
from openjiuwen.core.retrieval.code_graph.workspace_token import (
    compute_workspace_token,
    detect_changed_paths,
    hash_workspace_files,
    incremental_limit,
)
from tests.unit_tests.core.retrieval.code_graph.parser_guard import skip_unless_code_graph_parser

pytestmark = pytest.mark.level0


def test_same_path_and_symlink_share_repo_id(tmp_path: Path) -> None:
    real = tmp_path / "repo"
    real.mkdir()
    alias = tmp_path / "alias"
    os.symlink(real, alias)

    first = RepoIdentity.from_path(real)
    second = RepoIdentity.from_path(alias / ".")
    assert first.repo_id == second.repo_id
    assert first.canonical_root == second.canonical_root


def test_workspace_relative_path_strips_realpath_prefix(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "auth.py"
    target.write_text("x = 1\n", encoding="utf-8")
    identity = RepoIdentity.from_path(repo)
    assert workspace_relative_path(identity.canonical_root, target) == "auth.py"
    stripped = str(Path(identity.canonical_root).joinpath("auth.py")).lstrip("/")
    assert workspace_relative_path(identity.canonical_root, "/" + stripped) == "auth.py"
    assert workspace_relative_path(identity.canonical_root, "auth.py") == "auth.py"


@pytest.mark.asyncio
async def test_mark_dirty_keeps_repo_relative_paths(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text("def check():\n    return True\n", encoding="utf-8")
    cfg = CodeGraphConfig(cache_dir=None, max_files=50, freshness_check_interval_ms=0)
    manager = CodeGraphManager(max_cached_repos=2)
    await manager.ensure_fresh(repo, cfg)
    manager.mark_dirty(repo, [str((repo / "auth.py").resolve())], config=cfg)
    entry = manager._peek_entry(repo, cfg)
    assert entry is not None
    assert entry.dirty_paths == {"auth.py"}


@pytest.mark.asyncio
async def test_legacy_dirty_path_still_refreshes(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text("def check():\n    return 1\n", encoding="utf-8")
    cfg = CodeGraphConfig(cache_dir=None, max_files=50, freshness_check_interval_ms=0)
    manager = CodeGraphManager(max_cached_repos=2)
    first = await manager.ensure_fresh(repo, cfg)
    (repo / "auth.py").write_text("def check():\n    return 2\n", encoding="utf-8")
    entry = manager._peek_entry(repo, cfg)
    assert entry is not None
    entry.dirty_paths = {str((repo / "auth.py").resolve()).lstrip("/")}
    assert manager.stats(repo)["dirty_paths"] == ["auth.py"]
    second = await manager.ensure_fresh(repo, cfg)
    assert second.generation_id != first.generation_id
    assert second.index.file_hashes.get("auth.py") != first.index.file_hashes.get("auth.py")


def test_different_clones_and_worktrees_have_different_ids(tmp_path: Path) -> None:
    one = tmp_path / "one"
    two = tmp_path / "two"
    worktree = tmp_path / "one" / ".worktrees" / "agent"
    one.mkdir()
    two.mkdir()
    worktree.mkdir(parents=True)

    assert RepoIdentity.from_path(one).repo_id != RepoIdentity.from_path(two).repo_id
    assert RepoIdentity.from_path(one).repo_id != RepoIdentity.from_path(worktree).repo_id


def test_same_basename_different_path_does_not_collide_on_disk(tmp_path: Path) -> None:
    store = DiskIndexStore(tmp_path / "cache", max_size_mb=8)
    from openjiuwen.core.retrieval.code_graph.models import CodeGraphIndex

    left = RepoIdentity.from_path(tmp_path / "left" / "repo")
    right = RepoIdentity.from_path(tmp_path / "right" / "repo")
    (tmp_path / "left" / "repo").mkdir(parents=True)
    (tmp_path / "right" / "repo").mkdir(parents=True)
    left = RepoIdentity.from_path(tmp_path / "left" / "repo")
    right = RepoIdentity.from_path(tmp_path / "right" / "repo")

    store.save(f"{left.repo_id}/snap-cfg", CodeGraphIndex(repo_root=left.canonical_root, snapshot="a", config_hash="c"))
    store.save(f"{right.repo_id}/snap-cfg", CodeGraphIndex(repo_root=right.canonical_root, snapshot="b", config_hash="c"))

    loaded_left = store.load(f"{left.repo_id}/snap-cfg")
    loaded_right = store.load(f"{right.repo_id}/snap-cfg")
    assert loaded_left is not None and loaded_left.repo_root == left.canonical_root
    assert loaded_right is not None and loaded_right.repo_root == right.canonical_root
    assert left.repo_id != right.repo_id


def test_dirty_file_edited_twice_changes_token(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "src.py"
    target.write_text("a = 1\n", encoding="utf-8")
    first = compute_workspace_token(root, extra_paths=["src.py"])
    time.sleep(0.01)
    target.write_text("a = 2\n", encoding="utf-8")
    second = compute_workspace_token(root, extra_paths=["src.py"])
    assert first.digest != second.digest


def test_revert_to_original_content_restores_token(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "src.py"
    original = "value = 1\n"
    target.write_text(original, encoding="utf-8")
    baseline = compute_workspace_token(root, extra_paths=["src.py"])
    target.write_text("value = 2\n", encoding="utf-8")
    dirty = compute_workspace_token(root, extra_paths=["src.py"])
    target.write_text(original, encoding="utf-8")
    restored = compute_workspace_token(root, extra_paths=["src.py"])
    assert dirty.digest != baseline.digest
    assert restored.digest == baseline.digest


def test_detect_changed_paths_sees_second_edit(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("one", encoding="utf-8")
    hashes = hash_workspace_files(root, ["a.py"])
    time.sleep(0.01)
    (root / "a.py").write_text("two", encoding="utf-8")
    assert detect_changed_paths(root, hashes, extra_paths=["a.py"]) == ["a.py"]
    hashes = hash_workspace_files(root, ["a.py"])
    time.sleep(0.01)
    (root / "a.py").write_text("three", encoding="utf-8")
    assert detect_changed_paths(root, hashes, extra_paths=["a.py"]) == ["a.py"]


def test_detect_changed_paths_normalizes_stripped_absolute_extra(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "a.py"
    target.write_text("one", encoding="utf-8")
    hashes = hash_workspace_files(root, ["a.py"])
    target.write_text("two", encoding="utf-8")
    stripped = str(target.resolve()).lstrip("/")
    assert detect_changed_paths(root, hashes, extra_paths=["/" + stripped]) == ["a.py"]
    assert detect_changed_paths(root, hashes, extra_paths=[stripped]) == ["a.py"]
    assert hash_workspace_files(root, [stripped]) == hash_workspace_files(root, ["a.py"])


def test_worktrees_dir_is_excluded_by_default() -> None:
    assert ".worktrees" in DEFAULT_EXCLUDE_DIRS
    assert "_worktrees" in DEFAULT_EXCLUDE_DIRS
    assert "htmlcov" in DEFAULT_EXCLUDE_DIRS
    assert "coverage" in DEFAULT_EXCLUDE_DIRS
    assert "_build" in DEFAULT_EXCLUDE_DIRS
    assert ".next" in DEFAULT_EXCLUDE_DIRS
    assert "__snapshots__" in DEFAULT_EXCLUDE_DIRS
    assert ".cursor" in DEFAULT_EXCLUDE_DIRS
    assert ".claude" in DEFAULT_EXCLUDE_DIRS
    assert ".code_graph_cache" in DEFAULT_EXCLUDE_DIRS


def test_default_excludes_are_generic_artifact_names() -> None:
    from openjiuwen.core.retrieval.code_graph.models import DEFAULT_EXCLUDE_GLOBS

    joined_dirs = " ".join(DEFAULT_EXCLUDE_DIRS)
    assert "docs/ai" not in joined_dirs
    assert ".doc_project_maintainer" not in DEFAULT_EXCLUDE_DIRS
    assert "*.min.js" in DEFAULT_EXCLUDE_GLOBS
    assert "*.map" in DEFAULT_EXCLUDE_GLOBS
    assert "*.snap" in DEFAULT_EXCLUDE_GLOBS
    from openjiuwen.core.retrieval.code_graph.models import DEFAULT_EXCLUDE_DIR_GLOBS

    assert "*.egg-info" in DEFAULT_EXCLUDE_DIR_GLOBS


def test_minified_and_snapshot_files_are_not_walked(tmp_path: Path) -> None:
    from openjiuwen.core.retrieval.code_graph.indexing.builder import _iter_source_files
    from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.js").write_text("function main() {}\n", encoding="utf-8")
    (repo / "src" / "app.min.js").write_text("function main(){}\n", encoding="utf-8")
    (repo / "src" / "app.js.map").write_text("{}\n", encoding="utf-8")
    files = _iter_source_files(repo, CodeGraphConfig())
    rels = [path.relative_to(repo).as_posix() for path in files]
    assert "src/app.js" in rels
    assert "src/app.min.js" not in rels


def test_generated_output_dirs_are_not_walked(tmp_path: Path) -> None:
    from openjiuwen.core.retrieval.code_graph.indexing.builder import _iter_source_files
    from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "_build" / "html").mkdir(parents=True)
    (repo / ".next").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "_build" / "html" / "page.py").write_text("def noise():\n    return 0\n", encoding="utf-8")
    (repo / ".next" / "chunk.js").write_text("function noise() {}\n", encoding="utf-8")
    files = _iter_source_files(repo, CodeGraphConfig())
    rels = [path.relative_to(repo).as_posix() for path in files]
    assert "src/main.py" in rels
    assert not any(rel.startswith("_build/") for rel in rels)
    assert not any(rel.startswith(".next/") for rel in rels)


def test_default_config_skips_manual_text_files(tmp_path: Path) -> None:
    from openjiuwen.core.retrieval.code_graph.indexing.builder import _iter_text_documents
    from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("usage notes\n", encoding="utf-8")
    (repo / "notes.txt").write_text("plain notes\n", encoding="utf-8")
    assert CodeGraphConfig().index_text_files is False
    enabled = CodeGraphConfig(index_text_files=True)
    rels = [rel for rel, _ in _iter_text_documents(repo, enabled)]
    assert "README.md" in rels
    assert "notes.txt" in rels


def test_gitignore_directory_drops_everything_under_it(tmp_path: Path) -> None:
    from openjiuwen.core.retrieval.code_graph.indexing.builder import _iter_source_files
    from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "htmlcov").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "htmlcov" / "page.py").write_text("def noise():\n    return 0\n", encoding="utf-8")
    (repo / ".gitignore").write_text("htmlcov/\n", encoding="utf-8")
    files = _iter_source_files(
        repo,
        CodeGraphConfig(exclude_dirs=(".git",), max_files=1000, max_source_bytes=10_000_000),
    )
    rels = [path.relative_to(repo).as_posix() for path in files]
    assert "src/main.py" in rels
    assert "htmlcov/page.py" not in rels


def test_worktrees_are_not_indexed_in_parent(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / ".worktrees" / "agent").mkdir(parents=True)
    (repo / "_worktrees" / "clone").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / ".worktrees" / "agent" / "extra.py").write_text("def extra():\n    return 2\n", encoding="utf-8")
    (repo / "_worktrees" / "clone" / "extra.py").write_text("def extra():\n    return 3\n", encoding="utf-8")
    index = build_index(repo, CodeGraphConfig(cache_dir=None, max_files=50))
    assert any(symbol.file.endswith("main.py") for symbol in index.symbols.values())
    assert not any(".worktrees" in symbol.file for symbol in index.symbols.values())
    assert not any("_worktrees" in symbol.file for symbol in index.symbols.values())


@pytest.mark.asyncio
async def test_restart_loads_checkpoint_and_refreshes_only_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
    from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager

    builds = {"n": 0}
    real = build_index

    def counting(repo_root, config, **kwargs):
        builds["n"] += 1
        return real(repo_root, config, **kwargs)

    monkeypatch.setattr("openjiuwen.core.retrieval.code_graph.manager.build_index", counting)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    cache = tmp_path / "cache"
    cfg = CodeGraphConfig(cache_dir=str(cache), max_files=50, freshness_check_interval_ms=0)
    first_manager = CodeGraphManager(max_cached_repos=2)
    await first_manager.ensure_fresh(repo, cfg)
    assert builds["n"] == 1
    entry = first_manager._peek_entry(repo, cfg)
    assert entry is not None
    entry.last_full_build_seconds = 5.0
    first_manager.checkpoint(repo, reason="test", config=cfg)
    (repo / "src.py").write_text("def first():\n    return 1\n\ndef second():\n    return 2\n", encoding="utf-8")
    second_manager = CodeGraphManager(max_cached_repos=2)
    generation = await second_manager.ensure_fresh(repo, cfg)
    assert builds["n"] == 1
    assert "src.py::second" in generation.index.symbols


@pytest.mark.asyncio
async def test_restart_restores_full_build_seconds_for_small_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
    from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager

    builds = {"n": 0}
    real = build_index

    def counting(repo_root, config, **kwargs):
        builds["n"] += 1
        return real(repo_root, config, **kwargs)

    monkeypatch.setattr("openjiuwen.core.retrieval.code_graph.manager.build_index", counting)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    cache = tmp_path / "cache"
    cfg = CodeGraphConfig(cache_dir=str(cache), max_files=50, freshness_check_interval_ms=0)
    first_manager = CodeGraphManager(max_cached_repos=2)
    first = await first_manager.ensure_fresh(repo, cfg)
    assert first.reason == "full"
    assert builds["n"] == 1
    (repo / "src.py").write_text("def first():\n    return 1\n\ndef second():\n    return 2\n", encoding="utf-8")
    second_manager = CodeGraphManager(max_cached_repos=2)
    generation = await second_manager.ensure_fresh(repo, cfg)
    assert builds["n"] == 2
    assert generation.reason == "full"
    entry = second_manager._peek_entry(repo, cfg)
    assert entry is not None
    assert entry.last_full_build_seconds is not None
    assert "src.py::second" in generation.index.symbols


@pytest.mark.asyncio
async def test_write_during_refresh_is_not_lost(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    cfg = CodeGraphConfig(cache_dir=None, max_files=50, freshness_check_interval_ms=0)
    manager = CodeGraphManager(max_cached_repos=2)
    await manager.ensure_fresh(repo, cfg)
    (repo / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    manager.mark_dirty(repo, ["a.py"], config=cfg)
    (repo / "b.py").write_text("def b():\n    return 3\n", encoding="utf-8")
    manager.mark_dirty(repo, ["b.py"], config=cfg)
    generation = await manager.ensure_fresh(repo, cfg)
    assert "a.py::a" in generation.index.symbols
    assert "b.py::b" in generation.index.symbols


@pytest.mark.asyncio
async def test_over_max_files_does_not_build_a_graph(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.service import CodeGraphService

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (repo / "two.py").write_text("def two():\n    return 2\n", encoding="utf-8")
    service = CodeGraphService(repo, CodeGraphConfig(cache_dir=None, max_files=1))
    payload = await service.search_code("one")
    assert payload["status"] == "UNAVAILABLE"
    assert payload.get("reason") == "limit_exceeded"
    assert payload.get("limit") == "max_files"
    assert any(item.get("tool") == "grep" for item in (payload.get("next_actions") or []))
    assert "limit" in str(payload.get("message") or "").lower() or "cap" in str(payload.get("message") or "").lower()


@pytest.mark.asyncio
async def test_resolve_symbol_over_limit_is_unavailable(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.service import CodeGraphService

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "one.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (repo / "two.py").write_text("def leftover():\n    return 2\n", encoding="utf-8")
    service = CodeGraphService(repo, CodeGraphConfig(cache_dir=None, max_files=1))
    payload = await service.resolve_symbol("one")
    assert payload["status"] == "UNAVAILABLE"
    assert payload.get("reason") == "limit_exceeded"
    assert any(item.get("tool") == "grep" for item in (payload.get("next_actions") or []))


@pytest.mark.asyncio
async def test_old_reader_survives_publish(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=None, max_files=50)
    generation = await manager.ensure_fresh(repo, cfg)
    pinned = await manager.acquire(repo, cfg)
    old_index = pinned.index
    time.sleep(0.01)
    (repo / "src.py").write_text("def first():\n    return 1\n\ndef second():\n    return 2\n", encoding="utf-8")
    manager.mark_dirty(repo, ["src.py"], config=cfg)
    newer = await manager.ensure_fresh(repo, cfg)
    assert newer.generation_id != generation.generation_id
    assert "src.py::first" in old_index.symbols
    assert "src.py::second" not in old_index.symbols
    assert "src.py::second" in newer.index.symbols
    pinned.release()


def test_graph_tools_rebind_to_the_live_workspace(tmp_path: Path) -> None:
    from openjiuwen.harness.tools.code_graph._base import CodeGraphBaseTool, CodeGraphToolContext

    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    main.mkdir()
    worktree.mkdir()
    current = {"root": str(main)}
    context = CodeGraphToolContext(
        repo_root=str(main),
        config=CodeGraphConfig(cache_dir=None, max_files=10),
        resolve_root=lambda: current["root"],
    )

    class _Probe:
        def __init__(self) -> None:
            self.context = context

        current_repo_root = CodeGraphBaseTool.current_repo_root

    tool = _Probe()
    assert Path(tool.current_repo_root()) == main.resolve()
    current["root"] = str(worktree)
    assert Path(tool.current_repo_root()) == worktree.resolve()


def test_config_does_not_keep_dead_admission_knobs() -> None:
    cfg = CodeGraphConfig()
    for name in (
        "max_symbols",
        "max_edges",
        "max_single_index_mb",
        "index_time_budget_seconds",
        "incremental_max_ratio",
        "checkpoint_idle_seconds",
    ):
        assert not hasattr(cfg, name)


def test_egg_info_dir_is_not_walked(tmp_path: Path) -> None:
    from openjiuwen.core.retrieval.code_graph.indexing.builder import _iter_source_files
    from openjiuwen.core.retrieval.code_graph.models import DEFAULT_EXCLUDE_DIR_GLOBS, CodeGraphConfig

    assert "*.egg-info" in DEFAULT_EXCLUDE_DIR_GLOBS
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "pkg.egg-info").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "pkg.egg-info" / "mod.py").write_text("def noise():\n    return 0\n", encoding="utf-8")
    files = _iter_source_files(repo, CodeGraphConfig())
    rels = [path.relative_to(repo).as_posix() for path in files]
    assert "src/main.py" in rels
    assert "pkg.egg-info/mod.py" not in rels


def test_measured_rss_cap_refuses_the_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded
    from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.budgets.process_rss_bytes",
        lambda: 8 * 1024 * 1024 * 1024,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("def ready():\n    return 1\n", encoding="utf-8")
    with pytest.raises(CodeGraphLimitExceeded) as caught:
        build_index(repo, CodeGraphConfig(cache_dir=None, max_files=50, max_build_rss_mb=1024))
    assert caught.value.limit == "max_build_rss_mb"


def test_measured_disk_cap_refuses_the_index(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded
    from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "filler.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("def ready():\n    return 1\n", encoding="utf-8")
    with pytest.raises(CodeGraphLimitExceeded) as caught:
        build_index(
            repo,
            CodeGraphConfig(cache_dir=str(cache), max_files=50, max_cache_size_mb=1),
        )
    assert caught.value.limit == "max_cache_size_mb"


@pytest.mark.asyncio
async def test_slow_first_build_returns_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
    from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager
    from openjiuwen.core.retrieval.code_graph.service import CodeGraphService

    real = build_index

    def slow(repo_root, config, cancel=None):
        time.sleep(0.4)
        return real(repo_root, config, cancel=cancel)

    monkeypatch.setattr("openjiuwen.core.retrieval.code_graph.manager.build_index", slow)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("def ready():\n    return 1\n", encoding="utf-8")
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(
        cache_dir=None,
        max_files=20,
        query_wait_seconds=0.05,
        first_build_wait_seconds=0.05,
    )
    service = await manager.get_service(repo, cfg, ensure=False)
    payload = await service.search_code("ready")
    assert payload["status"] == "BUILDING"
    assert payload.get("index_state") == "building"
    await asyncio.sleep(0.5)
    later = await service.search_code("ready")
    assert later["status"] == "COMPLETE"


@pytest.mark.asyncio
async def test_admitted_repo_waits_until_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
    from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager
    from openjiuwen.core.retrieval.code_graph.service import CodeGraphService

    real = build_index

    def slow(repo_root, config, cancel=None):
        time.sleep(0.3)
        return real(repo_root, config, cancel=cancel)

    monkeypatch.setattr("openjiuwen.core.retrieval.code_graph.manager.build_index", slow)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("def ready():\n    return 1\n", encoding="utf-8")
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=None, max_files=20)
    service = await manager.get_service(repo, cfg, ensure=False)
    payload = await service.search_code("ready")
    assert payload["status"] == "COMPLETE"


def test_incremental_limit_small_repo_always_rebuilds() -> None:
    cfg = CodeGraphConfig()
    assert incremental_limit(50, cfg, last_full_build_seconds=0.2) == 0
    assert incremental_limit(400, cfg, last_full_build_seconds=0.9) == 0


def test_incremental_limit_medium_uses_dirty_break_even() -> None:
    cfg = CodeGraphConfig()
    assert incremental_limit(1600, cfg, last_full_build_seconds=3.0) == 60
    assert incremental_limit(3200, cfg, last_full_build_seconds=20.0) == 60


def test_incremental_limit_without_measured_rebuild_uses_dirty_cap() -> None:
    cfg = CodeGraphConfig()
    assert incremental_limit(50, cfg) == 60


def test_product_wait_is_unlimited_by_default() -> None:
    cfg = CodeGraphConfig()
    assert cfg.resolved_wait_seconds(first_build=True) is None
    assert cfg.resolved_wait_seconds(first_build=False) is None


def test_explicit_wait_still_available_for_tests() -> None:
    cfg = CodeGraphConfig(
        first_build_wait_seconds=0.2,
        query_wait_seconds=0.5,
    )
    assert cfg.resolved_wait_seconds(first_build=True) == 0.2
    assert cfg.resolved_wait_seconds(first_build=False) == 0.5


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-m", "init"],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_overlay_config_changes_snapshot_digest(tmp_path: Path) -> None:
    from openjiuwen.core.retrieval.code_graph.snapshot import compute_snapshot

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("a = 1\n", encoding="utf-8")
    default = compute_snapshot(repo)
    overlay = compute_snapshot(repo, CodeGraphConfig(max_files=100))
    assert default != overlay


def test_cache_pickle_inside_git_repo_does_not_change_token(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("a = 1\n", encoding="utf-8")
    _git_init(repo)
    first = compute_workspace_token(repo, CodeGraphConfig(max_files=100))
    cache = repo / ".code_graph_cache" / "active.pkl"
    cache.parent.mkdir()
    cache.write_bytes(b"not-a-source-file")
    second = compute_workspace_token(repo, CodeGraphConfig(max_files=100))
    assert first.digest == second.digest
    assert not any(".code_graph_cache" in path for path in second.dirty_paths)


def test_dot_cache_dir_is_ignored_even_if_rel_looks_like_lstrip_dot(tmp_path: Path) -> None:
    from openjiuwen.core.retrieval.code_graph.workspace_token import _ignored_workspace_rel

    cfg = CodeGraphConfig()
    assert _ignored_workspace_rel(".code_graph_cache/blob.pkl", cfg, None) is True
    assert _ignored_workspace_rel("src.py", cfg, None) is False


def test_custom_cache_dir_inside_repo_does_not_change_token(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("a = 1\n", encoding="utf-8")
    _git_init(repo)
    cache = repo / "my_graph_cache"
    cfg = CodeGraphConfig(cache_dir=str(cache), max_files=100)
    first = compute_workspace_token(repo, cfg)
    cache.mkdir()
    (cache / "blob.pkl").write_bytes(b"checkpoint")
    second = compute_workspace_token(repo, cfg)
    assert first.digest == second.digest
    assert not any("my_graph_cache" in path for path in second.dirty_paths)


def test_code_graph_cache_dir_is_not_indexed(tmp_path: Path) -> None:
    skip_unless_code_graph_parser()
    from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / ".code_graph_cache").mkdir()
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / ".code_graph_cache" / "noise.py").write_text("def noise():\n    return 0\n", encoding="utf-8")
    index = build_index(repo, CodeGraphConfig(cache_dir=None, max_files=50))
    assert any(symbol.file.endswith("main.py") for symbol in index.symbols.values())
    assert not any(".code_graph_cache" in symbol.file for symbol in index.symbols.values())
    assert ".code_graph_cache" in DEFAULT_EXCLUDE_DIRS


def test_disk_store_resolves_relative_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = DiskIndexStore(".nested_cache", max_size_mb=8)
    assert store.cache_dir.is_absolute()
    assert store.cache_dir == (tmp_path / ".nested_cache").resolve()

