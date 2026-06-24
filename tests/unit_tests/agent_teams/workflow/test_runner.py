# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for swarmflow runner journal-path wiring (``_resolve_journal_path``)."""
from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.agent_teams import paths
from openjiuwen.agent_teams.workflow.runner import _resolve_journal_path
from openjiuwen.core.common.exception.errors import BaseError


def teardown_function():
    paths.reset_openjiuwen_home()


def _write_script(tmp_path: Path, name: str | None) -> str:
    """Write a minimal swarmflow script and return its path."""
    if name is None:
        meta = '{"description": "d"}'
    else:
        meta = f'{{"name": "{name}", "description": "d"}}'
    script = tmp_path / "wf.py"
    script.write_text(f"META = {meta}\nasync def run(args):\n    return 1\n", encoding="utf-8")
    return str(script)


def test_resolve_journal_path_maps_to_session_workflow(tmp_path):
    paths.configure_openjiuwen_home(tmp_path / "home")
    script = _write_script(tmp_path, "myflow")

    result = _resolve_journal_path(script, "demo-team", "sess-1")

    expected = paths.workflow_journal_path("demo-team", "sess-1", "myflow")
    assert Path(result) == expected
    assert expected.parent.is_dir()  # parent dir is created for Journal.save


def test_resolve_journal_path_defaults_blank_session(tmp_path):
    paths.configure_openjiuwen_home(tmp_path / "home")
    script = _write_script(tmp_path, "myflow")

    result = _resolve_journal_path(script, "demo-team", "")

    assert Path(result) == paths.workflow_journal_path("demo-team", "default", "myflow")


def test_resolve_journal_path_requires_meta_name(tmp_path):
    paths.configure_openjiuwen_home(tmp_path / "home")
    script = _write_script(tmp_path, None)

    with pytest.raises(BaseError):
        _resolve_journal_path(script, "demo-team", "sess-1")
