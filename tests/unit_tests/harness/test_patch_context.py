# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PATCH_CONTEXT must always be a well-formed File/Lines block."""

from __future__ import annotations

from openjiuwen.harness.schema.code_graph import CodeGraphLocation
from openjiuwen.harness.tools.code_graph.patch_context import (
    format_patch_context,
    merge_locations,
    normalize_submit_locations,
    shrink_to_symbol,
)

import pytest

pytestmark = pytest.mark.level0


def test_format_patch_context_is_always_well_formed() -> None:
    block = format_patch_context(
        [
            CodeGraphLocation(
                symbol_id="a.py::Foo",
                file="pkg/a.py",
                start_line=10,
                end_line=20,
                reason="primary",
            ),
            CodeGraphLocation(
                symbol_id="a.py::Bar",
                file="pkg/a.py",
                start_line=18,
                end_line=30,
                reason="overlap",
            ),
        ]
    )
    assert block.startswith("<PATCH_CONTEXT>\n")
    assert block.endswith("\n</PATCH_CONTEXT>")
    assert "File: pkg/a.py" in block
    assert "Lines: 10-30" in block
    assert block.count("<PATCH_CONTEXT>") == 1
    assert block.count("</PATCH_CONTEXT>") == 1


def test_format_patch_context_empty_is_empty() -> None:
    assert format_patch_context([]) == ""


def test_shrink_to_symbol_keeps_the_definition_span() -> None:
    location = CodeGraphLocation(
        symbol_id="a.py::Foo",
        file="a.py",
        start_line=1,
        end_line=400,
        reason="whole file",
    )
    shrunk = shrink_to_symbol(
        location,
        {"symbol_id": "a.py::Foo", "file": "a.py", "start_line": 40, "end_line": 80},
    )
    assert shrunk.start_line == 40
    assert shrunk.end_line == 80


def test_merge_adjacent_spans() -> None:
    merged = merge_locations(
        [
            CodeGraphLocation("x", "a.py", 1, 4, "a"),
            CodeGraphLocation("y", "a.py", 5, 8, "b"),
        ]
    )
    assert len(merged) == 1
    assert merged[0].start_line == 1
    assert merged[0].end_line == 8


def test_normalize_replaces_large_class_with_methods() -> None:
    class_loc = CodeGraphLocation(
        symbol_id="card.py::Card",
        file="card.py",
        start_line=41,
        end_line=1243,
        reason="class",
        kind="class",
        name="Card",
    )
    normalized, blockers = normalize_submit_locations(
        [class_loc],
        read_evidence={
            "ev1": {
                "symbol_id": "card.py::Card._parse_value",
                "file": "card.py",
                "kind": "method",
                "name": "_parse_value",
                "symbol_start_line": 900,
                "symbol_end_line": 950,
                "evidence_id": "ev1",
            }
        },
    )
    assert not blockers
    assert len(normalized) == 1
    assert normalized[0].start_line == 900
    assert normalized[0].end_line == 950


def test_normalize_blocks_large_class_without_methods() -> None:
    class_loc = CodeGraphLocation(
        symbol_id="card.py::Card",
        file="card.py",
        start_line=41,
        end_line=1243,
        reason="class",
        kind="class",
        name="Card",
    )
    normalized, blockers = normalize_submit_locations([class_loc])
    assert normalized == []
    assert blockers
    assert "inspect_code_structure" in blockers[0]
