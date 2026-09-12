# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ripgrep binary resolution and GNU grep fallback quoting."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openjiuwen.harness.tools import GrepTool
from openjiuwen.harness.tools import rg_binary as rg_mod


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch: pytest.MonkeyPatch):
    rg_mod.clear_rg_binary_cache()
    monkeypatch.delenv("OPENJIUWEN_RG", raising=False)
    monkeypatch.delenv("ICODE_RG", raising=False)
    yield
    rg_mod.clear_rg_binary_cache()


def test_resolve_rg_binary_prefers_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "rg"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("OPENJIUWEN_RG", str(fake))
    rg_mod.clear_rg_binary_cache()
    assert rg_mod.resolve_rg_binary() == str(fake.resolve())


def test_build_grep_command_uses_dash_e_and_end_of_options() -> None:
    tool = GrepTool(MagicMock())
    cmd = tool._build_grep_command(
        pattern="--config",
        path="/workspace/ruff",
        glob="*.rs",
        output_mode="content",
        context_before=2,
        context_after=2,
        context_c=None,
        context=None,
        show_line_numbers=True,
        case_insensitive=False,
        multiline=False,
    )
    assert cmd is not None
    assert " -e " in cmd
    assert " -- " in cmd
    assert "grep: unrecognized option" not in cmd
    # Pattern must not appear as a bare argv that grep could treat as a flag.
    assert " -e '--config' -- " in cmd or ' -e "--config" -- ' in cmd or " -e --config -- " in cmd


def test_build_rg_command_quotes_custom_binary_path(tmp_path: Path) -> None:
    tool = GrepTool(MagicMock())
    rg = tmp_path / "my rg"
    rg.write_text("x", encoding="utf-8")
    cmd = tool._build_rg_command(
        pattern="foo",
        path="/tmp",
        glob=None,
        output_mode="content",
        context_before=None,
        context_after=None,
        context_c=None,
        context=None,
        show_line_numbers=True,
        case_insensitive=False,
        file_type=None,
        multiline=False,
        rg_path=str(rg),
    )
    assert str(rg) in cmd or "my rg" in cmd
    assert cmd.startswith("'") or cmd.startswith('"') or cmd.startswith(str(tmp_path))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("rs", "rust"),
        (".rs", "rust"),
        ("Rust", "rust"),
        ("py", "py"),
        ("python", "py"),
        ("ts", "ts"),
        ("tsx", "ts"),
        (".tsx", "ts"),
        ("jsx", "js"),
        ("javascript", "js"),
        ("unknownlang", "unknownlang"),
        ("MyType", "MyType"),
        (".CustomType", ".CustomType"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_rg_file_type(raw: str | None, expected: str | None) -> None:
    assert GrepTool._normalize_rg_file_type(raw) == expected


def test_resolve_rg_binary_picks_up_runtime_env_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "rg-a"
    second = tmp_path / "rg-b"
    for path in (first, second):
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)

    monkeypatch.setenv("OPENJIUWEN_RG", str(first))
    assert rg_mod.resolve_rg_binary() == str(first.resolve())

    monkeypatch.setenv("OPENJIUWEN_RG", str(second))
    assert rg_mod.resolve_rg_binary() == str(second.resolve())

    monkeypatch.delenv("OPENJIUWEN_RG", raising=False)
    # Falls through to PATH / vendor; just ensure env clear is observed.
    assert rg_mod.resolve_rg_binary() != str(second.resolve())


def test_build_rg_command_uses_normalized_rust_type() -> None:
    tool = GrepTool(MagicMock())
    normalized = GrepTool._normalize_rg_file_type("rs")
    cmd = tool._build_rg_command(
        pattern="--config",
        path="/workspace/ruff",
        glob="*.rs",
        output_mode="content",
        context_before=2,
        context_after=2,
        context_c=None,
        context=None,
        show_line_numbers=True,
        case_insensitive=False,
        file_type=normalized,
        multiline=False,
        rg_path="rg",
    )
    assert "--type rust" in cmd or "--type 'rust'" in cmd or '--type "rust"' in cmd
    assert "--type rs" not in cmd
