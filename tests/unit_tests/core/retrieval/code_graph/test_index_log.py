# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Index log helpers: definition count and aggregated unreadable skips."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.index_log import (
    SKIPPED_UNREADABLE_PREFIX,
    definition_count,
    note_skipped_unreadable,
)
from openjiuwen.core.retrieval.code_graph.models import CodeGraphIndex, Symbol, SymbolKind

pytestmark = pytest.mark.level0


def _index(*symbols: Symbol) -> CodeGraphIndex:
    index = CodeGraphIndex(repo_root="/tmp/repo", snapshot="snap", config_hash="hash")
    for symbol in symbols:
        index.add_symbol(symbol)
    return index


def _symbol(symbol_id: str, kind: SymbolKind, name: str = "x") -> Symbol:
    return Symbol(
        symbol_id=symbol_id,
        name=name,
        kind=kind,
        file="a.py",
        start_line=1,
        end_line=2,
    )


def test_definition_count_ignores_file_stubs() -> None:
    index = _index(
        _symbol("file:a.py", SymbolKind.FILE, "a.py"),
        _symbol("fn:ok", SymbolKind.FUNCTION, "ok"),
    )
    assert definition_count(index) == 1


def test_note_skipped_replaces_previous_warning() -> None:
    index = _index()
    note_skipped_unreadable(index, ["a.py", "b.py", "c.py", "d.py"])
    note_skipped_unreadable(index, ["only.py"])
    skip_warnings = [item for item in index.warnings if item.startswith(SKIPPED_UNREADABLE_PREFIX)]
    assert skip_warnings == [f"{SKIPPED_UNREADABLE_PREFIX}1 sample=only.py"]


def test_build_index_aggregates_unreadable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openjiuwen.core.retrieval.code_graph.indexing import builder as builder_mod
    from openjiuwen.core.retrieval.code_graph.indexing.builder import ParsedFile, build_index
    from openjiuwen.core.retrieval.code_graph.models import CodeGraphConfig

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "b.py").write_text("y = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        builder_mod,
        "extract_one_file",
        lambda path, rel, cfg: ParsedFile(rel_path=rel, unreadable=True, skip_reason="ENOENT"),
    )
    index = build_index(repo, CodeGraphConfig(cache_dir=None, max_files=20))
    assert any(item.startswith(f"{SKIPPED_UNREADABLE_PREFIX}2") for item in index.warnings)
    assert definition_count(index) == 0
