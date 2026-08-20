# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared reader for the Skill library's ``skills_state.json``.

The library-wide kill switch is read in one place and consumed by both the
single-agent rail assembly and the team Skill rail. These tests pin the
contract that reader has to keep: only ``enabled: false`` entries count, the
result is sorted and de-duplicated across roots, and every unreadable shape
degrades to "nothing is switched off" rather than blanking an agent's Skill
view. A guard pins that neither consumer grows a second parser.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.harness.skills import collect_disabled_skills
from openjiuwen.harness.skills.library_state import SKILLS_STATE_FILENAME
from tests.test_logger import logger as test_logger


def _write_state(library: Path, payload: object) -> None:
    """Write a ``skills_state.json`` payload into *library*."""
    library.mkdir(parents=True, exist_ok=True)
    (library / SKILLS_STATE_FILENAME).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


@pytest.mark.level0
def test_collect_disabled_skills_reads_disabled_entries(tmp_path: Path) -> None:
    """Only entries whose stored config says ``enabled: false`` are collected."""
    library = tmp_path / "library"
    _write_state(
        library,
        {
            "skill_configs": {
                "gamma": {"enabled": False},
                "alpha": {"enabled": True},
                "beta": {},
            }
        },
    )

    disabled = collect_disabled_skills([str(library)])

    test_logger.info(f"disabled skills: {disabled}")
    assert disabled == ["gamma"]


@pytest.mark.level1
def test_collect_disabled_skills_merges_roots_sorted(tmp_path: Path) -> None:
    """Names from several roots are merged, de-duplicated and sorted."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_state(first, {"skill_configs": {"zeta": {"enabled": False}}})
    _write_state(
        second,
        {"skill_configs": {"zeta": {"enabled": False}, "alpha": {"enabled": False}}},
    )

    disabled = collect_disabled_skills([first, str(second)])

    assert disabled == ["alpha", "zeta"]


@pytest.mark.level1
def test_collect_disabled_skills_tolerates_missing_and_corrupt_state(tmp_path: Path) -> None:
    """A missing, malformed or wrongly-shaped state file disables nothing."""
    missing = tmp_path / "missing"
    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / SKILLS_STATE_FILENAME).write_text("{not json", encoding="utf-8")
    not_an_object = tmp_path / "list-root"
    _write_state(not_an_object, ["gamma"])
    bad_configs = tmp_path / "bad-configs"
    _write_state(bad_configs, {"skill_configs": ["gamma"]})
    bad_entry = tmp_path / "bad-entry"
    _write_state(bad_entry, {"skill_configs": {"gamma": "disabled"}})

    disabled = collect_disabled_skills(
        [
            str(missing),
            str(corrupt),
            str(not_an_object),
            str(bad_configs),
            str(bad_entry),
        ],
    )

    test_logger.info(f"disabled skills from broken state files: {disabled}")
    assert disabled == []


def _names_state_file_in_code(module: object) -> bool:
    """Return whether *module* mentions the state file outside its docs.

    Prose is free to name the file; building a path from it is what marks a
    second parser, so docstrings are stripped before looking.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node, clean=False)
        if doc is not None:
            docstrings.add(doc)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.value in docstrings:
            continue
        if SKILLS_STATE_FILENAME in node.value:
            return True
    return False


@pytest.mark.level1
def test_skill_library_state_has_a_single_parser() -> None:
    """Both consumers route through this reader instead of parsing it again.

    The state file was parsed twice for a while — once privately inside the
    rail factory and once in the team package — so a format change had to be
    applied in two places or the two views of the library would drift. This
    pins that neither consumer rebuilds the path itself; naming the file in
    prose stays fine.
    """
    import inspect

    from openjiuwen.agent_teams.rails import team_skill_use_rail
    from openjiuwen.harness import factory

    assert not _names_state_file_in_code(factory)
    assert not _names_state_file_in_code(team_skill_use_rail)
    assert "collect_disabled_skills" in inspect.getsource(factory)
    assert "collect_disabled_skills" in inspect.getsource(team_skill_use_rail)
