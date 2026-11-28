"""The reference rail reports namespace roots that are missing on disk.

A pip-installed openjiuwen ships the package but not the repository's docs/
and examples/; the coding agent's openjiuwen_ref_* tools then see nothing
there. The rail must say so instead of running without its reference.
"""

from __future__ import annotations

import logging

import pytest

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails import openjiuwen_reference_rail as rail
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.reference_index import (
    ReferenceRoots,
)

_LOGGER_NAME = rail.__name__


@pytest.fixture(autouse=True)
def _reset_warned_roots():
    rail._warned_missing_roots.clear()
    yield
    rail._warned_missing_roots.clear()


def _roots(tmp_path, *, docs: bool, examples: bool) -> ReferenceRoots:
    source = tmp_path / "openjiuwen"
    source.mkdir()
    roots = ReferenceRoots(examples=tmp_path / "examples", source=source, docs=tmp_path / "docs")
    if docs:
        roots.docs.mkdir()
    if examples:
        roots.examples.mkdir()
    return roots


def test_missing_reference_roots_lists_absent_namespaces(tmp_path):
    roots = _roots(tmp_path, docs=False, examples=True)

    assert rail.missing_reference_roots(roots) == [("docs", tmp_path / "docs")]


def test_complete_checkout_logs_nothing(tmp_path, caplog):
    roots = _roots(tmp_path, docs=True, examples=True)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        assert rail.warn_missing_reference_roots(roots) == []

    assert not [r for r in caplog.records if r.name == _LOGGER_NAME]


def test_missing_roots_warn_once_per_root(tmp_path, caplog):
    roots = _roots(tmp_path, docs=False, examples=False)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        first = rail.warn_missing_reference_roots(roots)
        second = rail.warn_missing_reference_roots(roots)

    assert [name for name, _ in first] == ["examples", "docs"]
    assert [name for name, _ in second] == ["examples", "docs"]
    warnings = [r for r in caplog.records if r.name == _LOGGER_NAME and r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "'examples'" in warnings[0].getMessage() and "'docs'" in warnings[1].getMessage()
    assert "pip-installed" in warnings[0].getMessage()
