# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Code Graph parser stays optional when the language pack is missing or broken."""

from __future__ import annotations

import builtins
import sys
import types
from pathlib import Path

import pytest

from openjiuwen.core.retrieval.code_graph.indexing import parser as parser_mod
from openjiuwen.core.retrieval.code_graph.indexing.language_registry import SourceLanguage
from tests.unit_tests.core.retrieval.code_graph.parser_guard import skip_unless_code_graph_parser

pytestmark = pytest.mark.level0


@pytest.fixture(autouse=True)
def _reset_parser_state() -> None:
    parser_mod._reset_parser_state()
    yield
    parser_mod._reset_parser_state()


def test_parser_reports_unavailable_when_language_pack_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object):
        if name == "tree_sitter_language_pack" or name.startswith("tree_sitter_language_pack."):
            raise ImportError("language pack blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    parser_mod._reset_parser_state()
    assert parser_mod.parser_available() is False
    reason = parser_mod.parser_unavailable_reason()
    assert "tree-sitter-language-pack" in reason
    assert "openjiuwen[code-graph]" in reason


def test_profile_rail_warns_when_yaml_is_on_but_extra_is_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from openjiuwen.harness.rails.code_graph_profile_rail import _warn_if_parser_missing

    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.indexing.parser.parser_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "openjiuwen.core.retrieval.code_graph.indexing.parser.parser_unavailable_reason",
        lambda: "tree-sitter-language-pack is not installed; install openjiuwen[code-graph]",
    )
    _warn_if_parser_missing()
    assert "profile=graph but the parser is unavailable" in caplog.text
    assert "openjiuwen[code-graph]" in caplog.text


class DownloadError(Exception):
    """Stand-in for tree_sitter_language_pack.DownloadError."""


class CacheLockError(Exception):
    """Stand-in for tree_sitter_language_pack.CacheLockError."""


@pytest.fixture
def allow_parser_download(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parser_mod, "_network_fetch_allowed", lambda: True)


@pytest.mark.usefixtures("allow_parser_download")
def test_download_error_is_cached_and_disables_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _boom(_name: str) -> None:
        calls["n"] += 1
        raise DownloadError(
            "Failed to download https://github.com/xberg-io/tree-sitter-language-pack/"
            "releases/download/v1.15.0/parsers-linux-x86_64.tar.zst: timeout: global"
        )

    monkeypatch.setattr(parser_mod, "_load_get_parser", lambda: _boom)
    parser_mod._parser_for.cache_clear()

    assert parser_mod.parse_source_as(SourceLanguage.PYTHON, b"x = 1\n") is None
    assert parser_mod.parser_available() is False
    assert "Failed to download" in parser_mod.parser_unavailable_reason()
    assert "openjiuwen[code-graph] is not enough" in parser_mod.parser_unavailable_reason()
    assert parser_mod.parse_source_as(SourceLanguage.PYTHON, b"y = 2\n") is None
    assert calls["n"] == 1


@pytest.mark.usefixtures("allow_parser_download")
def test_cache_lock_error_disables_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_name: str) -> None:
        raise CacheLockError("Download cache lock error: acquire exclusive download lock")

    monkeypatch.setattr(parser_mod, "_load_get_parser", lambda: _boom)
    parser_mod._parser_for.cache_clear()

    assert parser_mod.parse_source_as(SourceLanguage.PYTHON, b"x = 1\n") is None
    assert parser_mod.parser_available() is False
    assert "download cache lock" in parser_mod.parser_unavailable_reason().lower()


@pytest.mark.usefixtures("allow_parser_download")
def test_unknown_language_error_does_not_disable_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_name: str) -> None:
        raise ValueError("unknown language: fortran")

    monkeypatch.setattr(parser_mod, "_load_get_parser", lambda: _boom)
    parser_mod._parser_for.cache_clear()

    assert parser_mod.parse_source_as(SourceLanguage.PYTHON, b"x = 1\n") is None
    assert parser_mod.parser_available() is True


def test_python_grammar_is_cached_false_when_cache_dir_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "libs"
    cache.mkdir()
    fake = types.ModuleType("tree_sitter_language_pack")
    fake.get_parser = lambda name: None
    fake.cache_dir = lambda: str(cache)
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", fake)
    parser_mod._reset_parser_state()
    assert parser_mod.python_grammar_is_cached() is False


def test_python_grammar_is_cached_true_when_python_so_is_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "libs"
    cache.mkdir()
    (cache / "libtree_sitter_python.so").write_bytes(b"")
    fake = types.ModuleType("tree_sitter_language_pack")
    fake.get_parser = lambda name: None
    fake.cache_dir = lambda: str(cache)
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", fake)
    parser_mod._reset_parser_state()
    assert parser_mod.python_grammar_is_cached() is True


def test_python_grammar_is_cached_true_when_so_is_nested_under_version_libs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "tree-sitter-language-pack"
    libs = cache / "v1.15.0" / "libs"
    libs.mkdir(parents=True)
    (libs / "libtree_sitter_python.so").write_bytes(b"")
    fake = types.ModuleType("tree_sitter_language_pack")
    fake.get_parser = lambda name: None
    fake.cache_dir = lambda: str(cache)
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", fake)
    parser_mod._reset_parser_state()
    assert parser_mod.python_grammar_is_cached() is True


def test_uncached_grammar_does_not_call_get_parser_during_pytest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "libs"
    cache.mkdir()
    calls = {"n": 0}

    def _boom(_name: str) -> None:
        calls["n"] += 1
        raise DownloadError("Failed to download parsers-linux-x86_64.tar.zst: timeout: global")

    fake = types.ModuleType("tree_sitter_language_pack")
    fake.get_parser = _boom
    fake.cache_dir = lambda: str(cache)
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", fake)
    parser_mod._reset_parser_state()

    assert parser_mod.parse_source_as(SourceLanguage.PYTHON, b"x = 1\n") is None
    assert parser_mod.parser_available() is False
    assert "not in the local cache" in parser_mod.parser_unavailable_reason()
    assert parser_mod.parse_source_as(SourceLanguage.PYTHON, b"y = 2\n") is None
    assert calls["n"] == 0


def test_python_grammar_is_cached_none_when_pack_has_no_cache_api(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("tree_sitter_language_pack")
    fake.get_parser = lambda name: None
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", fake)
    parser_mod._reset_parser_state()
    assert parser_mod.python_grammar_is_cached() is None


def test_skip_guard_skips_when_python_grammar_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tests.unit_tests.core.retrieval.code_graph.parser_guard.python_grammar_is_cached",
        lambda: False,
    )
    monkeypatch.setattr(pytest, "importorskip", lambda _name: None)
    with pytest.raises(pytest.skip.Exception, match="not cached"):
        skip_unless_code_graph_parser()


def test_skip_guard_continues_when_cache_api_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tests.unit_tests.core.retrieval.code_graph.parser_guard.python_grammar_is_cached",
        lambda: None,
    )
    monkeypatch.setattr(pytest, "importorskip", lambda _name: None)
    skip_unless_code_graph_parser()
