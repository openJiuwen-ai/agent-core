# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Incremental refresh: edits, new files, deletions, and stale reporting."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.indexing.builder import build_index
from openjiuwen.core.retrieval.code_graph.indexing.refresh import refresh_index_files
from openjiuwen.core.retrieval.code_graph.models import (
    CodeGraphConfig,
    CodeGraphIndex,
    RelationKind,
)
from openjiuwen.core.retrieval.code_graph.query.search_code import search_code
from tests.unit_tests.core.retrieval.code_graph.parser_guard import skip_unless_code_graph_parser

pytestmark = pytest.mark.level0
skip_unless_code_graph_parser()

BASE = """\
class Base:
    def render(self):
        return ""
"""

MAIN = """\
from pkg.base import Base


def run():
    return Base().render()
"""


def _write_repo(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "base.py").write_text(BASE, encoding="utf-8")
    (pkg / "main.py").write_text(MAIN, encoding="utf-8")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _write_repo(tmp_path / "repo")


@pytest.fixture
def index(repo: Path) -> CodeGraphIndex:
    return build_index(repo, CodeGraphConfig(max_files=200))


def _config() -> CodeGraphConfig:
    return CodeGraphConfig(max_files=200)


def test_an_edited_symbol_replaces_the_old_one(repo: Path, index: CodeGraphIndex) -> None:
    (repo / "pkg" / "base.py").write_text(
        "class Base:\n    def render(self, mode):\n        return mode\n",
        encoding="utf-8",
    )

    result = refresh_index_files(index, ["pkg/base.py"], _config())

    assert result.updated == ["pkg/base.py"]
    assert "mode" in (index.symbols["pkg/base.py::Base.render"].signature or "")


def test_a_new_file_is_added_and_its_call_resolves(repo: Path, index: CodeGraphIndex) -> None:
    (repo / "pkg" / "extra.py").write_text(
        "from pkg.base import Base\n\n\ndef extra():\n    return Base().render()\n",
        encoding="utf-8",
    )

    result = refresh_index_files(index, ["pkg/extra.py"], _config())

    assert result.updated == ["pkg/extra.py"]
    assert "pkg/extra.py::extra" in index.symbols
    callers = index.neighbors("pkg/base.py::Base.render", RelationKind.CALLED_BY)
    assert "pkg/extra.py::extra" in callers


def test_a_deleted_file_drops_its_symbols_and_edges(repo: Path, index: CodeGraphIndex) -> None:
    (repo / "pkg" / "main.py").unlink()

    result = refresh_index_files(index, ["pkg/main.py"], _config())

    assert result.removed == ["pkg/main.py"]
    assert "pkg/main.py::run" not in index.symbols
    assert list(index.neighbors("pkg/base.py::Base.render", RelationKind.CALLED_BY)) == []


def test_a_rename_is_a_delete_plus_an_add(repo: Path, index: CodeGraphIndex) -> None:
    (repo / "pkg" / "main.py").rename(repo / "pkg" / "entry.py")

    result = refresh_index_files(index, ["pkg/main.py", "pkg/entry.py"], _config())

    assert result.removed == ["pkg/main.py"]
    assert result.updated == ["pkg/entry.py"]
    assert "pkg/entry.py::run" in index.symbols
    assert "pkg/main.py::run" not in index.symbols


def test_an_unchanged_file_is_not_reindexed(repo: Path, index: CodeGraphIndex) -> None:
    revision = index.revision

    result = refresh_index_files(index, ["pkg/base.py"], _config())

    assert result.unchanged == ["pkg/base.py"]
    assert index.revision == revision


def test_the_snapshot_follows_the_working_tree_so_no_rebuild_is_triggered(repo: Path, index: CodeGraphIndex) -> None:
    before = index.snapshot
    (repo / "pkg" / "base.py").write_text(BASE + "\n\ndef helper():\n    return 1\n", encoding="utf-8")

    refresh_index_files(index, ["pkg/base.py"], _config())

    from openjiuwen.core.retrieval.code_graph.snapshot import compute_snapshot

    assert index.snapshot != before
    assert index.snapshot == compute_snapshot(repo)


def test_a_file_that_cannot_be_read_is_reported_as_stale(repo: Path, index: CodeGraphIndex, monkeypatch) -> None:
    from openjiuwen.core.retrieval.code_graph.indexing import refresh as refresh_module

    def boom(*args, **kwargs):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(refresh_module, "extract_one_file", boom)
    (repo / "pkg" / "base.py").write_text(BASE + "\n", encoding="utf-8")

    result = refresh_index_files(index, ["pkg/base.py"], _config())

    assert result.failed == ["pkg/base.py"]
    assert result.stale is True
    assert index.stale_files == ["pkg/base.py"]


def test_a_path_outside_the_repository_is_ignored(index: CodeGraphIndex, tmp_path: Path) -> None:
    result = refresh_index_files(index, [str(tmp_path / "elsewhere.py")], _config())

    assert result.updated == []
    assert any("outside repository" in item for item in result.warnings)


def test_lexical_search_sees_the_refreshed_definition(repo: Path, index: CodeGraphIndex) -> None:
    (repo / "pkg" / "extra.py").write_text(
        "def compute_discount_rate():\n    return 0.1\n",
        encoding="utf-8",
    )

    refresh_index_files(index, ["pkg/extra.py"], _config())
    matches = search_code(index, "compute_discount_rate", limit=5)

    assert any("compute_discount_rate" in match.symbol_id for match in matches)


def test_a_removed_definition_leaves_the_lexical_index(repo: Path, index: CodeGraphIndex) -> None:
    (repo / "pkg" / "main.py").unlink()

    refresh_index_files(index, ["pkg/main.py"], _config())
    matches = search_code(index, "run", limit=5)

    assert all("pkg/main.py" not in match.symbol_id for match in matches)
