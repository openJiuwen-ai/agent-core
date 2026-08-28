# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for BM25 search_code ranking and test-path filtering."""

from __future__ import annotations

import pytest

from openjiuwen.core.retrieval.code_graph.models import (
    CodeGraphConfig,
    CodeGraphIndex,
    Symbol,
    SymbolKind,
)
from openjiuwen.core.retrieval.code_graph.query.search_code import search_code
from openjiuwen.core.retrieval.code_graph.query.test_paths import is_test_path, issue_about_tests

pytestmark = pytest.mark.level0


def _symbol(
    *,
    name: str,
    file: str,
    kind: SymbolKind = SymbolKind.FUNCTION,
    qualified: str | None = None,
    line: int = 1,
) -> Symbol:
    qname = qualified or name
    return Symbol(
        symbol_id=f"{file}::{qname}",
        name=name,
        kind=kind,
        file=file,
        start_line=line,
        end_line=line + 4,
        qualified_name=qname,
        language="python",
        signature=f"def {name}():",
    )


def _index(*symbols: Symbol) -> CodeGraphIndex:
    index = CodeGraphIndex(repo_root="/repo", snapshot="s", config_hash="h")
    for symbol in symbols:
        index.add_symbol(symbol)
    return index


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_altaz.py", True),
        ("testing/helpers.py", True),
        ("src/test_foo.py", True),
        ("pkg/foo_test.py", True),
        ("conftest.py", True),
        ("astropy/coordinates/tests/test_frames.py", True),
        ("src/altaz.py", False),
        ("astropy/coordinates/builtin_frames/altaz.py", False),
    ],
)
def test_is_test_path(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("pytest is failing in the unit tests", True),
        ("ITRS to AltAz transform is missing", False),
        ("the test suite does not cover obstime", True),
    ],
)
def test_issue_about_tests(text: str, expected: bool) -> None:
    assert issue_about_tests(text) is expected


def test_class_name_query_ranks_exact_name_first() -> None:
    index = _index(
        _symbol(name="_HeaderComments", file="io/fits/header.py", kind=SymbolKind.CLASS),
        _symbol(name="Card", file="io/fits/card.py", kind=SymbolKind.CLASS),
        _symbol(name="CardList", file="io/fits/header.py", kind=SymbolKind.CLASS),
    )
    hits = search_code(index, "Card", limit=5)
    assert hits
    assert hits[0].name == "Card"
    assert hits[0].file == "io/fits/card.py"


def test_ban_tests_drops_test_files() -> None:
    index = _index(
        _symbol(name="AltAz", file="src/altaz.py", kind=SymbolKind.CLASS),
        _symbol(name="test_altaz", file="tests/test_altaz.py"),
        _symbol(name="AltAz", file="tests/test_frames.py", kind=SymbolKind.CLASS, qualified="test.AltAz"),
    )
    banned = search_code(index, "AltAz", limit=10, ban_tests=True)
    files = {item.file for item in banned}
    assert "src/altaz.py" in files
    assert not any(is_test_path(path) for path in files)

    included = search_code(index, "AltAz", limit=10, ban_tests=False)
    included_files = {item.file for item in included}
    assert "tests/test_frames.py" in included_files


def test_retrieval_settings_do_not_change_config_hash() -> None:
    base = CodeGraphConfig()
    other = CodeGraphConfig(ban_tests=False, search_backend="token_overlap")
    assert base.config_hash() == other.config_hash()


def test_lexical_settings_change_config_hash() -> None:
    base = CodeGraphConfig()
    other = CodeGraphConfig(text_chunk_chars=500)
    assert base.config_hash() != other.config_hash()
