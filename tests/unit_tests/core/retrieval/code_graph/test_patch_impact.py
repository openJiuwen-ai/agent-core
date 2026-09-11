# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Patch-focus matching and patch-level risk rules."""

from __future__ import annotations

import pytest

from openjiuwen.core.retrieval.code_graph.models import Symbol, SymbolKind
from openjiuwen.core.retrieval.code_graph.query.analyze_impact import RISK_HIGH, RISK_LOW, RISK_MEDIUM
from openjiuwen.core.retrieval.code_graph.query.patch_impact import (
    _file_matches_token_head,
    _patch_risk,
    _patch_risk_high,
    _patch_risk_medium,
    _suffix_overlap,
    _symbol_matches_focus,
)

pytestmark = pytest.mark.level0


def _symbol(*, symbol_id: str = "src/user.py::UserService.create_user", name: str = "create_user") -> Symbol:
    return Symbol(
        symbol_id=symbol_id,
        name=name,
        kind=SymbolKind.METHOD,
        file="src/user.py",
        start_line=4,
        end_line=8,
    )


def test_symbol_matches_exact_id_name_or_file() -> None:
    symbol = _symbol()
    assert _symbol_matches_focus(symbol, ["src/user.py::UserService.create_user"])
    assert _symbol_matches_focus(symbol, ["create_user"])
    assert _symbol_matches_focus(symbol, ["src/user.py"])
    assert not _symbol_matches_focus(symbol, ["missing"])


def test_symbol_matches_suffix_tokens_only_when_the_file_head_aligns() -> None:
    symbol = _symbol()
    assert _suffix_overlap("UserService.create_user", symbol.symbol_id, symbol.name)
    assert _file_matches_token_head(symbol.file, "src/user.py::UserService.create_user")
    assert _symbol_matches_focus(symbol, ["src/user.py::UserService.create_user"])
    assert not _symbol_matches_focus(symbol, ["other/path.py::create_user"])


def test_patch_risk_high_when_dangling_or_untested_removals() -> None:
    removed = [_symbol()]
    assert _patch_risk_high([{"missing": "x"}], set(), [], [])
    assert _patch_risk_high([], {RISK_HIGH}, [], [{"file": "tests/test_user.py"}])
    assert _patch_risk_high([], set(), removed, [])
    assert not _patch_risk_high([], set(), removed, [{"file": "tests/test_user.py"}])


def test_patch_risk_medium_covers_each_signal() -> None:
    assert _patch_risk_medium([{"id": "x"}], [], [{"file": "t.py"}], set(), False)
    assert _patch_risk_medium([], [("a", "calls", "b")], [{"file": "t.py"}], set(), False)
    assert _patch_risk_medium([], [], [], set(), False)
    assert _patch_risk_medium([], [], [{"file": "t.py"}], {RISK_MEDIUM}, False)
    assert _patch_risk_medium([], [], [{"file": "t.py"}], set(), True)
    assert not _patch_risk_medium([], [], [{"file": "t.py"}], set(), False)


def test_patch_risk_levels_stay_aligned_with_the_helpers() -> None:
    high = _patch_risk(
        surfaces=[],
        removed_edges=[],
        removed_symbols=[_symbol()],
        dangling=[],
        unwired=[],
        tests=[],
        truncated=False,
    )
    assert high["level"] == RISK_HIGH

    medium = _patch_risk(
        surfaces=[{"risk": {"level": RISK_MEDIUM}}],
        removed_edges=[],
        removed_symbols=[],
        dangling=[],
        unwired=[],
        tests=[{"file": "tests/test_user.py"}],
        truncated=False,
    )
    assert medium["level"] == RISK_MEDIUM

    low = _patch_risk(
        surfaces=[{"risk": {"level": RISK_LOW}}],
        removed_edges=[],
        removed_symbols=[],
        dangling=[],
        unwired=[],
        tests=[{"file": "tests/test_user.py"}],
        truncated=False,
    )
    assert low["level"] == RISK_LOW
