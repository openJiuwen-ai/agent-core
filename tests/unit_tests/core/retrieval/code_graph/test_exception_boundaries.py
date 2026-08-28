# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Boundary and exception paths for Code Graph admission and fallback."""

from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.errors import CodeGraphLimitExceeded
from openjiuwen.core.retrieval.code_graph.identity import RepoIdentity
from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
from openjiuwen.core.retrieval.code_graph.manager import CodeGraphManager, reset_code_graph_manager
from openjiuwen.core.retrieval.code_graph.models import (
    INDEX_SCHEMA_VERSION,
    CodeGraphConfig,
    CodeGraphIndex,
    Symbol,
    SymbolKind,
)
from openjiuwen.core.retrieval.code_graph.service import CodeGraphService
from openjiuwen.core.retrieval.code_graph.store.index_store import (
    CACHE_FORMAT_VERSION,
    DiskIndexStore,
)
from tests.unit_tests.core.retrieval.code_graph.parser_guard import skip_unless_code_graph_parser

pytestmark = pytest.mark.level0
skip_unless_code_graph_parser()


def _py(root: Path, name: str, body: str = "def ready():\n    return 1\n") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _reset_manager() -> None:
    reset_code_graph_manager()
    yield
    reset_code_graph_manager()


@pytest.mark.asyncio
async def test_new_files_over_max_source_bytes_clear_the_old_graph(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _py(repo, "small.py", "def small():\n    return 1\n")
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=None, max_files=50, max_source_bytes=80)
    first = await manager.ensure_fresh(repo, cfg)
    assert "small.py::small" in first.index.symbols

    _py(repo, "bulk.py", "def bulk():\n    return '" + ("x" * 200) + "'\n")
    manager.mark_dirty_unknown(repo, "growth", config=cfg)
    with pytest.raises(CodeGraphLimitExceeded) as caught:
        await manager.ensure_fresh(repo, cfg)
    assert caught.value.limit == "max_source_bytes"
    entry = manager._peek_entry(repo, cfg)
    assert entry is not None
    assert entry.active is None


@pytest.mark.asyncio
async def test_same_over_limit_tree_does_not_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builds = {"n": 0}
    real = build_index

    def counting(repo_root, config, **kwargs):
        builds["n"] += 1
        return real(repo_root, config, **kwargs)

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.manager.build_index",
        counting,
    )
    repo = tmp_path / "repo"
    _py(repo, "one.py")
    _py(repo, "two.py")
    manager = CodeGraphManager(max_cached_repos=2)
    cfg = CodeGraphConfig(cache_dir=None, max_files=1)
    with pytest.raises(CodeGraphLimitExceeded):
        await manager.ensure_fresh(repo, cfg)
    first = builds["n"]
    assert first >= 1
    with pytest.raises(CodeGraphLimitExceeded) as caught:
        await manager.ensure_fresh(repo, cfg)
    assert caught.value.limit == "max_files"
    assert builds["n"] == first


@pytest.mark.asyncio
async def test_raising_max_files_rebuilds_after_abandon(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _py(repo, "one.py")
    _py(repo, "two.py")
    manager = CodeGraphManager(max_cached_repos=2)
    tight = CodeGraphConfig(cache_dir=None, max_files=1)
    with pytest.raises(CodeGraphLimitExceeded):
        await manager.ensure_fresh(repo, tight)
    roomy = CodeGraphConfig(cache_dir=None, max_files=20)
    generation = await manager.ensure_fresh(repo, roomy)
    assert "one.py::ready" in generation.index.symbols
    assert "two.py::ready" in generation.index.symbols


@pytest.mark.asyncio
async def test_raised_cap_updates_service_config_so_query_does_not_reabandon(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _py(repo, "one.py")
    _py(repo, "two.py")
    manager = CodeGraphManager(max_cached_repos=2)
    tight = CodeGraphConfig(cache_dir=None, max_files=1)
    with pytest.raises(CodeGraphLimitExceeded):
        await manager.ensure_fresh(repo, tight)
    roomy = CodeGraphConfig(cache_dir=None, max_files=20)
    service = await manager.get_service(repo, roomy, ensure=True)
    assert service.config.max_files == 20
    index = await service.ensure_ready()
    assert "one.py::ready" in index.symbols
    payload = manager.stats(repo, roomy)
    assert payload["state"] == "ready"
    assert payload.get("limit_exceeded") is not True


def test_stats_does_not_report_stale_refuse_after_cap_raise(tmp_path: Path) -> None:
    from openjiuwen.core.retrieval.code_graph.lifecycle import GraphEntry

    repo = tmp_path / "repo"
    _py(repo, "one.py")
    _py(repo, "two.py")
    manager = CodeGraphManager(max_cached_repos=2)
    identity = RepoIdentity.from_path(repo)
    entry = GraphEntry(identity=identity, config_hash="tight")
    entry.limit_error = CodeGraphLimitExceeded(
        "max_files is 2, cap is 1",
        limit="max_files",
        observed=2,
        cap=1,
    )
    manager._entries[identity.repo_id] = entry
    roomy = CodeGraphConfig(cache_dir=None, max_files=20)
    payload = manager.stats(repo, roomy)
    assert payload["state"] != "unavailable"
    assert payload.get("limit_exceeded") is not True
    assert entry.limit_error is None


def test_oversized_file_is_skipped_and_the_rest_is_indexed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _py(repo, "ok.py", "def ok():\n    return 1\n")
    _py(repo, "huge.py", "x = '" + ("n" * 200) + "'\n")
    index = build_index(repo, CodeGraphConfig(cache_dir=None, max_files=20, max_file_bytes=80))
    assert "ok.py::ok" in index.symbols
    assert not any(symbol.file == "huge.py" for symbol in index.symbols.values())


def test_markdown_does_not_count_toward_max_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _py(repo, "only.py")
    (repo / "README.md").write_text("# notes\n" * 20, encoding="utf-8")
    index = build_index(repo, CodeGraphConfig(cache_dir=None, max_files=1))
    assert "only.py::ready" in index.symbols


def test_unparsed_go_does_not_drop_the_python_graph(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _py(repo, "app.py", "def app():\n    return 1\n")
    (repo / "extra.go").write_text("package main\nfunc Extra() {}\n", encoding="utf-8")
    index = build_index(repo, CodeGraphConfig(cache_dir=None, max_files=20))
    assert "app.py::app" in index.symbols
    assert not any(
        symbol.file == "extra.go" and symbol.kind != SymbolKind.FILE
        for symbol in index.symbols.values()
    )


def test_go_file_still_counts_at_admission(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _py(repo, "app.py")
    (repo / "extra.go").write_text("package main\nfunc Extra() {}\n", encoding="utf-8")
    with pytest.raises(CodeGraphLimitExceeded) as caught:
        build_index(repo, CodeGraphConfig(cache_dir=None, max_files=1))
    assert caught.value.limit == "max_files"


@pytest.mark.asyncio
async def test_corrupt_active_pickle_rebuilds_and_ignores_leftover(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cache = tmp_path / "cache"
    _py(repo, "live.py", "def live():\n    return 1\n")
    cfg = CodeGraphConfig(cache_dir=str(cache), max_files=20)
    first_manager = CodeGraphManager(max_cached_repos=2)
    first = await first_manager.ensure_fresh(repo, cfg)
    assert "live.py::live" in first.index.symbols
    reset_code_graph_manager()

    identity = RepoIdentity.from_path(repo)
    folder = cache / DiskIndexStore._safe_part(identity.repo_id)
    active_pickles = list(folder.glob("*.pkl"))
    assert len(active_pickles) == 1
    leftover = CodeGraphIndex(repo_root=str(repo), snapshot="ghost", config_hash="old")
    leftover.add_symbol(
        Symbol(
            symbol_id="ghost.py::ghost",
            name="ghost",
            kind=SymbolKind.FUNCTION,
            file="ghost.py",
            start_line=1,
            end_line=2,
        )
    )
    leftover.schema_version = INDEX_SCHEMA_VERSION
    (folder / "leftover-old.pkl").write_bytes(
        pickle.dumps(
            {
                "version": CACHE_FORMAT_VERSION,
                "schema": INDEX_SCHEMA_VERSION,
                "index": leftover,
            },
            protocol=4,
        )
    )
    active_pickles[0].write_bytes(b"not-a-pickle")

    second = CodeGraphManager(max_cached_repos=2)
    rebuilt = await second.ensure_fresh(repo, cfg)
    assert "live.py::live" in rebuilt.index.symbols
    assert "ghost.py::ghost" not in rebuilt.index.symbols


@pytest.mark.asyncio
async def test_turning_the_rail_off_does_not_delete_the_checkpoint(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cache = tmp_path / "cache"
    _py(repo, "keep.py", "def keep():\n    return 1\n")
    cfg = CodeGraphConfig(cache_dir=str(cache), max_files=20)
    manager = CodeGraphManager(max_cached_repos=2)
    await manager.ensure_fresh(repo, cfg)
    before = list(cache.rglob("*.pkl"))
    assert before

    from openjiuwen.harness.rails.code_graph_profile_rail import CodeGraphProfileRail

    rail = CodeGraphProfileRail("off")
    rail.uninit(agent=None)
    assert list(cache.rglob("*.pkl")) == before


@pytest.mark.asyncio
async def test_missing_repo_is_error_not_unavailable(tmp_path: Path) -> None:
    service = CodeGraphService(
        tmp_path / "missing",
        CodeGraphConfig(cache_dir=None, max_files=20),
    )
    payload = await service.search_code("ready")
    assert payload["status"] == "ERROR"
    assert payload.get("reason") != "limit_exceeded"
