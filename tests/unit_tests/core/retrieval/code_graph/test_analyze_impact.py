# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Impact-surface grouping and deterministic risk rules."""

from __future__ import annotations

import pytest

from openjiuwen.core.retrieval.code_graph.models import Symbol, SymbolKind
from openjiuwen.core.retrieval.code_graph.query.analyze_impact import (
    HIGH_CALLER_COUNT,
    HIGH_DERIVED_COUNT,
    HIGH_MODULE_COUNT,
    MEDIUM_CALLER_COUNT,
    MEDIUM_MODULE_COUNT,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    _high_change_surface,
    _medium_change_surface,
    _risk,
    _rows_from_levels,
)

pytestmark = pytest.mark.level0


def _symbol(*, name: str = "Foo", kind: SymbolKind = SymbolKind.CLASS) -> Symbol:
    return Symbol(
        symbol_id=f"pkg/mod.py::{name}",
        name=name,
        kind=kind,
        file="pkg/mod.py",
        start_line=1,
        end_line=10,
    )


def _empty_risk_kwargs() -> dict[str, object]:
    return {
        "direct_callers": [],
        "transitive_callers": [],
        "subclasses": [],
        "implementations": [],
        "imports": [],
        "tests": [],
        "truncated": False,
        "unresolved": [],
    }


def test_rows_from_levels_keeps_depth_order_without_nested_comprehensions() -> None:
    levels = {
        2: [{"name": "mid"}],
        1: [{"name": "direct"}],
        3: [{"name": "deep"}],
    }
    assert [row["name"] for row in _rows_from_levels(levels)] == ["direct", "mid", "deep"]
    assert [row["name"] for row in _rows_from_levels(levels, min_level=2)] == ["mid", "deep"]


def test_high_change_surface_uses_independent_thresholds() -> None:
    assert _high_change_surface(HIGH_CALLER_COUNT, set(), 0)
    assert _high_change_surface(0, {f"m{i}" for i in range(HIGH_MODULE_COUNT)}, 0)
    assert _high_change_surface(0, set(), HIGH_DERIVED_COUNT)
    assert not _high_change_surface(HIGH_CALLER_COUNT - 1, {"a", "b"}, HIGH_DERIVED_COUNT - 1)


def test_medium_change_surface_covers_each_signal() -> None:
    assert _medium_change_surface(MEDIUM_CALLER_COUNT, set(), 0, False, [])
    assert _medium_change_surface(0, {f"m{i}" for i in range(MEDIUM_MODULE_COUNT)}, 0, False, [])
    assert _medium_change_surface(0, set(), 1, False, [])
    assert _medium_change_surface(0, set(), 0, True, [])
    assert _medium_change_surface(0, set(), 0, False, [{"callee": "x"}])
    assert not _medium_change_surface(0, set(), 0, False, [])


def test_risk_levels_stay_aligned_with_the_helpers() -> None:
    private = _symbol(name="_hidden", kind=SymbolKind.FUNCTION)
    public = _symbol(name="Visible")

    low = _risk(private, **_empty_risk_kwargs())  # type: ignore[arg-type]
    assert low["level"] == RISK_LOW

    medium_kwargs = _empty_risk_kwargs()
    medium_kwargs["truncated"] = True
    medium = _risk(public, **medium_kwargs)  # type: ignore[arg-type]
    assert medium["level"] == RISK_MEDIUM

    high_kwargs = _empty_risk_kwargs()
    high_kwargs["direct_callers"] = [{"file": f"m{i}/x.py"} for i in range(HIGH_MODULE_COUNT)]
    high = _risk(public, **high_kwargs)  # type: ignore[arg-type]
    assert high["level"] == RISK_HIGH
