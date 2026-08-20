# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for exact symbol resolve and file-scope listing."""

from __future__ import annotations

import pytest

from openjiuwen.core.retrieval.code_graph.models import CodeGraphIndex, Symbol, SymbolKind
from openjiuwen.core.retrieval.code_graph.query.list_symbols import list_symbols
from openjiuwen.core.retrieval.code_graph.query.lexical import tokenize
from openjiuwen.core.retrieval.code_graph.query.resolve_symbol import resolve_symbol

pytestmark = pytest.mark.level0


def _symbol(
    *,
    name: str,
    file: str,
    kind: SymbolKind = SymbolKind.FUNCTION,
    qualified: str | None = None,
    line: int = 1,
    symbol_id: str | None = None,
) -> Symbol:
    qname = qualified or name
    return Symbol(
        symbol_id=symbol_id or f"{file}::{qname}",
        name=name,
        kind=kind,
        file=file,
        start_line=line,
        end_line=line + 4,
        qualified_name=qname,
        language="python",
    )


def _index(*symbols: Symbol) -> CodeGraphIndex:
    index = CodeGraphIndex(repo_root="/repo", snapshot="s", config_hash="h")
    for symbol in symbols:
        index.add_symbol(symbol)
    return index


def test_resolve_file_uri_exact_symbol_id() -> None:
    method = _symbol(
        name="_parse_value",
        file="astropy/io/fits/card.py",
        kind=SymbolKind.METHOD,
        qualified="Card._parse_value",
        line=751,
        symbol_id="astropy/io/fits/card.py::Card._parse_value",
    )
    index = _index(method)
    hits = resolve_symbol(index, "file://astropy/io/fits/card.py::Card._parse_value")
    assert len(hits) == 1
    assert hits[0].name == "_parse_value"


def test_resolve_requires_exact_name_or_qualified_name() -> None:
    hit = _symbol(
        name="RelatedFieldListFilter",
        file="django/contrib/admin/filters.py",
        kind=SymbolKind.CLASS,
        qualified="RelatedFieldListFilter",
    )
    index = _index(hit)
    hits = resolve_symbol(
        index,
        "django.contrib.admin.filters.RelatedFieldListFilter",
        kind="class",
    )
    assert hits == []


def test_wrong_path_hint_does_not_fall_back() -> None:
    hit = _symbol(
        name="Card",
        file="astropy/io/fits/card.py",
        kind=SymbolKind.CLASS,
    )
    index = _index(hit)
    hits = resolve_symbol(index, "Card", kind="class", path_hint="astropy/io/fits/cards.py")
    assert hits == []


def test_list_symbols_without_contains_edges_returns_empty() -> None:
    file_sym = _symbol(
        name="card.py",
        file="astropy/io/fits/card.py",
        kind=SymbolKind.FILE,
        symbol_id="astropy/io/fits/card.py",
        qualified="astropy/io/fits/card.py",
    )
    klass = _symbol(
        name="Card",
        file="astropy/io/fits/card.py",
        kind=SymbolKind.CLASS,
        line=10,
    )
    index = _index(file_sym, klass)
    hits = list_symbols(index, file="astropy/io/fits/card.py", depth=1)
    assert hits == []


def test_tokenize_trailing_underscore_keeps_the_raw_token() -> None:
    tokens = tokenize("SECURE_")
    assert "secure_" in tokens
